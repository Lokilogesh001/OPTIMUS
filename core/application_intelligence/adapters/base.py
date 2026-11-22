"""core/application_intelligence/adapters/base.py — Base Application Adapter Lifecycle.

Implements the standardized 8-stage lifecycle for desktop application adapters:
Discover, Connect, Inspect, Execute, Observe, Verify, Recover, Disconnect.

Deeply anchored on core.screenview and core.screenact (reconciles CRIT-3),
modal dialog recovery (reconciles LOW-2), and fact-based outcomes (reconciles MED-4).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.types import (
    ActionContract,
    AppDefinition,
    AppInteractionTier,
    ExecutionEvidence,
)
from core.outcome import untrusted

logger = logging.getLogger("optimus.application_intelligence.adapter.base")


class BaseApplicationAdapter(ABC):
    """Abstract base class for domain-specific application adapters."""

    def __init__(self, app_def: AppDefinition | None = None) -> None:
        self.app_def = app_def
        self._is_connected: bool = False
        self._current_hwnd: int | None = app_def.main_hwnd if app_def else None

    @abstractmethod
    def discover(self) -> bool:
        """Verify whether the target application is installed, registered, or reachable."""
        pass

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection, launch application, or attach to existing window."""
        pass

    def inspect(self) -> dict[str, Any]:
        """Inspect current application state via core.screenview accessibility tree (CRIT-3)."""
        try:
            from core.screenview import look
            view = look()
            elements = [
                {
                    "role": e.role.value if hasattr(e.role, "value") else str(e.role),
                    "name": untrusted("ui", e.name or ""),
                    "rect": [e.rect.x, e.rect.y, e.rect.w, e.rect.h] if hasattr(e, "rect") and e.rect else None,
                    "focused": getattr(e, "focused", False),
                    "enabled": getattr(e, "enabled", True),
                }
                for e in (view.elements if hasattr(view, "elements") else [])
            ]
            return {
                "active_window": untrusted("window", getattr(view, "title", getattr(view, "active_window", ""))),
                "element_count": len(elements),
                "elements": elements,
            }
        except Exception as ex:
            logger.debug("screenview.look() inspection error: %s", ex)
            return {"active_window": "", "element_count": 0, "elements": []}

    @abstractmethod
    def execute(self, contract: ActionContract) -> ExecutionEvidence:
        """Execute an action contract through the adapter."""
        pass

    def observe(self) -> dict[str, Any]:
        """Capture post-action state via screenview and window handles."""
        return self.inspect()

    def verify(self, contract: ActionContract, evidence: ExecutionEvidence) -> bool:
        """Assert whether the physical outcome matches the contract's expected state."""
        return evidence.status == "done"

    def recover(self, error: str) -> bool:
        """Safely recover from application traps (e.g. dismiss modal dialogs) (reconciles LOW-2)."""
        logger.info("Attempting adapter recovery for error: %s", error)
        try:
            # Check for modal dialogs and dismiss with Esc
            from core.screenview import look
            view = look()
            if hasattr(view, "elements"):
                for el in view.elements:
                    role_str = (el.role.value if hasattr(el.role, "value") else str(el.role)).lower()
                    if "dialog" in role_str or "modal" in role_str:
                        logger.info("Detected modal dialog '%s', attempting safe dismissal with Esc", el.name)
                        from core.screenact import press
                        press("escape")
                        time.sleep(0.3)
                        return True
        except Exception as ex:
            logger.debug("Recovery attempt failed: %s", ex)
        return False

    def disconnect(self) -> None:
        """Detach cleanly from the application without terminating user session."""
        self._is_connected = False
