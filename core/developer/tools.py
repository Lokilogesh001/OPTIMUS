"""core/developer/tools.py — Developer Tool Suite for OPTIMUS.

Registers specialized developer tools into ToolRegistry:
- dev_workspace_info
- dev_file_read
- dev_file_write
- dev_file_diff
- dev_file_rollback
- dev_search_symbols
- dev_grep
- dev_run_command
- dev_run_tests
- dev_git_status
"""

from __future__ import annotations

from typing import Any

from core.developer.terminal import TerminalManager
from core.developer.workspace import DevelopmentWorkspace
from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)

# Global singleton workspace and terminal manager
_WORKSPACE = DevelopmentWorkspace()
_TERMINAL = TerminalManager(_WORKSPACE.root)


def get_active_workspace() -> DevelopmentWorkspace:
    """Retrieve the global DevelopmentWorkspace instance."""
    return _WORKSPACE


def set_active_workspace(ws: DevelopmentWorkspace) -> None:
    """Update global active DevelopmentWorkspace."""
    global _WORKSPACE, _TERMINAL
    _WORKSPACE = ws
    _TERMINAL = TerminalManager(ws.root)


def get_active_terminal() -> TerminalManager:
    """Retrieve the global TerminalManager instance."""
    return _TERMINAL


def _get_ws(context: dict[str, Any] | None) -> DevelopmentWorkspace:
    if context and "workspace" in context and isinstance(context["workspace"], DevelopmentWorkspace):
        return context["workspace"]
    return _WORKSPACE


def _get_term(context: dict[str, Any] | None) -> TerminalManager:
    if context and "terminal" in context and isinstance(context["terminal"], TerminalManager):
        return context["terminal"]
    return _TERMINAL


def dev_workspace_info_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Get statistics, root path, and metadata for the active development workspace."""
    ws = _get_ws(context)
    term = _get_term(context)
    stats = ws.get_stats()
    git_res = term.run_git(["status", "--short"])
    stats["git_status"] = git_res.output.strip() or "Clean"
    return ToolResult(
        status="done",
        output=f"Workspace: {stats['name']} ({stats['root']})\nFiles: {stats['total_files']} ({stats['python_files']} Python)\nSize: {stats['total_size_mb']} MB\nGit: {stats['git_status']}",
        evidence=stats,
    )


def dev_file_read_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Read a file within the authorized development workspace."""
    ws = _get_ws(context)
    path = str(params.get("path") or "").strip()
    if not path:
        return ToolResult(status="failed", error="Parameter 'path' is required.")

    try:
        content, digest, size_bytes = ws.read_file(path)
        return ToolResult(
            status="done",
            output=content,
            evidence={"path": path, "sha256": digest, "size_bytes": size_bytes},
        )
    except Exception as e:
        return ToolResult(status="failed", error=str(e), output=f"File read failed: {e}")


def dev_file_write_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Write or update a file in the workspace with automatic backup and diff tracking."""
    ws = _get_ws(context)
    path = str(params.get("path") or "").strip()
    content = str(params.get("content") or "")
    create_backup = bool(params.get("create_backup", True))

    if not path:
        return ToolResult(status="failed", error="Parameter 'path' is required.")

    ok, msg, evidence = ws.write_file(path, content, create_backup=create_backup)
    if not ok:
        return ToolResult(status="failed", error=msg, output=msg)

    return ToolResult(
        status="done",
        output=msg + f"\nDiff: {evidence.get('diff_summary', '')}",
        evidence=evidence,
    )


def dev_file_diff_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """View unified diff of a file between its current contents and backup snapshot."""
    ws = _get_ws(context)
    path = str(params.get("path") or "").strip()
    if not path:
        return ToolResult(status="failed", error="Parameter 'path' is required.")

    try:
        diff_str = ws.get_diff(path)
        return ToolResult(
            status="done",
            output=diff_str or "No diff available.",
            evidence={"path": path, "has_diff": bool(diff_str and diff_str != "No diff available.")},
        )
    except Exception as e:
        return ToolResult(status="failed", error=str(e), output=f"Diff failed: {e}")


def dev_file_rollback_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Roll back a modified workspace file to its previous backup snapshot."""
    ws = _get_ws(context)
    path = str(params.get("path") or "").strip()
    snap_id = params.get("snapshot_id")
    if not path:
        return ToolResult(status="failed", error="Parameter 'path' is required.")

    ok, msg = ws.rollback(path, snapshot_id=snap_id)
    if not ok:
        return ToolResult(status="failed", error=msg, output=msg)

    return ToolResult(
        status="done",
        output=msg,
        evidence={"path": path, "snapshot_id": snap_id, "rolled_back": True},
    )


def dev_search_symbols_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Search functions, classes, and methods across workspace Python files."""
    ws = _get_ws(context)
    query = str(params.get("query") or "").strip()
    limit = int(params.get("limit") or 30)

    symbols = ws.search_symbols(query, max_results=limit)
    lines = [f"Found {len(symbols)} symbol(s) matching '{query}':"]
    for s in symbols:
        lines.append(f"• [{s['kind'].upper()}] {s['name']} -> {s['file']}:{s['line']}")
    return ToolResult(
        status="done",
        output="\n".join(lines),
        evidence={"query": query, "count": len(symbols), "symbols": symbols},
    )


def dev_grep_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Search text or regular expressions across workspace files."""
    ws = _get_ws(context)
    query = str(params.get("query") or "").strip()
    is_regex = bool(params.get("is_regex", False))
    limit = int(params.get("limit") or 50)

    matches = ws.grep_search(query, is_regex=is_regex, max_matches=limit)
    lines = [f"Found {len(matches)} match(es) for '{query}':"]
    for m in matches:
        lines.append(f"{m['file']}:{m['line']} — {m['content']}")
    return ToolResult(
        status="done",
        output="\n".join(lines),
        evidence={"query": query, "count": len(matches), "matches": matches},
    )


