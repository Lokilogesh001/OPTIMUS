"""core/filesystem/service.py — Unified Filesystem Access Service.

Implements Milestone 15:
- Centralized filesystem authorization and operation engine.
- Mounted drive support across all available local drives (C:, D:, etc.).
- Strict path canonicalization, UNC and device namespace rejection.
- Sensitive credential repository protection (.ssh, .aws, browser secrets, .env).
- Protected system path mutation approval gates.
- Contextual workspace write confinement for developer agents.
- NTFS junction and symlink cycle detection and broken junction tolerance.
- Pre-write atomic backup snapshotting before overwriting non-empty files.
- Accurate error classification distinguishing OS NTFS denials from application policies.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import re
import shutil
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

from core.orchestrator.tools import ToolResult

logger = logging.getLogger("optimus.filesystem.service")


class ErrorCategory(str, Enum):
    """Standardized error classifications for filesystem operations."""
    ACCESS_DENIED = "ACCESS_DENIED"                         # Windows OS or filesystem permission denial (e.g. WinError 5)
    POLICY_DENIED = "POLICY_DENIED"                         # Application authorization or boundary rejection
    PATH_NOT_FOUND = "PATH_NOT_FOUND"                       # Nonexistent path or directory (WinError 2/3)
    DRIVE_UNAVAILABLE = "DRIVE_UNAVAILABLE"                 # Target drive is not mounted or available
    PATH_INVALID = "PATH_INVALID"                           # Invalid or prohibited path (UNC, namespace, relative syntax)
    PROCESS_IDENTITY_MISMATCH = "PROCESS_IDENTITY_MISMATCH" # Identity verification mismatch


# ── Protected Pattern Registries ─────────────────────────────────────────────

# System directories where mutating operations require explicit operator approval
_PROTECTED_SYSTEM_DIRECTORIES = [
    re.compile(r"^[a-zA-Z]:\\Windows(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Program Files(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Program Files \(x86\)(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Recovery(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\System Volume Information(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\\$Recycle\.Bin(\\.*)?$", re.IGNORECASE),
]

# Sensitive user credential and secret repositories requiring approval for any access
_PROTECTED_CREDENTIAL_PATTERNS = [
    re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\\.(ssh|aws|azure|kube)(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\AppData\\Local\\(Google\\Chrome|Microsoft\\Edge)\\User Data(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\AppData\\Roaming\\Microsoft\\Protect(\\.*)?$", re.IGNORECASE),
    re.compile(r".*[\/\\](\.env(\..+)?|\.git-credentials|\.netrc)($|[\/\\].*)", re.IGNORECASE),
]


def _format_size(b: int) -> str:
    """Format bytes into human-readable metric string."""
    val = float(b)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if val < 1024.0:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} TB"


class UnifiedFilesystemService:
    """Centralized, authoritative filesystem service for all OPTIMUS agents and tools."""

    _instance: UnifiedFilesystemService | None = None
    _lock = threading.RLock()

    def __init__(self, backup_dir: Path | str | None = None) -> None:
        self.backup_dir = Path(backup_dir) if backup_dir else Path.cwd() / ".optimus" / "backups"
        self._mounted_drives_cache: list[str] = []
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 30.0  # seconds

    @classmethod
    def instance(cls, backup_dir: Path | str | None = None) -> UnifiedFilesystemService:
        """Singleton accessor."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(backup_dir=backup_dir)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (useful for test isolation)."""
        with cls._lock:
            cls._instance = None

    # ── Drive Discovery & Path Canonicalization ──────────────────────────────

    def discover_mounted_drives(self, force_refresh: bool = False) -> list[str]:
        """Dynamically enumerate all mounted logical drives on the host Windows system."""
        now = time.monotonic()
        if not force_refresh and self._mounted_drives_cache and (now - self._cache_timestamp) < self._cache_ttl:
            return self._mounted_drives_cache

        drives: list[str] = []
        try:
            from core.windows_runtime.policy import (
                discover_mounted_drives as win_drives,
            )
            drives = win_drives()
        except Exception as ex:  # noqa: BLE001  # noqa: BLE001
            logger.debug("discover_mounted_drives fallback: %s", ex)

        if not drives:
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                candidate = f"{letter}:\\"
                if os.path.exists(candidate):
                    drives.append(candidate)

        self._mounted_drives_cache = drives or ["C:\\"]
        self._cache_timestamp = now
        return self._mounted_drives_cache

    def resolve_path(self, raw_path: str | Path) -> Path:
        """Strictly canonicalize path, expand user shortcuts, and validate syntax and drive boundaries.

        Enforces:
        - Rejection of remote UNC network paths (\\\\server\\share).
        - Rejection of device namespace paths (\\\\?\\, \\\\.\\, \\??\\).
        - Rejection of drive-relative syntax (C:folder).
        - Expansion of standard user shortcuts (desktop, downloads, documents, music, home).
        - Canonical resolution via Path.resolve().
        - Mounted drive presence check.
        """
        raw = str(raw_path or "").strip().strip('"').strip("'")
        if not raw:
            return Path.cwd().resolve()

        # 1. Prohibit remote UNC paths (NTLM leakage vector over SMB)
        if raw.startswith(("\\\\", "//")):
            raise ValueError(f"{ErrorCategory.PATH_INVALID.value}: UNC_VIOLATION: Remote UNC network paths are prohibited for security: '{raw}'")

        # 2. Prohibit device namespace prefixes
        if re.match(r"^(\\\\(\.|\?)|\\\\\?\\|\/\/\?\/|\\\?\?\\)", raw) or raw.startswith("\\??\\"):
            raise ValueError(f"{ErrorCategory.PATH_INVALID.value}: NAMESPACE_VIOLATION: Device namespace paths are prohibited: '{raw}'")

        # 3. Prohibit ambiguous drive-relative syntax (e.g. 'C:folder' instead of 'C:\\folder')
        if re.match(r"^[a-zA-Z]:[^\/\\]", raw):
            raise ValueError(f"{ErrorCategory.PATH_INVALID.value}: DRIVE_RELATIVE_VIOLATION: Drive-relative paths without leading separator are prohibited: '{raw}'")

        # 4. Expand shortcuts
        shortcuts: dict[str, Path] = {
            "desktop": Path.home() / "Desktop",
            "downloads": Path.home() / "Downloads",
            "documents": Path.home() / "Documents",
            "pictures": Path.home() / "Pictures",
            "music": Path.home() / "Music",
            "videos": Path.home() / "Videos",
            "home": Path.home(),
        }

        lower = raw.lower()
        if lower in shortcuts:
            target = shortcuts[lower]
        else:
            # Check shortcut prefixes like 'desktop/notes.txt' or 'downloads\\doc.pdf'
            head, sep, rest = raw.replace("\\", "/").partition("/")
            if sep and head.lower() in shortcuts:
                rest_clean = rest.strip("/")
                target = shortcuts[head.lower()] / rest_clean if rest_clean else shortcuts[head.lower()]
            else:
                target = Path(raw).expanduser()

        # 5. Canonicalize
        resolved = target.resolve()

        # 6. Mounted Drive Validation
        if resolved.drive:
            drive_prefix = resolved.drive.upper()
            mounted = [d.upper().rstrip("\\") for d in self.discover_mounted_drives()]
            if drive_prefix not in mounted and f"{drive_prefix}\\" not in [d.upper() for d in self.discover_mounted_drives()]:
                raise FileNotFoundError(f"{ErrorCategory.DRIVE_UNAVAILABLE.value}: Target drive '{drive_prefix}' is not currently mounted or available.")

        return resolved

    # ── Policy & Authorization Checking ─────────────────────────────────────

    def check_access(
        self,
        target: Path,
        operation: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, bool, str]:
        """Evaluate access authorization for the target path and operation.

        Returns:
            (is_authorized, requires_approval, reason)
        """
        ctx = context or {}
        op = operation.lower().strip()
        is_approved = bool(ctx.get("is_approved") or ctx.get("approval_token"))
        ctx.get("approval_token")
        target_str = str(target)

        # 1. Sensitive User Credential & Secret Repositories (.ssh, .aws, chrome data, .env)
        for pattern in _PROTECTED_CREDENTIAL_PATTERNS:
            if pattern.search(target_str):
                if is_approved:
                    return True, False, f"Authorized access to sensitive store '{target.name}' with approval token."
                return (
                    False,
                    True,
                    (f"{ErrorCategory.POLICY_DENIED.value}: APPROVAL_REQUIRED: SENSITIVE_CREDENTIAL_STORE: "
                    f"Access to credential store '{target}' requires explicit operator approval."),
                )

        # 2. Protected Operating System Directories (Windows, Program Files, System Volume Info)
        is_mutating = op in ("write", "create_file", "create_folder", "create_directory", "delete", "move")
        for pattern in _PROTECTED_SYSTEM_DIRECTORIES:
            if pattern.match(target_str):
                if is_mutating:
                    if is_approved:
                        return True, False, f"Authorized mutation in system path '{target}' with approval token."
                    return (
                        False,
                        True,
                        (f"{ErrorCategory.POLICY_DENIED.value}: APPROVAL_REQUIRED: PROTECTED_PATH_RESTRICTION: "
                        f"Mutating protected system path '{target}' requires explicit operator approval."),
                    )
                # Routine reads of system files/directories are permitted without repeated friction
                return True, False, "Routine system path inspection authorized."

        # 3. Contextual Workspace Confinement (For DeveloperAgent and workspace-scoped tasks)
        workspace_root = ctx.get("workspace_root")
        if workspace_root and is_mutating and not ctx.get("cross_workspace_mutations"):
            ws_path = Path(workspace_root).resolve()
            if target != ws_path and ws_path not in target.parents:
                return (
                    False,
                    False,
                    (f"{ErrorCategory.POLICY_DENIED.value}: WORKSPACE_CONFINEMENT_VIOLATION: "
                    f"Mutating operation '{operation}' on '{target}' escapes assigned workspace root '{ws_path}'."),
                )

        # 4. Destructive Deletion Operations
        if op in ("delete", "delete_file", "delete_directory"):
            if is_approved:
                return True, False, f"Pre-approved deletion of '{target}' authorized."
            return (
                False,
                True,
                (f"{ErrorCategory.POLICY_DENIED.value}: APPROVAL_REQUIRED: DESTRUCTIVE_OPERATION: "
                f"Deleting '{target}' requires explicit operator approval."),
            )

        # 5. Routine Operations (Read, List, Search, Normal Project File Write)
        return True, False, "Operation authorized."

    # ── Atomic Pre-Write Backup Snapshotting ─────────────────────────────────

    def _create_backup_snapshot(self, target: Path) -> Path | None:
        """Create an atomic timestamped backup copy in .optimus/backups before overwriting a non-empty file."""
        try:
            if not target.is_file() or target.stat().st_size == 0:
                return None

            self.backup_dir.mkdir(parents=True, exist_ok=True)
            # Create a safe unique file prefix from target path
            safe_name = re.sub(r"[:\\\/]", "_", str(target).lstrip("\\/"))
            ts = int(time.time() * 1000)
            backup_file = self.backup_dir / f"{safe_name}_{ts}.bak"

            shutil.copy2(target, backup_file)
            logger.info("Created atomic pre-write backup of '%s' at '%s'", target, backup_file)
            return backup_file
        except Exception as ex:  # noqa: BLE001  # noqa: BLE001
            logger.warning("Failed creating pre-write backup for '%s': %s", target, ex)
            return None

    # ── Authoritative Operations ─────────────────────────────────────────────

    def list_directory(
        self,
        path: str = ".",
        show_hidden: bool = False,
        limit: int = 200,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """List directory contents safely with broken junction tolerance."""
        t0 = time.perf_counter()
        try:
            target = self.resolve_path(path)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Policy check
        auth, req_appr, reason = self.check_access(target, "list", context=context)
        if not auth:
            status = "unverified" if req_appr else "failed"
            return ToolResult(status=status, error=reason, output=reason)

        if not target.exists():
            return ToolResult(
                status="failed",
                error=f"{ErrorCategory.PATH_NOT_FOUND.value}: Directory not found: '{target}'",
                output=f"Path not found: '{target}'",
            )
        if not target.is_dir():
            return ToolResult(
                status="failed",
                error=f"{ErrorCategory.PATH_INVALID.value}: Not a directory: '{target}'",
                output=f"Not a directory: '{target}'",
            )

        entries: list[str] = []
        count = 0
        try:
            with os.scandir(target) as it:
                # Sort entries for deterministic output
                scanned_entries = list(it)
                scanned_entries.sort(key=lambda e: e.name.lower())

                for entry in scanned_entries:
                    if not show_hidden and entry.name.startswith("."):
                        continue

                    try:
                        is_sym = entry.is_symlink()
                        is_dir = entry.is_dir(follow_symlinks=False)
                        st = entry.stat(follow_symlinks=False)
                        size_str = _format_size(st.st_size)

                        if is_sym:
                            entries.append(f"📁 [junction/symlink] {entry.name}/" if is_dir else f"📄 [symlink] {entry.name}")
                        elif is_dir:
                            entries.append(f"📁 {entry.name}/")
                        else:
                            entries.append(f"📄 {entry.name} ({size_str})")
                    except OSError:
                        # Gracefully handle broken junctions or unlinked symlinks
                        entries.append(f"📁 [broken junction] {entry.name}/")

                    count += 1
                    if count >= limit:
                        break

            out_header = f"Contents of {target.name or str(target)} ({count} items):\n"
            out = out_header + "\n".join(entries) if entries else f"Directory is empty: {target.name}/"
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(
                status="done",
                output=out,
                evidence={"path": str(target), "count": count, "truncated": count >= limit},
                duration_ms=dur,
            )

        except PermissionError as p_err:
            dur = (time.perf_counter() - t0) * 1000.0
            err_msg = f"{ErrorCategory.ACCESS_DENIED.value}: [WinError 5] Operating system access denied: '{target}' ({p_err})"
            return ToolResult(status="failed", error=err_msg, output=err_msg, duration_ms=dur)
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"Directory listing error: {ex}", duration_ms=dur)

    def read_file(
        self,
        path: str,
        max_bytes: int = 500000,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Safely read text or data from a file with size limits."""
        t0 = time.perf_counter()
        try:
            target = self.resolve_path(path)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Policy check
        auth, req_appr, reason = self.check_access(target, "read", context=context)
        if not auth:
            status = "unverified" if req_appr else "failed"
            return ToolResult(status=status, error=reason, output=reason)

        if not target.exists():
            return ToolResult(
                status="failed",
                error=f"{ErrorCategory.PATH_NOT_FOUND.value}: File not found: '{target}'",
                output=f"File not found: '{target}'",
            )
        if not target.is_file():
            return ToolResult(
                status="failed",
                error=f"{ErrorCategory.PATH_INVALID.value}: Path is not a regular file: '{target}'",
                output=f"Not a file: '{target}'",
            )

        try:
            raw_bytes = target.read_bytes()
            if len(raw_bytes) > max_bytes:
                content = raw_bytes[:max_bytes].decode("utf-8", errors="replace") + f"\n... [Truncated after {max_bytes} bytes]"
            else:
                content = raw_bytes.decode("utf-8", errors="replace")

            digest = hashlib.sha256(raw_bytes).hexdigest()
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(
                status="done",
                output=content,
                evidence={"path": str(target), "bytes": len(raw_bytes), "sha256": digest},
                duration_ms=dur,
            )
        except PermissionError as p_err:
            dur = (time.perf_counter() - t0) * 1000.0
            err_msg = f"{ErrorCategory.ACCESS_DENIED.value}: [WinError 5] Operating system access denied: '{target}' ({p_err})"
            return ToolResult(status="failed", error=err_msg, output=err_msg, duration_ms=dur)
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"File read error: {ex}", duration_ms=dur)

    def write_file(
        self,
        path: str,
        content: str | bytes,
        append: bool = False,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Write content to file, creating parents and generating pre-write backups if overwriting."""
        t0 = time.perf_counter()
        try:
            target = self.resolve_path(path)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Policy check
        auth, req_appr, reason = self.check_access(target, "write", context=context)
        if not auth:
            status = "unverified" if req_appr else "failed"
            return ToolResult(status=status, error=reason, output=reason)

        try:
            # Atomic pre-write backup if target exists and is non-empty (and not appending)
            backup_path = self._create_backup_snapshot(target) if not append else None

            target.parent.mkdir(parents=True, exist_ok=True)
            raw_bytes = content.encode("utf-8") if isinstance(content, str) else content

            if append and target.exists():
                with open(target, "ab") as f:
                    f.write(raw_bytes)
            else:
                target.write_bytes(raw_bytes)
            digest = hashlib.sha256(raw_bytes).hexdigest()
            dur = (time.perf_counter() - t0) * 1000.0

            evidence: dict[str, Any] = {
                "path": str(target),
                "bytes": len(raw_bytes),
                "sha256": digest,
            }
            if backup_path:
                evidence["backup_path"] = str(backup_path)

            msg = f"Successfully wrote {len(raw_bytes)} bytes to '{target}' (sha256={digest[:8]})."
            if backup_path:
                msg += f" (Backup created at '{backup_path.name}')"

            return ToolResult(
                status="done",
                output=msg,
                evidence=evidence,
                duration_ms=dur,
            )
        except PermissionError as p_err:
            dur = (time.perf_counter() - t0) * 1000.0
            err_msg = f"{ErrorCategory.ACCESS_DENIED.value}: [WinError 5] Operating system write denied: '{target}' ({p_err})"
            return ToolResult(status="failed", error=err_msg, output=err_msg, duration_ms=dur)
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"File write error: {ex}", duration_ms=dur)

    def create_directory(
        self,
        path: str,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Create a new folder safely."""
        t0 = time.perf_counter()
        try:
            target = self.resolve_path(path)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Policy check
        auth, req_appr, reason = self.check_access(target, "create_directory", context=context)
        if not auth:
            status = "unverified" if req_appr else "failed"
            return ToolResult(status=status, error=reason, output=reason)

        try:
            target.mkdir(parents=True, exist_ok=True)
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(
                status="done",
                output=f"Successfully created directory: '{target}'",
                evidence={"path": str(target)},
                duration_ms=dur,
            )
        except PermissionError as p_err:
            dur = (time.perf_counter() - t0) * 1000.0
            err_msg = f"{ErrorCategory.ACCESS_DENIED.value}: [WinError 5] Operating system creation denied: '{target}' ({p_err})"
            return ToolResult(status="failed", error=err_msg, output=err_msg, duration_ms=dur)
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"Directory creation error: {ex}", duration_ms=dur)

    def copy_file(
        self,
        src: str,
        dst: str,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Copy a file or directory tree safely."""
        t0 = time.perf_counter()
        try:
            src_target = self.resolve_path(src)
            dst_target = self.resolve_path(dst)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Check source read permission and destination write permission
        auth_src, req_s, r_src = self.check_access(src_target, "read", context=context)
        if not auth_src:
            return ToolResult(status="unverified" if req_s else "failed", error=r_src, output=r_src)

        auth_dst, req_d, r_dst = self.check_access(dst_target, "write", context=context)
        if not auth_dst:
            return ToolResult(status="unverified" if req_d else "failed", error=r_dst, output=r_dst)

        try:
            if not src_target.exists():
                return ToolResult(status="failed", error=f"{ErrorCategory.PATH_NOT_FOUND.value}: Source not found: '{src_target}'")

            dst_target.parent.mkdir(parents=True, exist_ok=True)
            if src_target.is_dir():
                shutil.copytree(src_target, dst_target, dirs_exist_ok=True)
            else:
                shutil.copy2(src_target, dst_target)

            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(
                status="done",
                output=f"Successfully copied '{src_target.name}' to '{dst_target}'.",
                evidence={"src": str(src_target), "dst": str(dst_target)},
                duration_ms=dur,
            )
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"Copy error: {ex}", duration_ms=dur)

    def move_file(
        self,
        src: str,
        dst: str,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Move / rename a file or directory safely."""
        t0 = time.perf_counter()
        try:
            src_target = self.resolve_path(src)
            dst_target = self.resolve_path(dst)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Moving mutates both source (removal) and destination (creation)
        auth_src, req_s, r_src = self.check_access(src_target, "move", context=context)
        if not auth_src:
            return ToolResult(status="unverified" if req_s else "failed", error=r_src, output=r_src)

        auth_dst, req_d, r_dst = self.check_access(dst_target, "write", context=context)
        if not auth_dst:
            return ToolResult(status="unverified" if req_d else "failed", error=r_dst, output=r_dst)

        try:
            if not src_target.exists():
                return ToolResult(status="failed", error=f"{ErrorCategory.PATH_NOT_FOUND.value}: Source not found: '{src_target}'")

            dst_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_target), str(dst_target))
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(
                status="done",
                output=f"Successfully moved '{src_target.name}' to '{dst_target}'.",
                evidence={"src": str(src_target), "dst": str(dst_target)},
                duration_ms=dur,
            )
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"Move error: {ex}", duration_ms=dur)

    def delete_file(
        self,
        path: str,
        permanent: bool = False,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Safely delete a file or directory tree (approval-gated)."""
        t0 = time.perf_counter()
        try:
            target = self.resolve_path(path)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Policy check
        auth, req_appr, reason = self.check_access(target, "delete", context=context)
        if not auth:
            status = "unverified" if req_appr else "failed"
            return ToolResult(status=status, error=reason, output=reason)

        if not target.exists():
            return ToolResult(
                status="failed",
                error=f"{ErrorCategory.PATH_NOT_FOUND.value}: Target does not exist: '{target}'",
                output=f"Target does not exist: '{target}'",
            )

        try:
            if not permanent:
                try:
                    import send2trash
                    send2trash.send2trash(str(target))
                    dur = (time.perf_counter() - t0) * 1000.0
                    return ToolResult(
                        status="done",
                        output=f"Moved to Recycle Bin: '{target.name}'",
                        evidence={"path": str(target), "recycle_bin": True},
                        duration_ms=dur,
                    )
                except ImportError:
                    pass

            # Permanent deletion fallback if requested or send2trash absent
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(
                status="done",
                output=f"Permanently deleted: '{target.name}'",
                evidence={"path": str(target), "permanent": True},
                duration_ms=dur,
            )
        except PermissionError as p_err:
            dur = (time.perf_counter() - t0) * 1000.0
            err_msg = f"{ErrorCategory.ACCESS_DENIED.value}: [WinError 5] Operating system deletion denied: '{target}' ({p_err})"
            return ToolResult(status="failed", error=err_msg, output=err_msg, duration_ms=dur)
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"Deletion error: {ex}", duration_ms=dur)

    def search_files(
        self,
        root_path: str = ".",
        pattern: str = "*",
        recursive: bool = True,
        max_results: int = 100,
        max_depth: int = 12,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Search files by pattern with symlink cycle detection and depth boundaries."""
        t0 = time.perf_counter()
        try:
            start_dir = self.resolve_path(root_path)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        # Check access to root
        auth, req_appr, reason = self.check_access(start_dir, "search", context=context)
        if not auth:
            status = "unverified" if req_appr else "failed"
            return ToolResult(status=status, error=reason, output=reason)

        if not start_dir.is_dir():
            return ToolResult(status="failed", error=f"{ErrorCategory.PATH_NOT_FOUND.value}: Not a directory: '{start_dir}'")

        results: list[dict[str, Any]] = []
        visited_real_paths: set[str] = set()
        visited_real_paths.add(str(start_dir.resolve()).lower())

        def _walk(curr_path: Path, current_depth: int) -> None:
            if current_depth > max_depth or len(results) >= max_results:
                return

            try:
                with os.scandir(curr_path) as it:
                    for entry in it:
                        if len(results) >= max_results:
                            break

                        # Check sensitive credential directories to skip them during auto search
                        entry_str = str(Path(entry.path).resolve())
                        if any(pat.search(entry_str) for pat in _PROTECTED_CREDENTIAL_PATTERNS):
                            continue

                        try:
                            is_sym = entry.is_symlink()
                            is_dir = entry.is_dir(follow_symlinks=False)

                            if fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                                st = entry.stat(follow_symlinks=False)
                                results.append({
                                    "name": entry.name,
                                    "path": entry.path,
                                    "is_dir": is_dir,
                                    "size": st.st_size if not is_dir else 0,
                                })

                            if recursive and is_dir and not is_sym:
                                real_sub = str(Path(entry.path).resolve()).lower()
                                if real_sub not in visited_real_paths:
                                    visited_real_paths.add(real_sub)
                                    _walk(Path(entry.path), current_depth + 1)
                        except OSError:
                            continue
            except PermissionError:
                pass
            except OSError:
                pass

        _walk(start_dir, 0)
        dur = (time.perf_counter() - t0) * 1000.0

        lines = [f"{'📁' if r['is_dir'] else '📄'} {r['name']} ({_format_size(r['size']) if not r['is_dir'] else 'dir'}) -> {r['path']}" for r in results]
        out = f"Found {len(results)} matches for '{pattern}' in '{start_dir}':\n" + "\n".join(lines) if results else f"No matches found for '{pattern}' in '{start_dir}'."

        return ToolResult(
            status="done",
            output=out,
            evidence={"root": str(start_dir), "count": len(results), "matches": results},
            duration_ms=dur,
        )

    def get_file_info(
        self,
        path: str,
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Inspect file or directory metadata."""
        t0 = time.perf_counter()
        try:
            target = self.resolve_path(path)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex), output=f"Path resolution error: {ex}")

        auth, req_appr, reason = self.check_access(target, "info", context=context)
        if not auth:
            status = "unverified" if req_appr else "failed"
            return ToolResult(status=status, error=reason, output=reason)

        if not target.exists():
            return ToolResult(status="failed", error=f"{ErrorCategory.PATH_NOT_FOUND.value}: Path not found: '{target}'")

        try:
            st = target.stat(follow_symlinks=False)
            is_dir = target.is_dir()
            is_sym = target.is_symlink()

            meta = {
                "name": target.name or str(target),
                "path": str(target),
                "type": "directory" if is_dir else "file",
                "is_symlink": is_sym,
                "size_bytes": st.st_size if not is_dir else 0,
                "size_human": _format_size(st.st_size) if not is_dir else "N/A",
                "created_time": time.ctime(st.st_ctime),
                "modified_time": time.ctime(st.st_mtime),
            }

            out = (
                f"Path: {meta['path']}\n"
                f"Type: {meta['type']}{' (symlink/junction)' if is_sym else ''}\n"
                f"Size: {meta['size_human']} ({meta['size_bytes']} bytes)\n"
                f"Created: {meta['created_time']}\n"
                f"Modified: {meta['modified_time']}"
            )
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="done", output=out, evidence=meta, duration_ms=dur)
        except PermissionError as p_err:
            dur = (time.perf_counter() - t0) * 1000.0
            err_msg = f"{ErrorCategory.ACCESS_DENIED.value}: [WinError 5] Operating system metadata access denied: '{target}' ({p_err})"
            return ToolResult(status="failed", error=err_msg, output=err_msg, duration_ms=dur)
        except Exception as ex:  # noqa: BLE001
            dur = (time.perf_counter() - t0) * 1000.0
            return ToolResult(status="failed", error=str(ex), output=f"Metadata error: {ex}", duration_ms=dur)
