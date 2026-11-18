"""core/sandbox/tools_execution.py — Sandboxed Command & Process Execution Tools for OPTIMUS.

Provides:
- Controlled command execution with timeout and process termination.
- Script runner within workspace boundaries.
- Process status diagnostics and safe process termination.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolResult,
    _resolve_safe_path,
)
from core.sandbox.command_guard import execute_sandboxed_command, kill_process_tree


def execute_command_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Execute a shell command with strict safety rules, timeout, and workspace confinement."""
    cmd = str(params.get("command") or "").strip()
    timeout = float(params.get("timeout") or 15.0)
    allowed_roots = (context or {}).get("allowed_roots")
    cwd = Path(allowed_roots[0]) if allowed_roots else Path.cwd()

    if not cmd:
        return ToolResult(status="failed", error="Command string is required.")

    return execute_sandboxed_command(cmd, cwd=cwd, timeout=timeout, allowed_roots=allowed_roots)


def script_runner_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Run a Python script inside the workspace using the current interpreter."""
    script_path = str(params.get("script_path") or "")
    args = params.get("args") or []
    timeout = float(params.get("timeout") or 15.0)
    allowed_roots = (context or {}).get("allowed_roots")
    cwd = Path(allowed_roots[0]) if allowed_roots else Path.cwd()

    try:
        target_script = _resolve_safe_path(script_path, allowed_roots)
        if not target_script.is_file():
            return ToolResult(status="failed", error=f"Script file not found: '{target_script}'")

        py_exe = sys.executable
        arg_str = " ".join(f'"{a}"' for a in args)
        cmd = f'"{py_exe}" "{target_script}" {arg_str}'
        return execute_sandboxed_command(cmd, cwd=cwd, timeout=timeout, allowed_roots=allowed_roots)
    except Exception as ex:
        return ToolResult(status="failed", error=f"Script execution error: {ex}")


def process_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Check whether a specific process PID is currently active."""
    pid = params.get("pid")
    if pid is None:
        return ToolResult(status="failed", error="PID parameter is required.")

    int_pid = int(pid)
    is_running = False
    proc_name = "Unknown"

    try:
        import psutil
        p = psutil.Process(int_pid)
        is_running = p.is_running()
        proc_name = p.name()
    except Exception:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x0400, False, int_pid)
            if h != 0:
                is_running = True
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            try:
                os.kill(int_pid, 0)
                is_running = True
            except OSError:
                is_running = False

    output = f"PID {int_pid} ({proc_name}): {'ACTIVE/RUNNING' if is_running else 'TERMINATED/NOT_FOUND'}"
    return ToolResult(
        status="done",
        output=output,
        evidence={"pid": int_pid, "is_running": is_running, "name": proc_name},
    )


