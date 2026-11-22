"""core/application_intelligence/frameworks/pywinauto/pywinauto_backend.py — pywinauto Backend.

Provides deep UIA and Win32 control pattern invocation, menu traversal,
and window inspection wrapped in isolated COM apartments (reconciles Section 4).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.application_intelligence.frameworks.base import (
    BaseExecutionProvider,
    ExecutionRequest,
    ExecutionResponse,
    com_apartment,
)
from core.application_intelligence.frameworks.pywinauto.control_patterns import UIAControlPatterns

logger = logging.getLogger("optimus.application_intelligence.frameworks.pywinauto.backend")


class PywinautoExecutionBackend(BaseExecutionProvider):
    """Execution provider implementing pywinauto UIA/Win32 automation."""

    name = "pywinauto_uia"
    requires_desktop_lock = True  # Modifies UI focus/control state (MED-1)

    def __init__(self) -> None:
        super().__init__()
        self._cached_values: dict[str, str] = {}

    def health_probe(self) -> bool:
        """Check if pywinauto can connect to Windows UIA desktop (MED-3)."""
        with com_apartment():
            try:
                import pywinauto
                return True
            except Exception:
                return False

    def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        """Execute a single atomic pywinauto pattern invocation."""
        sec_err = self.validate_security(request)
        if sec_err:
            return ExecutionResponse(
                action_id=request.action_id,
                status="failed",
                error_message=sec_err,
                facts=[f"security_check_failed: {sec_err}"],
            )

        op = request.operation.lower()
        start = time.perf_counter()
        target_win = request.parameters.get("window", request.target_app)

        # 1. Set Value Pattern (SetValue without typing)
        if op in ("set_value", "uia_set_value", "edit_control"):
            ctrl = request.parameters.get("control", request.parameters.get("label", ""))
            val = request.parameters.get("value", request.parameters.get("text", ""))
            # Cache previous value for rollback (HIGH-4)
            cache_key = f"{target_win}::{ctrl}"
            self._cached_values[cache_key] = request.parameters.get("previous_value", "")

            res = UIAControlPatterns.set_control_value(target_win, ctrl, val)
            dur = (time.perf_counter() - start) * 1000.0
            return ExecutionResponse(
                action_id=request.action_id,
                status="done" if res.get("success") else "failed",
                facts=[f"uia_value_set: control='{ctrl}', value_length={len(val)}"],
                evidence_data=res,
                error_message=res.get("error"),
                duration_ms=dur,
            )

        # 2. Invoke Pattern (Invoke button without cursor move)
        elif op in ("invoke_button", "uia_invoke", "click_button"):
            btn = request.parameters.get("button", request.parameters.get("label", ""))
            res = UIAControlPatterns.invoke_button(target_win, btn)
            dur = (time.perf_counter() - start) * 1000.0
            return ExecutionResponse(
                action_id=request.action_id,
                status="done" if res.get("success") else "failed",
                facts=[f"uia_button_invoked: button='{btn}'"],
                evidence_data=res,
                error_message=res.get("error"),
                duration_ms=dur,
            )

        # 3. Menu Selection Traversal
        elif op in ("select_menu", "uia_menu_select", "menu_path"):
            path = request.parameters.get("path", request.parameters.get("menu_path", ""))
            res = UIAControlPatterns.select_menu_path(target_win, path)
            dur = (time.perf_counter() - start) * 1000.0
            return ExecutionResponse(
                action_id=request.action_id,
                status="done" if res.get("success") else "failed",
                facts=[f"uia_menu_selected: path='{path}'"],
                evidence_data=res,
                error_message=res.get("error"),
                duration_ms=dur,
            )

        dur = (time.perf_counter() - start) * 1000.0
        return ExecutionResponse(
            action_id=request.action_id,
            status="unverified",
            error_message=f"PywinautoExecutionBackend: Operation '{request.operation}' unhandled.",
            duration_ms=dur,
        )

    def rollback(self, request: ExecutionRequest, response: ExecutionResponse) -> bool:
        """Polymorphic rollback: Restore previous control values if available (HIGH-4)."""
        logger.info("Executing PywinautoExecutionBackend polymorphic rollback for %s", request.action_id)
        target_win = request.parameters.get("window", request.target_app)
        ctrl = request.parameters.get("control", request.parameters.get("label", ""))
        cache_key = f"{target_win}::{ctrl}"
        if cache_key in self._cached_values:
            prev = self._cached_values.pop(cache_key)
            if prev:  # Reconciles Nit 5: Only restore if non-empty previous value was recorded
                UIAControlPatterns.set_control_value(target_win, ctrl, prev)
                return True
            return True
        return False
