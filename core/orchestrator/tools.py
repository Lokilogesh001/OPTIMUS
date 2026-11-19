"""core/orchestrator/tools.py — Centralized Tool Registry & Capability Controller.

Enforces:
- Capability-Based Access Control (CBAC) with explicit permission tiers.
- Independent verification and physical postcondition tracking.
- Safe execution sandboxing (no direct, unverified execution from model text).
"""

from __future__ import annotations

import ast
import hashlib
import operator
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


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
    p = Path(raw_path)
    if allowed_roots:
        canonical_roots = [Path(r).resolve() for r in allowed_roots]
        # 1. If path is already inside an allowed root (e.g. absolute or normalized relative), use it directly
        p_resolved = p.resolve()
        if any(p_resolved == r or r in p_resolved.parents for r in canonical_roots):
            return p_resolved

        # 2. If relative, resolve relative to allowed_roots[0]
        if not p.is_absolute():
            p_under_root = (canonical_roots[0] / p).resolve()
            if any(p_under_root == r or r in p_under_root.parents for r in canonical_roots):
                return p_under_root

        # 3. Cross-drive / system path permission: if allowed or wildcard is present
        if allow_cross_drive or any(str(r) in ("*", "ALL_DRIVES") for r in allowed_roots):
            try:
                from core.windows_runtime.policy import WindowsRuntimePolicy
                if p_resolved.is_absolute() and p_resolved.drive:
                    mounted = WindowsRuntimePolicy.discover_mounted_drives()
                    drive_prefix = p_resolved.drive.upper()
                    if any(m.upper().startswith(drive_prefix) for m in mounted):
                        return p_resolved
            except Exception:  # noqa: BLE001, S110
                pass

        raise PermissionError(f"PATH_VIOLATION: Access denied: path '{p}' is outside permitted directories (allowed roots: {allowed_roots})")

    return p.resolve()


# ── Central Tool Registry ────────────────────────────────────────────────────

class ToolRegistry:
    """Central repository for tool discovery, capability checks, and sandboxed dispatch."""

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
            try:
                target = _resolve_safe_path(raw_path, allowed_roots, allow_cross_drive=allow_cross_drive)
                if not target.is_file():
                    return ToolResult(status="failed", error=f"File not found: '{target}'", output="File not found")
                try:
                    content = target.read_text(encoding="utf-8", errors="replace")
                except PermissionError:
                    from core.windows_runtime.client import get_windows_runtime_client
                    client = get_windows_runtime_client()
                    content = client.read_file(str(target))
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                return ToolResult(
                    status="done",
                    output=content[:20000],
                    evidence={"path": str(target), "bytes": len(content), "sha256": digest}
                )
            except Exception as e:
                return ToolResult(status="failed", error=str(e), output=f"Read error: {e}")

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
            try:
                target = _resolve_safe_path(raw_path, allowed_roots, allow_cross_drive=allow_cross_drive)
                raw_bytes = content.encode("utf-8")
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw_bytes)
                except PermissionError:
                    from core.windows_runtime.client import get_windows_runtime_client
                    client = get_windows_runtime_client()
                    client.write_file(str(target), content)
                digest = hashlib.sha256(raw_bytes).hexdigest()
                return ToolResult(
                    status="done",
                    output=f"Successfully wrote {len(raw_bytes)} bytes to '{target}' (sha256={digest[:8]}).",
                    evidence={
                        "path": str(target),
                        "bytes": len(raw_bytes),
                        "sha256": digest,
                    }
                )
            except Exception as e:
                return ToolResult(status="failed", error=str(e), output=f"Write error: {e}")

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
            try:
                target = _resolve_safe_path(raw_path, allowed_roots, allow_cross_drive=allow_cross_drive)
                if not target.is_dir():
                    return ToolResult(status="failed", error=f"Not a directory: '{target}'", output="Not a directory")
                entries = []
                try:
                    for entry in target.iterdir():
                        kind = "DIR" if entry.is_dir() else "FILE"
                        size = entry.stat().st_size if entry.is_file() else 0
                        entries.append(f"{kind:4}  {size:10} bytes  {entry.name}")
                except PermissionError:
                    from core.windows_runtime.client import get_windows_runtime_client
                    client = get_windows_runtime_client()
                    res = client.list_directory(str(target))
                    if res.get("error"):
                        raise PermissionError(res["error"])
                    entries = []
                    for e in res.get("entries", []):
                        kind = "DIR" if e.get("is_dir") else "FILE"
                        size = e.get("size", 0)
                        entries.append(f"{kind:4}  {size:10} bytes  {e.get('name')}")
                out = "\n".join(entries[:100])
                return ToolResult(
                    status="done",
                    output=out or "Directory is empty.",
                    evidence={"path": str(target), "count": len(entries)}
                )
            except Exception as e:
                return ToolResult(status="failed", error=str(e), output=f"List error: {e}")

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

        # 3.5 Central Permission Profile & Approval Gate Enforcement
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
