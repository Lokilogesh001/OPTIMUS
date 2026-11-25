"""Phase 2 Stage 2: Task Execution Reliability & UI-Backend State Parity Tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import (
    ExecutionPlan,
    Subtask,
    SubtaskStatus,
    TaskStatus,
    TaskStore,
)
from core.orchestrator.events import EventBus, TaskCancelledEvent


def _make_orchestrator(tmp_path: Path) -> CentralOrchestrator:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    st = tmp_path / "store"
    st.mkdir(exist_ok=True)
    return CentralOrchestrator(
        task_store=TaskStore(st),
        event_bus=EventBus(),
        allowed_roots=[str(ws)],
    )


def _single_step_plan(task_id: str, goal: str) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=f"plan_{task_id}",
        task_id=task_id,
        goal=goal,
        subtasks=[
            Subtask(
                subtask_id=f"sub_{task_id}_1",
                task_id=task_id,
                index=1,
                title="Execute step",
                description=goal,
                assigned_agent="general",
                allowed_tools=[],
            )
        ],
    )


class TestStage2TaskReliability:
    def test_bounded_retry_recovers_within_max_retries(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)
        calls = {"n": 0}

        def _flaky_exec(subtask, workflow_context, task_id):
            calls["n"] += 1
            if calls["n"] < 2:
                return False, "Transient timeout from upstream", []
            return True, "Recovered and completed", []

        with patch.object(orch, "_try_fast_path", return_value=None):
            with patch.object(orch, "plan", side_effect=lambda g, tid: _single_step_plan(tid, g)):
                with patch.object(orch, "_execute_subtask", side_effect=_flaky_exec):
                    with patch.object(orch, "_verify_deliverables", return_value=(True, "Verified", {})):
                        rec = orch.execute_task("Run flaky step")

        assert rec.status == TaskStatus.COMPLETED
        assert rec.current_stage == "COMPLETED"
        assert rec.retry_count == 1
        assert calls["n"] == 2

    def test_retries_are_strictly_bounded_at_max_retries(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)
        calls = {"n": 0}

        def _always_fail(subtask, workflow_context, task_id):
            calls["n"] += 1
            return False, "Network connection error", []

        with patch.object(orch, "_try_fast_path", return_value=None):
            with patch.object(orch, "plan", side_effect=lambda g, tid: _single_step_plan(tid, g)):
                with patch.object(orch, "_execute_subtask", side_effect=_always_fail):
                    rec = orch.execute_task("Run failing step")

        assert rec.status == TaskStatus.FAILED
        assert rec.current_stage == "FAILED"
        assert rec.final_outcome == "FAILED"
        assert rec.retry_count == rec.max_retries == 2
        assert calls["n"] == 3  # 1 initial + 2 retries

    def test_approval_required_pauses_in_waiting_for_approval_without_retrying(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)
        calls = {"n": 0}

        def _ask_approval(subtask, workflow_context, task_id):
            calls["n"] += 1
            return False, "[CONFIRMATION_PENDING] Awaiting human confirmation on HUD.", []

        with patch.object(orch, "_try_fast_path", return_value=None):
            with patch.object(orch, "plan", side_effect=lambda g, tid: _single_step_plan(tid, g)):
                with patch.object(orch, "_execute_subtask", side_effect=_ask_approval):
                    rec = orch.execute_task("Send sensitive email")

        assert rec.status == TaskStatus.WAITING_FOR_APPROVAL
        assert rec.current_stage == "WAITING_FOR_APPROVAL"
        assert rec.retry_count == 0
        assert calls["n"] == 1
        persisted = orch.task_store.get_task(rec.task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.WAITING_FOR_APPROVAL

    def test_cancellation_stops_waiting_task_and_emits_event(self, tmp_path: Path):
        orch = _make_orchestrator(tmp_path)
        events: list[TaskCancelledEvent] = []
        orch.event_bus.subscribe(TaskCancelledEvent, lambda e: events.append(e))

        with patch.object(orch, "_try_fast_path", return_value=None):
            with patch.object(orch, "plan", side_effect=lambda g, tid: _single_step_plan(tid, g)):
                with patch.object(orch, "_execute_subtask", return_value=(False, "[CONFIRMATION_PENDING] wait", [])):
                    rec = orch.execute_task("Paused approval task")

        assert rec.status == TaskStatus.WAITING_FOR_APPROVAL
        ok = orch.cancel(rec.task_id)
        assert ok is True
        after = orch.task_store.get_task(rec.task_id)
        assert after is not None
        assert after.status == TaskStatus.CANCELLED
        assert after.plan.subtasks[0].status == SubtaskStatus.CANCELLED
        assert len(events) == 1
        assert events[0].task_id == rec.task_id

    def test_service_bridge_uses_authoritative_task_store_state(self, tmp_path: Path):
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        from gui.optimus.service_bridge import OptimusServiceBridge

        orch = _make_orchestrator(tmp_path)
        with patch.object(orch, "_try_fast_path", return_value=None):
            with patch.object(orch, "plan", side_effect=lambda g, tid: _single_step_plan(tid, g)):
                with patch.object(orch, "_execute_subtask", return_value=(False, "[CONFIRMATION_PENDING] wait", [])):
                    rec = orch.execute_task("Check authoritative bridge sync")

        OptimusServiceBridge.reset()
        bridge = OptimusServiceBridge.instance()
        bridge.orchestrator = orch
        bridge._active_task_id = rec.task_id

        state = bridge.get_active_task_status()
        assert state["task_id"] == rec.task_id
        assert state["status"] == "WAITING_FOR_APPROVAL"
        assert state["waiting_for_approval"] is True
        assert state["current_stage"] == "WAITING_FOR_APPROVAL"
        assert len(state["subtasks"]) == 1
        recent = bridge.list_recent_tasks(limit=5)
        assert any(t["task_id"] == rec.task_id and t["status"] == "WAITING_FOR_APPROVAL" for t in recent)
        OptimusServiceBridge.reset()
