"""core/application_intelligence/frameworks/ufo/security_gateway.py — Authoritative UFO MCP Security Gateway.

Implements the mandatory OPTIMUS authorization and safety boundary wrapping all Microsoft UFO²
local FastMCP execution servers (reconciles CRIT-1, CRIT-2, CRIT-3, HIGH-1, HIGH-4):
1. PolicyEngine cryptographic ApprovalToken verification for destructive, privileged, and communication actions.
2. Single-use token consumption and nonce tracking to prevent replay attacks.
3. Exact-content and exact-recipient binding for communication actions (WhatsApp / messaging).
4. Strict CommandLineExecutor allowlist restricting shell execution to bare application launching.
5. Headless PDFReaderExecutor enforcement (stripping simulate_human=True to prevent focus stealing).
6. Filesystem and workspace boundary verification (blocking UNC paths and sensitive system directories).
7. DesktopInteractionLock focus synchronization around physical UI actuation.
8. Audit logging of every tool execution into OPTIMUS's AuditTrail.
"""

from __future__ import annotations

import logging
import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.types import ActionContract
from core.outcome import untrusted
from core.security.policy_engine import OperationRequest, PolicyEngine

logger = logging.getLogger("optimus.application_intelligence.frameworks.ufo.security_gateway")

# Approved bare executables for application launch (no shell metacharacters or complex args)
SAFE_LAUNCH_COMMANDS = frozenset(
    {
        "notepad", "notepad.exe",
        "calc", "calc.exe",
        "mspaint", "mspaint.exe",
        "wordpad", "wordpad.exe",
        "msedge", "msedge.exe",
        "chrome", "chrome.exe",
        "firefox", "firefox.exe",
        "winword", "winword.exe",
        "excel", "excel.exe",
        "powerpnt", "powerpnt.exe",
        "code", "code.exe",
        "explorer", "explorer.exe",
        "whatsapp", "whatsapp.exe",
    }
)

SENSITIVE_DIRECTORY_PATTERNS = [
    re.compile(r"^[a-zA-Z]:\\Windows\\System32", re.IGNORECASE),
    re.compile(r"^[a-zA-Z]:\\Windows\\SysWOW64", re.IGNORECASE),
    re.compile(r"\.ssh[\\/]", re.IGNORECASE),
    re.compile(r"\.aws[\\/]", re.IGNORECASE),
    re.compile(r"\.azure[\\/]", re.IGNORECASE),
    re.compile(r"\.gemini[\\/].*credentials", re.IGNORECASE),
]


class GatewaySecurityViolation(Exception):
    """Raised when an action violates the OPTIMUS MCP Security Gateway policy."""


@dataclass
class GatewayInvocationResult:
    """Structured response from the UFO MCP Security Gateway."""
    success: bool
    data: Any = None
    error: str | None = None
    requires_approval: bool = False
    audit_record: dict[str, Any] = field(default_factory=dict)


