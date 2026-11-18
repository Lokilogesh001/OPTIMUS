"""core/windows_runtime/policy.py — Central Capability Authorization & Policy Engine for Windows Runtime.

Implements Section 5 of Milestone 11:
- Routine operations (read, search, list, launch apps, open Explorer, inspect vitals): NO REPEATED APPROVAL.
- Critical or destructive operations (delete files, terminate critical processes, modify system directories):
  MANDATORY EXPLICIT OPERATOR APPROVAL.
- Dynamically discovers all mounted local drives (C:, D:, etc.) without hardcoded drive restrictions.
- Central decision engine that cannot be bypassed by agents or prompt instructions.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from core.windows_runtime.types import OperationType

logger = logging.getLogger("optimus.windows_runtime.policy")

# Critical system directories where mutations require explicit approval
_PROTECTED_SYSTEM_DIRECTORIES = [
    re.compile(r"^[a-zA-Z]:\\Windows(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Program Files(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Program Files \(x86\)(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Recovery(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\System Volume Information(\\.*)?$", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\\$Recycle\.Bin(\\.*)?$", re.IGNORECASE),
]

# Critical Windows system processes that must NEVER be terminated without approval
_PROTECTED_PROCESS_NAMES = {
    "winlogon.exe", "csrss.exe", "smss.exe", "services.exe", "lsass.exe",
    "svchost.exe", "explorer.exe", "dwm.exe", "taskmgr.exe", "spoolsv.exe",
    "securityhealthservice.exe", "msmpeng.exe", "nissrv.exe",
}

# Operations that are always categorized as routine (read-only, exploratory, or application launching)
_ROUTINE_OPERATIONS = {
    OperationType.DISCOVER_DRIVES,
    OperationType.LIST_DIRECTORY,
    OperationType.SEARCH_FILES,
    OperationType.READ_FILE,
    OperationType.FILE_METADATA,
    OperationType.LAUNCH_APPLICATION,
    OperationType.OPEN_WITH_ASSOCIATED,
    OperationType.OPEN_IN_EXPLORER,
    OperationType.LIST_PROCESSES,
    OperationType.INSPECT_PROCESS,
    OperationType.LIST_SERVICES,
    OperationType.INSPECT_SERVICE,
    OperationType.GET_SYSTEM_TELEMETRY,
    OperationType.GET_EXECUTION_IDENTITY,
}


def discover_mounted_drives() -> list[str]:
    """Dynamically enumerate all mounted logical drives on the host Windows system."""
    try:
        import win32api
        import win32file
        drives_str = win32api.GetLogicalDriveStrings()
        raw_drives = [d.rstrip("\\") + "\\" for d in drives_str.split("\x00") if d]
        # Include fixed, removable, and ramdisks
        valid_drives = []
        for d in raw_drives:
            dtype = win32file.GetDriveType(d)
            if dtype in (
                win32file.DRIVE_FIXED,
                win32file.DRIVE_REMOVABLE,
                win32file.DRIVE_RAMDISK,
                win32file.DRIVE_REMOTE,
            ):
                valid_drives.append(d)
        return valid_drives or ["C:\\"]
    except Exception as ex:  # noqa: BLE001
        logger.warning("Failed to query Win32 drives (%s), falling back to drive scan.", ex)
        found = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                found.append(drive)
        return found or ["C:\\"]


class WindowsRuntimePolicy:
    """Central policy evaluator governing Windows privileged broker operations."""

    @classmethod
    def is_protected_path(cls, path_str: str) -> bool:
        """Check if target path is in a protected Windows operating system directory."""
        if not path_str:
            return False
        normalized = str(Path(path_str).resolve())
        for pat in _PROTECTED_SYSTEM_DIRECTORIES:
            if pat.match(normalized):
                return True
        return False

    @classmethod
    def is_protected_process(cls, process_name: str) -> bool:
        """Check if target process name is a critical Windows system or security process."""
        if not process_name:
            return False
        clean = process_name.strip().lower()
        if not clean.endswith(".exe"):
            clean += ".exe"
        return clean in _PROTECTED_PROCESS_NAMES

    @classmethod
    def evaluate_authorization(
        cls,
        operation: OperationType | Any,
        params: dict[str, Any] | None = None,
        is_pre_approved: bool = False,
    ) -> tuple[bool, bool, str]:
        """Evaluate operation request against the 2-tier authorization policy.
        
        Returns:
            (is_allowed, requires_approval, reason)
        """
        if hasattr(operation, "operation") and hasattr(operation, "params"):
            params = getattr(operation, "params", {})
            is_pre_approved = getattr(operation, "is_pre_approved", False)
            operation = operation.operation
        params = params or {}
        # 1. Routine Operations -> Allowed immediately without repeated approval
        if operation in _ROUTINE_OPERATIONS:
            return True, False, "Routine operation authorized without approval."

        # 2. File and Directory Creation / Normal Writes
        if operation in (OperationType.CREATE_DIRECTORY, OperationType.WRITE_FILE):
            target_path = params.get("path") or params.get("target_path", "")
            # Check if attempting to write into protected Windows System directory
            if cls.is_protected_path(target_path):
                if is_pre_approved:
                    return True, False, "Pre-approved mutation in protected system path."
                return (
                    False,
                    True,
                    f"Modifying system-protected directory '{target_path}' requires explicit operator approval.",
                )
            # Ordinary writes and directory creations on any drive are routine
            return True, False, "Ordinary filesystem creation/write authorized."

        # 3. File Copy / Move / Rename
        if operation in (OperationType.COPY_FILE, OperationType.MOVE_FILE, OperationType.RENAME_FILE):
            dst_path = params.get("dst_path") or params.get("target_path") or params.get("new_path", "")
            src_path = params.get("src_path") or params.get("path", "")
            if cls.is_protected_path(dst_path) or cls.is_protected_path(src_path):
                if is_pre_approved:
                    return True, False, "Pre-approved copy/move in system path."
                return (
                    False,
                    True,
                    f"File move/copy impacting system path '{dst_path or src_path}' requires explicit approval.",
                )
            return True, False, "Routine file management authorized."

        # 4. Critical & Destructive Operation: Delete File or Directory
        if operation == OperationType.DELETE_FILE:
            target_path = params.get("path") or params.get("target_path", "")
            if is_pre_approved:
                return True, False, f"Pre-approved deletion of '{target_path}'."
            return (
                False,
                True,
                f"Permanent deletion of '{target_path}' is an irreversible action requiring explicit confirmation.",
            )

        # 5. Process Termination
        if operation == OperationType.STOP_PROCESS:
            pid = params.get("pid")
            process_name = params.get("process_name", "")
            if cls.is_protected_process(process_name):
                if is_pre_approved:
                    return True, False, f"Pre-approved termination of system process '{process_name}'."
                return (
                    False,
                    True,
                    f"Terminating critical Windows system process '{process_name}' (PID: {pid}) requires confirmation.",
                )
            # Ordinary user processes can be managed without repeated approval
            return True, False, f"Ordinary process management for '{process_name or pid}' authorized."

        # 6. Process Start
        if operation == OperationType.START_PROCESS:
            command = params.get("command", "")
            # Reject raw shell scripting or formatting
            if any(danger in command.lower() for danger in ("format ", "diskpart", "mkfs", "rmdir /s")):
                if is_pre_approved:
                    return True, False, "Pre-approved administrative command."
                return False, True, f"Dangerous command '{command[:40]}' requires explicit operator approval."
            return True, False, "Process launch authorized."

        # Default fallback
        if is_pre_approved:
            return True, False, "Operation pre-approved."
        return False, True, f"Operation '{operation.value}' requires operator authorization."
