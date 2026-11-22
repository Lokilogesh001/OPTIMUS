"""core/plugin_loader.py — Plugin discovery, validation, security screening, and dispatch.

Discovery runs once (JarvisLive.__init__ calls discover_plugins()); the resulting
PluginRegistry is cached for the process lifetime. Enable/disable state is re-read
from config on every call to get_tool_declarations() / run() / list_for_ui(), so
toggling a plugin does not require restarting the app or re-importing anything.

Security and hardening additions (M20 EGM):
- Static AST pre-screening before import (rejects top-level execution and forbidden imports).
- CapabilityTier alignment and fail-closed authorization via PolicyEngine.
- Redaction of sensitive tokens and credentials from error responses and logs.
- Single-token consumption protection with pre_authorized flag.
- ToolRegistry bridging for unified agent discovery and CBAC enforcement.
- Clean sys.modules cleanup on validation or collision failure.
- Bounded 30.0s execution timeout per invocation.
"""
from __future__ import annotations

import ast
import concurrent.futures
import importlib.util
import inspect
import re
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memory.config_manager import get_plugin_config, get_plugin_enabled

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}

_BEHAVIORS = ("BLOCKING", "NON_BLOCKING")
_SCHEDULING = ("WHEN_IDLE", "SILENT", "INTERRUPT")

# Permitted module-level top-level AST nodes (whitelisted syntax)
_SAFE_ROOT_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.TypeAlias if hasattr(ast, "TypeAlias") else ast.AST,
)

# Modules forbidden for non-execution tiers
_DANGEROUS_MODULES = {"subprocess", "ctypes", "pty", "multiprocessing"}


class PluginASTValidator:
    """Pre-import syntactic AST screening gate (reconciles CRT-02 and VULN-01).
    
    Enforces deep syntactic checks across the AST:
    1. Clean syntax without SyntaxError.
    2. Root consists solely of safe declaration nodes.
    3. Rejection of forbidden modules (_DANGEROUS_MODULES).
    4. Forbids standalone root function calls and expressions.
    5. Prohibits executable statements and calls inside ClassDef bodies.
    6. Forbids dynamic call invocations in function or class decorators.
    7. Forbids dangerous invocations inside assignments or comprehensions.
    """

    @classmethod
    def validate_source(cls, path: Path) -> tuple[bool, str]:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path.name)
        except SyntaxError as se:
            return False, f"AST Syntax Error: {se}"
        except Exception as ex:
            return False, f"AST Parse Error: {ex}"

        # 1. Global AST walk for dangerous imports
        for subnode in ast.walk(tree):
            if isinstance(subnode, ast.Import):
                for alias in subnode.names:
                    top_pkg = alias.name.split(".")[0]
                    if top_pkg in _DANGEROUS_MODULES:
                        return False, f"SECURITY_VIOLATION: Import of dangerous module '{alias.name}' is prohibited."
            elif isinstance(subnode, ast.ImportFrom):
                if subnode.module:
                    top_pkg = subnode.module.split(".")[0]
                    if top_pkg in _DANGEROUS_MODULES:
                        return False, f"SECURITY_VIOLATION: Import from dangerous module '{subnode.module}' is prohibited."

        # 2. Inspect root level nodes
        for node in tree.body:
            # Allow docstrings or string constant expressions
            if isinstance(node, ast.Expr):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    continue
                # Reject standalone calls: e.g. os.system(...), init()
                if isinstance(node.value, ast.Call):
                    callee = ast.unparse(node.value.func) if hasattr(ast, "unparse") else "call"
                    return False, f"SECURITY_VIOLATION: Standalone top-level function call '{callee}' is prohibited."
                return False, f"SECURITY_VIOLATION: Non-whitelisted root expression node '{type(node.value).__name__}' is prohibited."

            if not isinstance(node, _SAFE_ROOT_NODES):
                return False, f"SECURITY_VIOLATION: Disallowed root node '{type(node).__name__}' at line {node.lineno}."

            # Inspect ClassDef bodies and decorators (reconciles VULN-01)
            if isinstance(node, ast.ClassDef):
                # Forbid dynamic decorator calls on classes
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call):
                        return False, f"SECURITY_VIOLATION: Dynamic class decorator call on '{node.name}' is prohibited."
                # Class body statements execute at import time!
                for stmt in node.body:
                    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if isinstance(stmt, ast.Pass):
                        continue
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                        continue
                    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                        # Only allow assignments to simple constants or data structures
                        for sub_stmt in ast.walk(stmt):
                            if isinstance(sub_stmt, ast.Call):
                                return False, f"SECURITY_VIOLATION: Call inside class body '{node.name}' at line {stmt.lineno} is prohibited."
                        continue
                    return False, f"SECURITY_VIOLATION: Executable statement '{type(stmt).__name__}' in class body '{node.name}' is prohibited."

            # Inspect FunctionDef / AsyncFunctionDef decorators
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call):
                        return False, f"SECURITY_VIOLATION: Dynamic function decorator call on '{node.name}' is prohibited."

            # Inspect calls inside assignments or comprehensions (e.g. X = os.system("..."))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func_name = ""
                        if isinstance(sub.func, ast.Name):
                            func_name = sub.func.id
                        elif isinstance(sub.func, ast.Attribute):
                            func_name = ast.unparse(sub.func) if hasattr(ast, "unparse") else ""
                        forbidden_calls = {"eval", "exec", "__import__", "os.system", "os.popen", "subprocess.run", "subprocess.Popen"}
                        if func_name in forbidden_calls:
                            return False, f"SECURITY_VIOLATION: Dangerous top-level invocation '{func_name}' is prohibited."

        return True, ""


