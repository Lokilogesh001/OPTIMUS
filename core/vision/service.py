"""core/vision/service.py — Unified Vision Service Facade for OPTIMUS.

Central coordinator exposing typed, secure interfaces for:
- Continuous Screen Observation & Multi-Monitor discovery.
- Native Windows window intelligence and focus restoration.
- Complete Browser Tab inventory and navigation.
- Autonomous Task Visual Supervision and verification evidence.
- Full testability via reset_for_testing().
"""

from __future__ import annotations

import threading
from typing import Any

from core.vision.browser_intelligence import BrowserIntelligence
from core.vision.context_classifier import (
    detect_duplicate_tabs,
    group_tabs_by_task,
    rank_tabs_for_task,
)
from core.vision.screen_observer import ScreenObserver
from core.vision.task_supervisor import AutonomousTaskSupervisor
from core.vision.types import (
    BrowserTabInfo,
    DesktopState,
    PrivacyConfig,
    SupervisionStepResult,
    WindowInfo,
)
from core.vision.window_intelligence import (
    enumerate_windows,
    find_existing_application_window,
    focus_window,
    get_active_window,
)

_INSTANCE_LOCK = threading.Lock()
_GLOBAL_VISION_SERVICE: UnifiedVisionService | None = None


class UnifiedVisionService:
    """Unified service providing desktop observation, window management, browser tabs, and task supervision."""

    def __init__(self, privacy_config: PrivacyConfig | None = None) -> None:
        self.privacy_config = privacy_config or PrivacyConfig()
        self.observer = ScreenObserver(privacy_config=self.privacy_config)
        self.browser_intel = BrowserIntelligence()
        self.supervisor = AutonomousTaskSupervisor()
        self._cached_tabs: list[BrowserTabInfo] = []
        self._cached_tabs_t: float = 0.0

    @classmethod
    def instance(cls) -> UnifiedVisionService:
        global _GLOBAL_VISION_SERVICE
        with _INSTANCE_LOCK:
            if _GLOBAL_VISION_SERVICE is None:
                _GLOBAL_VISION_SERVICE = cls()
            return _GLOBAL_VISION_SERVICE

    @classmethod
    def reset_for_testing(cls) -> None:
        """Reset global singleton state for test isolation."""
        global _GLOBAL_VISION_SERVICE
        with _INSTANCE_LOCK:
            if _GLOBAL_VISION_SERVICE is not None:
                _GLOBAL_VISION_SERVICE.observer.stop()
                _GLOBAL_VISION_SERVICE = None

    # ── Observer Controls ────────────────────────────────────────────────────

    def start_observer(self) -> None:
        self.observer.start()

    def stop_observer(self) -> None:
        self.observer.stop()

    def pause(self) -> None:
        self.observer.pause()
        self.supervisor.pause()

    def resume(self) -> None:
        self.observer.resume()
        self.supervisor.resume()

    def is_paused(self) -> bool:
        return self.observer.is_paused

    def clear_visual_context(self) -> None:
        self.observer.clear_context()

    def get_desktop_state(self, force_scan: bool = False) -> DesktopState:
        """Capture and return the full current desktop state."""
        fg = get_active_window()
        wins = self.enumerate_windows(include_minimized=True)
        tabs = self.enumerate_browser_tabs()
        return self.observer.observe_once(
            active_window=fg,
            windows=wins,
            browser_tabs=tabs,
            force_scan=force_scan,
        )

    # ── Window Management ────────────────────────────────────────────────────

    def enumerate_windows(self, include_minimized: bool = True) -> list[WindowInfo]:
        return enumerate_windows(include_minimized=include_minimized)

    def focus_window(self, target: int | str, restore_minimized: bool = True) -> tuple[bool, str]:
        return focus_window(target, restore_minimized=restore_minimized)

    def find_existing_app(self, app_name: str) -> WindowInfo | None:
        return find_existing_application_window(app_name)

    # ── Browser Tab Intelligence ─────────────────────────────────────────────

    def enumerate_browser_tabs(self, browser: str | None = None, force: bool = False) -> list[BrowserTabInfo]:
        import time
        now = time.time()
        if not force and browser is None and self._cached_tabs and (now - self._cached_tabs_t < 10.0):
            return self._cached_tabs
        tabs = self.browser_intel.enumerate_tabs(browser_name=browser)
        if browser is None:
            self._cached_tabs = tabs
            self._cached_tabs_t = now
        return tabs

    def find_browser_tabs(self, query: str) -> list[BrowserTabInfo]:
        return self.browser_intel.find_tabs(query)

    def switch_browser_tab(self, query_or_id: str) -> tuple[bool, str]:
        return self.browser_intel.switch_to_tab(query_or_id)

    def group_tabs_by_task(self) -> dict[str, list[BrowserTabInfo]]:
        tabs = self.enumerate_browser_tabs()
        return group_tabs_by_task(tabs)

    def detect_duplicate_tabs(self) -> list[tuple[BrowserTabInfo, BrowserTabInfo]]:
        tabs = self.enumerate_browser_tabs()
        return detect_duplicate_tabs(tabs)

    def rank_tabs_for_task(self, task_goal: str) -> list[tuple[BrowserTabInfo, float]]:
        tabs = self.enumerate_browser_tabs()
        return rank_tabs_for_task(tabs, task_goal)

    # ── Task Supervision ─────────────────────────────────────────────────────

    def supervise_step(
        self,
        task_id: str,
        step_index: int,
        action_type: str,
        target_element: str,
        expected_state: str,
        pre_state: DesktopState,
        post_state: DesktopState,
        action_result: dict[str, Any] | None = None,
        retry_count: int = 0,
    ) -> SupervisionStepResult:
        return self.supervisor.supervise_step(
            task_id=task_id,
            step_index=step_index,
            action_type=action_type,
            target_element=target_element,
            expected_state=expected_state,
            pre_state=pre_state,
            post_state=post_state,
            action_result=action_result,
            retry_count=retry_count,
        )

    def register_screen_event_listener(self, callback: Any) -> None:
        self.observer.register_event_listener(callback)

    def observe_act_verify(
        self,
        action_fn: Any,
        *,
        task_id: str = "vision_op",
        step_index: int = 1,
        action_type: str = "click",
        target_element: str = "",
        expected_state: str = "",
    ) -> dict[str, Any]:
        """Execute OBSERVE -> UNDERSTAND -> ACT -> VERIFY -> UPDATE STATE safely."""
        pre_state = self.get_desktop_state(force_scan=True)
        pre_epoch = pre_state.element_cache_epoch

        # Re-check element epoch & execute action
        action_result = action_fn()
        post_state = self.get_desktop_state(force_scan=True)

        step_res = self.supervise_step(
            task_id=task_id,
            step_index=step_index,
            action_type=action_type,
            target_element=target_element,
            expected_state=expected_state,
            pre_state=pre_state,
            post_state=post_state,
            action_result=action_result if isinstance(action_result, dict) else {"result": action_result},
        )
        self.observer.record_action_outcome(
            f"{action_type}:{target_element}",
            step_res.actual_state,
        )
        if not step_res.is_successful or post_state.element_cache_epoch != pre_epoch:
            self.observer.invalidate_elements("post_action_reassess")
        return {
            "pre_state": pre_state.to_dict(),
            "post_state": post_state.to_dict(),
            "action_result": action_result,
            "supervision": step_res.to_dict(),
            "coordinates_invalidated": post_state.element_cache_epoch != pre_epoch or not step_res.is_successful,
        }

    # ── Natural Language State Explanation ───────────────────────────────────

    def explain_desktop_state(self, question: str | None = None) -> str:
        """Produce an untrusted-safe textual summary of the current desktop for LLM reasoning."""
        state = self.get_desktop_state()

        if state.is_paused:
            return "Visual Awareness is currently PAUSED. No desktop screen information is available."

        if state.privacy_redacted:
            return "Visual Awareness has redacted the current desktop because an excluded or sensitive credential window is active."

        active_str = f"Active Window: '{state.active_window.title}' ({state.active_window.app_name}, PID: {state.active_window.pid})" if state.active_window else "Active Window: None"
        windows_str = ", ".join(f"'{w.title}' [{w.app_name}]" for w in state.windows[:10])
        tabs_str = ", ".join(f"'{t.title}' ({t.domain_hint or t.domain or 'tab'})" for t in state.browser_tabs[:10])

        explanation = (
            f"<untrusted_desktop_context>\n"
            f"{active_str}\n"
            f"Open Windows ({len(state.windows)}): {windows_str}\n"
            f"Open Browser Tabs ({len(state.browser_tabs)}): {tabs_str}\n"
            f"Visible UI Elements: {state.ui_elements_count}\n"
            f"</untrusted_desktop_context>"
        )
        return explanation