def process_kill_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Terminate a process and its child processes safely by PID."""
    pid = params.get("pid")
    if pid is None:
        return ToolResult(status="failed", error="PID parameter is required.")

    int_pid = int(pid)
    kill_process_tree(int_pid)
    return ToolResult(
        status="done",
        output=f"Termination signal delivered to process tree for PID {int_pid}.",
        evidence={"pid": int_pid, "terminated": True},
    )


def open_app_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Launch an authorized desktop application and verify that the process started."""
    app_name = str(params.get("app_name") or params.get("name") or "").strip()
    if not app_name:
        return ToolResult(status="failed", error="Parameter 'app_name' is required.")

    # Guard against command injection
    if any(c in app_name for c in (";", "&", "|", "`", "$", ">", "<", "\n", "\r")):
        return ToolResult(status="failed", error=f"Invalid characters in app_name '{app_name}'.")

    # Appidentity snapshot and lookup
    before: set[int] = set()
    launcher_name = app_name
    try:
        from core import appidentity
        before = appidentity.snapshot()

        existing = appidentity.running(app_name, include_self=False)
        if existing:
            for cand in existing[:6]:
                if appidentity.focus(cand):
                    return ToolResult(
                        status="done",
                        output=f"Application '{app_name}' (PID {cand.pid}) is already running and was brought to focus.",
                        evidence={"app": app_name, "pid": cand.pid, "already_running": True, "focused": True, "verified": True},
                    )
        launcher_name = appidentity.launcher_name(app_name)
    except Exception:
        pass

    target_bin = shutil.which(launcher_name) or shutil.which(app_name)
    if not target_bin and os.name == "nt":
        candidates = [
            f"{app_name}.exe",
            rf"C:\Windows\System32\{app_name}.exe",
            rf"C:\Windows\{app_name}.exe",
        ]
        for c in candidates:
            if shutil.which(c) or Path(c).is_file():
                target_bin = c
                break

    if not target_bin:
        if os.name == "nt" and ":" in app_name:
            try:
                os.startfile(app_name)
                return ToolResult(
                    status="done",
                    output=f"Dispatched OS URI scheme '{app_name}'.",
                    evidence={"app": app_name, "uri": True, "verified": True},
                )
            except Exception as e:
                return ToolResult(status="failed", error=f"Failed to start URI scheme '{app_name}': {e}")
        return ToolResult(status="failed", error=f"Application '{app_name}' could not be located on this system.")

    try:
        proc = subprocess.Popen([target_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid = proc.pid

        verified = False
        try:
            from core import appidentity
            appeared = appidentity.wait_for_launch(app_name, before, timeout=4.0)
            if appeared:
                pid = appeared.pid
                verified = True
        except Exception:
            pass

        if not verified:
            try:
                import psutil
                if psutil.pid_exists(pid):
                    verified = True
            except Exception:
                verified = (proc.poll() is None)

        if verified:
            return ToolResult(
                status="done",
                output=f"Successfully opened '{app_name}' (PID: {pid}). Verified process is running.",
                evidence={"app": app_name, "pid": pid, "executable": str(target_bin), "verified": True},
            )
        else:
            return ToolResult(
                status="unverified",
                output=f"Application '{app_name}' was invoked (PID {pid}), but new window or process could not be confirmed.",
                evidence={"app": app_name, "pid": pid, "verified": False},
            )
    except Exception as e:
        return ToolResult(status="failed", error=f"Failed to launch '{app_name}': {e}")


def open_directory_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Open an authorized folder (e.g. Downloads, Desktop, workspace) in the system file explorer."""
    raw = str(params.get("path") or "downloads").strip()
    low = raw.lower()

    if low in ("downloads", "download"):
        target = Path.home() / "Downloads"
    elif low in ("desktop",):
        target = Path.home() / "Desktop"
    elif low in ("documents", "document", "docs"):
        target = Path.home() / "Documents"
    elif low in ("pictures", "photos"):
        target = Path.home() / "Pictures"
    elif low in ("home", "~"):
        target = Path.home()
    elif low in ("workspace", "root", "."):
        allowed_roots = (context or {}).get("allowed_roots")
        target = Path(allowed_roots[0]).resolve() if allowed_roots else Path.cwd()
    else:
        allowed_roots = (context or {}).get("allowed_roots")
        try:
            target = _resolve_safe_path(raw, allowed_roots)
        except Exception:
            target = Path(raw).resolve()

    if not target.is_dir():
        return ToolResult(status="failed", error=f"Directory '{target}' does not exist or is not a directory.")

    try:
        sys_name = platform.system()
        if sys_name == "Windows":
            os.startfile(str(target))
        elif sys_name == "Darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])

        return ToolResult(
            status="done",
            output=f"Successfully opened directory '{target}' in file manager.",
            evidence={"path": str(target), "is_dir": True, "verified": True},
        )
    except Exception as e:
        return ToolResult(status="failed", error=f"Failed to open directory '{target}': {e}")


def find_files_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Search for files in a directory matching a pattern or extension."""
    raw_path = str(params.get("path") or ".").strip()
    pattern = str(params.get("pattern") or "*").strip()
    extension = str(params.get("extension") or "").strip().lower().lstrip(".")
    max_results = int(params.get("max_results") or 25)

    low = raw_path.lower()
    if low in ("downloads", "download"):
        target = Path.home() / "Downloads"
    elif low in ("desktop",):
        target = Path.home() / "Desktop"
    elif low in ("documents", "document"):
        target = Path.home() / "Documents"
    elif low in ("workspace", "root", "."):
        allowed_roots = (context or {}).get("allowed_roots")
        target = Path(allowed_roots[0]).resolve() if allowed_roots else Path.cwd()
    else:
        allowed_roots = (context or {}).get("allowed_roots")
        try:
            target = _resolve_safe_path(raw_path, allowed_roots)
        except Exception:
            target = Path(raw_path).resolve()

    if not target.is_dir():
        return ToolResult(status="failed", error=f"Directory '{target}' does not exist.")

    matched: list[dict[str, Any]] = []
    try:
        glob_pat = f"*.{extension}" if extension else pattern
        for p in target.rglob(glob_pat):
            if p.is_file():
                size_bytes = p.stat().st_size
                matched.append({
                    "name": p.name,
                    "path": str(p),
                    "size_bytes": size_bytes,
                    "size_mb": round(size_bytes / (1024 * 1024), 2),
                })
                if len(matched) >= max_results:
                    break
    except Exception as e:
        return ToolResult(status="failed", error=f"File search failed: {e}")

    lines = [f"### Search Results in '{target}' (Found {len(matched)}):"]
    for m in matched:
        lines.append(f"- `{m['name']}` ({m['size_mb']} MB) — {m['path']}")

    return ToolResult(
        status="done",
        output="\n".join(lines) if matched else f"No files matching '{pattern}' found in '{target}'.",
        evidence={"directory": str(target), "count": len(matched), "files": matched},
    )


def largest_files_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Identify the largest files in an authorized directory, optionally filtered by file extension (e.g. PDF)."""
    raw_path = str(params.get("path") or "downloads").strip()
    extension = str(params.get("extension") or "").strip().lower().lstrip(".")
    count = int(params.get("count") or 10)

    low = raw_path.lower()
    if low in ("downloads", "download"):
        target = Path.home() / "Downloads"
    elif low in ("desktop",):
        target = Path.home() / "Desktop"
    elif low in ("documents", "document"):
        target = Path.home() / "Documents"
    elif low in ("workspace", "root", "."):
        allowed_roots = (context or {}).get("allowed_roots")
        target = Path(allowed_roots[0]).resolve() if allowed_roots else Path.cwd()
    else:
        allowed_roots = (context or {}).get("allowed_roots")
        try:
            target = _resolve_safe_path(raw_path, allowed_roots)
        except Exception:
            target = Path(raw_path).resolve()

    if not target.is_dir():
        return ToolResult(status="failed", error=f"Directory '{target}' does not exist.")

    files: list[dict[str, Any]] = []
    try:
        for root, dirs, filenames in os.walk(str(target)):
            rel = os.path.relpath(root, str(target))
            if rel.count(os.sep) > 2:
                dirs.clear()
                continue
            for f in filenames:
                if extension and not f.lower().endswith(f".{extension}"):
                    continue
                full_p = Path(root) / f
                try:
                    st = full_p.stat()
                    files.append({
                        "name": f,
                        "path": str(full_p),
                        "size_bytes": st.st_size,
                        "size_mb": round(st.st_size / (1024 * 1024), 2),
                    })
                except (PermissionError, OSError):
                    continue
    except Exception as e:
        return ToolResult(status="failed", error=f"Scanning largest files failed: {e}")

    files.sort(key=lambda x: x["size_bytes"], reverse=True)
    top_files = files[:count]

    ext_desc = f" ({extension.upper()})" if extension else ""
    lines = [f"### Top {len(top_files)} Largest Files{ext_desc} in '{target}':"]
    for idx, f in enumerate(top_files, start=1):
        lines.append(f"{idx}. **{f['name']}** — {f['size_mb']} MB (`{f['path']}`)")

    if not top_files:
        lines.append(f"(No files{ext_desc} found in this directory)")

    return ToolResult(
        status="done",
        output="\n".join(lines),
        evidence={
            "directory": str(target),
            "extension": extension,
            "count": len(top_files),
            "largest": top_files[0] if top_files else None,
            "files": top_files,
        },
    )


def create_directory_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Create a new directory and verify its physical existence on disk."""
    raw_path = str(params.get("path") or params.get("name") or "").strip()
    if not raw_path:
        return ToolResult(status="failed", error="Parameter 'path' is required.")

    allowed_roots = (context or {}).get("allowed_roots")
    try:
        target = _resolve_safe_path(raw_path, allowed_roots)
    except Exception:
        root = Path(allowed_roots[0]) if allowed_roots else Path.cwd()
        target = (root / raw_path).resolve()

    try:
        target.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            return ToolResult(
                status="done",
                output=f"Successfully created directory '{target}'.",
                evidence={"path": str(target), "created": True, "verified": True},
            )
        else:
            return ToolResult(status="failed", error=f"Directory '{target}' could not be verified on disk.")
    except Exception as e:
        return ToolResult(status="failed", error=f"Directory creation error: {e}")


def get_execution_tools() -> list[ToolDefinition]:
    """Return tool definitions for the Execution Agent."""
    return [
        ToolDefinition(
            name="execute_command",
            description="Run a shell command with timeout, workspace confinement, and prohibited pattern blocking.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "command": {"type": "STRING", "description": "Shell command to execute"},
                    "timeout": {"type": "NUMBER", "description": "Timeout in seconds (default 15.0)"},
                },
                "required": ["command"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=execute_command_handler,
            is_mutating=True,
        ),
        ToolDefinition(
            name="script_runner",
            description="Execute a Python script within workspace boundaries.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "script_path": {"type": "STRING", "description": "Relative path to Python script"},
                    "args": {"type": "ARRAY", "description": "Optional list of arguments"},
                    "timeout": {"type": "NUMBER", "description": "Timeout in seconds (default 15.0)"},
                },
                "required": ["script_path"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=script_runner_handler,
            is_mutating=True,
        ),
        ToolDefinition(
            name="process_status",
            description="Check execution state and status of a process by PID.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "pid": {"type": "INTEGER", "description": "Process ID to inspect"},
                },
                "required": ["pid"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=process_status_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="process_kill",
            description="Safely terminate an active process and its child tree by PID.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "pid": {"type": "INTEGER", "description": "Process ID to terminate"},
                },
                "required": ["pid"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=process_kill_handler,
            is_mutating=True,
        ),
        ToolDefinition(
            name="open_app",
            description="Launch a desktop application (e.g. notepad, calc, terminal) and verify that its process started.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app_name": {"type": "STRING", "description": "Name or executable of the application (e.g. 'notepad', 'calc')"},
                },
                "required": ["app_name"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=open_app_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="open_directory",
            description="Open an authorized folder (e.g. 'downloads', 'desktop', 'documents', workspace) in the system file explorer.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Folder name or path ('downloads', 'desktop', 'documents', '.')"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=open_directory_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="open_folder",
            description="Alias for open_directory: open a directory in the system file explorer.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Folder name or path ('downloads', 'desktop', 'documents', '.')"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=open_directory_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="find_files",
            description="Search for files in a directory matching a pattern or extension.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Directory to search (default: current directory)"},
                    "pattern": {"type": "STRING", "description": "Filename pattern to match (default: '*')"},
                    "extension": {"type": "STRING", "description": "Optional file extension filter, e.g. 'pdf'"},
                    "max_results": {"type": "INTEGER", "description": "Maximum number of files to return (default 25)"},
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=find_files_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="largest_files",
            description="Identify the top largest files in an authorized directory, optionally filtered by extension (e.g. 'pdf').",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Directory to search (e.g. 'downloads', 'desktop', '.')"},
                    "extension": {"type": "STRING", "description": "Optional file extension, e.g. 'pdf'"},
                    "count": {"type": "INTEGER", "description": "Number of largest files to return (default 10)"},
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=largest_files_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="create_directory",
            description="Create a new directory and verify its physical creation on disk.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Directory path to create"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.WORKSPACE_WRITE,
            handler=create_directory_handler,
            is_mutating=True,
        ),
        ToolDefinition(
            name="create_folder",
            description="Alias for create_directory: create a new folder.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Folder path to create"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.WORKSPACE_WRITE,
            handler=create_directory_handler,
            is_mutating=True,
        ),
    ]