def _opt_upper(value, allowed: tuple[str, ...]) -> str | None:
    v = str(value or "").strip().upper()
    return v if v in allowed else None


def _opt_text(value) -> str | None:
    text = str(value or "").strip()
    return text or None


@dataclass
class PluginRecord:
    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: dict(_DEFAULT_PARAMS))
    run: Callable | None = None
    file: str = ""
    valid: bool = False
    error: str = ""
    settings: dict | None = None   # optional PLUGIN_SETTINGS schema (config fields)
    behavior: str | None = None    # None = the API's default (blocking)
    scheduling: str | None = None  # None = the API's default (WHEN_IDLE)
    tier: str = "READ_ONLY"        # CapabilityTier enum string (reconciles HIGH-02)
    capabilities: list[str] = field(default_factory=list) # Granular audit tags
    brief: str | None = None
    timeout: float = 30.0          # Bounded execution budget in seconds (reconciles HIGH-03)


class PluginRegistry:
    def __init__(self, plugins: dict[str, PluginRecord], logger: Callable[[str], None],
                 notify: Callable[[str], None] | None = None,
                 plugins_dir: Path | None = None):
        self._plugins = plugins          # name -> PluginRecord, VALID entries only
        self._all_records: list[PluginRecord] = []   # valid + invalid, for UI listing
        self._logger = logger
        self._notify = notify or (lambda _msg: None)
        self._plugins_dir = plugins_dir

    @staticmethod
    def sanitize_secrets(text: str) -> str:
        """Redact sensitive tokens, passwords, and API keys from text (reconciles SEC-04)."""
        if not text:
            return ""
        try:
            from core.orchestrator.tools import _SECRET_PATTERNS
            sanitized = text
            for pattern, replacement in _SECRET_PATTERNS:
                sanitized = pattern.sub(replacement, sanitized)
            return sanitized
        except Exception:
            return text

    # -- called by main.py at LiveConnectConfig build time --
    def get_tool_declarations(self) -> list[dict]:
        decls = []
        for name, rec in self._plugins.items():
            if get_plugin_enabled(name, rec.tier):
                decl = {
                    "name": rec.name,
                    "description": rec.description,
                    "parameters": rec.parameters,
                }
                if rec.behavior:
                    decl["behavior"] = rec.behavior
                if rec.brief:
                    decl["brief"] = rec.brief
                decls.append(decl)
        return decls

    def has(self, name: str) -> bool:
        return name in self._plugins

    def get(self, name: str) -> PluginRecord | None:
        return self._plugins.get(name)

    def scheduling(self, name: str) -> str | None:
        """How this plugin's result should re-enter the conversation, if it said."""
        rec = self._plugins.get(name)
        return rec.scheduling if rec else None

    # -- called by main.py / ToolRegistry to execute tool --
    def run(self, name: str, parameters: dict, player=None, session_memory=None,
            pre_authorized: bool = False, approval_token: str | None = None) -> str:
        """Execute a plugin tool with PolicyEngine evaluation, timeouts, and secret sanitization."""
        rec = self._plugins.get(name)
        if rec is None or not rec.valid:
            return f"Plugin '{name}' is not available."
        if not get_plugin_enabled(name, rec.tier):
            return f"The '{name}' plugin is currently disabled."

        # 1. PolicyEngine Authoritative Pre-Flight (reconciles CRT-01 & SEC-02)
        if not pre_authorized:
            try:
                from core.orchestrator.tools import CapabilityTier
                from core.security.policy_engine import OperationRequest, PolicyEngine
                pe = PolicyEngine.instance()
                token_obj = pe.get_token(approval_token) if approval_token else None
                tier_val = getattr(CapabilityTier, rec.tier, CapabilityTier.READ_ONLY)

                verdict = pe.evaluate_operation(OperationRequest(
                    tool_name=f"plugin.{name}",
                    agent_name="plugin_runner",
                    arguments=parameters or {},
                    allowed_tiers=[tier_val],
                    approval_token=token_obj,
                ))
                if verdict.requires_approval or not verdict.is_authorized:
                    return f"APPROVAL_REQUIRED: Plugin '{name}' execution blocked: {verdict.reason}"
            except Exception as pe_ex:
                err_msg = f"SECURITY_ERROR: PolicyEngine evaluation failed for '{name}': {pe_ex}"
                self._logger(err_msg)
                return err_msg

        # 2. Timeout-Bounded Execution (reconciles HIGH-03 and VULN-03)
        timeout_sec = rec.timeout or 30.0
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(_call_run, rec.run, parameters or {}, player, session_memory)
            result = future.result(timeout=timeout_sec)
            executor.shutdown(wait=False)
            # Redact secrets on successful return path (reconciles VULN-04)
            return self.sanitize_secrets(str(result or "Done."))
        except concurrent.futures.TimeoutError:
            executor.shutdown(wait=False, cancel_futures=True)
            msg = f"Sir, the '{name}' plugin timed out after {timeout_sec:.0f} seconds."
            self._logger(msg)
            self._notify(msg)
            return msg
        except Exception as e:
            executor.shutdown(wait=False)
            raw_err = str(e)
            sanitized = self.sanitize_secrets(raw_err)
            self._logger(f"Plugin '{name}' crashed during run(): {sanitized}")
            self._notify(f"Plugin '{name}' failed — see the console for details.")
            traceback.print_exc()
            return f"Sir, the '{name}' plugin failed: {sanitized}"

    # -- called by ui.py's settings tab to render per-plugin config forms --
    def settings_schemas(self) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for name, rec in self._plugins.items():
            if not rec.settings or not get_plugin_enabled(name, rec.tier):
                continue
            ns = rec.settings.get("namespace") or rec.name
            if ns in seen:
                continue
            seen.add(ns)
            out.append({
                "plugin":    rec.name,
                "namespace": ns,
                "title":     rec.settings.get("title") or rec.name,
                "fields":    rec.settings.get("fields", []),
                "values":    get_plugin_config(ns),
                "action":    rec.settings.get("action"),
            })
        return out

    # -- called by ui.py's Plugin Manager overlay --
    def list_for_ui(self) -> list[dict]:
        out = []
        for rec in self._all_records:
            out.append({
                "name": rec.name,
                "description": rec.description,
                "file": rec.file,
                "valid": rec.valid,
                "error": rec.error,
                "tier": rec.tier,
                "capabilities": rec.capabilities,
                "enabled": get_plugin_enabled(rec.name, rec.tier) if rec.valid else False,
            })
        return out

    def bridge_to_tool_registry(self, tool_registry: Any) -> int:
        """Register valid plugins into the central ToolRegistry (reconciles SEC-05)."""
        from core.orchestrator.tools import CapabilityTier, ToolDefinition, ToolResult
        count = 0
        for name, rec in self._plugins.items():
            if not rec.valid:
                continue

            tier_enum = getattr(CapabilityTier, rec.tier, CapabilityTier.READ_ONLY)
            is_mutating = tier_enum in (
                CapabilityTier.WORKSPACE_WRITE,
                CapabilityTier.EXECUTION,
                CapabilityTier.SYSTEM_MUTATING,
                CapabilityTier.SYSTEM_DESTRUCTIVE,
            )

            def _make_handler(p_name: str):
                def _handler(params: dict, context: dict | None = None) -> ToolResult:
                    ctx = context or {}
                    player = ctx.get("player")
                    # When called through ToolRegistry, ToolRegistry already evaluated PolicyEngine!
                    res = self.run(p_name, params, player=player, pre_authorized=True)
                    if "failed:" in res or "timed out" in res or "is currently disabled" in res:
                        return ToolResult(status="failed", error=res, output=res)
                    return ToolResult(status="done", output=res)
                return _handler

            tool_def = ToolDefinition(
                name=name,
                description=rec.description,
                parameters=rec.parameters,
                capability_tier=tier_enum,
                handler=_make_handler(name),
                is_mutating=is_mutating,
                is_plugin=True,
            )
            tool_registry.register(tool_def)
            count += 1
        return count


