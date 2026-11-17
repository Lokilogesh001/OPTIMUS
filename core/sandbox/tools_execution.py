"""core/sandbox/tools_execution.py — Sandboxed Command & Process Execution Tools for OPTIMUS.

Provides:
- Controlled command execution with timeout and process termination.
- Script runner within workspace boundaries.
- Process status diagnostics and safe process termination.
"""

from __future__ import annotations

import os
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
    ]
