"""core/orchestrator/tools.py — Centralized Tool Registry & Capability Controller.

Enforces:
- Capability-Based Access Control (CBAC) with explicit permission tiers.
- Independent verification and physical postcondition tracking.
- Safe execution sandboxing (no direct, unverified execution from model text).
"""

from __future__ import annotations

import ast
import logging
import operator
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("optimus.orchestrator.tools")


class CapabilityTier(str, Enum):
    """Hierarchy of operational risk tiers for tools."""
    READ_ONLY = "READ_ONLY"
    WORKSPACE_WRITE = "WORKSPACE_WRITE"
    NETWORK_EXTERNAL = "NETWORK_EXTERNAL"
    EXECUTION = "EXECUTION"
    SYSTEM_MUTATING = "SYSTEM_MUTATING"
    SYSTEM_DESTRUCTIVE = "SYSTEM_DESTRUCTIVE"


_TIER_RANK = {
    CapabilityTier.READ_ONLY: 1,
    CapabilityTier.WORKSPACE_WRITE: 2,
    CapabilityTier.NETWORK_EXTERNAL: 3,
    CapabilityTier.EXECUTION: 4,
    CapabilityTier.SYSTEM_MUTATING: 5,
    CapabilityTier.SYSTEM_DESTRUCTIVE: 5,
}


_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(['\"]?(?:password|passwd|secret|api_?key)['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])", re.IGNORECASE), r"\1[REDACTED]\2"),
]


def sanitize_tool_output(output: str, max_chars: int = 50000) -> str:
    """Sanitize and truncate tool output to prevent memory exhaustion and credential leaking."""
    if not output:
        return ""
    text = str(output)
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... [Output truncated to {max_chars} characters]"
    return text


@dataclass
class ToolResult:
    """Standardized tool execution outcome."""
    status: str = "done"  # "done", "unverified", "failed"
    output: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def is_success(self) -> bool:
        return self.status == "done"


