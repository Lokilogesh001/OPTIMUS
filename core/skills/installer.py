"""core/skills/installer.py — Native Skill Installation & Package Manager for OPTIMUS.

Implements the 9-stage installation lifecycle:
IMPORT → INSPECT → VALIDATE → RESOLVE DEPENDENCIES → PACKAGE → INSTALL → VERIFY → REGISTER → ENABLE

Guarantees:
- DISCOVERING A SKILL IS NOT INSTALLING A SKILL.
- Native storage in `data/skills/installed/<skill_id>/<version>/`.
- Complete runtime independence from original external sources.
- Atomic staging and transactional rollback on installation error.
- Non-destructive imports (zero code execution during import).
- Never stores credentials in skill packages.
- Safe uninstallation that modifies only OPTIMUS-managed library files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.skills.dependency import DependencyResolver
from core.skills.discovery import parse_frontmatter
from core.skills.models import (
    SkillCategory,
    SkillManifest,
    SkillSecurityReport,
    SkillTrustLevel,
    SkillType,
)
from core.skills.package import (
    DependencyStatus,
    HardwareRequirements,
    SkillInstallAction,
    SkillInstallHistoryRecord,
    SkillPackageManifest,
)
from core.skills.registry import SkillRegistry
from core.skills.security import SkillSecurityValidator

logger = logging.getLogger("optimus.skills.installer")


@dataclass
class InstallResult:
    """Outcome of a skill installation or lifecycle operation."""
    success: bool
    skill_id: str
    version: str = "1.0.0"
    message: str = ""
    installed_path: str = ""
    manifest: SkillPackageManifest | None = None
    security_report: SkillSecurityReport | None = None
    dependency_issues: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class NativeSkillInstaller:
    """Manages importation, validation, packaging, native installation, and lifecycle operations."""

    def __init__(
        self,
        data_root: Path | str | None = None,
        registry: SkillRegistry | None = None,
        security_validator: SkillSecurityValidator | None = None,
        dependency_resolver: DependencyResolver | None = None,
    ) -> None:
        if data_root is None:
            # Default to <project_root>/data/skills
            data_root = Path(__file__).resolve().parent.parent.parent / "data" / "skills"

        self.data_root = Path(data_root)
        self.installed_dir = self.data_root / "installed"
        self.staging_dir = self.data_root / ".staging"
        self.history_file = self.data_root / "install_history.json"
        self.manifest_file = self.data_root / "manifest.json"

        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        self.registry = registry or SkillRegistry.instance()
        self.security_validator = security_validator or SkillSecurityValidator()
        if dependency_resolver is None:
            from core.orchestrator.tools import ToolRegistry
            self.dependency_resolver = DependencyResolver(tool_registry=ToolRegistry())
        else:
            self.dependency_resolver = dependency_resolver

    # ── 1. Import Stage ──────────────────────────────────────────────────────

    def import_source(self, source: Path | str, skill_id_override: str | None = None) -> tuple[bool, Path | str, str]:
        """Stage an external skill directory, SKILL.md file, or ZIP archive into atomic staging."""
        src_path = Path(source).resolve()
        if not src_path.exists():
            return False, "", f"Source path '{source}' does not exist."

        # Determine target skill ID
        if skill_id_override:
            skill_id = skill_id_override.lower().strip()
        elif src_path.is_file() and src_path.suffix.lower() == ".zip":
            skill_id = src_path.stem.lower().strip()
        elif src_path.is_dir():
            skill_id = src_path.name.lower().strip()
        else:
            skill_id = src_path.parent.name.lower().strip()

        # Create isolated staging directory with unique timestamp
        ts = int(time.time() * 1000)
        target_stage = self.staging_dir / f"{skill_id}_{ts}"
        target_stage.mkdir(parents=True, exist_ok=True)

        try:
            if src_path.is_file() and src_path.suffix.lower() == ".zip":
                # Unpack zip safely
                with zipfile.ZipFile(src_path, "r") as zf:
                    for member in zf.infolist():
                        # Prevent Zip Slip directory traversal
                        filename = member.filename
                        if ".." in filename or filename.startswith("/"):
                            continue
                        zf.extract(member, target_stage)

                # If zip contains a single nested folder containing SKILL.md, hoist it
                entries = [e for e in target_stage.iterdir() if e.is_dir()]
                if len(entries) == 1 and (entries[0] / "SKILL.md").is_file():
                    nested = entries[0]
                    for item in nested.iterdir():
                        shutil.move(str(item), str(target_stage / item.name))
                    nested.rmdir()

            elif src_path.is_dir():
                # Copy entire directory contents (SKILL.md, resources, subfolders)
                for item in src_path.iterdir():
                    if item.name.startswith(".git"):
                        continue
                    dest = target_stage / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)

            elif src_path.is_file() and src_path.name.upper() == "SKILL.MD":
                # Copy single SKILL.md
                shutil.copy2(src_path, target_stage / "SKILL.md")

            else:
                shutil.rmtree(target_stage, ignore_errors=True)
                return False, "", f"Unsupported skill source format: '{src_path}'."

            # Verify SKILL.md exists in staged directory
            if not (target_stage / "SKILL.md").is_file():
                shutil.rmtree(target_stage, ignore_errors=True)
                return False, "", f"Staged skill at '{src_path}' does not contain 'SKILL.md'."

            return True, target_stage, f"Successfully imported '{skill_id}' to staging."

        except Exception as e:
            shutil.rmtree(target_stage, ignore_errors=True)
            return False, "", f"Failed to import skill: {e}"

    # ── 2. Full 9-Stage Installation Pipeline ────────────────────────────────

    def install_skill(
        self,
        source: Path | str,
        skill_id_override: str | None = None,
        auto_enable: bool = True,
        origin_provenance: str = "imported",
    ) -> InstallResult:
        """Execute the complete lifecycle pipeline to natively install an external skill."""
        # STAGE 1: IMPORT
        ok, stage_path, msg = self.import_source(source, skill_id_override)
        if not ok or not isinstance(stage_path, Path):
            return InstallResult(success=False, skill_id=str(source), message=msg, errors=[msg])

        staging_dir = stage_path

        try:
            # STAGE 2: INSPECT
            skill_file = staging_dir / "SKILL.md"
            text = skill_file.read_text(encoding="utf-8", errors="replace")
            meta, _body = parse_frontmatter(text)

            skill_id = skill_id_override or str(meta.get("name") or staging_dir.name.split("_")[0]).lower().strip()
            display_name = str(meta.get("name") or skill_id.replace("-", " ").title())
            version = str(meta.get("version") or "1.0.0")
            description = str(meta.get("description") or f"OPTIMUS native capability: {display_name}")
            author = str(meta.get("author") or "Ecosystem Developer")
            lic = str(meta.get("license") or "Open")
            cat_str = str(meta.get("category") or "GENERAL").upper()

            try:
                category = SkillCategory(cat_str)
            except ValueError:
                category = SkillCategory.GENERAL

            raw_tools = meta.get("tools") or []
            if isinstance(raw_tools, str):
                required_tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
            else:
                required_tools = [str(t).strip() for t in raw_tools]

            # Construct preliminary manifest for validator
            temp_manifest = SkillManifest(
                id=skill_id,
                name=display_name,
                description=description,
                version=version,
                author=author,
                license=lic,
                source_path=str(source),
                entrypoint_file="SKILL.md",
                category=category,
                required_tools=required_tools,
                instructions=text,
                provenance=origin_provenance,
            )

            # STAGE 3: VALIDATE (Security Audit)
            sec_report = self.security_validator.scan_skill(temp_manifest)
            if sec_report.trust_level == SkillTrustLevel.BLOCKED:
                shutil.rmtree(staging_dir, ignore_errors=True)
                issues = [f"{i.category}: {i.description}" for i in sec_report.issues]
                return InstallResult(
                    success=False,
                    skill_id=skill_id,
                    version=version,
                    message=f"SECURITY_BLOCKED: Skill '{skill_id}' failed static security audit.",
                    security_report=sec_report,
                    errors=issues,
                )

            # STAGE 4: RESOLVE DEPENDENCIES
            inferred_deps = self.dependency_resolver.extract_inferred_dependencies(required_tools, text)
            resolved_deps = self.dependency_resolver.resolve_dependencies(inferred_deps)
            missing_required = [
                d.name for d in resolved_deps
                if d.required and d.status == DependencyStatus.MISSING
            ]

            # STAGE 5: PACKAGE
            pkg_checksum, files_checksums = SkillPackageManifest.compute_directory_integrity(staging_dir)
            pkg_manifest = SkillPackageManifest(
                id=skill_id,
                name=display_name,
                version=version,
                description=description,
                author=author,
                license=lic,
                original_source=str(source),
                installed_at=time.time(),
                entrypoint_file="SKILL.md",
                skill_type=SkillType.INSTRUCTION if not required_tools else SkillType.TOOL_BASED,
                category=category,
                tags=[skill_id, category.value.lower(), origin_provenance],
                required_tools=required_tools,
                dependencies=resolved_deps,
                hardware_requirements=HardwareRequirements(),
                security_report=sec_report,
                integrity_checksum=pkg_checksum,
                files_checksums=files_checksums,
                enabled=auto_enable and sec_report.trust_level != SkillTrustLevel.SUSPICIOUS and not missing_required,
                metadata={"origin_provenance": origin_provenance},
            )

            # Write optimus_package.json into staging directory
            pkg_file = staging_dir / "optimus_package.json"
            pkg_file.write_text(json.dumps(pkg_manifest.to_dict(), indent=2), encoding="utf-8")

            # STAGE 6: INSTALL (Atomic Promotion to data/skills/installed/<id>/<version>/)
            target_skill_dir = self.installed_dir / skill_id
            target_version_dir = target_skill_dir / version
            backup_dir = target_skill_dir / "backups"

            target_skill_dir.mkdir(parents=True, exist_ok=True)
            backup_dir.mkdir(parents=True, exist_ok=True)

            # If any previous version exists in target_skill_dir, backup the latest one before replacement
            existing_versions = [v for v in target_skill_dir.iterdir() if v.is_dir() and v.name != "backups"]
            if existing_versions:
                latest_old = sorted(existing_versions)[-1]
                ts_bak = int(time.time())
                bak_dest = backup_dir / f"{latest_old.name}_{ts_bak}"
                shutil.copytree(latest_old, bak_dest, dirs_exist_ok=True)
                if latest_old.name == version:
                    shutil.rmtree(latest_old, ignore_errors=True)

            # Atomic move from staging to destination
            shutil.move(str(staging_dir), str(target_version_dir))

            # STAGE 7: VERIFY
            verify_ok, verify_msg = self.verify_installed_skill(skill_id, version)
            if not verify_ok:
                # Rollback staging/installation
                shutil.rmtree(target_version_dir, ignore_errors=True)
                return InstallResult(
                    success=False,
                    skill_id=skill_id,
                    version=version,
                    message=f"INSTALLATION_INTEGRITY_FAILED: {verify_msg}",
                    errors=[verify_msg],
                )

            # STAGE 8: REGISTER in Central SkillRegistry
            registered_manifest = SkillManifest(
                id=skill_id,
                name=display_name,
                description=description,
                version=version,
                author=author,
                license=lic,
                source_path=str(target_version_dir),
                entrypoint_file="SKILL.md",
                skill_type=pkg_manifest.skill_type,
                category=category,
                tags=pkg_manifest.tags,
                required_tools=required_tools,
                instructions=text,
                content_sha256=pkg_checksum,
                enabled=pkg_manifest.enabled,
                trust_level=sec_report.trust_level,
                security_report=sec_report,
                dependencies=[d.name for d in resolved_deps],
                provenance=f"native_{origin_provenance}",
                is_installed=True,
                installed_path=str(target_version_dir),
                installed_at=time.time(),
            )
            self.registry.register(registered_manifest, overwrite=True)

            # STAGE 9: AUDIT LOG
            self._log_history(
                skill_id=skill_id,
                action=SkillInstallAction.INSTALL,
                version=version,
                source=str(source),
                status="success",
                details=f"Installed natively to '{target_version_dir}'. Enabled={pkg_manifest.enabled}.",
            )

            # Update Native Library Manifest index
            self._update_library_manifest()

            return InstallResult(
                success=True,
                skill_id=skill_id,
                version=version,
                message=f"Successfully installed skill '{display_name}' ({version}) into native OPTIMUS library.",
                installed_path=str(target_version_dir),
                manifest=pkg_manifest,
                security_report=sec_report,
                dependency_issues=missing_required,
            )

        except Exception as e:
            shutil.rmtree(staging_dir, ignore_errors=True)
            logger.error(f"Installation pipeline error for '{source}': {e}", exc_info=True)
            return InstallResult(
                success=False,
                skill_id=skill_id_override or "unknown",
                message=f"Internal installation error: {e}",
                errors=[str(e)],
            )

    # ── 3. Integrity Verification ────────────────────────────────────────────

    def verify_installed_skill(self, skill_id: str, version: str) -> tuple[bool, str]:
        """Verify that an installed skill has complete files matching its manifest and loads independently."""
        pkg_dir = self.installed_dir / skill_id / version
        if not pkg_dir.is_dir():
            return False, f"Installed package directory not found: '{pkg_dir}'"

        pkg_manifest_file = pkg_dir / "optimus_package.json"
        if not pkg_manifest_file.is_file():
            return False, "Missing 'optimus_package.json' in installed package."

        try:
            data = json.loads(pkg_manifest_file.read_text(encoding="utf-8"))
            pkg = SkillPackageManifest.from_dict(data)

            # Check entrypoint file
            entrypoint = pkg_dir / pkg.entrypoint_file
            if not entrypoint.is_file():
                return False, f"Entrypoint file '{pkg.entrypoint_file}' not found."

            # Verify files checksums
            for rel_path, expected_digest in pkg.files_checksums.items():
                fpath = pkg_dir / rel_path
                if not fpath.is_file():
                    return False, f"Corrupted installation: missing file '{rel_path}'."
                current_digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
                if current_digest != expected_digest:
                    return False, f"Checksum mismatch in '{rel_path}' (expected {expected_digest[:8]}, got {current_digest[:8]})."

            return True, "Installation integrity verified."

        except Exception as e:
            return False, f"Verification error: {e}"

    # ── 4. Lifecycle Management Operations ───────────────────────────────────

    def uninstall_skill(self, skill_id: str) -> tuple[bool, str]:
        """Uninstall a skill from OPTIMUS native library, strictly removing only managed files."""
        sid = skill_id.lower().strip()
        pkg_dir = self.installed_dir / sid

        if not pkg_dir.is_dir():
            return False, f"Skill '{sid}' is not installed in native library."

        try:
            # Remove only the managed OPTIMUS library directory
            shutil.rmtree(pkg_dir)

            # Unregister from registry
            self.registry.unregister(sid)

            # Log audit record
            self._log_history(
                skill_id=sid,
                action=SkillInstallAction.UNINSTALL,
                version="all",
                source="library",
                status="success",
                details=f"Uninstalled and removed native directory '{pkg_dir}'.",
            )
            self._update_library_manifest()

            return True, f"Successfully uninstalled skill '{sid}' from OPTIMUS native library."

        except Exception as e:
            logger.error(f"Failed to uninstall '{sid}': {e}")
            return False, f"Uninstall failed: {e}"

    def rollback_skill(self, skill_id: str) -> tuple[bool, str]:
        """Roll back a skill to its previous backup version in native library."""
        sid = skill_id.lower().strip()
        skill_dir = self.installed_dir / sid
        backup_dir = skill_dir / "backups"

        if not backup_dir.is_dir():
            return False, f"No backups found for skill '{sid}'."

        candidates = sorted(backup_dir.glob("*_*"))
        if not candidates:
            return False, f"No previous backup version available for skill '{sid}'."

        latest_bak = candidates[-1]
        try:
            # Determine version name
            parts = latest_bak.name.split("_")
            restored_version = parts[0]
            current_active = skill_dir / restored_version

            # Remove any current active version directories
            for v_dir in list(skill_dir.iterdir()):
                if v_dir.is_dir() and v_dir.name != "backups":
                    shutil.rmtree(v_dir, ignore_errors=True)

            # Restore backup
            shutil.copytree(latest_bak, current_active)

            # Re-read and re-register
            pkg_data = json.loads((current_active / "optimus_package.json").read_text(encoding="utf-8"))
            pkg = SkillPackageManifest.from_dict(pkg_data)

            # Re-register
            text = (current_active / pkg.entrypoint_file).read_text(encoding="utf-8", errors="replace")
            restored_manifest = SkillManifest(
                id=pkg.id,
                name=pkg.name,
                description=pkg.description,
                version=pkg.version,
                author=pkg.author,
                license=pkg.license,
                source_path=str(current_active),
                entrypoint_file=pkg.entrypoint_file,
                skill_type=pkg.skill_type,
                category=pkg.category,
                tags=pkg.tags,
                required_tools=pkg.required_tools,
                instructions=text,
                content_sha256=pkg.integrity_checksum,
                enabled=pkg.enabled,
                trust_level=pkg.security_report.trust_level if pkg.security_report else SkillTrustLevel.TRUSTED,
                security_report=pkg.security_report,
                provenance="native_restored",
                is_installed=True,
                installed_path=str(current_active),
                installed_at=time.time(),
            )
            self.registry.register(restored_manifest, overwrite=True)

            self._log_history(
                skill_id=sid,
                action=SkillInstallAction.ROLLBACK,
                version=restored_version,
                source=str(latest_bak),
                status="success",
                details=f"Rolled back to version {restored_version} from '{latest_bak.name}'.",
            )
            self._update_library_manifest()

            return True, f"Successfully rolled back skill '{sid}' to version '{restored_version}'."

        except Exception as e:
            return False, f"Rollback failed: {e}"

    def export_skill(self, skill_id: str, output_zip_path: Path | str) -> tuple[bool, str]:
        """Export an installed native skill into a portable .zip / .skill package."""
        sid = skill_id.lower().strip()
        active = self.get_installed_package(sid)
        if not active:
            return False, f"Skill '{sid}' is not installed in native library."

        target_zip = Path(output_zip_path)
        target_zip.parent.mkdir(parents=True, exist_ok=True)

        try:
            pkg_dir = self.installed_dir / sid / active.version
            with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in pkg_dir.rglob("*"):
                    if file_path.is_file():
                        rel = file_path.relative_to(pkg_dir)
                        zf.write(file_path, arcname=str(rel))

            return True, f"Successfully exported '{sid}' to '{target_zip}'."

        except Exception as e:
            return False, f"Export failed: {e}"

    # ── 5. Queries & Catalog ─────────────────────────────────────────────────

    def list_installed_skills(self) -> list[SkillPackageManifest]:
        """List all packages currently installed in native library."""
        packages: list[SkillPackageManifest] = []
        if not self.installed_dir.is_dir():
            return packages

        for skill_dir in sorted(self.installed_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue

            # Look for version subdirectories
            for v_dir in sorted(skill_dir.iterdir()):
                if not v_dir.is_dir() or v_dir.name == "backups":
                    continue
                pkg_file = v_dir / "optimus_package.json"
                if pkg_file.is_file():
                    try:
                        data = json.loads(pkg_file.read_text(encoding="utf-8"))
                        packages.append(SkillPackageManifest.from_dict(data))
                    except Exception as e:
                        logger.warning(f"Could not load package '{pkg_file}': {e}")

        return packages

    def get_installed_package(self, skill_id: str) -> SkillPackageManifest | None:
        """Fetch package manifest for an installed skill."""
        sid = skill_id.lower().strip()
        skill_dir = self.installed_dir / sid
        if not skill_dir.is_dir():
            return None

        # Return latest active version
        versions = [v for v in skill_dir.iterdir() if v.is_dir() and v.name != "backups"]
        if not versions:
            return None

        latest_v = sorted(versions)[-1]
        pkg_file = latest_v / "optimus_package.json"
        if not pkg_file.is_file():
            return None

        try:
            data = json.loads(pkg_file.read_text(encoding="utf-8"))
            return SkillPackageManifest.from_dict(data)
        except Exception:
            return None

    def get_install_history(self, skill_id: str | None = None, limit: int = 50) -> list[SkillInstallHistoryRecord]:
        """Read historical installation log entries."""
        if not self.history_file.is_file():
            return []

        try:
            records = json.loads(self.history_file.read_text(encoding="utf-8"))
            results = [SkillInstallHistoryRecord.from_dict(r) for r in records if isinstance(r, dict)]
            if skill_id:
                sid = skill_id.lower().strip()
                results = [r for r in results if r.skill_id == sid]
            return results[-limit:]
        except Exception:
            return []

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _log_history(
        self,
        skill_id: str,
        action: SkillInstallAction,
        version: str,
        source: str,
        status: str,
        details: str,
    ) -> None:
        rec = SkillInstallHistoryRecord(
            skill_id=skill_id,
            action=action,
            version=version,
            source=source,
            status=status,
            details=details,
        )
        existing: list[dict[str, Any]] = []
        if self.history_file.is_file():
            try:
                existing = json.loads(self.history_file.read_text(encoding="utf-8"))
            except Exception:
                existing = []

        existing.append(rec.to_dict())
        try:
            self.history_file.write_text(json.dumps(existing[-500:], indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not persist install history: {e}")

    def _update_library_manifest(self) -> None:
        installed = self.list_installed_skills()
        summary = {
            "library_root": str(self.installed_dir),
            "total_installed": len(installed),
            "updated_at": time.time(),
            "skills": {p.id: {"version": p.version, "enabled": p.enabled, "category": p.category.value} for p in installed},
        }
        try:
            self.manifest_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save library manifest: {e}")
