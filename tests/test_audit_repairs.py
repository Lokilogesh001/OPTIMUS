"""tests/test_audit_repairs.py — Regression & Phase 5 scenario tests for Full-System Audit repairs."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_application_registry_non_blocking_during_slow_discovery(tmp_path):
    """AUDIT-DEF-01: list_all_apps and list_running_apps must NOT block while refresh() runs slow discovery."""
    from core.application_intelligence.registry import ApplicationRegistry
    from core.application_intelligence.types import AppCategory, AppDefinition

    cache_file = tmp_path / "app_cache.json"
    reg = ApplicationRegistry(cache_file=cache_file)
    reg._register_internal(AppDefinition(app_id="app.notepad", name="Notepad", category=AppCategory.SYSTEM_UTILITY))

    discovery_started = threading.Event()
    release_discovery = threading.Event()

    def slow_discover():
        discovery_started.set()
        release_discovery.wait(timeout=3.0)
        return {"app.calc": AppDefinition(app_id="app.calc", name="Calculator", category=AppCategory.SYSTEM_UTILITY)}

    with (
        patch("core.application_intelligence.registry.discover_all_installed_applications", side_effect=slow_discover),
        patch("core.application_intelligence.registry.discover_running_processes_and_windows", return_value=[]),
    ):
        bg = threading.Thread(target=lambda: reg.refresh(force=True), daemon=True)
        bg.start()
        assert discovery_started.wait(timeout=2.0)

        t0 = time.perf_counter()
        apps = reg.list_all_apps(auto_refresh=False)
        running = reg.list_running_apps(auto_refresh=False)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        release_discovery.set()
        bg.join(timeout=3.0)

    assert elapsed_ms < 25.0, f"list_all_apps blocked on refresh lock ({elapsed_ms:.1f} ms)"
    assert any(a.app_id == "app.notepad" for a in apps)
    assert isinstance(running, list)


def test_section_applications_signal_and_close_while_scanning(qapp, tmp_path):
    """AUDIT-DEF-01: SectionApplications renders via Qt signal without freezing and closes safely mid-scan."""
    from core.application_intelligence.registry import ApplicationRegistry
    from gui.optimus.section_applications import SectionApplications

    release_scan = threading.Event()
    with patch.object(ApplicationRegistry, "refresh", side_effect=lambda force=False: release_scan.wait(timeout=2.0)):
        sec = SectionApplications()
        sec.refresh_all(force=True)
        qapp.processEvents()
        sec.close()
        assert sec._is_closing is True
        release_scan.set()
        time.sleep(0.05)
        qapp.processEvents()
        sec.deleteLater()


def test_section_personal_oauth_signals_exist(qapp):
    """AUDIT-DEF-02: SectionPersonal uses thread-safe pyqtSignals instead of QTimer.singleShot from worker threads."""
    from gui.optimus.section_personal import SectionPersonal

    sec = SectionPersonal()
    assert hasattr(sec, "_oauth_succeeded")
    assert hasattr(sec, "_oauth_failed")
    sec.close()
    sec.deleteLater()


def test_devtools_adapter_blocks_prohibited_commands():
    """AUDIT-DEF-03: DevToolsAdapter.execute_bounded_command blocks dangerous commands via CommandGuard."""
    from core.application_intelligence.adapters.devtools import DevToolsAdapter

    adapter = DevToolsAdapter()
    res = adapter.execute_bounded_command("rm -rf /")
    assert res["is_success"] is False
    assert "COMMAND_PROHIBITED" in res["stderr"]


def test_ollama_list_models_fast_timeout_fallback():
    """AUDIT-DEF-04: OllamaProvider.list_models uses fast <=0.5s timeout and falls back cleanly."""
    from core.intelligence.providers.ollama import OllamaProvider

    seen_timeouts = []

    def fake_transport(url, data, headers, timeout):
        seen_timeouts.append(timeout)
        raise ConnectionRefusedError("offline")

    prov = OllamaProvider(transport=fake_transport)
    models = prov.list_models()
    assert len(models) >= 1
    assert seen_timeouts and seen_timeouts[0] <= 0.5


def test_control_center_all_22_sections_open_and_navigate(qapp):
    """Phase 5: Open all 22 Control Center sections and test repeated navigation."""
    from gui.optimus.control_center import OptimusControlCenterWidget

    cc = OptimusControlCenterWidget()
    assert len(cc._SECTION_REGISTRY) == 22
    for idx in range(22):
        w = cc._get_or_create_section(idx)
        assert w is not None
        cc.stack.setCurrentIndex(idx)
        qapp.processEvents()
    # Repeated navigation back and forth
    for idx in (0, 18, 5, 13, 21, 0):
        cc.stack.setCurrentIndex(idx)
        qapp.processEvents()
        assert cc.stack.currentIndex() == idx
    cc.close()
    cc.deleteLater()
    qapp.processEvents()


def test_execution_evidence_bidirectional_status_verdict():
    """AUDIT-DEF-05: ExecutionEvidence.__post_init__ preserves explicit status and syncs verdict."""
    from core.application_intelligence.types import ExecutionEvidence, ExecutionVerdict

    ev_fail = ExecutionEvidence(action_id="a1", status="failed")
    assert ev_fail.status == "failed"
    assert ev_fail.verdict == ExecutionVerdict.FAILED
    assert ev_fail.is_success is False

    ev_unverified = ExecutionEvidence(action_id="a2", status="unverified")
    assert ev_unverified.status == "unverified"
    assert ev_unverified.verdict == ExecutionVerdict.ACTION_EXECUTED_UNVERIFIED
    assert ev_unverified.is_success is False

    ev_blocked = ExecutionEvidence(action_id="a3", verdict=ExecutionVerdict.BLOCKED)
    assert ev_blocked.status == "blocked"
    assert ev_blocked.verdict == ExecutionVerdict.BLOCKED
    assert ev_blocked.is_success is False

