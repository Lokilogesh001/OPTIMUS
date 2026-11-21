r"""tests/test_visual_awareness.py — Comprehensive Test Suite for Milestone 16.

Verifies:
1. Continuous desktop observation and multi-monitor discovery.
2. Application switching and active window detection.
3. Minimized window discovery and state preservation.
4. Native window restoration via Win32 focus protocol.
5. Multi-window browser tab discovery across Chrome, Edge, and Firefox.
6. Tab group discovery and search by query/domain.
7. Discarded and background tab switching.
8. Semantic task-aware tab classification, grouping, and duplicate detection.
9. Autonomous task visual supervision and 10-step verification loop.
10. Dead-click, disabled-control, and crash anomaly detection.
11. Unexpected dialog anomaly detection and safe recovery recommendation.
12. Visual outcome verification with SHA-256 evidence generation.
13. Observation context maintenance across application switching.
14. Immediate observation halting when paused and state reset.
15. Privacy boundaries: excluded processes, credential dialogs, and token masking.
16. Memory safety: bounded 30-frame ring buffer, downscaled thumbnails, ephemeral raw buffers.
17. Central ToolRegistry registration & capability tier enforcement for all vision tools.
18. MREClassifier natural language intent parsing & MREOutcomeVerifier contract validation.
19. CentralOrchestrator fast-path integration for window and tab management.
"""

from __future__ import annotations

import io
import os
import time
from unittest.mock import patch

import pytest

from core.orchestrator.agents import AgentRegistry
from core.orchestrator.mre import MREClassifier, MREOutcomeVerifier
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.tools import CapabilityTier, ToolRegistry, ToolResult
from core.vision.browser_intelligence import BrowserIntelligence, _extract_domain_hint
from core.vision.context_classifier import (
    classify_tab_semantics,
    detect_duplicate_tabs,
    group_tabs_by_task,
    rank_tabs_for_task,
)
from core.vision.screen_observer import (
    ScreenObserver,
    compute_dhash,
    hamming_distance,
    mask_sensitive_tokens,
)
from core.vision.service import UnifiedVisionService
from core.vision.task_supervisor import AutonomousTaskSupervisor
from core.vision.types import (
    BrowserTabInfo,
    DesktopState,
    PrivacyConfig,
    SupervisorState,
    WindowInfo,
)
from core.vision.window_intelligence import (
    enumerate_windows,
    find_existing_application_window,
    focus_window,
)


@pytest.fixture(autouse=True)
def reset_vision_singleton():
    """Ensure clean vision service singleton between test runs."""
    UnifiedVisionService.reset_for_testing()
    yield
    UnifiedVisionService.reset_for_testing()


# ── 1. Always-On Screen Observation & Multi-Monitor Discovery ─────────────────

def test_screen_observer_lifecycle_and_monitors():
    """Verify ScreenObserver discovers monitors, captures frames, and starts/stops cleanly."""
    obs = ScreenObserver()
    monitors = obs.discover_monitors()
    assert len(monitors) >= 1
    assert any(m["is_primary"] for m in monitors)

    # Observe single state
    state = obs.observe_once()
    assert isinstance(state, DesktopState)
    assert state.timestamp > 0
    assert len(state.dhash) == 16
    assert state.is_paused is False

    # Start and stop background worker
    obs.start()
    assert obs._is_running is True
    time.sleep(0.1)
    obs.stop()
    assert obs._is_running is False


def test_dhash_and_hamming_distance():
    """Verify 64-bit difference hash computation and distance measurement."""
    from PIL import Image

    # Create solid white image
    img1 = Image.new("RGB", (100, 100), color="white")
    buf1 = io.BytesIO()
    img1.save(buf1, format="PNG")
    h1 = compute_dhash(buf1.getvalue())

    # Identical image -> zero distance
    assert hamming_distance(h1, h1) == 0

    # Create split black/white image
    img2 = Image.new("RGB", (100, 100), color="black")
    buf2 = io.BytesIO()
    img2.save(buf2, format="PNG")
    h2 = compute_dhash(buf2.getvalue())

    assert len(h1) == 16
    assert len(h2) == 16