@dataclass
class ToolDefinition:
    """Metadata, capability constraints, and callable handler for a tool."""
    name: str
    description: str
    parameters: dict[str, Any]
    capability_tier: CapabilityTier = CapabilityTier.READ_ONLY
    handler: Callable[..., Any] | None = None
    is_mutating: bool = False

    @property
    def tier(self) -> CapabilityTier:
        return self.capability_tier

    def to_schema(self) -> dict[str, Any]:
        """Produce standard OpenAI/Gemini function declaration schema."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# ── Built-in Safe AST Calculator (Zero eval()) ──────────────────────────────

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval_expression(expr: str) -> float | int:
    """Evaluate arithmetic expressions using AST walk. Completely prohibits exec/eval."""
    expr = str(expr or "").strip()
    if not expr:
        raise ValueError("empty expression")

    def _eval(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"unsupported constant type: {type(node.value)}")
        elif isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op_type = type(node.op)
            if op_type in _SAFE_OPERATORS:
                if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
                    raise ZeroDivisionError("division by zero")
                if op_type is ast.Pow and (right > 1000 or left > 100000):
                    raise OverflowError("exponentiation result too large")
                return _SAFE_OPERATORS[op_type](left, right)
            raise ValueError(f"unsupported binary operator: {op_type}")
        elif isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            op_type = type(node.op)
            if op_type in _SAFE_OPERATORS:
                return _SAFE_OPERATORS[op_type](operand)
            raise ValueError(f"Unsupported syntax or operation: {op_type}")
        raise ValueError(f"Unsupported syntax or operation: unsupported syntax element {type(node)}")

    parsed = ast.parse(expr, mode="eval")
    return _eval(parsed)


# ── Built-in Filesystem Operations with Boundary Enforcement ─────────────────

def _resolve_safe_path(
    raw_path: str,
    allowed_roots: list[str] | None = None,
    allow_cross_drive: bool = False,
) -> Path:
    """Canonicalize path and verify it does not traverse outside allowed roots."""
    from core.filesystem.service import UnifiedFilesystemService
    p_resolved = UnifiedFilesystemService.instance().resolve_path(raw_path)

    if allowed_roots:
        canonical_roots = [Path(r).resolve() for r in allowed_roots]
        # 1. If path is already inside an allowed root (e.g. absolute or normalized relative), use it directly
        if any(p_resolved == r or r in p_resolved.parents for r in canonical_roots):
            return p_resolved

        # 2. If relative, resolve relative to allowed_roots[0]
        p_raw = Path(raw_path)
        if not p_raw.is_absolute():
            p_under_root = (canonical_roots[0] / p_raw).resolve()
            if any(p_under_root == r or r in p_under_root.parents for r in canonical_roots):
                return p_under_root

        # 3. Cross-drive / system path permission: if allowed or wildcard is present
        if (allow_cross_drive or any(str(r) in ("*", "ALL_DRIVES") for r in allowed_roots)) and p_resolved.is_absolute() and p_resolved.drive:
            mounted = UnifiedFilesystemService.instance().discover_mounted_drives()
            drive_prefix = p_resolved.drive.upper()
            if any(m.upper().startswith(drive_prefix) for m in mounted):
                return p_resolved

        raise PermissionError(f"PATH_VIOLATION: Access denied: path '{raw_path}' is outside permitted directories (allowed roots: {allowed_roots})")

    return p_resolved


# ── Central Tool Registry ────────────────────────────────────────────────────

class ToolRegistry:
    """Central repository for tool discovery, capability checks, and sandboxed dispatch."""

    _instance: ToolRegistry | None = None

    @classmethod
    def instance(cls, event_bus: Any | None = None) -> ToolRegistry:
        """Global thread-safe singleton accessor."""
        if cls._instance is None:
            cls._instance = cls(event_bus=event_bus)
        return cls._instance

    def __init__(self, event_bus: Any | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._event_bus = event_bus
        self._register_default_tools()
        try:
            from core.sandbox import register_sandbox_tools
            register_sandbox_tools(self)
        except ImportError:
            pass
        try:
            from core.developer.tools import register_developer_tools
            register_developer_tools(self)
        except ImportError:
            pass
        try:
            from core.computer_use.tools import register_computer_tools
            register_computer_tools(self)
        except ImportError:
            pass
        try:
            from core.windows_runtime.tools import register_windows_runtime_tools
            register_windows_runtime_tools(self)
        except ImportError:
            pass
        try:
            from core.personal_intelligence.tools import register_personal_tools
            register_personal_tools(self)
        except ImportError:
            pass

    def set_event_bus(self, event_bus: Any) -> None:
        """Attach or update the EventBus for audit events."""
        self._event_bus = event_bus

    def unregister(self, name: str) -> bool:
        """Dynamically unregister/revoke a tool from the registry."""
        return self._tools.pop(name, None) is not None

    revoke = unregister

    def is_registered(self, name: str) -> bool:
        """Check if a tool is currently registered."""
        return name in self._tools

    def _emit_audit(
        self,
        tool_name: str,
        caller_agent: str,
        result: ToolResult,
        arguments: dict[str, Any],
    ) -> None:
        if self._event_bus is not None:
            try:
                from core.orchestrator.events import ToolExecutedEvent
                ev_digest = ""
                if result.evidence:
                    ev_digest = str(result.evidence.get("sha256") or result.evidence.get("digest") or "")
                self._event_bus.publish(ToolExecutedEvent(
                    tool_name=tool_name,
                    caller_agent=caller_agent,
                    status=result.status,
                    duration_ms=result.duration_ms,
                    arguments={k: str(v)[:100] for k, v in arguments.items()},
                    error=result.error,
                    evidence_digest=ev_digest,
                ))
            except Exception:  # noqa: BLE001, S110
                pass

    def _register_default_tools(self) -> None:
        """Register the baseline toolset."""

        # 1. Calculator
        def _calc_handler(params: dict, context: dict | None = None) -> ToolResult:
            expr = str(params.get("expression") or "")
            try:
                val = safe_eval_expression(expr)
                return ToolResult(status="done", output=str(val), evidence={"expression": expr, "result": val})
            except Exception as e:
                return ToolResult(status="failed", error=str(e), output=f"Calculation error: {e}")

        self.register(ToolDefinition(
            name="calculator",
            description="Perform precise, safe mathematical calculations (arithmetic, powers, roots).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "expression": {"type": "STRING", "description": "Mathematical expression, e.g. '256 * 4' or '100 / 5'"}
                },
                "required": ["expression"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_calc_handler,
            is_mutating=False,
        ))

        # 1b. Calculate alias
        self.register(ToolDefinition(
            name="calculate",
            description="Perform precise, safe mathematical calculations (arithmetic, powers, roots).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "expression": {"type": "STRING", "description": "Mathematical expression, e.g. '256 * 4' or '100 / 5'"}
                },
                "required": ["expression"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_calc_handler,
            is_mutating=False,
        ))

        # 2. Read File
        def _read_handler(params: dict, context: dict | None = None) -> ToolResult:
            raw_path = str(params.get("path") or "")
            ctx = context or {}
            allowed_roots = ctx.get("allowed_roots")
            allow_cross_drive = bool(ctx.get("cross_drive") or ctx.get("allow_system_paths") or params.get("cross_drive"))
            if allowed_roots and not (allow_cross_drive or any(str(r) in ("*", "ALL_DRIVES") for r in allowed_roots)):
                try:
                    _ = _resolve_safe_path(raw_path, allowed_roots, allow_cross_drive=False)
                except Exception as e:
                    return ToolResult(status="failed", error=str(e), output=f"Read error: {e}")
            from core.filesystem.service import UnifiedFilesystemService
            return UnifiedFilesystemService.instance().read_file(raw_path, context=ctx)

        self.register(ToolDefinition(
            name="read_file",
            description="Read text contents of a file within allowed directory boundaries.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Absolute or relative file path"}
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_read_handler,
            is_mutating=False,
        ))

        # 3. Write File
        def _write_handler(params: dict, context: dict | None = None) -> ToolResult:
            raw_path = str(params.get("path") or "")
            content = str(params.get("content") or "")
            ctx = context or {}
            allowed_roots = ctx.get("allowed_roots")
            allow_cross_drive = bool(ctx.get("cross_drive") or ctx.get("allow_system_paths") or params.get("cross_drive"))
            if allowed_roots and not (allow_cross_drive or any(str(r) in ("*", "ALL_DRIVES") for r in allowed_roots)):
                try:
                    _ = _resolve_safe_path(raw_path, allowed_roots, allow_cross_drive=False)
                except Exception as e:
                    return ToolResult(status="failed", error=str(e), output=f"Write error: {e}")
            from core.filesystem.service import UnifiedFilesystemService
            return UnifiedFilesystemService.instance().write_file(raw_path, content, context=ctx)

        self.register(ToolDefinition(
            name="write_file",
            description="Write text or data to a file within allowed directory boundaries.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "File path to create or overwrite"},
                    "content": {"type": "STRING", "description": "Full text content to write"}
                },
                "required": ["path", "content"],
            },
            capability_tier=CapabilityTier.WORKSPACE_WRITE,
            handler=_write_handler,
            is_mutating=True,
        ))

        # 4. List Directory
        def _list_handler(params: dict, context: dict | None = None) -> ToolResult:
            raw_path = str(params.get("path") or ".")
            ctx = context or {}
            allowed_roots = ctx.get("allowed_roots")
            allow_cross_drive = bool(ctx.get("cross_drive") or ctx.get("allow_system_paths") or params.get("cross_drive"))
            if allowed_roots and not (allow_cross_drive or any(str(r) in ("*", "ALL_DRIVES") for r in allowed_roots)):
                try:
                    _ = _resolve_safe_path(raw_path, allowed_roots, allow_cross_drive=False)
                except Exception as e:
                    return ToolResult(status="failed", error=str(e), output=f"List error: {e}")
            from core.filesystem.service import UnifiedFilesystemService
            return UnifiedFilesystemService.instance().list_directory(raw_path, context=ctx)

        self.register(ToolDefinition(
            name="list_directory",
            description="List files and subdirectories inside a directory.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Directory path to list (default: current directory)"}
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_list_handler,
            is_mutating=False,
        ))

        # 5. Open In Explorer
        def _open_explorer_handler(params: dict, context: dict | None = None) -> ToolResult:
            raw_path = str(params.get("path") or ".")
            select_file = bool(params.get("select_file", False))
            from core.filesystem.service import UnifiedFilesystemService
            return UnifiedFilesystemService.instance().open_in_explorer(raw_path, select_file=select_file, context=context)

        self.register(ToolDefinition(
            name="open_in_explorer",
            description="Open a directory or highlight a file in Windows File Explorer.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Folder or file path to open in File Explorer"},
                    "select_file": {"type": "BOOLEAN", "description": "Optional flag to highlight the target file in Explorer"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_open_explorer_handler,
            is_mutating=False,
        ))
        self.register(ToolDefinition(
            name="open_directory",
            description="Alias for open_in_explorer: open a directory in Windows File Explorer.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Folder or file path to open in File Explorer"},
                    "select_file": {"type": "BOOLEAN", "description": "Optional flag to highlight the target file in Explorer"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_open_explorer_handler,
            is_mutating=False,
        ))
        self.register(ToolDefinition(
            name="open_folder",
            description="Alias for open_in_explorer: open a folder in Windows File Explorer.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "Folder or file path to open in File Explorer"},
                    "select_file": {"type": "BOOLEAN", "description": "Optional flag to highlight the target file in Explorer"},
                },
                "required": ["path"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_open_explorer_handler,
            is_mutating=False,
        ))

        # ── Milestone 16: Visual Awareness, Window & Browser Tab Intelligence ──

        # 1. Enumerate Windows
        def _enum_windows_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.vision.service import UnifiedVisionService
            include_min = bool(params.get("include_minimized", True))
            windows = UnifiedVisionService.instance().enumerate_windows(include_minimized=include_min)
            lines = [
                f"HWND: {w.hwnd:8} | PID: {w.pid:6} | App: {w.app_name:18} | "
                f"State: {'[ACTIVE]' if w.is_active else ('[MIN]' if w.is_minimized else '[NORMAL]')} | "
                f"Title: '{w.title}'"
                for w in windows
            ]
            return ToolResult(
                status="done",
                output="\n".join(lines) if lines else "No open application windows discovered.",
                evidence={"count": len(windows), "windows": [w.to_dict() for w in windows[:25]]},
            )

        self.register(ToolDefinition(
            name="enumerate_windows",
            description="Enumerate all open desktop windows with title, process name, PID, monitor, and state (active/minimized).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "include_minimized": {"type": "BOOLEAN", "description": "Whether to include minimized windows (default: true)"}
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_enum_windows_handler,
            is_mutating=False,
        ))

        # 2. Focus Window
        def _focus_window_handler(params: dict, context: dict | None = None) -> ToolResult:
            target = params.get("target")
            if target is None:
                return ToolResult(status="failed", error="Target parameter (HWND or title/app string) is required.")
            restore_min = bool(params.get("restore_minimized", True))
            from core.vision.service import UnifiedVisionService
            ok, msg = UnifiedVisionService.instance().focus_window(target, restore_minimized=restore_min)
            return ToolResult(
                status="done" if ok else "failed",
                output=msg,
                evidence={"target": target, "focused": ok},
                error=None if ok else msg,
            )

        self.register(ToolDefinition(
            name="focus_window",
            description="Activate, restore from minimized, and bring an application window to the foreground by HWND, title, or app name.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "target": {"type": "STRING", "description": "HWND integer, window title substring, or application name"},
                    "restore_minimized": {"type": "BOOLEAN", "description": "Whether to restore window if minimized (default: true)"},
                },
                "required": ["target"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=_focus_window_handler,
            is_mutating=True,
        ))

        # 3. Enumerate Browser Tabs
        def _enum_tabs_handler(params: dict, context: dict | None = None) -> ToolResult:
            browser = params.get("browser")
            from core.vision.service import UnifiedVisionService
            tabs = UnifiedVisionService.instance().enumerate_browser_tabs(browser=browser)
            lines = [
                f"[{t.browser.upper()}] TabID: {t.tab_id} | "
                f"State: {'[ACTIVE]' if t.is_active else ('[PINNED]' if t.is_pinned else '[BG]')} | "
                f"Domain: {t.domain_hint or t.domain or 'unknown'} | "
                f"Title: '{t.title}'"
                for t in tabs
            ]
            return ToolResult(
                status="done",
                output="\n".join(lines) if lines else "No browser tabs discovered.",
                evidence={"count": len(tabs), "tabs": [t.to_dict() for t in tabs[:30]]},
            )

        self.register(ToolDefinition(
            name="enumerate_browser_tabs",
            description="Discover and list open tabs across Chrome, Edge, and Firefox with title, domain hints, and active/pinned state.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "browser": {"type": "STRING", "description": "Optional browser filter: 'chrome', 'msedge', or 'firefox'"}
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_enum_tabs_handler,
            is_mutating=False,
        ))

        # 4. Switch Browser Tab
        def _switch_tab_handler(params: dict, context: dict | None = None) -> ToolResult:
            query = params.get("query_or_id") or params.get("query")
            if not query:
                return ToolResult(status="failed", error="Parameter 'query_or_id' is required.")
            from core.vision.service import UnifiedVisionService
            ok, msg = UnifiedVisionService.instance().switch_browser_tab(str(query))
            return ToolResult(
                status="done" if ok else "failed",
                output=msg,
                evidence={"query": query, "switched": ok},
                error=None if ok else msg,
            )

        self.register(ToolDefinition(
            name="switch_browser_tab",
            description="Switch to an open browser tab by tab_id, title substring, or domain, restoring the browser window if minimized.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "query_or_id": {"type": "STRING", "description": "Tab ID, title keyword, or domain"}
                },
                "required": ["query_or_id"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=_switch_tab_handler,
            is_mutating=True,
        ))

        # 5. Capture Visual State
        def _capture_visual_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.vision.service import UnifiedVisionService
            state = UnifiedVisionService.instance().get_desktop_state(force_scan=True)
            active_title = state.active_window.title if state.active_window else "None"
            return ToolResult(
                status="done",
                output=(
                    f"Desktop Visual State:\n"
                    f"- Active Window: {active_title}\n"
                    f"- Open Windows: {len(state.windows)}\n"
                    f"- Open Tabs: {len(state.browser_tabs)}\n"
                    f"- Visual dHash: {state.dhash}\n"
                    f"- Elements: {state.ui_elements_count}\n"
                    f"- Privacy Redacted: {state.privacy_redacted}"
                ),
                evidence=state.to_dict(),
            )

        self.register(ToolDefinition(
            name="capture_visual_state",
            description="Capture current desktop visual state, active window, dhash, and open application/tab counts.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_capture_visual_handler,
            is_mutating=False,
        ))

        # 6. Explain Desktop State
        def _explain_desktop_handler(params: dict, context: dict | None = None) -> ToolResult:
            question = params.get("question")
            from core.vision.service import UnifiedVisionService
            explanation = UnifiedVisionService.instance().explain_desktop_state(question=question)
            return ToolResult(
                status="done",
                output=explanation,
                evidence={"question": question},
            )

        self.register(ToolDefinition(
            name="explain_desktop_state",
            description="Explain the current desktop state, active window, and visible UI context in natural language.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "question": {"type": "STRING", "description": "Optional specific question about the screen"}
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_explain_desktop_handler,
            is_mutating=False,
        ))

        # ── M17 Universal Application Intelligence Tools ─────────────────────────
        def _app_discover_inventory_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.registry import ApplicationRegistry
            registry = ApplicationRegistry.instance()
            force = params.get("force", False)
            registry.refresh(force=force)
            running_only = params.get("running_only", False)
            apps = registry.list_running_apps() if running_only else registry.list_all_apps()
            app_list = [a.to_dict() for a in apps]
            return ToolResult(
                status="done",
                output=f"Discovered {len(app_list)} {'running' if running_only else 'installed'} applications.",
                evidence={"count": len(app_list), "applications": app_list},
            )

        self.register(ToolDefinition(
            name="app_discover_inventory",
            description="Discover or query installed and currently running Windows applications.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "running_only": {"type": "BOOLEAN", "description": "If true, return only currently running applications with window handles."},
                    "force": {"type": "BOOLEAN", "description": "Force refresh the system discovery inventory."},
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_app_discover_inventory_handler,
            is_mutating=False,
        ))

        def _app_launch_or_activate_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.registry import ApplicationRegistry
            query = params.get("app_name") or params.get("query", "")
            args = params.get("args")
            registry = ApplicationRegistry.instance()
            res = registry.launch_or_activate(query, args=args)
            if res.get("success"):
                return ToolResult(
                    status="done",
                    output=res.get("message", f"Application '{query}' successfully activated/launched."),
                    evidence=res,
                )
            return ToolResult(
                status="failed",
                error=res.get("error", f"Failed to activate or launch '{query}'."),
                evidence=res,
            )

        self.register(ToolDefinition(
            name="app_launch_or_activate",
            description="Launch an installed application or activate/restore an existing window by name or alias.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app_name": {"type": "STRING", "description": "Name, alias, or ID of the application (e.g. 'whatsapp', 'vscode', 'notepad')."},
                    "args": {"type": "ARRAY", "description": "Optional command-line arguments to pass if launched."},
                },
                "required": ["app_name"],
            },
            capability_tier=CapabilityTier.SYSTEM_MUTATING,
            handler=_app_launch_or_activate_handler,
            is_mutating=True,
        ))

        def _app_execute_action_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.interaction_router import InteractionRouter
            from core.application_intelligence.types import ActionContract
            router = InteractionRouter()
            contract = ActionContract(
                action_id=params.get("action_id", f"act_{int(time.time()*1000)}"),
                target_app=params.get("target_app", ""),
                operation=params.get("operation", ""),
                parameters=params.get("parameters", {}),
            )
            evidence = router.dispatch(contract, tool_registry=self)
            ev_dict = evidence.to_dict()
            if isinstance(evidence.evidence_data, dict):
                ev_dict.update(evidence.evidence_data)
            if evidence.is_success:
                return ToolResult(
                    status="done",
                    output=f"Successfully executed '{contract.operation}' on '{contract.target_app}'.",
                    evidence=ev_dict,
                )
            elif "APPROVAL_REQUIRED" in (evidence.error_message or ""):
                return ToolResult(
                    status="unverified",
                    output=evidence.error_message,
                    error="APPROVAL_REQUIRED",
                    evidence=ev_dict,
                )
            return ToolResult(
                status="failed",
                error=evidence.error_message or f"Action '{contract.operation}' on '{contract.target_app}' failed.",
                evidence=ev_dict,
            )

        self.register(ToolDefinition(
            name="app_execute_action",
            description="Execute a high-level application action (e.g. search contacts, draft message, send message, web search, open document).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "target_app": {"type": "STRING", "description": "Target application (e.g. 'whatsapp', 'browser', 'document', 'devtools')."},
                    "operation": {"type": "STRING", "description": "Operation name (e.g. 'send_message', 'search_web', 'open_document')."},
                    "parameters": {"type": "OBJECT", "description": "Action parameters."},
                },
                "required": ["target_app", "operation"],
            },
            capability_tier=CapabilityTier.SYSTEM_MUTATING,
            handler=_app_execute_action_handler,
            is_mutating=True,
        ))

        def _app_resolve_entity_handler(params: dict, context: dict | None = None) -> ToolResult:
            entity_type = params.get("entity_type", "contact").lower()
            query = params.get("query", "")
            if entity_type == "contact":
                from core.application_intelligence.adapters.whatsapp import WhatsAppAdapter
                wa = WhatsAppAdapter()
                res = wa.resolve_contact(query)
            elif entity_type == "document":
                from core.application_intelligence.adapters.document import DocumentAdapter
                doc = DocumentAdapter()
                res = doc.resolve_document(query, target_extension=params.get("file_extension"))
            else:
                return ToolResult(status="failed", error=f"Unknown entity type: {entity_type}")

            return ToolResult(
                status="done" if res.is_matched else ("unverified" if res.is_ambiguous else "failed"),
                output=res.clarification_prompt or f"Resolved {entity_type}: {res.resolved_value}",
                evidence=res.to_dict(),
            )

        self.register(ToolDefinition(
            name="app_resolve_entity",
            description="Resolve a contact or document entity with composite scoring and disambiguation prompt.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "entity_type": {"type": "STRING", "description": "'contact' or 'document'"},
                    "query": {"type": "STRING", "description": "Name or query string to resolve"},
                    "file_extension": {"type": "STRING", "description": "Optional file extension for documents (e.g. '.pdf')"},
                },
                "required": ["entity_type", "query"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_app_resolve_entity_handler,
            is_mutating=False,
        ))

        def _app_learn_workflow_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.learning_engine import ApplicationLearningEngine
            from core.application_intelligence.workflow_store import WorkflowStore
            engine = ApplicationLearningEngine(workflow_store=WorkflowStore.instance())
            app_name = params.get("app_name", "")
            goal = params.get("goal")
            if goal:
                wf = engine.learn_workflow_for_goal(app_name, goal)
                return ToolResult(
                    status="done",
                    output=f"Synthesized workflow '{wf.workflow_id}' for goal '{goal}' on '{app_name}' (Trust: UNVERIFIED).",
                    evidence=wf.to_dict(),
                )
            else:
                explore_data = engine.passive_explore_app(app_name)
                return ToolResult(
                    status="done",
                    output=f"Passive exploration of '{app_name}' discovered {explore_data.get('control_count', 0)} controls.",
                    evidence=explore_data,
                )

        self.register(ToolDefinition(
            name="app_learn_workflow",
            description="Explore an unfamiliar application and synthesize a reusable workflow (strictly UNVERIFIED initially).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app_name": {"type": "STRING", "description": "Name of the target application."},
                    "goal": {"type": "STRING", "description": "Optional goal to synthesize a workflow for."},
                },
                "required": ["app_name"],
            },
            capability_tier=CapabilityTier.SYSTEM_MUTATING,
            handler=_app_learn_workflow_handler,
            is_mutating=True,
        ))

        # ── Milestone 18: Advanced Execution Framework Integration Tools ──────────

        def _app_provider_status_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.frameworks.registry import ProviderRegistry
            reg = ProviderRegistry.instance()
            providers = reg.list_providers()
            lines = []
            ev_list = []
            for p in providers:
                status = reg.check_and_update_health(p)
                line = (
                    f"[{p.name.upper()}] Status: {status.value.upper()} | "
                    f"Successes: {p.success_count} | Failures: {p.consecutive_failures} | "
                    f"Avg Latency: {p.get_average_latency_ms():.1f}ms | NeedsLock: {p.requires_desktop_lock}"
                )
                lines.append(line)
                ev_list.append({
                    "name": p.name,
                    "status": status.value,
                    "success_count": p.success_count,
                    "consecutive_failures": p.consecutive_failures,
                    "avg_latency_ms": p.get_average_latency_ms(),
                    "requires_desktop_lock": p.requires_desktop_lock,
                })

            return ToolResult(
                status="done",
                output="\n".join(lines) if lines else "No execution framework providers registered.",
                evidence={"providers": ev_list, "count": len(ev_list)},
            )

        self.register(ToolDefinition(
            name="app_provider_status",
            description="Query the operational status, circuit-breaker states, and latency metrics of execution framework providers (UFO, Playwright, Stagehand, pywinauto, OmniParser).",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_app_provider_status_handler,
            is_mutating=False,
        ))

        def _app_benchmark_providers_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.frameworks.omniparser.benchmark import OmniParserBenchmark
            bench = OmniParserBenchmark.run_benchmark()
            evidence_data = {
                **bench,
                "hardware_environment": bench.get("hardware", {}),
                "benchmark": bench,
            }
            return ToolResult(
                status="done",
                output=(
                    f"Framework Benchmark Complete:\n"
                    f"- Device: {bench['hardware']['device_name']}\n"
                    f"- CUDA Available: {bench['hardware']['cuda_available']}\n"
                    f"- Free VRAM: {bench['hardware']['free_vram_mb']:.1f} MB (Meets 3GB: {bench['hardware']['meets_3gb_threshold']})\n"
                    f"- Benchmark Latency: {bench['benchmark_latency_ms']:.1f} ms\n"
                    f"- Adoption Decision: {bench['adoption_decision']}\n"
                    f"- Recommendation: {bench['recommendation']}"
                ),
                evidence=evidence_data,
            )

        self.register(ToolDefinition(
            name="app_benchmark_providers",
            description="Benchmark execution providers (including OmniParser visual grounding on RTX 3050 / CPU) for latency, memory, and VRAM feasibility.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_app_benchmark_providers_handler,
            is_mutating=False,
        ))

        # ── Milestone 19: Full Microsoft UFO² Desktop Agent Tools ─────────
        def _ufo_desktop_mission_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.frameworks.ufo.mission_runner import UFODesktopMissionRunner
            from core.application_intelligence.types import ActionContract, AppInteractionTier

            target_app = str(params.get("target_app") or "")
            allowed_apps = params.get("allowed_apps") or ([target_app] if target_app else [])
            operation = str(params.get("operation") or "execute_desktop_task")
            inner_params = params.get("parameters") or {}
            timeout = float(params.get("timeout") or 60.0)

            contract = ActionContract(
                target_app=target_app,
                operation=operation,
                parameters=inner_params,
                tier=AppInteractionTier.TIER_5_UFO_DESKTOP_AGENT,
                allowed_apps=allowed_apps,
                allowed_operations=params.get("allowed_operations", [operation]),
            )

            runner = UFODesktopMissionRunner()
            evidence = runner.execute_mission(contract, timeout=timeout)

            if evidence.status == "done":
                return ToolResult(
                    status="done",
                    output=f"UFO Desktop Mission completed successfully: {len(evidence.facts)} facts recorded.",
                    evidence=evidence.evidence_data,
                )
            else:
                return ToolResult(
                    status="failed" if evidence.status == "failed" else "unverified",
                    error=evidence.error_message or "UFO Desktop Mission failed or requires authorization.",
                    output=f"UFO Desktop Mission status: {evidence.status}. Error: {evidence.error_message}",
                    evidence=evidence.evidence_data,
                )

        self.register(ToolDefinition(
            name="ufo_desktop_mission",
            description="Delegate a bounded multi-application or desktop automation mission to Microsoft UFO² (HostAgent, AppAgents, FastMCP).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "target_app": {"type": "STRING", "description": "Primary target application name or executable"},
                    "allowed_apps": {"type": "ARRAY", "description": "List of authorized application executables (e.g. ['WINWORD.EXE', 'EXCEL.EXE'])"},
                    "operation": {"type": "STRING", "description": "High-level goal or operation to execute"},
                    "parameters": {"type": "OBJECT", "description": "Operation arguments"},
                    "timeout": {"type": "NUMBER", "description": "Maximum execution budget in seconds (default: 60.0)"},
                },
                "required": ["operation"],
            },
            capability_tier=CapabilityTier.EXECUTION,
            handler=_ufo_desktop_mission_handler,
            is_mutating=True,
        ))

        def _ufo_mcp_servers_status_handler(params: dict, context: dict | None = None) -> ToolResult:
            from core.application_intelligence.frameworks.ufo.compat import ensure_ufo_environment
            ensure_ufo_environment()
            try:
                from ufo.client.mcp.local_servers import (
                    cli_mcp_server, excel_wincom_mcp_server, pdf_reader_mcp_server,
                    ppt_wincom_mcp_server, ui_mcp_server, word_wincom_mcp_server,
                )
                from ufo.client.mcp.mcp_registry import MCPRegistry
                servers = MCPRegistry.list()
                ev_data = {"servers": servers, "count": len(servers)}
                return ToolResult(
                    status="done",
                    output=f"UFO Local FastMCP Servers: {len(servers)} registered ({', '.join(servers)})",
                    evidence=ev_data,
                )
            except Exception as e:
                return ToolResult(status="failed", error=str(e), output=f"Failed querying UFO MCP servers: {e}")

        self.register(ToolDefinition(
            name="ufo_mcp_servers_status",
            description="Inspect registered Microsoft UFO² local FastMCP execution servers (UICollector, HostUI, AppUI, WinCOM, CLI, PDF).",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_ufo_mcp_servers_status_handler,
            is_mutating=False,
        ))

    def register(self, tool_def: ToolDefinition) -> None:
        """Register or update a tool definition in the registry."""
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> ToolDefinition | None:
        """Fetch tool definition by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        """Return all registered tool definitions."""
        return list(self._tools.values())

    def get_schemas(
        self,
        allowed_tools: list[str] | None = None,
        allowed_tiers: list[CapabilityTier] | None = None
    ) -> list[dict[str, Any]]:
        """Return tool schemas filtered strictly by declared tool names and capability tiers."""
        allowed_names_set = set(allowed_tools) if allowed_tools is not None else None
        allowed_tiers_set = set(allowed_tiers) if allowed_tiers is not None else None

        schemas = []
        for name, tool in self._tools.items():
            if allowed_names_set is not None and name not in allowed_names_set:
                continue
            if allowed_tiers_set is not None and tool.capability_tier not in allowed_tiers_set:
                continue
            schemas.append(tool.to_schema())
        return schemas

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        caller_agent: str = "orchestrator",
        allowed_tools: list[str] | None = None,
        allowed_tiers: list[CapabilityTier] | None = None,
        context: dict[str, Any] | None = None,
        permission_context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Safely execute a tool after verifying capability envelopes.
        
        Strictly prevents peer-to-peer delegation and unauthorized capability escalation.
        """
        start = time.perf_counter()

        if permission_context:
            caller_agent = permission_context.get("agent", caller_agent)
            if "tier" in permission_context:
                tier = permission_context["tier"]
                allowed_tiers = [tier] if isinstance(tier, CapabilityTier) else [CapabilityTier(str(tier))]
            elif "tiers" in permission_context:
                allowed_tiers = permission_context["tiers"]
            if "allowed_roots" in permission_context:
                context = context or {}
                context["allowed_roots"] = permission_context["allowed_roots"]
            if "allowed_tools" in permission_context:
                allowed_tools = permission_context["allowed_tools"]

        if allowed_tools is None:
            if caller_agent == "orchestrator":
                allowed_tools = list(self._tools.keys())
            else:
                allowed_tools = [tool_name]

        if allowed_tiers is None:
            if caller_agent == "orchestrator":
                allowed_tiers = list(CapabilityTier)
            else:
                try:
                    from core.orchestrator.agents import AgentRegistry
                    prof = AgentRegistry().get(caller_agent)
                    if prof and prof.allowed_tiers:
                        allowed_tiers = prof.allowed_tiers
                except Exception as e:  # noqa: BLE001
                    logger.debug("Could not resolve allowed tiers for agent %s: %s", caller_agent, e)
                if not allowed_tiers:
                    allowed_tiers = [CapabilityTier.READ_ONLY, CapabilityTier.WORKSPACE_WRITE]

        # 1. Existence check in tool registry
        _TOOL_ALIASES = {
            "web_search": "search_web",
            "python_env": "python_version",
            "ollama_check": "check_ollama",
            "disk_space": "disk_status",
            "git_status": "dev_git_status",
        }
        resolved_name = _TOOL_ALIASES.get(tool_name, tool_name)
        tool = self._tools.get(resolved_name)
        if not tool:
            res = ToolResult(
                status="failed",
                error=f"UNKNOWN_TOOL: Tool '{tool_name}' is not registered in the system.",
                duration_ms=(time.perf_counter() - start) * 1000.0,
            )
            self._emit_audit(tool_name, caller_agent, res, arguments)
            return res

        # 2. Verification of tool permission
        allowed_set = {t.lower() for t in allowed_tools}
        canonical_allowed = {_TOOL_ALIASES.get(t, t) for t in allowed_set}
        if tool_name.lower() not in allowed_set and resolved_name.lower() not in canonical_allowed:
            res = ToolResult(
                status="failed",
                error=f"TOOL_NOT_PERMITTED: UNAUTHORIZED_TOOL: Agent '{caller_agent}' is not permitted to use '{tool_name}'. Allowed: {allowed_tools}",
                duration_ms=(time.perf_counter() - start) * 1000.0,
            )
            self._emit_audit(tool_name, caller_agent, res, arguments)
            return res

        # 3. Verification of capability tier
        if allowed_tiers:
            tool_rank = _TIER_RANK.get(tool.capability_tier, 99)
            max_allowed_rank = max(_TIER_RANK.get(t, 0) for t in allowed_tiers)
            if tool_rank > max_allowed_rank:
                res = ToolResult(
                    status="failed",
                    error=(
                        f"TIER_VIOLATION: CAPABILITY_VIOLATION: Tool '{tool_name}' requires capability tier '{tool.capability_tier.name}' "
                        f"which exceeds agent '{caller_agent}' allowed tiers: {[t.name for t in allowed_tiers]}."
                    ),
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                )
                self._emit_audit(tool_name, caller_agent, res, arguments)
                return res

        # 3.5 Central PolicyEngine & Permission Profile Enforcement
        try:
            from core.security.policy_engine import OperationRequest, PolicyEngine
            pe = PolicyEngine.instance()
            pe_verdict = pe.evaluate_operation(OperationRequest(
                tool_name=tool_name,
                agent_name=caller_agent,
                arguments=arguments,
                approval_token=(context or {}).get("approval_token"),
            ))
            if not pe_verdict.is_authorized:
                if pe_verdict.requires_approval:
                    res = ToolResult(
                        status="unverified",
                        error=f"APPROVAL_REQUIRED: {pe_verdict.reason}",
                        evidence={"requires_approval": True, "challenge": pe_verdict.approval_challenge},
                        duration_ms=(time.perf_counter() - start) * 1000.0,
                    )
                else:
                    res = ToolResult(
                        status="failed",
                        error=f"PERMISSION_DENIED: {pe_verdict.reason}",
                        duration_ms=(time.perf_counter() - start) * 1000.0,
                    )
                self._emit_audit(tool_name, caller_agent, res, arguments)
                return res
        except ImportError:
            pass

        try:
            from core.security.permissions import get_permission_manager
            perm_mgr = get_permission_manager()
            is_pre_approved = bool((context or {}).get("is_approved") or (permission_context or {}).get("is_approved"))
            workspace_roots = (context or {}).get("allowed_roots") or (permission_context or {}).get("allowed_roots")
            verdict = perm_mgr.evaluate_tool(
                tool=tool,
                caller_agent=caller_agent,
                arguments=arguments,
                workspace_roots=workspace_roots,
                is_pre_approved=is_pre_approved,
            )
            if not verdict.allowed:
                if verdict.requires_approval:
                    res = ToolResult(
                        status="unverified",
                        error=f"APPROVAL_REQUIRED: {verdict.approval_reason}",
                        evidence={
                            "requires_approval": True,
                            "approval_reason": verdict.approval_reason,
                            "profile": perm_mgr.get_profile().value,
                        },
                        duration_ms=(time.perf_counter() - start) * 1000.0,
                    )
                else:
                    res = ToolResult(
                        status="failed",
                        error=f"PERMISSION_DENIED: {verdict.reason}",
                        duration_ms=(time.perf_counter() - start) * 1000.0,
                    )
                self._emit_audit(tool_name, caller_agent, res, arguments)
                return res
        except ImportError:
            pass

        # 4. Input parameter schema validation
        if tool.parameters and isinstance(tool.parameters, dict):
            required_params = tool.parameters.get("required") or []
            for rp in required_params:
                if rp not in arguments:
                    res = ToolResult(
                        status="failed",
                        error=f"VALIDATION_ERROR: Missing required parameter '{rp}' for tool '{tool_name}'.",
                        duration_ms=(time.perf_counter() - start) * 1000.0,
                    )
                    self._emit_audit(tool_name, caller_agent, res, arguments)
                    return res

        # 4. Invocation of handler
        if not tool.handler:
            res = ToolResult(
                status="failed",
                error=f"Tool '{tool_name}' has no executable handler registered.",
                duration_ms=(time.perf_counter() - start) * 1000.0,
            )
            self._emit_audit(tool_name, caller_agent, res, arguments)
            return res

        try:
            raw_res = tool.handler(arguments, context=context or {})
            duration = (time.perf_counter() - start) * 1000.0
            if isinstance(raw_res, ToolResult):
                raw_res.duration_ms = duration
                raw_res.output = sanitize_tool_output(raw_res.output)
                self._emit_audit(tool_name, caller_agent, raw_res, arguments)
                return raw_res
            elif isinstance(raw_res, str):
                cleaned = sanitize_tool_output(raw_res)
                r = ToolResult(status="done", output=cleaned, duration_ms=duration)
                self._emit_audit(tool_name, caller_agent, r, arguments)
                return r
            else:
                cleaned = sanitize_tool_output(str(raw_res))
                r = ToolResult(status="done", output=cleaned, duration_ms=duration)
                self._emit_audit(tool_name, caller_agent, r, arguments)
                return r
        except PermissionError as pe:
            res = ToolResult(
                status="failed",
                error=f"Permission error in tool '{tool_name}': {pe}. Path is outside permitted directories.",
                duration_ms=(time.perf_counter() - start) * 1000.0,
            )
            self._emit_audit(tool_name, caller_agent, res, arguments)
            return res
        except Exception as e:
            res = ToolResult(
                status="failed",
                error=f"Execution exception in tool '{tool_name}': {e}",
                duration_ms=(time.perf_counter() - start) * 1000.0,
            )
            self._emit_audit(tool_name, caller_agent, res, arguments)
            return res
