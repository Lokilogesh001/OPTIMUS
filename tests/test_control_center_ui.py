"""tests/test_control_center_ui.py — Comprehensive Test Suite for OPTIMUS Component 7.

Validates the complete OPTIMUS Control Center UI subsystem:
1. Thread-safe Service Bridge initialization, singleton guarantee, and subsystem wiring.
2. Real-time telemetry gathering across CPU, RAM, storage, and processes.
3. EventBus-to-Qt signal bridge dispatching events across thread boundaries.
4. Navigation bar tab switching, dynamic approval badges, and health badges.
5. Home Dashboard view with all 6 real-time modules active and populated.
6. Command Center directive dispatch, task store tracking, and history updates.
7. Workflows section DAG step inspection, parameter inputs, and execution tracking.
8. Specialized Agents roster, domain profiles, CBAC tiering, and tool whitelists.
9. Model Intelligence gateway controls, provider health cards, and routing policy switching.
10. Memory subsystem search, category filtering, entry viewer, and Context Bus channel monitoring.
11. Verification & Physical Postcondition Ledger with cryptographic SHA-256 chain verification.
12. Approvals section human authorization gates, anti-LLM validation, and sign-off dispatch.
13. System Monitor gauges, per-core CPU bars, and active process inspection.
14. Settings section local-only privacy toggling and dynamic theme accent updates.
15. OptimusControlCenterWidget multi-view container navigation and stacked widget synchronization.
16. MainWindow and JarvisUI integration, F2 hotkey toggling, and Classic HUD compatibility.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

# Force offscreen rendering for headless execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

import pytest
from PyQt6.QtWidgets import QApplication

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.intelligence.types import RoutingPolicy
from core.orchestrator.events import TaskCreatedEvent
from core.orchestrator.runtime import TaskRecord, TaskStatus
from core.verification.types import VerificationRecord, VerificationStatus
from core.workflows.events import WorkflowStartedEvent
from core.workflows.types import (
    ApprovalStatus,
    WorkflowDefinition,
    WorkflowStepDefinition,
)
from gui.optimus import (
    OptimusAgentsSection,
    OptimusApprovalsSection,
    OptimusCommandSection,
    OptimusControlCenterWidget,
    OptimusDashboardView,
    OptimusIntelligenceSection,
    OptimusMemorySection,
    OptimusNavBar,
    OptimusServiceBridge,
    OptimusSettingsSection,
    OptimusSystemSection,
    OptimusVerificationSection,
    OptimusWorkflowsSection,
)


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication instance for headless Qt testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "-platform", "offscreen"])
    yield app


@pytest.fixture
def bridge(qapp):
    """Fixture providing the OptimusServiceBridge singleton."""
    b = OptimusServiceBridge.instance()
    # Force event processing
    qapp.processEvents()
    return b


def test_service_bridge_initialization_and_singleton(bridge, qapp):
    """Test 1: Verify service bridge initializes as singleton with all OPTIMUS subsystems."""
    b2 = OptimusServiceBridge.instance()
    assert bridge is b2

    assert bridge.orchestrator is not None
    assert bridge.event_bus is not None
    assert bridge.workflow_engine is not None
    assert bridge.intelligence_gateway is not None
    assert bridge.memory_manager is not None
    assert bridge.verification_engine is not None


def test_service_bridge_real_telemetry_query(bridge, qapp):
    """Test 2: Verify service bridge queries actual system telemetry via psutil."""
    received = []
    bridge.telemetry_updated.connect(lambda data: received.append(data))

    bridge._poll_telemetry()
    qapp.processEvents()

    assert len(received) >= 1
    data = received[-1]
    assert "cpu_pct" in data
    assert "ram_pct" in data
    assert "storage_pct" in data
    assert "processes" in data
    assert "app_health" in data
    assert isinstance(data["processes"], list)
    assert data["app_health"] in ("HEALTHY", "DEGRADED", "CRITICAL")


def test_service_bridge_event_to_qt_signal_dispatch(bridge, qapp):
    """Test 3: Verify bridge converts background EventBus events into Qt signals."""
    task_signals = []
    wf_signals = []
    bridge.task_created.connect(lambda t: task_signals.append(t))
    bridge.workflow_started.connect(lambda w: wf_signals.append(w))

    bridge.event_bus.publish(TaskCreatedEvent(task_id="task_test_signal_01", goal="Test signal dispatch"))
    bridge.event_bus.publish(WorkflowStartedEvent(execution_id="exec_test_01", workflow_id="wf_test_signal_01"))

    # Process pending Qt events from bridge signals
    qapp.processEvents()

    assert len(task_signals) >= 1
    assert task_signals[-1]["task_id"] == "task_test_signal_01"
    assert len(wf_signals) >= 1
    assert wf_signals[-1]["workflow_id"] == "wf_test_signal_01"
    assert wf_signals[-1]["workflow_id"] == "wf_test_signal_01"


def test_nav_bar_tab_selection_and_approval_badge(qapp):
    """Test 4: Verify navigation bar handles tab changes, active styling, and badges."""
    nav = OptimusNavBar()
    nav.show()
    qapp.processEvents()

    selected_tabs = []
    nav.tab_selected.connect(lambda t: selected_tabs.append(t))

    # Test selecting each tab programmatically
    nav.select_tab("workflows")
    assert nav.active_tab == "workflows"
    assert "workflows" in selected_tabs

    nav.select_tab("memory")
    assert nav.active_tab == "memory"
    assert "memory" in selected_tabs

    # Test approval badge counter
    nav.set_approval_badge(4)
    assert nav._approval_badge.isVisible()
    assert nav._approval_badge.text() == "4"

    nav.set_approval_badge(0)
    assert not nav._approval_badge.isVisible()

    # Test health badge
    nav.update_health("HEALTHY", 5)
    assert "HEALTHY" in nav._health_badge.text()
    assert "5 AGENTS" in nav._health_badge.text()


def test_dashboard_view_all_six_modules_populated(bridge, qapp):
    """Test 5: Verify home dashboard renders and populates all 6 real-time modules."""
    dash = OptimusDashboardView(bridge)
    qapp.processEvents()

    # Module 1: Command Center
    assert hasattr(dash, "cmd_input")
    assert hasattr(dash, "btn_dispatch")

    # Module 2: Agent Activity
    assert hasattr(dash, "table_agents")
    assert dash.agents_lay.count() >= 1

    # Module 3: Workflow Monitor
    assert hasattr(dash, "table_workflows")

    # Module 4: Intelligence Monitor
    assert hasattr(dash, "provider_status_lbl")
    assert hasattr(dash, "active_model_lbl")

    # Module 5: System Monitor
    assert hasattr(dash, "gauge_cpu")
    assert hasattr(dash, "gauge_ram")

    # Module 6: Execution Timeline
    assert hasattr(dash, "table_timeline")

    # Test live telemetry update propagation to dashboard
    dash._on_telemetry_updated({
        "cpu_pct": 25.0,
        "ram_pct": 55.0,
        "storage_pct": 42.0,
        "app_health": "HEALTHY",
        "processes": [],
    })
    qapp.processEvents()
    assert dash.gauge_cpu.value() == 25
    assert dash.gauge_ram.value() == 55


def test_command_center_section_dispatch_and_history(bridge, qapp):
    """Test 6: Verify command section dispatches directives and reflects execution history."""
    cmd_sec = OptimusCommandSection(bridge)
    qapp.processEvents()

    # Submit a directive
    cmd_sec.cmd_input.setText("Inspect system disk storage")
    cmd_sec._dispatch_command()
    qapp.processEvents()

    # Verify input cleared and activity logged
    assert cmd_sec.cmd_input.text() == ""
    assert "DISPATCH" in cmd_sec.output_console.toPlainText()

    # Force task update signal and verify history table population
    t = TaskRecord(
        task_id="task_cmd_sec_01",
        goal="Inspect system disk storage",
        status=TaskStatus.COMPLETED,
        result="350 GB free",
    )
    bridge.orchestrator.task_store.save_task(t)
    cmd_sec._refresh_task_history()
    qapp.processEvents()

    assert cmd_sec.table_history.rowCount() >= 1


def test_workflows_section_dag_inspection(bridge, qapp):
    """Test 7: Verify workflows section renders DAG, step inspector, and controls."""
    wf_sec = OptimusWorkflowsSection(bridge)
    qapp.processEvents()

    # Register a test workflow definition
    wf_def = WorkflowDefinition(
        workflow_id="wf_ui_test_01",
        name="UI Pipeline Test",
        description="Verifies workflow DAG UI rendering",
        steps=[
            WorkflowStepDefinition(step_id="step_a", name="Step Alpha", assigned_agent="central_orchestrator"),
            WorkflowStepDefinition(step_id="step_b", name="Step Beta", assigned_agent="code_intel", depends_on=["step_a"]),
        ],
    )
    bridge.workflow_engine.store.save_definition(wf_def)

    wf_sec.refresh_catalog()
    qapp.processEvents()

    # Select definition in table
    assert wf_sec.table_defs.rowCount() >= 1
    target_row = 0
    for r in range(wf_sec.table_defs.rowCount()):
        it = wf_sec.table_defs.item(r, 0)
        if it and it.text() == "wf_ui_test_01":
            target_row = r
            break
    item = wf_sec.table_defs.item(target_row, 0)
    wf_sec._on_workflow_selected(item)
    qapp.processEvents()

    assert wf_sec._selected_def_id == "wf_ui_test_01"
    assert "Alpha" in wf_sec.dag_view.toPlainText()
    assert "Beta" in wf_sec.dag_view.toPlainText()


def test_agents_section_roster_and_cbac_tools(bridge, qapp):
    """Test 8: Verify agents section displays specialized roster and CBAC tool whitelists."""
    ag_sec = OptimusAgentsSection(bridge)
    qapp.processEvents()

    assert ag_sec.table_agents.rowCount() >= 5

    # Select row 0 (central_orchestrator or code_intel)
    item = ag_sec.table_agents.item(0, 0)
    ag_sec._on_agent_selected(item)
    qapp.processEvents()

    assert ag_sec._selected_agent is not None
    assert ag_sec.lbl_agent_name.text() != ""
    assert ag_sec.system_instructions_edit.toPlainText() != ""
    assert ag_sec.table_tools.rowCount() >= 1


def test_intelligence_section_providers_and_circuit_breaker(bridge, qapp):
    """Test 9: Verify intelligence section displays providers, models, and routing policy."""
    intel_sec = OptimusIntelligenceSection(bridge)
    qapp.processEvents()

    # Providers list populated
    assert len(intel_sec.provider_cards) >= 4
    assert intel_sec.table_models.rowCount() >= 1

    # Switch routing policy
    intel_sec.policy_combo.setCurrentText("PERFORMANCE_OPTIMIZED")
    qapp.processEvents()

    assert bridge.intelligence_gateway.router.policy == RoutingPolicy.PERFORMANCE_OPTIMIZED


def test_memory_section_search_and_channels(bridge, qapp):
    """Test 10: Verify memory section searches records and displays partition channels."""
    mem_sec = OptimusMemorySection(bridge)
    qapp.processEvents()

    # Store a test memory record
    bridge.memory_manager.remember_semantic(
        key="ui_test_token",
        content="Optimus UI verification test key token",
        tags=["ui", "test"],
    )

    mem_sec.search_input.setText("token")
    mem_sec.on_search()
    qapp.processEvents()

    assert mem_sec.table_memories.rowCount() >= 1
    first_cell = mem_sec.table_memories.item(0, 3)
    assert first_cell is not None
    assert "token" in first_cell.text()


def test_verification_section_cryptographic_audit(bridge, qapp):
    """Test 11: Verify verification section inspects evidence and checks cryptographic chain."""
    ver_sec = OptimusVerificationSection(bridge)
    qapp.processEvents()

    # Record a verification entry
    rec = VerificationRecord(
        evidence_id="rec_ui_test_01",
        task_id="task_ui_test_01",
        subtask_id="subtask_ui_test_01",
        verifier_identity="data_verifier",
        status=VerificationStatus.PASS,
        verdict="Status code 200 matches expected schema",
        expected_postcondition={"schema": "ok"},
        actual_observed_result={"status": "ok"},
    )
    bridge.verification_engine.ledger.record(rec)

    ver_sec.refresh_ledger()
    qapp.processEvents()

    assert ver_sec.table_records.rowCount() >= 1

    # Verify cryptographic integrity
    ver_sec._verify_integrity()
    qapp.processEvents()

    assert "PASSED" in ver_sec.status_lbl.text()
    assert "CRYPTOGRAPHIC INTEGRITY: VALID" in ver_sec.status_lbl.text()


def test_approvals_section_role_check_and_anti_llm(bridge, qapp):
    """Test 12: Verify approvals section enforces anti-LLM rules and resolves human sign-offs."""
    app_sec = OptimusApprovalsSection(bridge)
    app_sec.show()
    qapp.processEvents()

    # Request an approval
    decision = bridge.workflow_engine.approval_manager.request_approval(
        workflow_id="wf_app_sec_01",
        execution_id="exec_app_sec_01",
        step_id="deploy_production",
        required_role="security_admin",
        reason="Deploy code to production",
        timeout_seconds=300.0,
    )
    approval_id = decision.approval_id

    app_sec.refresh_approvals()
    qapp.processEvents()

    assert app_sec.table_approvals.rowCount() >= 1
    assert app_sec._selected_approval_id == approval_id

    # Test anti-LLM validation: attempt approval with AI string
    app_sec.approver_input.setText("gpt-4-system-agent")
    app_sec.reason_input.setText("Automated sign-off")
    app_sec._do_approve()
    qapp.processEvents()

    # Must be blocked client-side
    assert not app_sec.error_banner.isHidden()
    assert "Automated LLM" in app_sec.error_banner.text()

    # Now approve with legitimate human name
    app_sec.approver_input.setText("Alice (SecOps Lead)")
    app_sec.reason_input.setText("Verified staging tests and physical ledger integrity")
    app_sec._do_approve()
    qapp.processEvents()
    time.sleep(0.05)
    qapp.processEvents()

    assert app_sec.error_banner.isHidden()
    # Check that approval resolved
    res = bridge.workflow_engine.approval_manager.get_approval(approval_id)
    assert res.status == ApprovalStatus.APPROVED
    assert res.approver == "Alice (SecOps Lead)"


def test_system_section_telemetry_and_processes(bridge, qapp):
    """Test 13: Verify system section telemetry gauges and active process explorer."""
    sys_sec = OptimusSystemSection(bridge)
    qapp.processEvents()

    # Emit telemetry update with sample processes
    sys_sec._on_telemetry({
        "cpu_pct": 32.5,
        "ram_pct": 68.0,
        "storage_pct": 51.2,
        "app_health": "HEALTHY",
        "cpu_cores": [25.0, 40.0, 15.0, 50.0],
        "processes": [
            {"pid": 1234, "name": "python.exe", "memory_mb": 185.4, "cpu_pct": 4.5},
            {"pid": 5678, "name": "optimus_worker.exe", "memory_mb": 92.1, "cpu_pct": 2.1},
        ],
    })
    qapp.processEvents()

    assert int(sys_sec.gauge_cpu.value()) == 32
    assert int(sys_sec.gauge_ram.value()) == 68
    assert int(sys_sec.gauge_storage.value()) == 51
    assert sys_sec.table_proc.rowCount() == 2


def test_settings_section_privacy_and_theme_accent(bridge, qapp):
    """Test 14: Verify settings section toggles local privacy and custom accents."""
    set_sec = OptimusSettingsSection(bridge)
    qapp.processEvents()

    # Toggle privacy mode
    set_sec.chk_privacy.setChecked(False)
    qapp.processEvents()
    set_sec.chk_privacy.setChecked(True)
    qapp.processEvents()

    assert bridge.intelligence_gateway.router.policy == RoutingPolicy.PRIVACY_STRICT

    # Test accent selection
    set_sec._on_accent_selected("#ff6b00")
    qapp.processEvents()
    # Restore default palette for test isolation
    from gui.theme import restore_default_palette
    restore_default_palette()
    qapp.processEvents()


def test_control_center_navigation_and_stack_switching(bridge, qapp):
    """Test 15: Verify OptimusControlCenterWidget manages all 10 views synchronously."""
    cc = OptimusControlCenterWidget()
    try:
        qapp.processEvents()

        assert cc.stack.count() >= 11

        # Test section navigation
        section_map = {
            "dashboard": 0,
            "command": 1,
            "workflows": 2,
            "agents": 3,
            "intelligence": 4,
            "memory": 5,
            "verification": 6,
            "approvals": 7,
            "system": 8,
            "settings": 9,
            "planning": 10,
        }
        if cc.stack.count() >= 13:
            section_map["developer"] = 11
            section_map["skills"] = 12

        for name, expected_idx in section_map.items():
            cc.show_section(name)
            qapp.processEvents()
            assert cc.stack.currentIndex() == expected_idx
            assert cc.nav_bar.active_tab == name
    finally:
        cc.close()
        cc.deleteLater()
        qapp.processEvents()


def test_main_window_integration_and_f2_mode_toggle(qapp):
    """Test 16: Verify MainWindow integrates OPTIMUS Control Center, F2 shortcut, and JarvisUI shim."""
    from gui.window import MainWindow
    from ui import JarvisUI

    win = MainWindow(face_path="")
    try:
        qapp.processEvents()

        assert hasattr(win, "_body_stack")
        assert win._body_stack.count() == 2
        assert win._body_stack.currentIndex() == 0  # Initial: Classic HUD

        # Switch to OPTIMUS
        win.show_optimus("workflows")
        qapp.processEvents()
        assert win._body_stack.currentIndex() == 1
        assert win._optimus_btn.isChecked() is True
        assert win._optimus_control_center.nav_bar.active_tab == "workflows"

        # Switch back to Classic HUD
        win.show_classic_hud()
        qapp.processEvents()
        assert win._body_stack.currentIndex() == 0
        assert win._optimus_btn.isChecked() is False

        # Test F2 toggle
        win._toggle_optimus_shortcut()
        qapp.processEvents()
        assert win._body_stack.currentIndex() == 1

        win._toggle_optimus_shortcut()
        qapp.processEvents()
        assert win._body_stack.currentIndex() == 0

        # Test JarvisUI shim access
        mock_ui = MagicMock(spec=JarvisUI)
        mock_ui._win = win
        assert JarvisUI.control_center.fget(mock_ui) is win._optimus_control_center
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