def dev_run_command_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Execute a development command within workspace boundaries with risk check and timeout."""
    term = _get_term(context)
    command = str(params.get("command") or "").strip()
    timeout = float(params.get("timeout") or 30.0)
    is_approved = bool((context or {}).get("is_approved", False))

    rec = term.execute(command, timeout=timeout, is_approved=is_approved)
    status = "done" if rec.exit_code == 0 else "failed"

    out_msg = rec.output or ""
    if rec.error:
        out_msg += f"\n[STDERR]: {rec.error}"

    return ToolResult(
        status=status,
        output=out_msg.strip() or f"Command finished with exit code {rec.exit_code}",
        error=rec.error if rec.exit_code != 0 else None,
        duration_ms=rec.duration_ms,
        evidence={
            "command": rec.command,
            "exit_code": rec.exit_code,
            "duration_ms": rec.duration_ms,
            "is_destructive": rec.is_destructive,
            "requires_approval": rec.requires_approval,
        },
    )


def dev_run_tests_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Run pytest suite on the project."""
    term = _get_term(context)
    test_path = str(params.get("test_path") or "tests").strip()
    timeout = float(params.get("timeout") or 60.0)

    rec = term.run_tests(test_path=test_path, timeout=timeout)
    status = "done" if rec.exit_code == 0 else "failed"
    return ToolResult(
        status=status,
        output=rec.output or rec.error or f"Pytest exit code: {rec.exit_code}",
        error=rec.error if rec.exit_code != 0 else None,
        duration_ms=rec.duration_ms,
        evidence={"test_path": test_path, "exit_code": rec.exit_code, "passed": (rec.exit_code == 0)},
    )


def dev_git_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Get active Git status and modified files in the workspace."""
    term = _get_term(context)
    status_rec = term.run_git(["status", "--short"])
    diff_rec = term.run_git(["diff", "--stat"])
    out = f"=== Git Status ===\n{status_rec.output.strip() or 'Working tree clean'}\n\n=== Diff Stat ===\n{diff_rec.output.strip() or 'No uncommitted diffs'}"
    return ToolResult(
        status="done",
        output=out,
        evidence={"status": status_rec.output.strip(), "stat": diff_rec.output.strip()},
    )


def get_developer_tools() -> list[ToolDefinition]:
    """Return all developer tools for registration into ToolRegistry."""
    return [
        ToolDefinition(
            name="dev_workspace_info",
            description="Get metadata, directory root, file counts, and Git status for the active development workspace.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=dev_workspace_info_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="dev_file_read",
            description="Safely read a file within the authorized development workspace.",
            parameters={
                "type": "OBJECT",
                "properties": {"path": {"type": "STRING", "description": "Relative path to file in workspace"}},
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=dev_file_read_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="dev_file_write",
            description="Safely create or modify a file in the workspace with automatic backup snapshot and diff calculation.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Relative path to write"},
                    "content": {"type": "STRING", "description": "Full text content to write"},
                    "create_backup": {"type": "BOOLEAN", "description": "Whether to create a backup snapshot (default: true)"},
                },
                "required": ["path", "content"],
            },
            capability_tier=CapabilityTier.WORKSPACE_WRITE,
            handler=dev_file_write_handler,
            is_mutating=True,
        ),
        ToolDefinition(
            name="dev_file_diff",
            description="Inspect unified diff of a workspace file compared to its previous backup snapshot.",
            parameters={
                "type": "OBJECT",
                "properties": {"path": {"type": "STRING", "description": "Relative path to file"}},
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=dev_file_diff_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="dev_file_rollback",
            description="Rollback a workspace file to its backup snapshot prior to modification.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Relative path to file to restore"},
                    "snapshot_id": {"type": "STRING", "description": "Optional specific snapshot ID"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.WORKSPACE_WRITE,
            handler=dev_file_rollback_handler,
            is_mutating=True,
        ),
        ToolDefinition(
            name="dev_search_symbols",
            description="Search functions, classes, and methods across workspace Python files using AST parsing.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Symbol name or substring filter"},
                    "limit": {"type": "INTEGER", "description": "Max results (default: 30)"},
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=dev_search_symbols_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="dev_grep",
            description="Search text or regular expressions across files in the workspace.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Text or regex search pattern"},
                    "is_regex": {"type": "BOOLEAN", "description": "Whether query is a regex"},
                    "limit": {"type": "INTEGER", "description": "Max results (default: 50)"},
                },
                "required": ["query"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=dev_grep_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="dev_run_command",
            description="Execute a sandboxed development command (git, npm, python, etc.) with timeout and safety checks.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "command": {"type": "STRING", "description": "Command string to execute"},
                    "timeout": {"type": "NUMBER", "description": "Execution timeout in seconds (default: 30.0)"},
                },
                "required": ["command"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=dev_run_command_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="dev_run_tests",
            description="Run the project test suite using pytest with captured results.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "test_path": {"type": "STRING", "description": "Target test directory or file (default: 'tests')"},
                    "timeout": {"type": "NUMBER", "description": "Test execution timeout in seconds (default: 60.0)"},
                },
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=dev_run_tests_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="dev_git_status",
            description="Inspect active Git working tree status and diff stat in the workspace.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=dev_git_status_handler,
            is_mutating=False,
        ),
    ]


def register_developer_tools(registry: ToolRegistry) -> None:
    """Register all developer tools into the central ToolRegistry."""
    for tool_def in get_developer_tools():
        registry.register(tool_def)
