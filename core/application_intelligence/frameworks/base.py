"""core/application_intelligence/frameworks/base.py — Base Framework Execution Contracts.

Defines the core interface, data contracts, health states, and COM apartment isolation
for all external framework execution providers in OPTIMUS (M18).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generator

logger = logging.getLogger("optimus.application_intelligence.frameworks.base")


class ProviderHealthStatus(str, Enum):
    """Circuit breaker health states for execution providers."""
    AVAILABLE = "available"      # Circuit CLOSED: Provider healthy & operational
    DEGRADED = "degraded"        # Intermittent failures / elevated latency
    UNAVAILABLE = "unavailable"  # Circuit OPEN: Provider failed, bypassed for cooldown
    PROBING = "probing"          # Circuit HALF_OPEN: Probing synthetic health check


@dataclass
class ExecutionRequest:
    """Standardized action execution request sent to a framework provider."""
    action_id: str = ""
    target_app: str = ""
    operation: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    approval_token: str | None = None          # Cryptographic ApprovalToken (CRIT-2)
    session_nonce: str | None = None           # Nonce tracking (CRIT-2)
    caller_identity: str = "orchestrator"      # Identity of calling agent
    timeout: float = 15.0
    provider_name: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id:
            import uuid
            self.action_id = f"act_{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "target_app": self.target_app,
            "operation": self.operation,
            "parameters": self.parameters,
            "approval_token": self.approval_token,
            "session_nonce": self.session_nonce,
            "caller_identity": self.caller_identity,
            "timeout": self.timeout,
            "provider_name": self.provider_name,
        }


@dataclass
class ExecutionResponse:
    """Standardized result returned by a framework execution provider."""
    action_id: str = ""
    status: str = "done"                       # "done", "unverified", "failed"
    facts: list[str] = field(default_factory=list)
    evidence_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    duration_ms: float = 0.0
    output_data: dict[str, Any] | None = None
    success: bool | None = None

    def __post_init__(self) -> None:
        if self.success is not None:
            self.status = "done" if self.success else "failed"
        else:
            self.success = (self.status == "done")
        if self.output_data is not None and not self.evidence_data:
            self.evidence_data = self.output_data
        elif self.evidence_data and self.output_data is None:
            self.output_data = self.evidence_data

    @property
    def is_success(self) -> bool:
        return self.status == "done"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status,
            "facts": self.facts,
            "evidence_data": self.evidence_data,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
        }


@contextmanager
def com_apartment() -> Generator[None, None, None]:
    """Unified COM apartment initialization context manager.

    Initializes both pythoncom and comtypes on the current thread, preventing
    0x800401f0 CO_E_NOTINITIALIZED conflicts when calling WinCOM and pywinauto (reconciles HIGH-3).
    Guarantees clean CoUninitialize teardown upon exit.
    """
    pythoncom_initialized = False
    comtypes_initialized = False

    try:
        try:
            import pythoncom
            pythoncom.CoInitialize()
            pythoncom_initialized = True
        except Exception as e:
            logger.debug("pythoncom.CoInitialize() skipped or already initialized: %s", e)

        try:
            import comtypes
            comtypes.CoInitialize()
            comtypes_initialized = True
        except Exception as e:
            logger.debug("comtypes.CoInitialize() skipped or already initialized: %s", e)

        yield

    finally:
        if comtypes_initialized:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception as e:
                logger.debug("comtypes.CoUninitialize() cleanup exception: %s", e)

        if pythoncom_initialized:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception as e:
                logger.debug("pythoncom.CoUninitialize() cleanup exception: %s", e)


class BaseExecutionProvider(ABC):
    """Abstract base class for all advanced execution framework backends."""

    name: str = "base_provider"
    requires_desktop_lock: bool = True         # Headless browser / background COM set False (MED-1)

    def __init__(self) -> None:
        self.health_status: ProviderHealthStatus = ProviderHealthStatus.AVAILABLE
        self.consecutive_failures: int = 0
        self.last_failure_time: float = 0.0
        self.success_count: int = 0
        self.total_latency_ms: float = 0.0

    @abstractmethod
    def health_probe(self) -> bool:
        """Non-mutating synthetic check testing whether the provider is operational (MED-3)."""
        pass

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        """Execute a single atomic action without autonomous sub-agent loops (HIGH-1)."""
        pass

    def rollback(self, request: ExecutionRequest, response: ExecutionResponse) -> bool:
        """Polymorphic provider-specific state rollback on execution failure (HIGH-4).

        Overridden by specific providers (e.g. WinCOM document discard, Playwright cancel).
        """
        return False

    def get_average_latency_ms(self) -> float:
        """Calculate average latency for provider scoring."""
        if self.success_count == 0:
            return 50.0
        return self.total_latency_ms / self.success_count

    def validate_security(self, request: ExecutionRequest) -> str | None:
        """Defense-in-depth verification for privileged or destructive operations (reconciles CRIT-2).

        Returns an error message string if authorization fails, or None if permitted.
        """
        is_destructive = bool(request.parameters.get("is_destructive", False))
        sensitive_prefixes = ("delete_", "wipe_", "format_", "drop_", "truncate", "pay_", "send_message")
        op_lower = request.operation.lower()
        is_sensitive_op = (
            op_lower in ("eval", "exec")
            or any(op_lower.startswith(sens) or f"_{sens}" in op_lower for sens in sensitive_prefixes)
        )
        if is_destructive or is_sensitive_op:
            if not request.approval_token:
                return (
                    "AUTHORIZATION_REQUIRED: Privileged or destructive operation requires a valid "
                    "cryptographic approval_token (CRIT-2 defense-in-depth)."
                )
            try:
                from core.security.policy_engine import PolicyEngine
                pe = PolicyEngine.instance()
                token = pe.get_token(request.approval_token)
                if not token or not token.is_valid:
                    return "AUTHORIZATION_FAILED: Invalid or expired approval_token for privileged operation."
            except Exception as e:
                return f"AUTHORIZATION_ERROR: Failed to validate approval_token: {e}"
        return None