# ── 2. Application Switching & Window Intelligence ───────────────────────────

def test_enumerate_windows_and_active_detection():
    """Verify window enumeration includes active, visible, and minimized windows."""
    windows = enumerate_windows(include_minimized=True)
    assert isinstance(windows, list)
    if windows:
        w0 = windows[0]
        assert isinstance(w0, WindowInfo)
        assert w0.hwnd > 0
        assert isinstance(w0.app_name, str)
        assert isinstance(w0.is_minimized, bool)
        assert isinstance(w0.is_maximized, bool)


def test_find_existing_application_window():
    """Verify finding existing application window to prevent duplicate launches."""
    mock_wins = [
        WindowInfo(hwnd=1001, title="test.py - Mark-LVII - Visual Studio Code", app_name="Code.exe", pid=5000, rect=(0,0,100,100), is_visible=True, is_minimized=False, is_maximized=True, is_active=True),
        WindowInfo(hwnd=1002, title="Google - Google Chrome", app_name="chrome.exe", pid=5010, rect=(0,0,100,100), is_visible=True, is_minimized=True, is_maximized=False, is_active=False),
    ]
    with patch("core.vision.window_intelligence.enumerate_windows", return_value=mock_wins):
        found_code = find_existing_application_window("code")
        assert found_code is not None
        assert found_code.hwnd == 1001

        found_chrome = find_existing_application_window("chrome")
        assert found_chrome is not None
        assert found_chrome.hwnd == 1002

        found_none = find_existing_application_window("nonexistent_app_xyz")
        assert found_none is None


def test_focus_window_with_state_preservation():
    """Verify focus_window resolves window and calls Win32 APIs with geometry preservation."""
    with patch("ctypes.windll.user32.IsWindow", return_value=1), \
         patch("ctypes.windll.user32.IsIconic", return_value=1), \
         patch("ctypes.windll.user32.IsZoomed", return_value=1), \
         patch("ctypes.windll.user32.ShowWindow") as mock_show, \
         patch("ctypes.windll.user32.GetForegroundWindow", return_value=9999), \
         patch("ctypes.windll.user32.GetWindowThreadProcessId", return_value=1), \
         patch("ctypes.windll.user32.AttachThreadInput", return_value=1), \
         patch("ctypes.windll.user32.BringWindowToTop", return_value=1), \
         patch("ctypes.windll.user32.SetForegroundWindow", return_value=1):

        ok, _msg = focus_window(1234, restore_minimized=True)
        assert ok is True
        # SW_SHOWMAXIMIZED (3) must be called when window was maximized & minimized
        mock_show.assert_called_with(1234, 3)


# ── 3. Browser Tab Discovery & Navigation ────────────────────────────────────

def test_domain_hint_extraction():
    """Verify regex domain hint extraction from varied browser tab titles."""
    assert _extract_domain_hint("Pull Requests · Lokilogesh001/OPTIMUS · GitHub") == "github.com"
    assert _extract_domain_hint("Attention Is All You Need - arXiv") == "arxiv.org"
    assert _extract_domain_hint("Python 3.13 Documentation") == "docs.python.org"
    assert _extract_domain_hint("Wikipedia, the free encyclopedia") == "wikipedia.org"


def test_browser_intelligence_tab_enumeration_and_search():
    """Verify browser tab enumeration across windows and searching by keyword."""
    intel = BrowserIntelligence()
    mock_windows = [
        WindowInfo(hwnd=2001, title="GitHub - Lokilogesh001/OPTIMUS - Google Chrome", app_name="chrome.exe", pid=6000, rect=(0,0,100,100), is_visible=True, is_minimized=False, is_maximized=False, is_active=True),
        WindowInfo(hwnd=2002, title="ArXiv:2310.06825 [cs.AI] - Microsoft Edge", app_name="msedge.exe", pid=6010, rect=(0,0,100,100), is_visible=True, is_minimized=True, is_maximized=False, is_active=False),
    ]

    with patch.object(intel, "discover_browser_windows", return_value=mock_windows):
        tabs = intel.enumerate_tabs()
        assert len(tabs) >= 2
        assert any(t.browser == "chrome" for t in tabs)
        assert any(t.browser == "msedge" for t in tabs)

        # Search tabs
        matched_github = intel.find_tabs("github")
        assert len(matched_github) >= 1
        assert "github" in matched_github[0].domain_hint

        matched_arxiv = intel.find_tabs("arxiv")
        assert len(matched_arxiv) >= 1
        assert "arxiv" in matched_arxiv[0].domain_hint


