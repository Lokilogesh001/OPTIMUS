"""core/filesystem/run_opener.py — Verified Windows Run Dialog (Win+R) Opener & Sequential Queue.

Implements Milestone 3:
- VerifiedRunFileOpener: Centralized, strictly validated file/folder opener using
  the Windows Run dialog (Win+R) as the default interactive opening mechanism.
- Full path validation: env vars, ~ shortcuts, canonical resolution, symlink/junction
  safety policy, exact target existence check (never guesses/substitutes), and
  UnifiedFilesystemService policy enforcement.
- SharedActionScheduler coordination across RUN_DIALOG, KEYBOARD_MOUSE, ACTIVE_WINDOW,
  and FILE_OPENING so multiple agents or parallel opens never open concurrent Run dialogs.
- Sequential multi-item queue that validates all paths first, opens one item at a time,
  waits for the Run dialog to close and launch acknowledgement, records per-item results,
  and continues safely on individual failures.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.filesystem.service import ErrorCategory, UnifiedFilesystemService
from core.orchestrator.scheduler import ResourceType, SharedActionScheduler
from core.orchestrator.tools import ToolResult

logger = logging.getLogger("optimus.filesystem.run_opener")


@dataclass
class RunOpenResult:
    """Structured outcome for a single VerifiedRunFileOpener request."""
    requested_path: str
    canonical_path: str | None
    success: bool
    method: str = "win_r_run_dialog"
    error: str | None = None
    requires_approval: bool = False
    is_dir: bool = False
    duration_ms: float = 0.0
    launch_state: str = "launch_requested"
    verification_details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_path": self.requested_path,
            "canonical_path": self.canonical_path,
            "success": self.success,
            "method": self.method,
            "error": self.error,
            "requires_approval": self.requires_approval,
            "is_dir": self.is_dir,
            "duration_ms": self.duration_ms,
            "launch_state": self.launch_state,
            "verification_details": self.verification_details,
        }


class WindowsRunDialogDriver:
    """UI automation driver for Windows Run (Win+R) dialog with testable seam."""

    @staticmethod
    def _find_run_dialog_hwnd() -> int:
        """Find visible Windows '#32770' Run dialog window handle, or 0 if absent."""
        if os.name != "nt":
            return 0
        try:
            import win32gui
            found = 0

            def _enum_cb(hwnd: int, _: Any) -> bool:
                nonlocal found
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd).strip().lower()
                if cls == "#32770" and title in ("run", "ausführen", "exécuter", "ejecutar"):
                    found = hwnd
                    return False
                return True

            win32gui.EnumWindows(_enum_cb, None)
            return found
        except Exception:
            return 0

    def open_and_submit(self, canonical_path: str, timeout: float = 6.0) -> tuple[bool, str]:
        """Press Win+R, wait for Run dialog, enter verified canonical path, submit, and confirm close."""
        if os.name != "nt":
            return False, "RUN_DIALOG_UNAVAILABLE: Windows Run dialog requires Windows OS."

        # Check Session 0 isolation before UI interaction
        try:
            import ctypes
            s_id = ctypes.c_ulong(1)
            if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(s_id)) and s_id.value == 0:
                return False, "SESSION_0_ISOLATION: Cannot open Windows Run dialog from non-interactive Session 0."
        except Exception:
            pass

        try:
            import pyautogui
        except ImportError as exc:
            return False, f"RUN_DIALOG_UNAVAILABLE: pyautogui not available ({exc})."

        try:
            # 1. Trigger Win+R
            pyautogui.hotkey("win", "r")

            # 2. Wait for actual Run dialog window to become available
            deadline = time.monotonic() + min(timeout, 4.0)
            run_hwnd = 0
            while time.monotonic() < deadline:
                run_hwnd = self._find_run_dialog_hwnd()
                if run_hwnd:
                    break
                time.sleep(0.08)

            if not run_hwnd:
                return False, "RUN_DIALOG_TIMEOUT: Windows Run dialog did not appear after Win+R."

            # 3. Focus dialog and populate Open field cleanly (clipboard preferred for Unicode/spaces)
            try:
                import win32gui
                win32gui.SetForegroundWindow(run_hwnd)
            except Exception:
                pass

            quoted_path = f'"{canonical_path}"' if " " in canonical_path else canonical_path
            used_clipboard = False
            try:
                import pyperclip
                prev_clip = None
                try:
                    prev_clip = pyperclip.paste()
                except Exception:
                    prev_clip = None
                pyperclip.copy(quoted_path)
                pyautogui.hotkey("ctrl", "a")
                pyautogui.hotkey("ctrl", "v")
                used_clipboard = True
                if prev_clip is not None:
                    # Brief delay so target receives WM_PASTE before restoring
                    time.sleep(0.08)
                    pyperclip.copy(prev_clip)
            except Exception:
                used_clipboard = False

            if not used_clipboard:
                pyautogui.hotkey("ctrl", "a")
                pyautogui.write(quoted_path, interval=0.01)

            # 4. Submit dialog
            pyautogui.press("enter")

            # 5. Observe Run dialog closing (launch acknowledged)
            close_deadline = time.monotonic() + min(timeout, 3.0)
            while time.monotonic() < close_deadline:
                if not self._find_run_dialog_hwnd():
                    return True, f"Opened '{canonical_path}' via Windows Run dialog (Win+R)."
                time.sleep(0.08)

            # Recover: dismiss hung Run dialog cleanly with Esc so UI is not stranded
            pyautogui.press("esc")
            return False, "RUN_DIALOG_ERROR: Windows Run dialog remained open after submission (invalid target or OS error prompt)."
        except Exception as exc:
            return False, f"RUN_DIALOG_ERROR: {type(exc).__name__}: {exc}"


class VerifiedRunFileOpener:
    """Centralized verified file & folder opener using Win+R and SharedActionScheduler."""

    _instance: VerifiedRunFileOpener | None = None
    _singleton_lock = threading.RLock()

    def __init__(
        self,
        fs_service: UnifiedFilesystemService | None = None,
        scheduler: SharedActionScheduler | None = None,
        run_driver: WindowsRunDialogDriver | None = None,
        window_inspector: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.fs = fs_service or UnifiedFilesystemService.instance()
        self.scheduler = scheduler or SharedActionScheduler.instance()
        self.run_driver = run_driver or WindowsRunDialogDriver()
        self._window_inspector = window_inspector
        self._queue_lock = threading.RLock()

    @classmethod
    def instance(cls) -> VerifiedRunFileOpener:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._singleton_lock:
            cls._instance = None

    def verify_target(
        self,
        raw_path: str | Path,
        *,
        allow_symlinks: bool = False,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, Path | None, str, bool]:
        """Validate, expand, canonicalize, and authorize an exact file or directory path.

        Returns (ok, canonical_path, error_or_ok_msg, requires_approval).
        Never guesses or substitutes a similar filename.
        """
        raw_str = str(raw_path or "").strip()
        if not raw_str:
            return False, None, f"{ErrorCategory.PATH_INVALID.value}: Empty path provided.", False

        # Expand environment variables before fs.resolve_path (~ and desktop/downloads shortcuts)
        expanded = os.path.expandvars(raw_str)
        try:
            pre_resolve = Path(expanded).expanduser()
        except Exception as exc:
            return False, None, f"{ErrorCategory.PATH_INVALID.value}: Invalid path syntax: {exc}", False

        # Check symlink / junction policy BEFORE resolving target away silently,
        # walking both the target and every intermediate parent component.
        if not allow_symlinks:
            for comp in (pre_resolve, *pre_resolve.parents):
                if comp.is_symlink():
                    return (
                        False,
                        None,
                        f"{ErrorCategory.POLICY_DENIED.value}: SYMLINK_POLICY_DENIED: Refusing symlink/junction component '{comp}' for '{raw_str}'.",
                        False,
                    )

        try:
            canonical = self.fs.resolve_path(expanded)
        except Exception as exc:
            return False, None, str(exc), False

        # Symlink cycle / broken junction check: walk parents and target
        if canonical.is_symlink() and not allow_symlinks:
            return (
                False,
                None,
                f"{ErrorCategory.POLICY_DENIED.value}: SYMLINK_POLICY_DENIED: Canonical target is a symlink '{canonical}'.",
                False,
            )

        # Verify exact target exists and is a file or directory (never guess alternatives)
        if not canonical.exists():
            return (
                False,
                None,
                f"{ErrorCategory.PATH_NOT_FOUND.value}: Exact target does not exist: '{canonical}'",
                False,
            )

        if not (canonical.is_file() or canonical.is_dir()):
            return (
                False,
                None,
                f"{ErrorCategory.PATH_INVALID.value}: Target is neither a file nor directory: '{canonical}'",
                False,
            )

        # Policy & permission check
        auth, req_appr, reason = self.fs.check_access(canonical, "open_in_explorer", context=context)
        if not auth:
            return False, canonical, reason, bool(req_appr)

        return True, canonical, "OK", False

    def _inspect_open_windows(self) -> list[dict[str, Any]]:
        """Enumerate visible top-level windows or call injected window inspector."""
        if self._window_inspector is not None:
            try:
                return list(self._window_inspector() or [])
            except Exception:
                return []
        if os.name != "nt":
            return []
        try:
            from core.vision.window_intelligence import enumerate_windows
            return [
                {"title": w.title, "process": w.app_name, "hwnd": w.hwnd}
                for w in enumerate_windows(include_minimized=True)
            ]
        except Exception:
            return []

    def _classify_launch_state(
        self,
        canonical: Path,
        before_windows: list[dict[str, Any]],
        *,
        is_dir: bool,
        verify_window: bool,
    ) -> tuple[str, str]:
        """Distinguish document/app detection vs launch_unconfirmed without false-negative delays."""
        if not verify_window:
            return "run_dialog_submitted", "Run dialog submitted and closed cleanly."

        target_name = canonical.name.lower()
        target_stem = canonical.stem.lower()
        viewer_procs = (
            "msedge", "chrome", "brave", "firefox", "acrobat", "acrord32",
            "foxitpdfreader", "sumatrapdf", "explorer", "winword", "powerpnt",
            "notepad", "code",
        )
        before_map = {w.get("hwnd"): str(w.get("title") or "") for w in before_windows if w.get("hwnd")}
        deadline = time.monotonic() + (0.0 if self._window_inspector is not None else 1.5)

        while True:
            after_windows = self._inspect_open_windows()
            for w in after_windows:
                title = str(w.get("title") or "").lower()
                if target_name in title or (len(target_stem) >= 3 and target_stem in title):
                    return "target_document_detected", f"Detected target window/tab title matching '{canonical.name}'."
            for w in after_windows:
                proc = str(w.get("process") or "").lower()
                title = str(w.get("title") or "")
                title_l = title.lower()
                hwnd = w.get("hwnd")
                is_viewer = any(vp in proc or vp in title_l for vp in viewer_procs)
                is_new_or_changed = (hwnd not in before_map) or (before_map.get(hwnd) != title)
                if is_dir and "explorer" in proc and hwnd not in before_map:
                    return "target_document_detected", f"Opened Explorer window for '{canonical.name}'."
                if is_viewer and is_new_or_changed:
                    return (
                        "target_app_detected",
                        f"Active viewer handler detected ({w.get('process') or w.get('title')}); document may share existing tab/process.",
                    )
            if time.monotonic() >= deadline:
                break
            time.sleep(0.15)

        return (
            "launch_unconfirmed",
            f"Run dialog submitted and closed cleanly for '{canonical.name}', but viewer window/tab could not be independently confirmed.",
        )

    def open_path(
        self,
        raw_path: str | Path,
        *,
        task_id: str = "file_opener",
        allow_symlinks: bool = False,
        timeout: float = 10.0,
        verify_window: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> RunOpenResult:
        """Verify exact path and open it through the coordinated Windows Run (Win+R) dialog."""
        t0 = time.perf_counter()
        ok, canonical, msg, req_appr = self.verify_target(
            raw_path, allow_symlinks=allow_symlinks, context=context
        )
        if not ok or canonical is None:
            return RunOpenResult(
                requested_path=str(raw_path),
                canonical_path=str(canonical) if canonical else None,
                success=False,
                error=msg,
                requires_approval=req_appr,
                launch_state="launch_failed",
                verification_details=msg,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

        is_dir = canonical.is_dir()
        canonical_str = str(canonical)
        do_verify = verify_window if verify_window is not None else (
            self._window_inspector is not None or (os.name == "nt" and type(self.run_driver) is WindowsRunDialogDriver)
        )
        before_windows = self._inspect_open_windows() if do_verify else []

        def _do_open() -> tuple[bool, str]:
            return self.run_driver.open_and_submit(canonical_str, timeout=timeout)

        try:
            launched, launch_msg = self.scheduler.run_coordinated(
                [
                    ResourceType.RUN_DIALOG,
                    ResourceType.KEYBOARD_MOUSE,
                    ResourceType.ACTIVE_WINDOW,
                    ResourceType.FILE_OPENING,
                ],
                _do_open,
                task_id=task_id,
                priority=4,
                timeout=timeout,
            )
            if launched:
                l_state, l_detail = self._classify_launch_state(
                    canonical, before_windows, is_dir=is_dir, verify_window=do_verify
                )
            else:
                l_state, l_detail = "launch_failed", launch_msg
            return RunOpenResult(
                requested_path=str(raw_path),
                canonical_path=canonical_str,
                success=launched,
                method="win_r_run_dialog",
                error=None if launched else launch_msg,
                is_dir=is_dir,
                launch_state=l_state,
                verification_details=l_detail,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            return RunOpenResult(
                requested_path=str(raw_path),
                canonical_path=canonical_str,
                success=False,
                method="win_r_run_dialog",
                error=err_msg,
                is_dir=is_dir,
                launch_state="launch_failed",
                verification_details=err_msg,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

    def open_sequential(
        self,
        paths: list[str | Path],
        *,
        task_id: str = "file_opener_batch",
        allow_symlinks: bool = False,
        per_item_timeout: float = 10.0,
        deduplicate: bool = True,
        verify_window: bool | None = None,
        cancel_event: threading.Event | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate all requested paths, deduplicate canonical paths preserving order, then open sequentially via Win+R."""
        with self._queue_lock:
            results: list[RunOpenResult] = []
            # Validate all paths upfront, preserving order
            validated_queue: list[tuple[str | Path, RunOpenResult | None]] = []
            seen_canonical: set[str] = set()
            for p in paths:
                ok, canonical, msg, req_appr = self.verify_target(
                    p, allow_symlinks=allow_symlinks, context=context
                )
                if not ok or canonical is None:
                    validated_queue.append((
                        p,
                        RunOpenResult(
                            requested_path=str(p),
                            canonical_path=str(canonical) if canonical else None,
                            success=False,
                            error=msg,
                            requires_approval=req_appr,
                            launch_state="launch_failed",
                            verification_details=msg,
                        ),
                    ))
                else:
                    canon_key = str(canonical).lower()
                    if deduplicate and canon_key in seen_canonical:
                        continue
                    seen_canonical.add(canon_key)
                    validated_queue.append((canonical, None))

            # Open each valid target sequentially (never simultaneous Run dialogs)
            for item, pre_failed in validated_queue:
                if cancel_event and cancel_event.is_set():
                    results.append(RunOpenResult(
                        requested_path=str(item),
                        canonical_path=str(item) if pre_failed is None else None,
                        success=False,
                        error="CANCELLED: Sequential open queue cancelled.",
                        launch_state="launch_failed",
                    ))
                    continue

                if pre_failed is not None:
                    results.append(pre_failed)
                    continue

                res = self.open_path(
                    item,
                    task_id=task_id,
                    allow_symlinks=allow_symlinks,
                    timeout=per_item_timeout,
                    verify_window=verify_window,
                    context=context,
                )
                results.append(res)

            succeeded = sum(1 for r in results if r.success)
            failed = len(results) - succeeded
            return {
                "total": len(results),
                "succeeded": succeeded,
                "failed": failed,
                "results": [r.to_dict() for r in results],
                "status": (
                    "done" if failed == 0
                    else ("partial" if succeeded > 0 else "failed")
                ),
            }

    def as_tool_result(self, path_or_paths: str | Path | list[str | Path], **kwargs: Any) -> ToolResult:
        """Convert opener execution into standard OPTIMUS ToolResult."""
        if isinstance(path_or_paths, list):
            batch = self.open_sequential(path_or_paths, **kwargs)
            status = "done" if batch["status"] == "done" else ("unverified" if batch["status"] == "partial" else "failed")
            return ToolResult(
                status=status,
                output=f"Sequential Win+R open complete: {batch['succeeded']}/{batch['total']} opened.",
                evidence=batch,
                error=None if batch["failed"] == 0 else f"{batch['failed']} items failed to open.",
            )

        res = self.open_path(path_or_paths, **kwargs)
        if res.success:
            return ToolResult(
                status="done",
                output=f"Opened '{res.canonical_path}' via Windows Run (Win+R).",
                evidence=res.to_dict(),
                duration_ms=res.duration_ms,
            )
        return ToolResult(
            status="unverified" if res.requires_approval else "failed",
            output=res.error or f"Failed to open '{res.requested_path}'.",
            error=res.error,
            evidence=res.to_dict(),
            duration_ms=res.duration_ms,
        )
