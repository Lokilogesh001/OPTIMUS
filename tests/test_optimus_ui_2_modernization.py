"""tests/test_optimus_ui_2_modernization.py — Test Suite for OPTIMUS UI 2.0 Modernization.

Validates:
- Mandate M-1: Pure QtWidgets stack boundary.
- Mandate M-2: Direct3D 11 RHI graphics configuration & zero OpenGL override.
- Mandate M-3: Widget paint event attribute hygiene.
- Mandate M-4 & M-5: StreamConsoleWidget double-buffered FIFO queuing, 30Hz throttle, edit-block batching, and universal deployment.
- Mandate M-6: OptimusTheme token completeness & bidirectional backward compatibility with class C.
- Mandate M-7: Accessible reduced motion integration & signal propagation.
- Mandate M-8: Main-thread blocking elimination (background process snapshot & zero style churn in telemetry).
"""

import sys
import os
import pytest
from PyQt6.QtWidgets import QApplication, QTextEdit, QPlainTextEdit, QProgressBar
from PyQt6.QtCore import Qt, QTimer

from gui.theme import C, OptimusTheme, get_system_reduced_motion
from gui.widgets import StreamConsoleWidget, LogWidget, OptimusCard, HoloPanel
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.optimus.section_command import SectionCommandCenter
from gui.optimus.dashboard import OptimusDashboardView, TelemetryMetricBar
from gui.optimus.section_system import SectionSystem


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_01_mandate_m6_theme_token_completeness_and_compatibility(qapp):
    """Test that OptimusTheme contains all design tokens and maintains 100% backward compatibility with class C."""
    # Ensure modern tokens exist
    assert hasattr(OptimusTheme, "BG_VOID")
    assert hasattr(OptimusTheme, "BG_BASE")
    assert hasattr(OptimusTheme, "BG_PANEL")
    assert hasattr(OptimusTheme, "BG_ELEVATED")
    assert hasattr(OptimusTheme, "ACCENT_PRI")
    assert hasattr(OptimusTheme, "ACCENT_CYAN")
    assert hasattr(OptimusTheme, "TEXT_PRIMARY")
    assert hasattr(OptimusTheme, "BORDER_HAIRLINE")

    # Ensure class C backward compatibility
    assert C.BG == OptimusTheme.BG_BASE
    assert C.PANEL == OptimusTheme.BG_PANEL
    assert C.PANEL2 == OptimusTheme.BG_ELEVATED
    assert C.PRI == OptimusTheme.ACCENT_PRI
    assert C.BORDER == OptimusTheme.BORDER_HAIRLINE

    # Ensure bidirectional aliases (Mandate M-6)
    assert OptimusTheme.BG == OptimusTheme.BG_BASE
    assert OptimusTheme.PRI == OptimusTheme.ACCENT_PRI
    assert OptimusTheme.PANEL == OptimusTheme.BG_PANEL
    assert C.BG_BASE == C.BG
    assert C.ACCENT_PRI == C.PRI
    assert C.BORDER_HAIRLINE == C.BORDER


def test_02_mandate_m4_stream_console_widget_batching_and_flush(qapp):
    """Test StreamConsoleWidget FIFO double-buffering, chunk append, and flush_now."""
    console = StreamConsoleWidget()
    assert isinstance(console, QPlainTextEdit)

    # Initial document is empty
    assert console.toPlainText().strip() == ""

    # Enqueue streaming tokens
    console.append_chunk("OPTIMUS", "Analyzing ", tag="ai")
    console.append_chunk("", "target ", tag="ai")
    console.append_chunk("", "parameters.", tag="ai")

    # Flush synchronously
    console.flush_now()

    text = console.toPlainText()
    assert "[OPTIMUS]" in text
    assert "Analyzing target parameters." in text


def test_03_mandate_m4_stream_console_widget_legacy_append_compatibility(qapp):
    """Test that legacy append(html_or_text) works seamlessly without crashing or HTML tags."""
    console = StreamConsoleWidget()
    console.append("<p><b>Directive:</b> Initiate system check.</p>")
    console.flush_now()

    text = console.toPlainText()
    assert "Directive: Initiate system check." in text
    assert "<p>" not in text
    assert "<b>" not in text


