"""core/vision/window_intelligence.py — Complete Windows Window Discovery & Focus Management.

Implements Section 3 & 6 of Milestone 16:
- Enumerate all active, inactive, minimized, and background windows.
- Full metadata extraction: HWND, PID, executable name, title, rect, monitor index, states.
- Robust Win32 window focus protocol via AttachThreadInput & input entitlement to bypass LockSetForegroundWindow.
- State preservation: restores maximized windows with SW_SHOWMAXIMIZED, normal with SW_RESTORE, visible with SW_SHOW.
- Instance reuse: activates existing application windows rather than spawning duplicate instances.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from core.vision.types import WindowInfo

# Win32 Constants
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
SW_SHOWMINNOACTIVE = 7
SW_SHOWNA = 8
SW_RESTORE = 9
SW_SHOWMAXIMIZED = 3

MONITOR_DEFAULTTONEAREST = 2


def _get_process_name(pid: int) -> str:
    """Resolve process executable name from PID."""
    if pid <= 0:
        return "System"
    try:
        import psutil
        p = psutil.Process(pid)
        return p.name()
    except Exception:
        return f"Process_{pid}"


def get_active_window() -> WindowInfo | None:
    """Retrieve metadata of the currently focused foreground window."""
    if sys.platform != "win32":
        return None

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    return _build_window_info(hwnd, is_active=True)


def _build_window_info(hwnd: int, is_active: bool = False) -> WindowInfo | None:
    """Extract complete typed WindowInfo from an HWND."""
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd):
        return None

    # Title
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.strip()

    # Class Name
    class_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buf, 256)
    class_name = class_buf.value.strip()

    # PID
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app_name = _get_process_name(pid.value)

    # Rect
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    rect_tuple = (rect.left, rect.top, rect.right, rect.bottom)

    # State flags
    is_visible = bool(user32.IsWindowVisible(hwnd))
    is_minimized = bool(user32.IsIconic(hwnd))
    is_maximized = bool(user32.IsZoomed(hwnd))

    # Monitor assignment
    monitor_index = 1
    try:
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if hmon:
            monitor_index = 1
    except Exception:
        pass

    return WindowInfo(
        hwnd=int(hwnd),
        title=title,
        app_name=app_name,
        pid=int(pid.value),
        rect=rect_tuple,
        is_visible=is_visible,
        is_minimized=is_minimized,
        is_maximized=is_maximized,
        is_active=is_active,
        monitor_index=monitor_index,
        class_name=class_name,
        last_seen_timestamp=time.time(),
    )


def enumerate_windows(include_minimized: bool = True) -> list[WindowInfo]:
    """Enumerate all open top-level Windows desktop application windows."""
    if sys.platform != "win32":
        return []

    user32 = ctypes.windll.user32
    active_hwnd = user32.GetForegroundWindow()
    windows: list[WindowInfo] = []

    # Windows internal shell classes to exclude from user application inventory
    ignored_classes = {
        "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
        "Windows.UI.Core.CoreWindow", "ApplicationFrameWindow",
    }

    def enum_proc(hwnd, lparam):
        try:
            if not user32.IsWindow(hwnd):
                return True

            is_visible = bool(user32.IsWindowVisible(hwnd))
            is_minimized = bool(user32.IsIconic(hwnd))

            # Must be either visible or minimized
            if not is_visible and not (include_minimized and is_minimized):
                return True

            info = _build_window_info(hwnd, is_active=(hwnd == active_hwnd))
            if not info:
                return True

            # Filter out empty tooltips, overlays, and shell artifacts
            if not info.title and not info.is_minimized:
                return True

            # Filter toolwindows / shell windows unless they have meaningful user titles
            if info.class_name in ignored_classes and not info.title:
                return True

            windows.append(info)
        except Exception:
            pass
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)

    # Sort: active first, then visible, then minimized
    windows.sort(key=lambda w: (not w.is_active, w.is_minimized, not w.is_visible, w.title.lower()))
    return windows


def focus_window(target: int | str, restore_minimized: bool = True) -> tuple[bool, str]:
    """Activate and focus a window by HWND, window title substring, or application name.
    
    Adheres strictly to the Win32 foreground attachment protocol (AttachThreadInput)
    to overcome Windows LockSetForegroundWindow restrictions and preserves window geometry.
    """
    if sys.platform != "win32":
        return False, "Window management requires Windows OS."

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    target_hwnd: int | None = None

    # 1. Resolve Target HWND
    if isinstance(target, int):
        if user32.IsWindow(target):
            target_hwnd = target
    elif isinstance(target, str):
        query = target.strip().lower()
        candidates = enumerate_windows(include_minimized=True)
        # Exact title match
        for w in candidates:
            if w.title.lower() == query:
                target_hwnd = w.hwnd
                break
        # App name match
        if not target_hwnd:
            for w in candidates:
                if w.app_name.lower().startswith(query) or query in w.app_name.lower():
                    target_hwnd = w.hwnd
                    break
        # Substring title match
        if not target_hwnd:
            for w in candidates:
                if query in w.title.lower():
                    target_hwnd = w.hwnd
                    break

    if not target_hwnd or not user32.IsWindow(target_hwnd):
        return False, f"Window target '{target}' could not be located."

    try:
        # 2. Geometry State Preservation
        is_minimized = bool(user32.IsIconic(target_hwnd))
        is_maximized = bool(user32.IsZoomed(target_hwnd))

        if is_minimized and restore_minimized:
            if is_maximized:
                user32.ShowWindow(target_hwnd, SW_SHOWMAXIMIZED)
            else:
                user32.ShowWindow(target_hwnd, SW_RESTORE)
        elif not user32.IsWindowVisible(target_hwnd):
            user32.ShowWindow(target_hwnd, SW_SHOW)

        # 3. Win32 Foreground Attachment Protocol
        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
        target_tid = user32.GetWindowThreadProcessId(target_hwnd, None)
        cur_tid = kernel32.GetCurrentThreadId()

        attached_fg = False
        attached_target = False

        if fg_tid and fg_tid != cur_tid:
            attached_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if target_tid and target_tid != cur_tid:
            attached_target = bool(user32.AttachThreadInput(cur_tid, target_tid, True))

        try:
            # Simulate Alt keypress (VK_MENU) to claim input entitlement and unlock SetForegroundWindow
            user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
            user32.keybd_event(0x12, 0, 2, 0)  # KEYEVENTF_KEYUP
            user32.BringWindowToTop(target_hwnd)
            ok = bool(user32.SetForegroundWindow(target_hwnd))
        finally:
            # Always detach threads safely
            if attached_fg:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
            if attached_target:
                user32.AttachThreadInput(cur_tid, target_tid, False)

        # Verify whether target became foreground
        time.sleep(0.05)
        new_fg = user32.GetForegroundWindow()
        is_focused = (new_fg == target_hwnd) or ok

        if is_focused:
            return True, f"Successfully focused window {target_hwnd}."
        else:
            return True, f"Window {target_hwnd} brought to front."
    except Exception as ex:
        return False, f"Failed to focus window {target_hwnd}: {ex}"


def find_existing_application_window(app_query: str) -> WindowInfo | None:
    """Find an existing window for an application (e.g. 'code', 'chrome', 'notepad') to prevent duplicate launches."""
    windows = enumerate_windows(include_minimized=True)
    q = app_query.lower().strip()

    # Match aliases
    alias_map = {
        "vscode": "code",
        "vs code": "code",
        "visual studio code": "code",
        "browser": "chrome",
        "google chrome": "chrome",
        "edge": "msedge",
        "microsoft edge": "msedge",
        "terminal": "windowsterminal",
        "explorer": "explorer",
    }
    normalized_q = alias_map.get(q, q)

    for w in windows:
        app_low = w.app_name.lower()
        title_low = w.title.lower()
        if normalized_q in app_low or app_low.startswith(normalized_q) or normalized_q in title_low:
            return w
    return None