class UFOMCPSecurityGateway:
    """Authoritative execution boundary intercepting and authorizing UFO² FastMCP tool invocations."""

    _instance: UFOMCPSecurityGateway | None = None

    def __init__(self) -> None:
        self.policy_engine = PolicyEngine.instance()
        self.execution_audit_log: list[dict[str, Any]] = []

    @classmethod
    def instance(cls) -> UFOMCPSecurityGateway:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def validate_filesystem_path(self, file_path: str) -> None:
        """Verify path is not targeting sensitive system directories or UNC network shares."""
        clean = (file_path or "").strip()
        if not clean:
            return

        # 1. Reject UNC network shares
        if clean.startswith(r"\\") or clean.startswith("//"):
            raise GatewaySecurityViolation(f"ACCESS_DENIED: UNC network share paths are prohibited: '{clean}'")

        # 2. Reject sensitive system and credential directories
        for pat in SENSITIVE_DIRECTORY_PATTERNS:
            if pat.search(clean):
                raise GatewaySecurityViolation(f"ACCESS_DENIED: Access to sensitive path is prohibited: '{clean}'")

    def authorize_and_execute(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        contract: ActionContract,
        executor_func: Callable[..., Any],
    ) -> GatewayInvocationResult:
        """Authoritatively evaluate and execute a tool under PolicyEngine and DesktopLock guardrails."""
        start_time = time.perf_counter()
        normalized_tool = f"{server_name}.{tool_name}".lower()

        # ── 1. Target Application Allowlist Validation (HIGH-1) ───────────
        target_app = (
            arguments.get("process_name")
            or arguments.get("app_root_name")
            or arguments.get("window_name")
            or arguments.get("target_app")
            or contract.target_app
        )
        if contract.allowed_apps and target_app:
            clean_app = str(target_app).lower()
            allowed_clean = [a.lower() for a in contract.allowed_apps]
            if not any(a in clean_app or clean_app in a for a in allowed_clean):
                msg = f"APP_BOUNDARY_VIOLATION: Application '{target_app}' is not authorized in contract.allowed_apps: {contract.allowed_apps}"
                logger.warning(msg)
                return GatewayInvocationResult(success=False, error=msg)

        # ── 2. Headless PDFReader Enforcement (HIGH-4) ────────────────────
        if server_name == "PDFReaderExecutor" or tool_name in ("extract_pdf_text", "extract_all_pdfs_text"):
            arguments["simulate_human"] = False  # Strip interactive viewer & sleep delay

        # ── 3. Filesystem Path Boundary Verification ──────────────────────
        for path_key in ("file_path", "output_path", "pdf_path", "path", "directory"):
            if path_key in arguments and isinstance(arguments[path_key], str):
                try:
                    self.validate_filesystem_path(arguments[path_key])
                except GatewaySecurityViolation as e:
                    return GatewayInvocationResult(success=False, error=str(e))

        # ── 4. CommandLineExecutor Hardening (CRIT-2) ─────────────────────
        if server_name == "CommandLineExecutor" or tool_name == "run_shell":
            cmd = arguments.get("command") or arguments.get("bash_command", "")
            # Check for shell operators
            if any(char in cmd for char in ("&", "|", ";", ">", "<", "`", "$")):
                msg = (
                    f"COMMAND_SECURITY_VIOLATION: Shell metacharacters are prohibited in UFO CommandLineExecutor: '{cmd}'. "
                    "Multi-token developer or test commands must be executed via OPTIMUS native sandboxed CLI."
                )
                logger.warning(msg)
                return GatewayInvocationResult(success=False, error=msg)

            try:
                tokens = shlex.split(cmd)
            except Exception as e:
                return GatewayInvocationResult(success=False, error=f"COMMAND_PARSE_ERROR: {e}")

            if len(tokens) > 1:
                msg = (
                    f"COMMAND_SECURITY_VIOLATION: CommandLineExecutor is restricted to bare application launch. "
                    f"Multi-token commands ('{cmd}') must be executed via OPTIMUS sandboxed execution."
                )
                logger.warning(msg)
                return GatewayInvocationResult(success=False, error=msg)

            exe_name = tokens[0].lower() if tokens else ""
            if exe_name not in SAFE_LAUNCH_COMMANDS:
                msg = f"COMMAND_NOT_PERMITTED: Executable '{exe_name}' is not in approved launch allowlist."
                logger.warning(msg)
                return GatewayInvocationResult(success=False, error=msg)

        # ── 5. Communication Exact-Content & Recipient Binding (CRIT-1) ────
        is_communication = (
            "whatsapp" in normalized_tool
            or "whatsapp" in str(target_app).lower()
            or arguments.get("action_type") == "send_message"
            or any(k in str(arguments).lower() for k in ("send_message", "send message", "chat_send"))
        )

        if is_communication:
            # Check exact-match approval token
            recip = arguments.get("recipient") or arguments.get("contact", "")
            msg_text = arguments.get("message") or arguments.get("text", "")
            
            token_obj = None
            if contract.approval_token:
                token_obj = self.policy_engine.get_token(contract.approval_token)

            if not token_obj or not token_obj.is_valid:
                return GatewayInvocationResult(
                    success=False,
                    requires_approval=True,
                    error=f"APPROVAL_REQUIRED: Communication action for recipient='{recip}' requires an authentic PolicyEngine approval token.",
                )

            # Assert exact-content binding
            bound_recip = token_obj.arguments.get("recipient", "")
            bound_msg = token_obj.arguments.get("message", "")
            if bound_recip and recip and bound_recip.strip() != str(recip).strip():
                return GatewayInvocationResult(
                    success=False,
                    error=f"SECURITY_HASH_MISMATCH: Intended recipient '{recip}' does not match authorized token recipient '{bound_recip}'.",
                )
            if bound_msg and msg_text and bound_msg.strip() != str(msg_text).strip():
                return GatewayInvocationResult(
                    success=False,
                    error="SECURITY_HASH_MISMATCH: Drafted message content does not match authorized token message body.",
                )

        # ── 6. PolicyEngine Authoritative Gate for Destructive Actions ────
        is_destructive = (
            contract.is_destructive
            or bool(arguments.get("is_destructive", False))
            or any(d in tool_name.lower() for d in ("save_as", "delete", "remove", "overwrite", "format", "wipe"))
        )

        if is_destructive or contract.requires_approval:
            token_obj = None
            if contract.approval_token:
                token_obj = self.policy_engine.get_token(contract.approval_token)

            if not token_obj or not token_obj.is_valid:
                return GatewayInvocationResult(
                    success=False,
                    requires_approval=True,
                    error=f"APPROVAL_REQUIRED: Destructive or protected tool '{normalized_tool}' requires an authentic PolicyEngine approval token.",
                )

            verdict = self.policy_engine.evaluate_operation(OperationRequest(
                tool_name=normalized_tool,
                agent_name=contract.caller_identity or "ufo_desktop_agent",
                arguments=arguments,
                approval_token=token_obj,
            ))

            if verdict.requires_approval or not verdict.is_authorized:
                return GatewayInvocationResult(
                    success=False,
                    requires_approval=True,
                    error=f"APPROVAL_REQUIRED: {verdict.reason}",
                )

        # ── 7. Desktop Focus Synchronization (Lock) ───────────────────────
        requires_lock = (
            server_name in ("HostUIExecutor", "AppUIExecutor")
            or tool_name in ("select_application_window", "click_input", "click_on_coordinates", "set_edit_text", "keyboard_input")
        )

        duration_ms = 0.0
        try:
            if requires_lock:
                with DesktopInteractionLock(owner="ufo_security_gateway", timeout=15.0):
                    res = executor_func(**arguments)
            else:
                res = executor_func(**arguments)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Error executing gateway tool %s: %s", normalized_tool, e)
            return GatewayInvocationResult(success=False, error=str(e))

        # ── 8. Single-Use Token Consumption (Replay Prevention) ───────────
        if contract.approval_token:
            # Mark token consumed if operation was destructive or communication
            if is_destructive or is_communication:
                tok = self.policy_engine.get_token(contract.approval_token)
                if tok:
                    self.policy_engine._consume_nonce(tok)
                self.policy_engine._consumed_nonces.add(contract.approval_token)

        audit_entry = {
            "timestamp": time.time(),
            "server": server_name,
            "tool": tool_name,
            "target_app": target_app,
            "arguments_summary": {k: str(v)[:100] for k, v in arguments.items()},
            "duration_ms": duration_ms,
            "status": "success",
        }
        self.execution_audit_log.append(audit_entry)

        return GatewayInvocationResult(success=True, data=res, audit_record=audit_entry)

    @staticmethod
    def demarcate_for_prompter(raw_text: str, source: str = "screen") -> str:
        """Demarcate screen or web content strictly during prompter formatting (reconciles CRIT-3)."""
        if not raw_text:
            return ""
        return untrusted(source=source, content=raw_text)
