"""core/windows_runtime/tools.py — Windows Privileged Runtime Tool Registration.

Exposes typed, validated system-wide tools to the Central ToolRegistry for:
- Filesystem (cross-drive access across C:, D:, etc.)
- Process & Application lifecycle
- Windows Services and telemetry inspection
"""

from __future__ import annotations

from typing import Any

from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from core.windows_runtime.client import get_windows_runtime_client
from core.windows_runtime.types import OperationType


def register_windows_runtime_tools(registry: ToolRegistry) -> None:
    """Register system-wide Windows runtime tools into the central ToolRegistry."""
    client = get_windows_runtime_client()

    # 1. Discover Drives
    def _discover_drives_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        try:
            drives = client.discover_drives()
            return ToolResult(status="done", output="\n".join(drives), evidence={"drives": drives})
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="discover_drives",
        description="Discover and list all mounted local drives (e.g. C:\\, D:\\).",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_discover_drives_handler,
        is_mutating=False,
    ))

    # 2. Search Files Across Drives
    def _search_files_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        root_path = params.get("root_path", "C:\\")
        pattern = params.get("pattern", "*")
        limit = int(params.get("max_results", 50))
        recursive = bool(params.get("recursive", True))
        try:
            results = client.search_files(root_path=root_path, pattern=pattern, max_results=limit, recursive=recursive)
            lines = [f"{r['size_bytes']:10} B  {r['path']}" for r in results]
            return ToolResult(
                status="done",
                output="\n".join(lines) if lines else f"No files matching '{pattern}' in '{root_path}'",
                evidence={"count": len(results), "results": results[:20]},
            )
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="search_files",
        description="Search files across any local drive by pattern and root path.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "root_path": {"type": "STRING", "description": "Starting directory (e.g. C:\\, D:\\)"},
                "pattern": {"type": "STRING", "description": "File glob pattern (e.g. *.txt, *.log)"},
                "max_results": {"type": "INTEGER", "description": "Maximum results to return"},
            },
            "required": ["root_path"],
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_search_files_handler,
        is_mutating=False,
    ))

    # 3. Create Directory
    def _create_dir_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        path = params.get("path", "")
        try:
            ok = client.create_directory(path)
            return ToolResult(status="done" if ok else "failed", output=f"Directory '{path}' created.")
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="create_directory",
        description="Create a directory or folder tree on any authorized drive.",
        parameters={
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Full directory path to create"}},
            "required": ["path"],
        },
        capability_tier=CapabilityTier.WORKSPACE_WRITE,
        handler=_create_dir_handler,
        is_mutating=True,
    ))

    # 4. Copy File
    def _copy_file_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        src = params.get("src", "")
        dst = params.get("dst", "")
        try:
            ok = client.copy_file(src, dst)
            return ToolResult(status="done" if ok else "failed", output=f"Copied '{src}' -> '{dst}'.")
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="copy_file",
        description="Copy a file from source to destination path across local drives.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "src": {"type": "STRING", "description": "Source file path"},
                "dst": {"type": "STRING", "description": "Destination file path"},
            },
            "required": ["src", "dst"],
        },
        capability_tier=CapabilityTier.WORKSPACE_WRITE,
        handler=_copy_file_handler,
        is_mutating=True,
    ))

    # 5. Move / Rename File
    def _move_file_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        src = params.get("src", "")
        dst = params.get("dst", "")
        try:
            ok = client.move_file(src, dst)
            return ToolResult(status="done" if ok else "failed", output=f"Moved '{src}' -> '{dst}'.")
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="move_file",
        description="Move or rename a file from source to destination path.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "src": {"type": "STRING", "description": "Source path"},
                "dst": {"type": "STRING", "description": "Destination path"},
            },
            "required": ["src", "dst"],
        },
        capability_tier=CapabilityTier.WORKSPACE_WRITE,
        handler=_move_file_handler,
        is_mutating=True,
    ))

    # 6. Delete File (Gated by Approval)
    def _delete_file_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        path = params.get("path", "")
        is_approved = bool((context or {}).get("is_approved"))
        try:
            resp = client.execute_operation(OperationType.DELETE_FILE, {"path": path}, is_pre_approved=is_approved)
            if resp.requires_approval and not is_approved:
                return ToolResult(
                    status="unverified",
                    error=f"APPROVAL_REQUIRED: {resp.approval_reason}",
                    evidence={"requires_approval": True, "approval_reason": resp.approval_reason},
                )
            if not resp.success:
                return ToolResult(status="failed", error=resp.error_message)
            return ToolResult(status="done", output=f"Deleted '{path}'.")
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="delete_file",
        description="Permanently delete a file or directory. Critical action requiring human approval.",
        parameters={
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Path to delete"}},
            "required": ["path"],
        },
        capability_tier=CapabilityTier.SYSTEM_DESTRUCTIVE,
        handler=_delete_file_handler,
        is_mutating=True,
    ))

    # 7. Launch Application
    def _launch_app_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        app_name = params.get("app_name", "")
        args = params.get("args", [])
        try:
            res = client.launch_application(app_name, args=args)
            if res.get("status") == "failed":
                return ToolResult(status="failed", error=res.get("error", "Launch failed"))
            return ToolResult(
                status="done",
                output=f"Application '{app_name}' launched with PID {res.get('pid')}.",
                evidence=res,
            )
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="launch_application",
        description="Launch an installed Windows desktop application (e.g. notepad.exe, calc.exe).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "app_name": {"type": "STRING", "description": "Application executable or name"},
                "args": {"type": "ARRAY", "description": "Optional command line arguments"},
            },
            "required": ["app_name"],
        },
        capability_tier=CapabilityTier.EXECUTION,
        handler=_launch_app_handler,
        is_mutating=True,
    ))

    # 8. Open In Explorer
    def _open_explorer_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        path = params.get("path", "")
        try:
            ok = client.open_in_explorer(path)
            return ToolResult(status="done" if ok else "failed", output=f"Opened '{path}' in File Explorer.")
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="open_in_explorer",
        description="Open a directory or highlight a file in Windows File Explorer.",
        parameters={
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Directory or file to show in Explorer"}},
            "required": ["path"],
        },
        capability_tier=CapabilityTier.EXECUTION,
        handler=_open_explorer_handler,
        is_mutating=False,
    ))

    # 9. List Processes
    def _list_processes_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        name_filter = params.get("name_filter", "")
        limit = int(params.get("limit", 100))
        try:
            procs = client.list_processes(limit=limit, name_filter=name_filter)
            lines = [f"PID {p['pid']:6}  {p['name']:25}  RAM: {p['memory_percent']}%" for p in procs]
            return ToolResult(
                status="done",
                output="\n".join(lines) if lines else "No matching processes found.",
                evidence={"count": len(procs), "processes": procs[:20]},
            )
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="list_processes",
        description="List running Windows processes with PID, executable name, and memory utilization.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "name_filter": {"type": "STRING", "description": "Optional substring filter for process name"},
                "limit": {"type": "INTEGER", "description": "Maximum number of processes to return"},
            },
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_list_processes_handler,
        is_mutating=False,
    ))

    # 10. Inspect Process
    def _inspect_process_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        pid = int(params.get("pid", 0))
        try:
            details = client.inspect_process(pid)
            lines = [f"{k:15}: {v}" for k, v in details.items()]
            return ToolResult(status="done", output="\n".join(lines), evidence=details)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="inspect_process",
        description="Inspect detailed metadata of a running process by PID.",
        parameters={
            "type": "OBJECT",
            "properties": {"pid": {"type": "INTEGER", "description": "Process ID to inspect"}},
            "required": ["pid"],
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_inspect_process_handler,
        is_mutating=False,
    ))

    # 11. Inspect Service
    def _inspect_service_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        svc_name = params.get("service_name", "")
        try:
            details = client.inspect_service(svc_name)
            lines = [f"{k:15}: {v}" for k, v in details.items()]
            return ToolResult(status="done", output="\n".join(lines), evidence=details)
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="inspect_service",
        description="Inspect Windows service configuration, status, and binary executable path.",
        parameters={
            "type": "OBJECT",
            "properties": {"service_name": {"type": "STRING", "description": "Name of Windows service"}},
            "required": ["service_name"],
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_inspect_service_handler,
        is_mutating=False,
    ))

    # 12. Execution Identity
    def _identity_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        try:
            ident = client.get_execution_identity()
            return ToolResult(
                status="done",
                output=f"User: {ident.domain}\\{ident.username} (Admin: {ident.is_admin}, Integrity: {ident.integrity_level})",
                evidence=ident.to_dict(),
            )
        except Exception as ex:  # noqa: BLE001
            return ToolResult(status="failed", error=str(ex))

    registry.register(ToolDefinition(
        name="get_execution_identity",
        description="Query the Windows user identity and elevation status of the execution runtime.",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_identity_handler,
        is_mutating=False,
    ))