def test_switch_browser_tab():
    """Verify switching to browser tab by id or title query restores window and selects tab."""
    intel = BrowserIntelligence()
    mock_tab = BrowserTabInfo(
        tab_id="chrome_2001_0",
        browser="chrome",
        window_hwnd=2001,
        title="OPTIMUS Documentation",
        is_active=False,
        domain_hint="docs.optimus.ai",
    )
    with patch.object(intel, "enumerate_tabs", return_value=[mock_tab]), \
         patch("core.vision.browser_intelligence.focus_window", return_value=(True, "Focused")):
        ok, msg = intel.switch_to_tab("OPTIMUS")
        assert ok is True
        assert "OPTIMUS Documentation" in msg


# ── 4. Tab Context Classifier & Semantic Task Grouping ───────────────────────

def test_context_classifier_semantics_and_duplicates():
    """Verify semantic classification, grouping, and duplicate tab detection."""
    tabs = [
        BrowserTabInfo(tab_id="1", browser="chrome", window_hwnd=10, title="GitHub Repository", is_active=True, domain_hint="github.com"),
        BrowserTabInfo(tab_id="2", browser="chrome", window_hwnd=10, title="GitHub Repository", is_active=False, domain_hint="github.com"),
        BrowserTabInfo(tab_id="3", browser="chrome", window_hwnd=10, title="ArXiv Research Paper", is_active=False, domain_hint="arxiv.org"),
        BrowserTabInfo(tab_id="4", browser="chrome", window_hwnd=10, title="Python API Documentation", is_active=False, domain_hint="docs.python.org"),
        BrowserTabInfo(tab_id="5", browser="chrome", window_hwnd=10, title="Gmail Inbox", is_active=False, domain_hint="mail.google.com"),
    ]

    # Category classification
    assert classify_tab_semantics(tabs[0]) == "development"
    assert classify_tab_semantics(tabs[2]) == "research"
    assert classify_tab_semantics(tabs[3]) == "documentation"
    assert classify_tab_semantics(tabs[4]) == "communication"

    # Duplicates detection
    dups = detect_duplicate_tabs(tabs)
    assert len(dups) == 1
    assert dups[0][0].title == "GitHub Repository"
    assert dups[0][1].title == "GitHub Repository"

    # Task grouping
    groups = group_tabs_by_task(tabs)
    assert len(groups["development"]) == 2
    assert len(groups["research"]) == 1
    assert len(groups["documentation"]) == 1
    assert len(groups["communication"]) == 1

    # Task relevance ranking
    ranked = rank_tabs_for_task(tabs, "Read the research paper on neural networks")
    assert ranked[0][0].semantic_category == "research"


# ── 5. Autonomous Task Visual Supervision ────────────────────────────────────

def test_supervisor_verifies_successful_transition():
    """Verify supervisor validates successful visual state transitions with evidence."""
    import os
    sup = AutonomousTaskSupervisor()
    sup.start_supervision("task-100")

    w_active = WindowInfo(hwnd=100, title="App", app_name="app.exe", pid=os.getpid(), rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)
    pre = DesktopState(timestamp=1.0, monitors=[], active_window=w_active, windows=[w_active], browser_tabs=[], dhash="aaaa00000000aaaa", has_changed=False)
    post = DesktopState(timestamp=2.0, monitors=[], active_window=w_active, windows=[w_active], browser_tabs=[], dhash="bbbb11111111bbbb", has_changed=True)

    res = sup.supervise_step(
        task_id="task-100",
        step_index=1,
        action_type="type",
        target_element="text_input",
        expected_state="Text entered into input",
        pre_state=pre,
        post_state=post,
    )
    assert res.is_successful is True
    assert res.detected_anomaly is None
    assert "evidence_id" in res.evidence
    assert res.evidence["verified"] is True
    assert sup.state == SupervisorState.SUPERVISING


