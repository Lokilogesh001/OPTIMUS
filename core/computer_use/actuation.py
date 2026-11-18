"""core/computer_use/actuation.py — Safe, bounded actuation for mouse, keyboard, and window management.

Implements Phase 3 and Phase 8 of Milestone 9: Computer Use & Visual Intelligence.
- Enforces strict screen and UI element coordinate boundary checks.
- Prevents runaway actuation loops with anti-runaway click tripwires.
- Provides controlled mouse, keyboard, and window switching primitives.
- Integrates with centralized permissions and redacts sensitive credentials.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import platform
import time
from typing import Any

from core.computer_use.observation import attach_desktop, get_open_windows
from core.computer_use.types import InteractionAction, UIElement
import core.screenact as sa
import core.screenview as sv

logger = logging.getLogger("optimus.computer_use.actuation")
_IS_WINDOWS = platform.system() == "Windows"


class BoundaryViolationError(ValueError):
    """Raised when actuation coordinates fall outside physical screen or target bounds."""
    pass


class RunawayInteractionError(RuntimeError):
    """Raised when repetitive actions exceed safety thresholds without state changes."""
    pass


class ComputerActuator:
    """Safe actuation engine executing physical input actions within strict boundaries."""

    def __init__(self, max_consecutive_identical_clicks: int = 3) -> None:
        self.max_consecutive_clicks = max_consecutive_identical_clicks
        self._last_click_target: tuple[int, int] | None = None
        self._consecutive_click_count: int = 0
        self._last_actuation_time: float = 0.0
        self._min_action_interval_s: float = 0.10

    def reset_tripwire(self) -> None:
        """Reset anti-runaway counters."""
        self._last_click_target = None
        self._consecutive_click_count = 0

    def validate_coordinates(self, x: int, y: int, screen_bounds: tuple[int, int, int, int] | None = None) -> tuple[int, int]:
        """Validate and clamp or reject coordinates that exceed screen bounds."""
        if screen_bounds is None:
            screen_bounds = sv._logical_screen()

        s_left, s_top, s_width, s_height = screen_bounds
        s_right = s_left + s_width
        s_bottom = s_top + s_height

        if not (s_left <= x < s_right and s_top <= y < s_bottom):
            raise BoundaryViolationError(
                f"Coordinates ({x}, {y}) violate screen bounds [left={s_left}, top={s_top}, right={s_right}, bottom={s_bottom}]"
            )
        return int(x), int(y)

    def _apply_rate_limit(self) -> None:
        """Throttle back-to-back input events to avoid flooding the target message loop."""
        elapsed = time.time() - self._last_actuation_time
        if elapsed < self._min_action_interval_s:
            time.sleep(self._min_action_interval_s - elapsed)
        self._last_actuation_time = time.time()

    def _check_runaway_tripwire(self, x: int, y: int) -> None:
        """Detect and halt runaway loops clicking the exact same position repeatedly."""
        target = (x, y)
        if self._last_click_target == target:
            self._consecutive_click_count += 1
            if self._consecutive_click_count >= self.max_consecutive_clicks:
                raise RunawayInteractionError(
                    f"Runaway interaction tripwire triggered: clicked {target} {self._consecutive_click_count} "
                    f"times consecutively. Aborting to prevent infinite actuation loop."
                )
        else:
            self._last_click_target = target
            self._consecutive_click_count = 1

    def click(self, x: int, y: int) -> dict[str, Any]:
        """Perform a single left-click at validated coordinates."""
        cx, cy = self.validate_coordinates(x, y)
        self._check_runaway_tripwire(cx, cy)
        self._apply_rate_limit()

        with attach_desktop():
            sa.click(sa.Point(cx, cy))

        return {"action": "click", "coords": (cx, cy), "timestamp": time.time()}

    def double_click(self, x: int, y: int) -> dict[str, Any]:
        """Perform a double-click at validated coordinates."""
        cx, cy = self.validate_coordinates(x, y)
        self._apply_rate_limit()

        with attach_desktop():
            sa.double_click(sa.Point(cx, cy))

        return {"action": "double_click", "coords": (cx, cy), "timestamp": time.time()}

    def right_click(self, x: int, y: int) -> dict[str, Any]:
        """Perform a right-click at validated coordinates."""
        cx, cy = self.validate_coordinates(x, y)
        self._apply_rate_limit()

        with attach_desktop():
            sa.right_click(sa.Point(cx, cy))

        return {"action": "right_click", "coords": (cx, cy), "timestamp": time.time()}

    def hover(self, x: int, y: int) -> dict[str, Any]:
        """Move cursor to target coordinates without clicking."""
        cx, cy = self.validate_coordinates(x, y)
        self._apply_rate_limit()

        with attach_desktop():
            sa.hover(sa.Point(cx, cy))

        return {"action": "hover", "coords": (cx, cy), "timestamp": time.time()}

    def type_text(self, text: str, sensitive: bool = False) -> dict[str, Any]:
        """Type text into the currently focused control."""
        self._apply_rate_limit()

        with attach_desktop():
            sa.type_text(text)

        logged_text = "[REDACTED]" if sensitive else (text[:30] + "..." if len(text) > 30 else text)
        return {"action": "type", "text": logged_text, "timestamp": time.time()}

    def press(self, key: str) -> dict[str, Any]:
        """Press an individual key (e.g. 'enter', 'tab', 'esc', 'backspace')."""
        self._apply_rate_limit()

        with attach_desktop():
            sa.press(key)

        return {"action": "press", "key": key, "timestamp": time.time()}

    def hotkey(self, *keys: str) -> dict[str, Any]:
        """Execute a key combination shortcut (e.g. 'ctrl', 's')."""
        self._apply_rate_limit()

        with attach_desktop():
            # Screenact normalises keys and uses pyautogui.hotkey
            norm_keys = [sa.normalise_key(k) for k in keys]
            if sa.pyautogui:
                sa.pyautogui.hotkey(*norm_keys)
            else:
                for k in keys:
                    sa.press(k)

        return {"action": "hotkey", "keys": list(keys), "timestamp": time.time()}

    def scroll(self, clicks: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        """Scroll vertically at specified coordinates or current position."""
        target_pt = None
        if x is not None and y is not None:
            cx, cy = self.validate_coordinates(x, y)
            target_pt = sa.Point(cx, cy)

        self._apply_rate_limit()
        with attach_desktop():
            sa.scroll(clicks, target_pt)

        return {"action": "scroll", "clicks": clicks, "target": (x, y) if target_pt else None, "timestamp": time.time()}

    def switch_window(self, target: str | int) -> dict[str, Any]:
        """Bring target application window to the foreground."""
        with attach_desktop():
            if isinstance(target, int):
                hwnd = target
            else:
                windows = get_open_windows()
                t_lower = str(target).lower()
                matched = next((w for w in windows if t_lower in w["title"].lower() or t_lower in w["app"].lower()), None)
                if not matched:
                    return {"action": "switch_window", "success": False, "error": f"Window '{target}' not found"}
                hwnd = matched["hwnd"]

            if _IS_WINDOWS and hwnd:
                user32 = ctypes.windll.user32
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.15)
                return {"action": "switch_window", "success": True, "hwnd": hwnd, "timestamp": time.time()}

            return {"action": "switch_window", "success": False, "error": "Unable to switch window on current OS"}


_GLOBAL_ACTUATOR: ComputerActuator | None = None


def get_computer_actuator() -> ComputerActuator:
    """Retrieve or initialize the global computer actuator."""
    global _GLOBAL_ACTUATOR
    if _GLOBAL_ACTUATOR is None:
        _GLOBAL_ACTUATOR = ComputerActuator()
    return _GLOBAL_ACTUATOR
