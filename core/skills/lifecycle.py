"""core/skills/lifecycle.py — Skill Lifecycle Management for OPTIMUS.

Supports:
- Rescan: Automated discovery and registration from configured ecosystem roots.
- Validate: On-demand security and structural audit.
- Enable / Disable: User and policy level activation controls.
- Update & Rollback: Version backups prior to update, one-click rollback.
- Install & Remove: Safe management of user-installed skills.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from core.skills.discovery import SkillDiscoveryEngine
from core.skills.models import SkillManifest, SkillTrustLevel
from core.skills.registry import SkillRegistry
from core.skills.validator import SkillValidator

logger = logging.getLogger("optimus.skills.lifecycle")


class SkillLifecycleManager:
    """Orchestrates discovery, validation, activation, updates, and rollbacks for skills."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        discovery_engine: SkillDiscoveryEngine | None = None,
        validator: SkillValidator | None = None,
        backup_dir: Path | str | None = None,
    ) -> None:
        self.registry = registry or SkillRegistry.instance()
        self.discovery_engine = discovery_engine or SkillDiscoveryEngine()
        self.validator = validator or SkillValidator()

        if backup_dir is None:
            backup_dir = Path(__file__).resolve().parent.parent.parent / ".optimus" / "skill_backups"
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def rescan(self) -> dict[str, int]:
        """Discover skills across all configured ecosystem locations, validate, and register."""
        discovered = self.discovery_engine.discover_all(max_skills=300)
        registered_count = 0
        updated_count = 0
        blocked_count = 0

        for manifest in discovered:
            # Validate and security scan
            _is_valid, _ = self.validator.validate(manifest)

            if manifest.trust_level == SkillTrustLevel.BLOCKED:
                blocked_count += 1

            existing = self.registry.get(manifest.id)
            if existing is None:
                self.registry.register(manifest)
                registered_count += 1
            else:
                self.registry.register(manifest, overwrite=False)
                updated_count += 1

        summary = {
            "total_discovered": len(discovered),
            "newly_registered": registered_count,
            "refreshed": updated_count,
            "blocked_by_security": blocked_count,
        }
        logger.info(f"Skill rescan completed: {summary}")
        return summary

    def enable_skill(self, skill_id: str) -> bool:
        """Enable a skill if safe."""
        manifest = self.registry.get(skill_id)
        if not manifest:
            return False
        if manifest.trust_level == SkillTrustLevel.BLOCKED:
            return False
        return self.registry.set_enabled(skill_id, True)

    def disable_skill(self, skill_id: str) -> bool:
        """Disable a skill."""
        return self.registry.set_enabled(skill_id, False)

    def validate_skill(self, skill_id: str) -> tuple[bool, list[str]]:
        """Run an on-demand security and structural audit on a registered skill."""
        manifest = self.registry.get(skill_id)
        if not manifest:
            return False, ["Skill not found in registry."]

        is_valid, errors = self.validator.validate(manifest)
        self.registry.save()
        return is_valid, errors

    def update_skill_instructions(self, skill_id: str, new_instructions: str) -> tuple[bool, str]:
        """Update instructions for a skill, creating an automatic backup snapshot."""
        manifest = self.registry.get(skill_id)
        if not manifest:
            return False, f"Skill '{skill_id}' not found."

        # Backup current version
        ts = int(time.time())
        bak_file = self.backup_dir / f"{skill_id}_{ts}.json"
        bak_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")

        # Apply update
        manifest.instructions = new_instructions
        manifest.metadata["last_backup_file"] = str(bak_file)

        # Re-validate
        self.validator.validate(manifest)
        self.registry.save()

        return True, f"Skill '{skill_id}' updated (backup created at '{bak_file.name}')."

    def rollback_skill(self, skill_id: str) -> tuple[bool, str]:
        """Restore previous backup version of a skill."""
        manifest = self.registry.get(skill_id)
        if not manifest:
            return False, f"Skill '{skill_id}' not found."

        last_bak = manifest.metadata.get("last_backup_file")
        if not last_bak or not Path(last_bak).is_file():
            # Check backup directory for latest
            candidates = sorted(self.backup_dir.glob(f"{skill_id}_*.json"))
            if not candidates:
                return False, f"No backup versions found for skill '{skill_id}'."
            last_bak = str(candidates[-1])

        try:
            data = json.loads(Path(last_bak).read_text(encoding="utf-8"))
            restored = SkillManifest.from_dict(data)
            self.registry.register(restored, overwrite=True)
            return True, f"Successfully rolled back skill '{skill_id}' from backup '{Path(last_bak).name}'."
        except Exception as e:
            return False, f"Failed to restore backup: {e}"

    def remove_skill(self, skill_id: str) -> bool:
        """Remove a skill from the active registry."""
        return self.registry.unregister(skill_id)
