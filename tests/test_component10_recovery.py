"""tests/test_component10_recovery.py — Component 10 State, Persistence & Crash Recovery Tests.

Validates:
- JSON file corruption handling across TaskStore, MemoryStore, and OutcomeStore.
- Atomic file write resilience with temporary file replacement.
- Workflow crash recovery: in-flight executions reconcile to AMBIGUOUS_EXECUTION on restart.
- Approval expiration: timed-out approval gates expire cleanly.
- Planning outcome restoration after restart.
- Memory store persistence and multi-category reload.
- TaskStore cascading cancellation across subtask hierarchy.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest

from core.memory import MemoryCategory, MemoryEntry, MemoryStore
from core.orchestrator.runtime import (
    ExecutionPlan,
    Subtask,
    SubtaskStatus,
    TaskRecord,
    TaskStatus,
    TaskStore,
)
from core.planning.outcome_store import OutcomeStore
from core.planning.types import (
    ExecutionOutcome,
    GoalCategory,
    OutcomeType,
    StrategyType,
)
from core.workflows.approval import ApprovalManager
from core.workflows.storage import WorkflowStore
from core.workflows.types import (
    ApprovalStatus,
    StepExecutionRecord,
    StepStatus,
    WorkflowExecutionRecord,
    WorkflowStatus,
)

# ── Test 1: TaskStore JSON corruption resilience ─────────────────────────────

def test_task_store_recovers_from_corrupt_json(tmp_path):
    """TaskStore skips corrupt JSON task files without crashing."""
    store_dir = tmp_path / "tasks_corrupt"
    store_dir.mkdir(parents=True, exist_ok=True)
    tasks_sub = store_dir / "tasks"
    tasks_sub.mkdir(parents=True, exist_ok=True)

    # Write a valid task
    valid_task = TaskRecord(
        task_id="task-valid-01",
        goal="Valid task goal",
        status=TaskStatus.COMPLETED,
    )
    (tasks_sub / "task-valid-01.json").write_text(
        json.dumps(valid_task.to_dict()), encoding="utf-8"
    )

    # Write a corrupt file
    (tasks_sub / "task-corrupt-02.json").write_text(
        "{truncated json: true, bad: ", encoding="utf-8"
    )

    # Loading the store should succeed and keep the valid task
    store = TaskStore(storage_root=store_dir)
    assert store.get_task("task-valid-01") is not None
    assert store.get_task("task-corrupt-02") is None
    assert len(store.list_tasks()) == 1


# ── Test 2: MemoryStore JSON corruption resilience ───────────────────────────

def test_memory_store_recovers_from_corrupt_category_file(tmp_path):
    """MemoryStore gracefully ignores corrupted category files on disk."""
    mem_dir = tmp_path / "memory_corrupt"
    mem_dir.mkdir(parents=True, exist_ok=True)

    # Write a corrupt episodic memory file
    (mem_dir / "episodic.json").write_text("[{'invalid': json", encoding="utf-8")

    # Write valid working memory file
    valid_entry = MemoryEntry(
        entry_id="mem-valid-01",
        category=MemoryCategory.WORKING,
        key="test_key",
        content="some working content",
    )
    (mem_dir / "working.json").write_text(
        json.dumps([valid_entry.to_dict()]), encoding="utf-8"
    )

    # Store hydration should not raise an exception
    store = MemoryStore(storage_root=mem_dir, sync_legacy=False)
    assert store.get("mem-valid-01") is not None
    assert store.get_by_key(MemoryCategory.WORKING, "test_key") is not None


# ── Test 3: OutcomeStore JSON corruption resilience ───────────────────────────

def test_outcome_store_recovers_from_corrupt_file(tmp_path):
    """OutcomeStore starts with empty state when outcomes file is corrupted."""
    outcomes_file = tmp_path / "planning_outcomes.json"
    outcomes_file.write_text("NOT_JSON_AT_ALL!!!", encoding="utf-8")

    store = OutcomeStore(storage_path=outcomes_file)
    assert len(store.list_outcomes()) == 0
    assert store.get_success_rate() == 1.0


# ── Test 4: TaskStore cascading cancellation ─────────────────────────────────

def test_task_store_cascading_cancellation(tmp_path):
    """TaskStore cancels parent task and cascades CANCELLED to all active/pending subtasks."""
    store = TaskStore(storage_root=tmp_path / "task_cascade")

    plan = ExecutionPlan(
        plan_id="plan-cascade-01",
        task_id="task-cascade-01",
        goal="Cascade test",
        subtasks=[
            Subtask(
                subtask_id="sub-1",
                task_id="task-cascade-01",
                index=0,
                title="Step 1",
                status=SubtaskStatus.COMPLETED,
            ),
            Subtask(
                subtask_id="sub-2",
                task_id="task-cascade-01",
                index=1,
                title="Step 2",
                status=SubtaskStatus.RUNNING,
            ),
            Subtask(
                subtask_id="sub-3",
                task_id="task-cascade-01",
                index=2,
                title="Step 3",
                status=SubtaskStatus.PENDING,
            ),
        ],
    )
    task = TaskRecord(
        task_id="task-cascade-01",
        goal="Cascade test",
        status=TaskStatus.RUNNING,
        plan=plan,
    )
    store.save_task(task)

    # Cancel parent task
    cancelled = store.cancel_task("task-cascade-01", reason="User stopped execution")
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.error == "User stopped execution"

    # Completed subtask remains COMPLETED
    sub1 = next(s for s in cancelled.plan.subtasks if s.subtask_id == "sub-1")
    assert sub1.status == SubtaskStatus.COMPLETED

    # Running subtask becomes CANCELLED
    sub2 = next(s for s in cancelled.plan.subtasks if s.subtask_id == "sub-2")
    assert sub2.status == SubtaskStatus.CANCELLED

    # Pending subtask becomes CANCELLED
    sub3 = next(s for s in cancelled.plan.subtasks if s.subtask_id == "sub-3")
    assert sub3.status == SubtaskStatus.CANCELLED


# ── Test 5: Workflow crash recovery & AMBIGUOUS_EXECUTION ─────────────────────

def test_workflow_storage_reconciles_interrupted_executions(tmp_path):
    """Interrupted workflows have in-flight RUNNING steps marked AMBIGUOUS_EXECUTION."""
    storage = WorkflowStore(base_dir=tmp_path / "wf_recovery")

    # Create an interrupted execution record (simulates process dying mid-step)
    rec = WorkflowExecutionRecord(
        execution_id="exec-crash-01",
        workflow_id="wf-test-01",
        status=WorkflowStatus.RUNNING,
        step_states={
            "step-1": StepExecutionRecord(
                step_id="step-1",
                status=StepStatus.COMPLETED,
            ),
            "step-2": StepExecutionRecord(
                step_id="step-2",
                status=StepStatus.RUNNING,  # Died while this was running!
            ),
            "step-3": StepExecutionRecord(
                step_id="step-3",
                status=StepStatus.PENDING,
            ),
        },
    )
    storage.save_execution(rec)

    # Simulate restart: reconcile interrupted executions
    recovered = storage.reconcile_interrupted_executions()
    assert len(recovered) == 1
    rec_recovered = recovered[0]

    # Workflow must be PAUSED, not left in RUNNING
    assert rec_recovered.status == WorkflowStatus.PAUSED
    assert rec_recovered.recovery_metadata.get("requires_reconciliation") is True

    # Step-2 must be AMBIGUOUS_EXECUTION to prevent unverified re-execution
    step2 = rec_recovered.step_states["step-2"]
    assert step2.status == StepStatus.AMBIGUOUS_EXECUTION
    assert "ambiguous" in step2.error.lower()


# ── Test 6: Approval timeout and expiration ───────────────────────────────────

def test_approval_manager_expires_timed_out_requests():
    """ApprovalManager marks requests older than timeout as EXPIRED."""
    mgr = ApprovalManager()

    # Request approval with a 0.05-second timeout
    app = mgr.request_approval(
        workflow_id="wf-test-01",
        execution_id="exec-app-01",
        step_id="step-destructive",
        reason="Delete production database",
        timeout_seconds=0.05,
    )
    assert app.status == ApprovalStatus.PENDING

    # Wait for timeout to elapse
    time.sleep(0.08)

    # Evaluating expirations should transition the status
    expired = mgr.check_expirations()
    assert len(expired) >= 1
    assert any(a.approval_id == app.approval_id for a in expired)

    # Attempting to approve after expiration must raise TimeoutError
    with pytest.raises(TimeoutError):
        mgr.approve(app.approval_id, approver="admin")


# ── Test 7: OutcomeStore restart persistence ──────────────────────────────────

def test_outcome_store_persistence_across_restart(tmp_path):
    """OutcomeStore accurately hydrates verified history across restarts."""
    outcomes_file = tmp_path / "outcomes_persist.json"

    # Session 1: record 2 outcomes
    store1 = OutcomeStore(storage_path=outcomes_file)
    store1.record_outcome(ExecutionOutcome(
        outcome_id="rec-01",
        plan_id="plan-01",
        goal="Task 1",
        goal_category=GoalCategory.ANALYSIS,
        strategy=StrategyType.CONSERVATIVE_SEQUENTIAL,
        outcome=OutcomeType.VERIFIED_SUCCESS,
        duration_seconds=1.5,
    ))
    store1.record_outcome(ExecutionOutcome(
        outcome_id="rec-02",
        plan_id="plan-02",
        goal="Task 2",
        goal_category=GoalCategory.ANALYSIS,
        strategy=StrategyType.CONSERVATIVE_SEQUENTIAL,
        outcome=OutcomeType.VERIFIED_FAILURE,
        duration_seconds=2.0,
    ))

    # Session 2: instantiate new store pointing to same file
    store2 = OutcomeStore(storage_path=outcomes_file)
    assert len(store2.list_outcomes()) == 2
    assert store2.get_success_rate(GoalCategory.ANALYSIS) == 0.5
    assert store2.get_average_duration(GoalCategory.ANALYSIS) == 1.75


# ── Test 8: MemoryStore persistence across restart ────────────────────────────

def test_memory_store_persists_and_reloads_across_restart(tmp_path):
    """MemoryStore writes entries atomically and hydrates all categories on reload."""
    mem_dir = tmp_path / "mem_persist"

    # Session 1: Store entries in multiple categories
    store1 = MemoryStore(storage_root=mem_dir, sync_legacy=False)
    e_semantic = MemoryEntry(
        entry_id="mem-sem-1",
        category=MemoryCategory.SEMANTIC,
        key="project_goal",
        content="Deliver OPTIMUS Release",
        is_verified=True,
    )
    e_procedural = MemoryEntry(
        entry_id="mem-proc-1",
        category=MemoryCategory.PROCEDURAL,
        key="build_recipe",
        content="run pytest; run ruff",
        is_verified=True,
    )
    store1.put(e_semantic)
    store1.put(e_procedural)

    # Session 2: Fresh instance
    store2 = MemoryStore(storage_root=mem_dir, sync_legacy=False)
    sem_recovered = store2.get_by_key(MemoryCategory.SEMANTIC, "project_goal")
    assert sem_recovered is not None
    assert sem_recovered.content == "Deliver OPTIMUS Release"
    assert sem_recovered.is_verified is True

    proc_recovered = store2.get_by_key(MemoryCategory.PROCEDURAL, "build_recipe")
    assert proc_recovered is not None
    assert proc_recovered.content == "run pytest; run ruff"