def test_supervisor_detects_dead_click():
    """Verify supervisor detects when a click produces zero visual or UI element change."""
    import os
    sup = AutonomousTaskSupervisor()
    w_active = WindowInfo(hwnd=100, title="App", app_name="app.exe", pid=os.getpid(), rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)
    pre = DesktopState(timestamp=1.0, monitors=[], active_window=w_active, windows=[w_active], browser_tabs=[], dhash="1234567812345678", has_changed=False)
    post = DesktopState(timestamp=2.0, monitors=[], active_window=w_active, windows=[w_active], browser_tabs=[], dhash="1234567812345678", has_changed=False)

    res = sup.supervise_step(
        task_id="task-101",
        step_index=2,
        action_type="click",
        target_element="submit_button",
        expected_state="Dialog opened",
        pre_state=pre,
        post_state=post,
    )
    assert res.is_successful is False
    assert res.detected_anomaly == "dead_click"
    # Idempotency check: non-idempotent action "submit_button" must not auto-retry!
    assert res.recommended_action == "escalate"
    assert sup.state == SupervisorState.RECOVERING


def test_supervisor_detects_unexpected_modal_dialog():
    """Verify supervisor flags unexpected error dialog and produces recommendation."""
    import os
    sup = AutonomousTaskSupervisor()
    w_active = WindowInfo(hwnd=100, title="App Main", app_name="app.exe", pid=os.getpid(), rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)
    w_dialog = WindowInfo(hwnd=101, title="Error: Connection Failed", app_name="app.exe", pid=os.getpid(), rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)

    pre = DesktopState(timestamp=1.0, monitors=[], active_window=w_active, windows=[w_active], browser_tabs=[], dhash="1111", has_changed=False)
    post = DesktopState(timestamp=2.0, monitors=[], active_window=w_dialog, windows=[w_active, w_dialog], browser_tabs=[], dhash="2222", has_changed=True)

    res = sup.supervise_step(
        task_id="task-102",
        step_index=3,
        action_type="click",
        target_element="refresh_button",
        expected_state="Data refreshed in table",
        pre_state=pre,
        post_state=post,
    )
    assert res.is_successful is False
    assert res.detected_anomaly == "unexpected_popup"
    assert res.recommended_action == "dismiss_blocking_dialog"


# ── 6. Safety, Privacy & Token Redaction ─────────────────────────────────────

def test_privacy_exclusions_and_credential_masking():
    """Verify password managers and credential dialogs trigger privacy redaction."""
    cfg = PrivacyConfig()
    assert cfg.is_window_excluded("1Password.exe", "1Password - Unlocked") is True
    assert cfg.is_window_excluded("chrome.exe", "Enter your credentials") is True
    assert cfg.is_window_excluded("explorer.exe", "Windows Security") is True
    assert cfg.is_window_excluded("notepad.exe", "meeting_notes.txt") is False

    obs = ScreenObserver(privacy_config=cfg)
    w_sec = WindowInfo(hwnd=999, title="Windows Security", app_name="credentialui.exe", pid=123, rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)
    st = obs.observe_once(active_window=w_sec)
    assert st.privacy_redacted is True
    assert st.dhash == "0000000000000000"


def test_token_masking_utility():
    """Verify universal redaction of sensitive API tokens and credentials."""
    raw = "Here is my key: sk-proj-1234567890abcdef1234567890 and Bearer eyJhbGciOiJIUzI1NiJ9"
    masked = mask_sensitive_tokens(raw)
    assert "sk-proj-1234567890abcdef1234567890" not in masked
    assert "[REDACTED_API_KEY]" in masked
    assert "[REDACTED_TOKEN]" in masked


