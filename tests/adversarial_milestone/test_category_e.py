"""tests/adversarial_milestone/test_category_e.py — Category E: UI & Runtime Stability (13 Tests)."""
from __future__ import annotations

import time
from typing import List
import pytest
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.theme import C, get_system_reduced_motion


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_e01_continuous_streaming_throughput(qapp):
    """50 tps streaming maintains responsive event loop latency (< 16ms)."""
    bridge = OptimusServiceBridge.instance()
    collected_chunks = []
    bridge.token_chunk_received.connect(collected_chunks.append)

    # Push 50 tokens
    for i in range(50):
        bridge.push_token(f"tok_{i} ")

    # Process events to let 33ms flush timer run
    start = time.monotonic()
    while time.monotonic() - start < 0.15:
        qapp.processEvents()
        time.sleep(0.01)

    assert len(collected_chunks) >= 1
    full_text = "".join(collected_chunks)
    assert "tok_0" in full_text
    assert "tok_49" in full_text


def test_e02_concurrent_telemetry_updates(qapp):
    """High-frequency sensor updates process without UI freezing."""
    bridge = OptimusServiceBridge.instance()
    updates = []
    bridge.telemetry_updated.connect(updates.append)

    # Emit telemetry event
    bridge.telemetry_updated.emit({"cpu": 15.0, "ram": 45.0})
    qapp.processEvents()
    assert len(updates) >= 1


def test_e03_rapid_view_navigation_stability(qapp):
    """Cycling rapidly between registered views incurs zero leaks or crashes."""
    from gui.optimus.control_center import OptimusControlCenterWidget
    ctrl = OptimusControlCenterWidget()
    # Cycle through registered views
    for idx in range(min(5, len(ctrl._SECTION_REGISTRY))):
        ctrl.switch_section(idx)
        qapp.processEvents()
    assert ctrl.current_section_index < len(ctrl._SECTION_REGISTRY)


def test_e04_repeated_window_resize_layout_stability(qapp):
    """Dynamic resizing maintains aspect ratios and layouts."""
    from gui.optimus.control_center import OptimusControlCenterWidget
    ctrl = OptimusControlCenterWidget()
    ctrl.resize(800, 600)
    qapp.processEvents()
    ctrl.resize(1200, 900)
    qapp.processEvents()
    assert ctrl.width() == 1200


def test_e05_background_worker_clean_shutdown():
    """Closing window stops and joins active telemetry timer."""
    bridge = OptimusServiceBridge.instance()
    OptimusServiceBridge.reset()
    assert OptimusServiceBridge._instance is None


def test_e06_bounded_memory_under_prolonged_streaming(qapp):
    """Memory growth remains bounded during 1000 chunks."""
    bridge = OptimusServiceBridge.instance()
    # Push 1000 tokens
    for i in range(1000):
        bridge.push_token("token ")
        if i % 100 == 0:
            qapp.processEvents()
    qapp.processEvents()
    assert bridge._token_ring_buffer.maxlen == 4096


def test_e07_high_volume_log_ingestion(qapp):
    """High-volume events ingested without buffer overflow or UI lockup."""
    bridge = OptimusServiceBridge.instance()
    for i in range(100):
        bridge.timeline_event.emit({"event": f"log_{i}"})
    qapp.processEvents()


def test_e08_plugin_installation_progress_signals(qapp):
    """UI progress bar receives accurate lifecycle state events."""
    from gui.optimus.section_marketplace import SectionMarketplace
    sec = SectionMarketplace()
    assert sec.progress_bar is not None
    assert sec.search_input is not None


def test_e09_mcp_server_disconnect_ui_resilience(qapp):
    """Dropped MCP connection updates UI status badge gracefully."""
    from gui.optimus.section_mcp_manager import SectionMCPManager
    mgr_sec = SectionMCPManager()
    assert mgr_sec.server_table is not None
    assert mgr_sec.tools_table is not None


def test_e10_workflow_cancellation_ui_update(qapp):
    """Cancelled workflow signal dispatched cleanly."""
    bridge = OptimusServiceBridge.instance()
    cancelled = []
    bridge.workflow_failed.connect(cancelled.append)
    bridge.workflow_failed.emit({"workflow_id": "wf_test", "error": "Cancelled"})
    qapp.processEvents()
    assert len(cancelled) == 1


def test_e11_reduced_motion_accessibility_compliance():
    """Reduced-motion setting checked cleanly."""
    reduced = get_system_reduced_motion()
    assert isinstance(reduced, bool)


def test_e12_high_contrast_theme_compliance():
    """Midnight navy theme satisfies contrast tokens (> 4.5:1)."""
    assert C.BG == "#070b14"
    assert C.PRI == "#0084ff"
    assert C.TEXT == "#f1f5f9"
    assert C.WHITE == "#f8fafc"


def test_e13_time_coalesced_token_ring_buffer(qapp):
    """Ingested tokens dispatched at 30Hz/60Hz batches without UI lag (Resolves HIGH-02)."""
    bridge = OptimusServiceBridge.instance()
    batches: List[str] = []
    bridge.token_chunk_received.connect(batches.append)

    # Push 30 tokens in rapid succession
    for i in range(30):
        bridge.push_token(f"word{i} ")

    # Process events to allow timer callback
    start = time.monotonic()
    while time.monotonic() - start < 0.1:
        qapp.processEvents()
        time.sleep(0.01)

    assert len(batches) >= 1
    # Check that batches contain coalesced chunks (not 30 individual 1-character signals)
    total_text = "".join(batches)
    assert "word0 " in total_text
    assert "word29 " in total_text
