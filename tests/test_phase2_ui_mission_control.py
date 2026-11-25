"""Phase 2 Stage 3: OPTIMUS Mission-Control UI Backend-State Sync Tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.orchestrator.events import EventBus
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import ExecutionPlan, Subtask, TaskStatus, TaskStore
from gui.optimus.dashboard import OptimusDashboardView
from gui.optimus.service_bridge import OptimusServiceBridge


def _make_orch_with_task(tmp_path: Path, status: TaskStatus, *, retry_count: int = 0, error: str | None = None):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    st = tmp_path / "store"
    st.mkdir(exist_ok=True)
    orch = CentralOrchestrator(task_store=TaskStore(st), event_bus=EventBus(), allowed_roots=[str(ws)])
    plan_fn = lambda g, tid: ExecutionPlan(
        plan_id=f"plan_{tid}",
        task_id=tid,
        goal=g,
        subtasks=[
            Subtask(
                subtask_id=f"sub_{tid}_1",
                task_id=tid,
                index=1,
                title="Inspect inbox",
                description=g,
                assigned_agent="personal_intelligence",
                allowed_tools=[],
            )
        ],
    )
    with patch.object(orch, "_try_fast_path", return_value=None):
        with patch.object(orch, "plan", side_effect=plan_fn):
            if status == TaskStatus.WAITING_FOR_APPROVAL:
                with patch.object(orch, "_execute_subtask", return_value=(False, "[CONFIRMATION_PENDING] wait", [])):
                    rec = orch.execute_task("Send email to Alice")
            elif status == TaskStatus.FAILED:
                with patch.object(orch, "_execute_subtask", return_value=(False, "Permission denied", [])):
                    rec = orch.execute_task("Protected task")
            else:
                with patch.object(orch, "_execute_subtask", return_value=(True, "Done", [])):
                    with patch.object(orch, "_verify_deliverables", return_value=(True, "Verified", {})):
                        rec = orch.execute_task("Read inbox")
    if retry_count or error is not None:
        rec.status = status
        rec.current_stage = status.value
        rec.retry_count = retry_count
        if error is not None:
            rec.error = error
        orch.task_store.save_task(rec)
    return orch, rec


def test_dashboard_displays_real_waiting_recovery_and_failure_states(tmp_path: Path):
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    orch, rec = _make_orch_with_task(tmp_path, TaskStatus.WAITING_FOR_APPROVAL)
    OptimusServiceBridge.reset()
    bridge = OptimusServiceBridge.instance()
    bridge.orchestrator = orch
    bridge._active_task_id = rec.task_id

    dash = OptimusDashboardView(bridge)
    try:
        dash.sync_authoritative_task_state()
        assert dash.cmd_status_lbl.text() == "WAITING FOR APPROVAL"
        assert "Send email to Alice" in dash.active_goal_lbl.text()
        assert dash.wf_dag_layout.count() >= 1

        rec.status = TaskStatus.RECOVERING
        rec.current_stage = "RECOVERING"
        rec.retry_count = 1
        orch.task_store.save_task(rec)
        dash.sync_authoritative_task_state()
        assert dash.cmd_status_lbl.text() == "RECOVERING (1/2)"
        assert "[retry 1/2]" in dash.active_goal_lbl.text()

        rec.status = TaskStatus.FAILED
        rec.current_stage = "FAILED"
        rec.error = "Upstream 503"
        orch.task_store.save_task(rec)
        dash.sync_authoritative_task_state()
        assert dash.cmd_status_lbl.text() == "FAILED"
        assert "Upstream 503" in dash.active_goal_lbl.text()
    finally:
        dash.close()
        dash.deleteLater()
        app.processEvents()
        OptimusServiceBridge.reset()