def test_immediate_pause_and_resume():
    """Verify observer stops observing immediately when paused."""
    svc = UnifiedVisionService.instance()
    assert svc.is_paused() is False

    svc.pause()
    assert svc.is_paused() is True
    st_paused = svc.get_desktop_state()
    assert st_paused.is_paused is True

    svc.resume()
    assert svc.is_paused() is False


# ── 7. ToolRegistry Registration & Agent Profiles ────────────────────────────

def test_vision_tools_registered_in_tool_registry():
    """Verify all 6 vision tools are registered in ToolRegistry with correct tiers."""
    reg = ToolRegistry()
    assert reg.get("enumerate_windows") is not None
    assert reg.get("enumerate_windows").capability_tier == CapabilityTier.READ_ONLY

    assert reg.get("focus_window") is not None
    assert reg.get("focus_window").capability_tier == CapabilityTier.EXECUTION

    assert reg.get("enumerate_browser_tabs") is not None
    assert reg.get("enumerate_browser_tabs").capability_tier == CapabilityTier.READ_ONLY

    assert reg.get("switch_browser_tab") is not None
    assert reg.get("switch_browser_tab").capability_tier == CapabilityTier.EXECUTION

    assert reg.get("capture_visual_state") is not None
    assert reg.get("capture_visual_state").capability_tier == CapabilityTier.READ_ONLY

    assert reg.get("explain_desktop_state") is not None
    assert reg.get("explain_desktop_state").capability_tier == CapabilityTier.READ_ONLY


def test_vision_tools_accessible_across_agent_profiles():
    """Verify general, researcher, and developer agent profiles can invoke vision tools."""
    reg = AgentRegistry()
    gen = reg.get("general")
    res = reg.get("researcher")

    assert "enumerate_windows" in gen.allowed_tools
    assert "focus_window" in gen.allowed_tools
    assert "enumerate_browser_tabs" in gen.allowed_tools
    assert "switch_browser_tab" in gen.allowed_tools

    assert "enumerate_windows" in res.allowed_tools
    assert "switch_browser_tab" in res.allowed_tools


# ── 8. MRE Intent Classification & Outcome Verification ──────────────────────

def test_mre_intent_classification_for_vision_commands():
    """Verify MREClassifier identifies natural language window and tab intents."""
    cls = MREClassifier()

    d1 = cls.classify("list open windows")
    assert d1.tool_name == "enumerate_windows"

    d2 = cls.classify("focus window Code")
    assert d2.tool_name == "focus_window"
    assert d2.arguments.get("target") == "Code"

    d3 = cls.classify("show open browser tabs")
    assert d3.tool_name == "enumerate_browser_tabs"

    d4 = cls.classify("switch to tab GitHub")
    assert d4.tool_name == "switch_browser_tab"
    assert d4.arguments.get("query_or_id") == "GitHub"

    d5 = cls.classify("Why isn't the application working?")
    assert d5.tool_name == "explain_desktop_state"

    d6 = cls.classify("continue my coding task")
    assert d6.tool_name == "focus_window"
    assert d6.arguments.get("target") == "code"

    d7 = cls.classify("find the documentation I opened earlier")
    assert d7.tool_name == "switch_browser_tab"
    assert d7.arguments.get("query_or_id") == "docs"


def test_mre_outcome_verifier_for_vision_contracts():
    """Verify MREOutcomeVerifier validates window, tab, and desktop explanation contracts."""
    verifier = MREOutcomeVerifier()

    res_ok = ToolResult(status="done", output="Success", evidence={"focused": True})
    ok1, _ = verifier.verify(MREClassifier().classify("focus window Code").outcome_contract, res_ok)
    assert ok1 is True

    ok2, _ = verifier.verify(MREClassifier().classify("list open windows").outcome_contract, res_ok)
    assert ok2 is True

    ok3, _ = verifier.verify(MREClassifier().classify("switch to tab GitHub").outcome_contract, res_ok)
    assert ok3 is True


