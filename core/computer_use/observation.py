"""core/computer_use/observation.py — Secure desktop observation and UI state capture.

Implements Phase 1 of Milestone 9: Computer Use & Visual Intelligence.
- Captures active desktop or authorized application window.
- Identifies active application, window title, pid, hwnd, and geometry.
- Extracts structured UI element tree via Windows UI Automation (UIA).
- Protects sensitive credentials (passwords, PINs, secret tokens).
- Calculates deterministic SHA-256 state digests.
- Maintains timestamped observation history for lifecycle diffing and verification.
"""

from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import platform
import time
from typing import Any, Generator

from core.computer_use.types import DesktopObservation, UIElement
import core.screenview as sv

_OS = platform.system()
_IS_WINDOWS = _OS == "Windows"


@contextlib.contextmanager
def attach_desktop(name: str = "Default") -> Generator[None, None, None]:
    """Context manager to ensure the current thread is attached to the interactive desktop.
    
    In Windows environments where background subshells/workers run under an isolated
    desktop station, attaching to WinSta0\\Default gives access to the real user desktop.
    """
    if not _IS_WINDOWS:
        yield
        return

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    h_cur = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    h_def = user32.OpenDesktopW(name, 0, False, 0x01FF)  # DESKTOP_ALL_ACCESS
    attached = False
    if h_def:
        try:
            attached = bool(user32.SetThreadDesktop(h_def))
        except Exception:
            attached = False

    try:
        yield
    finally:
        if attached and h_cur:
            try:
                user32.SetThreadDesktop(h_cur)
            except Exception:
                pass
        if h_def:
            try:
                user32.CloseDesktop(h_def)
            except Exception:
                pass


def get_open_windows() -> list[dict[str, Any]]:
    """Enumerate all visible top-level windows on the desktop."""
    if not _IS_WINDOWS:
        return []

    windows: list[dict[str, Any]] = []
    user32 = ctypes.windll.user32

    with attach_desktop():
        def enum_cb(hwnd: int, _lparam: Any) -> bool:
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if not length:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value or ""
                if not title.strip():
                    return True

                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pid_val = int(pid.value)

                app_name = ""
                try:
                    import psutil
                    app_name = psutil.Process(pid_val).name()
                except Exception:
                    app_name = "unknown"

                is_iconic = bool(user32.IsIconic(hwnd))

                windows.append({
                    "hwnd": int(hwnd),
                    "pid": pid_val,
                    "app": app_name,
                    "title": title.strip(),
                    "is_iconic": is_iconic,
                })
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)

    return windows


def get_foreground_window_info() -> dict[str, Any]:
    """Retrieve details about the current foreground window."""
    if not _IS_WINDOWS:
        return {"hwnd": 0, "pid": 0, "app": "", "title": ""}

    user32 = ctypes.windll.user32
    with attach_desktop():
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"hwnd": 0, "pid": 0, "app": "", "title": ""}

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = (buf.value or "").strip()

        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = int(pid.value)

        app_name = ""
        try:
            import psutil
            app_name = psutil.Process(pid_val).name()
        except Exception:
            app_name = "unknown"

        return {
            "hwnd": int(hwnd),
            "pid": pid_val,
            "app": app_name,
            "title": title,
        }


def _is_sensitive_element(role: str, name: str) -> bool:
    """Check if an element represents sensitive or password data."""
    role_lower = role.lower()
    name_lower = name.lower()
    if role_lower in ("password", "securetext"):
        return True
    sensitive_tokens = ("password", "pin", "secret", "private key", "api key", "access token", "cvv")
    return any(tok in name_lower for tok in sensitive_tokens)