def _call_run(run_fn, parameters, player, session_memory):
    """Invoke run() passing only the kwargs it actually declares."""
    sig = inspect.signature(run_fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    kwargs = {}
    if has_var_kw or "player" in sig.parameters:
        kwargs["player"] = player
    if has_var_kw or "session_memory" in sig.parameters:
        kwargs["session_memory"] = session_memory
    return run_fn(parameters, **kwargs)


def _validate(module, filename: str) -> PluginRecord:
    """Returns a PluginRecord; .valid=False + .error set on any problem. Never raises."""
    from core.orchestrator.tools import CapabilityTier

    plugin_meta = getattr(module, "PLUGIN", None)
    if not isinstance(plugin_meta, dict):
        return PluginRecord(name=Path(filename).stem, file=filename,
                             error="Missing PLUGIN dict constant.")

    name = plugin_meta.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return PluginRecord(name=str(name or Path(filename).stem), file=filename,
                             error="PLUGIN['name'] missing or not a valid identifier "
                                   "(letters/digits/underscore, must start with letter/underscore).")

    description = plugin_meta.get("description")
    if not isinstance(description, str) or not description.strip():
        return PluginRecord(name=name, file=filename,
                             error="PLUGIN['description'] missing or empty.")

    parameters = plugin_meta.get("parameters", _DEFAULT_PARAMS)
    if not isinstance(parameters, dict) or parameters.get("type") != "OBJECT":
        return PluginRecord(name=name, file=filename,
                             error="PLUGIN['parameters'] must be a dict with \"type\": \"OBJECT\".")

    run_fn = getattr(module, "run", None)
    if not callable(run_fn):
        return PluginRecord(name=name, file=filename,
                             error="Missing callable run(parameters, ...) function.")

    # Validate CapabilityTier (reconciles HIGH-02)
    tier_raw = str(plugin_meta.get("tier") or "READ_ONLY").upper()
    if tier_raw not in CapabilityTier.__members__:
        return PluginRecord(
            name=name, file=filename,
            error=f"Invalid PLUGIN['tier'] '{tier_raw}'. Must be one of: {list(CapabilityTier.__members__.keys())}."
        )

    capabilities = plugin_meta.get("capabilities", [])
    if not isinstance(capabilities, list):
        capabilities = [str(capabilities)]

    # Optional settings schema
    settings = getattr(module, "PLUGIN_SETTINGS", None)
    if not (isinstance(settings, dict) and isinstance(settings.get("fields"), list)):
        settings = None

    timeout_val = float(plugin_meta.get("timeout", 30.0))

    return PluginRecord(
        name=name,
        description=description.strip(),
        parameters=parameters,
        run=run_fn,
        file=filename,
        valid=True,
        error="",
        settings=settings,
        behavior=_opt_upper(plugin_meta.get("behavior"), _BEHAVIORS),
        scheduling=_opt_upper(plugin_meta.get("scheduling"), _SCHEDULING),
        tier=tier_raw,
        capabilities=capabilities,
        brief=_opt_text(plugin_meta.get("brief")),
        timeout=timeout_val,
    )


_FIRST_PARTY = {"core", "memory", "actions", "plugins", "dashboard", "config", "gui"}


def _is_first_party(top: str, plugins_dir: Path) -> bool:
    if top in _FIRST_PARTY:
        return True
    root = plugins_dir.parent
    try:
        return (root / top).is_dir() or (root / f"{top}.py").is_file()
    except OSError:
        return False


def _load_error(path: Path, plugins_dir: Path, exc: Exception) -> str:
    if isinstance(exc, ModuleNotFoundError):
        missing = (getattr(exc, "name", "") or "").split(".")
        if len(missing) == 2 and missing[0] == "plugins" and missing[1].startswith("_"):
            helper = missing[1] + ".py"
            return (f"Needs the shared file '{helper}', which is not in "
                    f"{plugins_dir.name}/. It comes with this plugin — download "
                    f"'{helper}' into the same folder as {path.name} and restart.")
        if missing and missing[0] not in ("plugins",):
            if _is_first_party(missing[0], plugins_dir):
                needed = "/".join(missing) + ".py"
                return (f"Needs '{needed}', which is part of this app rather than "
                        f"something to install — your copy doesn't have it, so it "
                        f"comes with a newer version. Update the app; there is "
                        f"nothing to pip install.")
            return (f"Needs a package that is not installed: pip install {missing[0]}")
    return f"Failed to load: {exc}"


def discover_plugins(plugins_dir: Path, core_tool_names: set[str],
                      logger: Callable[[str], None] = print,
                      notify: Callable[[str], None] | None = None) -> PluginRegistry:
    """Scan plugins_dir for *.py with AST pre-screening, collision checks, and clean cache handling."""
    plugins_dir.mkdir(parents=True, exist_ok=True)
    valid: dict[str, PluginRecord] = {}
    all_records: list[PluginRecord] = []

    files = sorted(plugins_dir.glob("*.py"), key=lambda p: p.name)
    for path in files:
        if path.name.startswith("_"):
            continue

        # 1. Static AST Pre-Screening (reconciles CRT-02)
        ast_ok, ast_err = PluginASTValidator.validate_source(path)
        if not ast_ok:
            rec = PluginRecord(name=path.stem, file=path.name, valid=False, error=ast_err)
            all_records.append(rec)
            logger(f"Plugin rejected by AST screen: {path.name} — {ast_err}")
            continue

        module_name = f"plugins.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("could not build import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise

            rec = _validate(module, path.name)

            if rec.valid and rec.name in core_tool_names:
                rec = PluginRecord(name=rec.name, file=path.name,
                                    error=f"Name '{rec.name}' collides with a core tool — rejected.")
                sys.modules.pop(module_name, None)  # Clean cache (HIGH-04)
            elif rec.valid and rec.name in valid:
                other = valid[rec.name].file
                rec = PluginRecord(name=rec.name, file=path.name,
                                    error=f"Name '{rec.name}' already used by plugin '{other}' — rejected.")
                sys.modules.pop(module_name, None)  # Clean cache (HIGH-04)
            elif not rec.valid:
                sys.modules.pop(module_name, None)  # Clean cache (HIGH-04)

        except Exception as e:
            sys.modules.pop(module_name, None)
            rec = PluginRecord(name=path.stem, file=path.name,
                                error=_load_error(path, plugins_dir, e))
            traceback.print_exc()

        all_records.append(rec)
        if rec.valid:
            valid[rec.name] = rec
            logger(f"Plugin loaded: {rec.name} ({path.name})")
        else:
            logger(f"Plugin rejected: {path.name} — {rec.error}")

    notify = notify or (lambda _msg: None)
    registry = PluginRegistry(valid, logger, notify, plugins_dir=plugins_dir)
    registry._all_records = all_records
    rejected = len(all_records) - len(valid)
    logger(f"Plugin discovery complete: {len(valid)} active, "
           f"{rejected} rejected, {len(all_records)} total.")
    if rejected:
        notify(f"{rejected} plugin(s) could not be loaded — see the console.")
    return registry