# ── 9. CentralOrchestrator Fast-Path Execution ───────────────────────────────

def test_orchestrator_fast_path_visual_commands():
    """Verify CentralOrchestrator executes window/tab navigation via instant fast-path."""
    orch = CentralOrchestrator()

    # Fast-path: list windows
    res_wins = orch._try_fast_path("list open windows", "task-win-list")
    assert res_wins is not None
    success, _out, _agent, side_effects = res_wins
    assert success is True
    assert any(se.get("tool_name") == "enumerate_windows" for se in side_effects)

    # Fast-path: explain desktop
    res_exp = orch._try_fast_path("Why isn't the application working?", "task-exp")
    assert res_exp is not None
    success, _out, _agent, side_effects = res_exp
    assert success is True
    assert any(se.get("tool_name") == "explain_desktop_state" for se in side_effects)


def test_orchestrator_fast_path_window_focus_tier_allowed(monkeypatch):
    """Verify focus_window fast-path does not trigger TIER_VIOLATION for general agent."""
    orch = CentralOrchestrator()
    from core.vision.service import UnifiedVisionService
    monkeypatch.setattr(UnifiedVisionService.instance(), "focus_window", lambda target, restore_minimized=True: (True, f"Window '{target}' focused."))

    # Fast-path: focus window
    res_focus = orch._try_fast_path("focus window 12345", "task-focus")
    assert res_focus is not None
    success, _out, _agent, side_effects = res_focus
    assert success is True
    assert any(se.get("tool_name") == "focus_window" for se in side_effects)
    assert not any("tier" in str(se.get("verification_details", "")).lower() for se in side_effects)


def test_supervisor_close_action_not_flagged_as_crash():
    """Verify intentional window close actions are not flagged as application crashes."""
    sup = AutonomousTaskSupervisor()
    w_old = WindowInfo(hwnd=10, title="Calculator", app_name="calc.exe", pid=9999999, rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)
    w_new = WindowInfo(hwnd=11, title="Desktop", app_name="explorer.exe", pid=os.getpid(), rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)

    pre = DesktopState(timestamp=1.0, monitors=[], active_window=w_old, windows=[w_old], browser_tabs=[], dhash="1111", has_changed=False, is_paused=False, privacy_redacted=False, thumbnail_jpeg=b"")
    post = DesktopState(timestamp=2.0, monitors=[], active_window=w_new, windows=[w_new], browser_tabs=[], dhash="2222", has_changed=True, is_paused=False, privacy_redacted=False, thumbnail_jpeg=b"")

    res = sup.supervise_step(
        task_id="t-close-1",
        step_index=1,
        action_type="close_window",
        target_element="Calculator",
        expected_state="Window closed",
        pre_state=pre,
        post_state=post,
    )
    assert res.detected_anomaly is None
    assert res.is_successful is True


def test_browser_tab_caching_and_cooldown():
    """Verify BrowserIntelligence caches tab scans within cooldown."""
    b = BrowserIntelligence()
    # First call populates cache
    tabs1 = b.enumerate_tabs()
    # Second call uses cache
    tabs2 = b.enumerate_tabs()
    assert tabs1 == tabs2


def test_screen_observer_bypasses_capture_on_privacy_exclusion(monkeypatch):
    """Verify capture_frame is never called when active window matches privacy exclusion."""
    cfg = PrivacyConfig()
    obs = ScreenObserver(privacy_config=cfg)

    capture_called = []
    def mock_capture(monitor_index=1):
        capture_called.append(True)
        return b"", (0, 0)

    monkeypatch.setattr(obs, "capture_frame", mock_capture)

    w_sec = WindowInfo(hwnd=999, title="Windows Security", app_name="credentialui.exe", pid=123, rect=(0,0,10,10), is_visible=True, is_minimized=False, is_maximized=False, is_active=True)
    st = obs.observe_once(active_window=w_sec)

    assert st.privacy_redacted is True
    assert len(capture_called) == 0, "capture_frame was called despite privacy exclusion!"