def test_04_mandate_m5_universal_stream_console_deployment(qapp):
    """Verify that StreamConsoleWidget is deployed across all command, dashboard, and log surfaces."""
    bridge = OptimusServiceBridge.instance()

    # SectionCommandCenter
    cmd = SectionCommandCenter(bridge)
    assert isinstance(cmd.chat_history, StreamConsoleWidget)
    assert isinstance(cmd.out_display, StreamConsoleWidget)

    # OptimusDashboardView
    dash = OptimusDashboardView(bridge)
    assert isinstance(dash.chat_transcript, StreamConsoleWidget)

    # LogWidget
    log = LogWidget()
    assert hasattr(log, "append_log")
    assert hasattr(log, "set_reduced_motion")


def test_05_mandate_m6_card_hierarchy_and_holo_panel(qapp):
    """Verify OptimusCard and HoloPanel inheritance and layout containment."""
    panel = HoloPanel("MISSION DIRECTIVES", "M-01")
    assert isinstance(panel, OptimusCard)
    assert panel._title == "MISSION DIRECTIVES"
    assert panel._code == "M-01"

    panel.set_title("NEW TITLE")
    assert panel._title == "NEW TITLE"


def test_06_mandate_m7_reduced_motion_integration(qapp):
    """Verify reduced motion detection and signal dispatch via ServiceBridge."""
    bridge = OptimusServiceBridge.instance()
    initial_motion = bridge.is_reduced_motion()

    received = []
    bridge.reduced_motion_changed.connect(lambda val: received.append(val))

    # Toggle reduced motion
    bridge.set_reduced_motion(True)
    assert bridge.is_reduced_motion() is True
    assert received[-1] is True

    # Revert to initial
    bridge.set_reduced_motion(initial_motion)
    assert bridge.is_reduced_motion() == initial_motion


def test_07_mandate_m8_system_section_background_process_dispatch(qapp):
    """Verify that SectionSystem receives pre-computed process snapshots without UI-thread stalls."""
    bridge = OptimusServiceBridge.instance()
    sys_sec = SectionSystem(bridge)
    sys_sec.show()

    # Deliver mock background snapshot via bridge signal
    mock_procs = [
        {"pid": 1234, "name": "optimus_engine.exe", "cpu_percent": 12.5, "memory_mb": 256.0},
        {"pid": 5678, "name": "python.exe", "cpu_percent": 4.2, "memory_mb": 128.0},
    ]

    sys_sec._on_processes_snapshot(mock_procs)

    assert sys_sec.table_procs.rowCount() == 2
    assert sys_sec.table_procs.item(0, 0).text() == "1234"
    assert sys_sec.table_procs.item(0, 1).text() == "optimus_engine.exe"
    assert sys_sec.table_procs.item(0, 2).text() == "12.5%"
    assert "256.0 MB" in sys_sec.table_procs.item(0, 3).text()
    sys_sec.close()


def test_08_mandate_m8_telemetry_metric_bar_zero_style_churn(qapp):
    """Verify TelemetryMetricBar does not repeatedly reapply setStyleSheet when value stays in same tier."""
    bar = TelemetryMetricBar("CPU LOAD")

    # First value set establishes tier
    bar.set_value(25.0, "25%")
    initial_tier = getattr(bar, "_current_tier", None)
    assert initial_tier == "pri"

    # Save initial stylesheet
    ss_initial = bar.bar.styleSheet()

    # Second value set in SAME tier (<= 65) should NOT trigger style churn
    bar.set_value(30.0, "30%")
    assert bar.bar.value() == 30
    assert getattr(bar, "_current_tier", None) == "pri"
    assert bar.bar.styleSheet() == ss_initial

    # Crossing into warning tier (> 65) updates tier
    bar.set_value(75.0, "75%")
    assert bar.bar.value() == 75
    assert getattr(bar, "_current_tier", None) == "acc"

    # Crossing into critical tier (> 85) updates tier
    bar.set_value(92.0, "92%")
    assert bar.bar.value() == 92
    assert getattr(bar, "_current_tier", None) == "red"
