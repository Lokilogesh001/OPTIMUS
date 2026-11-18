"""core/computer_use/types.py — Data contracts and models for Visual Computer Use.

Defines:
- UIElement: Structured representation of an interactive interface control.
- DesktopObservation: Complete snapshot of desktop state, active window, elements, and vitals.
- InteractionAction: Intent specification for mouse, keyboard, or window operations.
- InteractionResult: Verified result of an actuation lifecycle execution.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UIElement:
    """An interactive or structural interface control on screen."""
    element_id: str
    role: str                       # button, edit, menu_item, dialog, checkbox, text, window, etc.
    name: str                       # Verbatim text or accessibility label
    value: str = ""                 # Text content or input state
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # logical (left, top, width, height)
    center: tuple[int, int] = (0, 0)                # logical (cx, cy)
    enabled: bool = True
    focused: bool = False
    interactable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "rect": list(self.rect),
            "center": list(self.center),
            "enabled": self.enabled,
            "focused": self.focused,
            "interactable": self.interactable,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UIElement:
        rect_raw = data.get("rect", [0, 0, 0, 0])
        center_raw = data.get("center", [0, 0])
        rect = (int(rect_raw[0]), int(rect_raw[1]), int(rect_raw[2]), int(rect_raw[3])) if len(rect_raw) >= 4 else (0, 0, 0, 0)
        center = (int(center_raw[0]), int(center_raw[1])) if len(center_raw) >= 2 else (0, 0)
        return cls(
            element_id=data.get("element_id", f"el_{uuid.uuid4().hex[:8]}"),
            role=data.get("role", "unknown"),
            name=data.get("name", ""),
            value=data.get("value", ""),
            rect=rect,
            center=center,
            enabled=bool(data.get("enabled", True)),
            focused=bool(data.get("focused", False)),
            interactable=bool(data.get("interactable", True)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class DesktopObservation:
    """A point-in-time visual and accessibility observation of the computer desktop."""
    timestamp: float = field(default_factory=time.time)
    active_app: str = ""
    window_title: str = ""
    pid: int = 0
    hwnd: int | None = None
    screen_bounds: tuple[int, int, int, int] = (0, 0, 1920, 1080)  # logical (left, top, width, height)
    scale_factor: float = 1.0
    elements: list[UIElement] = field(default_factory=list)
    all_windows: list[dict[str, Any]] = field(default_factory=list)
    source: str = "uia"             # uia | ocr | hybrid | mock
    degraded: bool = False
    degraded_reason: str = ""
    digest: str = ""
    screenshot_path: str | None = None

    def find_element(self, name: str | None = None, role: str | None = None) -> UIElement | None:
        """Find first matching element by case-insensitive name and/or role."""
        name_lower = name.lower() if name else None
        role_lower = role.lower() if role else None

        for el in self.elements:
            if name_lower and name_lower not in el.name.lower():
                continue
            if role_lower and role_lower != el.role.lower():
                continue
            return el
        return None

    def get_dialog(self) -> UIElement | None:
        """Find active modal or confirmation dialog if present."""
        for el in self.elements:
            if el.role in ("dialog", "alert", "window") and el.name:
                return el
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "active_app": self.active_app,
            "window_title": self.window_title,
            "pid": self.pid,
            "hwnd": self.hwnd,
            "screen_bounds": list(self.screen_bounds),
            "scale_factor": self.scale_factor,
            "elements": [e.to_dict() for e in self.elements],
            "all_windows": list(self.all_windows),
            "source": self.source,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "digest": self.digest,
            "screenshot_path": self.screenshot_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesktopObservation:
        bounds_raw = data.get("screen_bounds", [0, 0, 1920, 1080])
        bounds = (int(bounds_raw[0]), int(bounds_raw[1]), int(bounds_raw[2]), int(bounds_raw[3])) if len(bounds_raw) >= 4 else (0, 0, 1920, 1080)
        elements_raw = data.get("elements", [])
        elements = [UIElement.from_dict(e) if isinstance(e, dict) else e for e in elements_raw]
        return cls(
            timestamp=float(data.get("timestamp", time.time())),
            active_app=data.get("active_app", ""),
            window_title=data.get("window_title", ""),
            pid=int(data.get("pid", 0)),
            hwnd=data.get("hwnd"),
            screen_bounds=bounds,
            scale_factor=float(data.get("scale_factor", 1.0)),
            elements=elements,
            all_windows=list(data.get("all_windows", [])),
            source=data.get("source", "uia"),
            degraded=bool(data.get("degraded", False)),
            degraded_reason=data.get("degraded_reason", ""),
            digest=data.get("digest", ""),
            screenshot_path=data.get("screenshot_path"),
        )


@dataclass
class InteractionAction:
    """Specification of a desired input or navigation action."""
    action_type: str                # click, double_click, right_click, hover, type, press, hotkey, scroll, switch_window
    target_element_id: str | None = None
    target_name: str | None = None
    target_role: str | None = None
    coords: tuple[int, int] | None = None
    text: str | None = None
    keys: list[str] | None = None
    requires_approval: bool = False
    risk_level: str = "LOW"         # LOW, MEDIUM, HIGH, CRITICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target_element_id": self.target_element_id,
            "target_name": self.target_name,
            "target_role": self.target_role,
            "coords": list(self.coords) if self.coords else None,
            "text": "[REDACTED]" if self.action_type == "type" and self.risk_level in ("HIGH", "CRITICAL") else self.text,
            "keys": list(self.keys) if self.keys else None,
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
        }


@dataclass
class InteractionResult:
    """The authoritative outcome of an actuation lifecycle execution."""
    success: bool
    action: str
    details: str
    postcondition_verified: bool
    confidence: float = 1.0
    before_digest: str = ""
    after_digest: str = ""
    state_changed: bool = False
    error: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "details": self.details,
            "postcondition_verified": self.postcondition_verified,
            "confidence": round(self.confidence, 3),
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "state_changed": self.state_changed,
            "error": self.error,
            "evidence": dict(self.evidence),
        }