class DesktopObserver:
    """Core desktop observation engine."""

    def __init__(self, screenshot_dir: str | Path | None = None) -> None:
        self.history: list[DesktopObservation] = []
        self._max_history = 50
        if screenshot_dir:
            self.screenshot_dir = Path(screenshot_dir)
        else:
            self.screenshot_dir = Path(".antigravity") / "evidence" / "screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def observe(
        self,
        target_window_title: str | None = None,
        target_hwnd: int | None = None,
        capture_screenshot: bool = False,
        budget_s: float = 3.0,
    ) -> DesktopObservation:
        """Capture a comprehensive point-in-time observation of the desktop or target window."""
        started = time.time()
        with attach_desktop():
            windows = get_open_windows()
            scale = sv.scale_factor()
            screen_bounds = sv._logical_screen()

            # Resolve target window
            target: dict[str, Any] | None = None
            if target_hwnd:
                target = next((w for w in windows if w["hwnd"] == target_hwnd), None)
            elif target_window_title:
                q = target_window_title.lower()
                target = next((w for w in windows if q in w["title"].lower() or q in w["app"].lower()), None)

            if not target:
                fg = get_foreground_window_info()
                # Check if foreground is valid and not our own process
                if fg["hwnd"] and not sv.is_own_pid(fg["pid"]) and fg["title"]:
                    target = next((w for w in windows if w["hwnd"] == fg["hwnd"]), fg)
                elif windows:
                    # Filter out own pid and background shell/desktop managers
                    candidates = [
                        w for w in windows
                        if not sv.is_own_pid(w["pid"])
                        and w["app"].lower() not in ("explorer.exe", "shellexperiencehost.exe")
                        and not w.get("is_iconic", False)
                    ]
                    target = candidates[0] if candidates else windows[0]

            active_app = target.get("app", "") if target else ""
            window_title = target.get("title", "") if target else ""
            pid = target.get("pid", 0) if target else 0
            hwnd = target.get("hwnd") if target else None

            elements: list[UIElement] = []
            source = "uia"
            degraded = False
            degraded_reason = ""

            # Scan UI Automation elements
            if hwnd:
                try:
                    deadline = time.monotonic() + budget_s
                    raw_elements = sv._scan_windows(hwnd, scale, deadline)
                    for el in raw_elements:
                        is_secret = el.protected or _is_sensitive_element(el.role, el.name)
                        value = "[REDACTED]" if is_secret else el.value
                        elements.append(
                            UIElement(
                                element_id=f"el_{el.eid}",
                                role=el.role,
                                name=el.name,
                                value=value,
                                rect=el.rect,
                                center=el.center,
                                enabled=el.enabled,
                                focused=el.focused,
                                interactable=el.interactable,
                                metadata={
                                    "protected": is_secret,
                                    "depth": el.depth,
                                    "scrollable": el.scrollable,
                                },
                            )
                        )
                except TimeoutError:
                    degraded = True
                    degraded_reason = "UI Automation tree walk exceeded time budget"
                except Exception as ex:
                    degraded = True
                    degraded_reason = f"Accessibility scan failed: {ex}"
            else:
                degraded = True
                degraded_reason = "No target window available for accessibility scan"

            # Compute state digest
            elements_summary = [
                f"{e.role}:{e.name}:{e.rect}" for e in sorted(elements, key=lambda x: (x.rect[1], x.rect[0]))
            ]
            digest_str = f"{active_app}|{window_title}|{len(elements)}|" + "|".join(elements_summary[:50])
            digest = hashlib.sha256(digest_str.encode("utf-8", errors="ignore")).hexdigest()[:16]

            screenshot_path = None
            if capture_screenshot:
                try:
                    img = sv.capture()
                    if img:
                        ts = int(time.time() * 1000)
                        fname = f"obs_{ts}_{digest[:8]}.png"
                        out_file = self.screenshot_dir / fname
                        img.save(str(out_file))
                        screenshot_path = str(out_file)
                except Exception:
                    screenshot_path = None

            obs = DesktopObservation(
                timestamp=started,
                active_app=active_app,
                window_title=window_title,
                pid=pid,
                hwnd=hwnd,
                screen_bounds=screen_bounds,
                scale_factor=scale,
                elements=elements,
                all_windows=windows,
                source=source,
                degraded=degraded,
                degraded_reason=degraded_reason,
                digest=digest,
                screenshot_path=screenshot_path,
            )

            self.history.append(obs)
            if len(self.history) > self._max_history:
                self.history.pop(0)

            return obs

    def get_latest_observation(self) -> DesktopObservation | None:
        """Return the most recent observation from history."""
        return self.history[-1] if self.history else None

    def get_history(self, limit: int = 10) -> list[DesktopObservation]:
        """Return the last N observations."""
        return self.history[-limit:]


_GLOBAL_OBSERVER: DesktopObserver | None = None


def get_desktop_observer() -> DesktopObserver:
    """Retrieve or initialize the global desktop observer instance."""
    global _GLOBAL_OBSERVER
    if _GLOBAL_OBSERVER is None:
        _GLOBAL_OBSERVER = DesktopObserver()
    return _GLOBAL_OBSERVER
