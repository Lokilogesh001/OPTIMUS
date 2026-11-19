"""tests/test_mission_store_reliability.py — Multiprocess Reliability and State Reconciliation Tests.

Tests:
1. Multiple processes writing different mission IDs concurrently.
2. Multiple processes writing the same mission ID concurrently with auto-merge.
3. No corrupted or partially written JSON under high concurrency.
4. Atomic replacement and temporary-file cleanup.
5. Recovery after interrupted writes and stale temp file cleanup.
6. Repeated reconciliation without duplicate execution (idempotency).
7. Ambiguous side effects requiring verification rather than automatic replay.
8. Inter-process file locking with mutate() preventing all lost updates.
9. Optimistic concurrency control (OCC) rejecting stale updates via ConcurrencyConflictError.
"""

from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

import pytest

from core.missions.engine import MissionExecutionEngine
from core.missions.storage import ConcurrencyConflictError, MissionStore
from core.missions.types import (
    MissionPlan,
    MissionRecord,
    MissionState,
    MissionSubtask,
    SubtaskStatus,
)

# ── Top-Level Multiprocessing Worker Functions (Required for Windows Spawn) ─

def _worker_write_distinct_missions(storage_dir_str: str, worker_id: int, num_missions: int) -> None:
    """Worker process that writes unique mission records to the shared store."""
    store = MissionStore(storage_dir_str)
    for i in range(num_missions):
        m_id = f"m_proc_{worker_id}_{i}"
        plan = MissionPlan(
            objective=f"Objective from worker {worker_id} mission {i}",
            subtasks=[
                MissionSubtask(
                    subtask_id=f"st_{worker_id}_{i}_1",
                    title=f"Subtask 1 for {m_id}",
                    status=SubtaskStatus.COMPLETED,
                    result_output="Initial findings",
                )
            ],
        )
        rec = MissionRecord(mission_id=m_id, objective=plan.objective, state=MissionState.EXECUTE, plan=plan)
        rec.add_timeline_event("worker_event", f"Worker {worker_id} wrote mission {i}")
        store.save(rec)


def _worker_write_same_mission(storage_dir_str: str, mission_id: str, worker_id: int, updates: int) -> None:
    """Worker process that repeatedly updates the same mission record concurrently."""
    store = MissionStore(storage_dir_str)
    for i in range(updates):
        rec = store.get(mission_id)
        if rec is None:
            rec = MissionRecord(mission_id=mission_id, objective="Shared concurrent objective")
        rec.add_timeline_event("concurrent_update", f"Worker {worker_id} update {i}")
        rec.updated_at = time.time()
        store.save(rec, auto_merge=True)


def _worker_mutate_mission(storage_dir_str: str, mission_id: str, worker_id: int, updates: int) -> None:
    """Worker process that mutates the same mission record under an exclusive lock."""
    store = MissionStore(storage_dir_str)
    for i in range(updates):
        step_idx = i

        def _updater(rec: MissionRecord, step: int = step_idx) -> None:
            rec.add_timeline_event("locked_mutation", f"Worker {worker_id} mutation {step}")
            rec.deliverables.append(f"deliverable_{worker_id}_{step}.txt")

        store.mutate(mission_id, _updater)


# ── Test Cases ───────────────────────────────────────────────────────────────

def test_atomic_replacement_and_temp_file_cleanup(tmp_path: Path):
    """Verify writes use atomic replacement and leave zero temporary files behind."""
    store = MissionStore(tmp_path)
    rec = MissionRecord(mission_id="m_atomic_test", objective="Test atomic replacement")
    store.save(rec)

    # File must exist and contain valid JSON
    target_path = tmp_path / "m_atomic_test.json"
    assert target_path.exists()
    assert target_path.stat().st_size > 0

    # No leftover .tmp files
    tmp_files = list(tmp_path.glob("*.tmp")) + list(tmp_path.glob(".*.tmp"))
    assert len(tmp_files) == 0

    # Load back
    loaded = store.get("m_atomic_test")
    assert loaded is not None
    assert loaded.mission_id == "m_atomic_test"


def test_multiprocess_concurrent_writes_distinct_missions(tmp_path: Path):
    """Verify multiple independent OS processes writing different mission IDs concurrently."""
    num_workers = 4
    missions_per_worker = 10
    total_expected = num_workers * missions_per_worker

    processes = []
    for w_id in range(num_workers):
        p = multiprocessing.Process(
            target=_worker_write_distinct_missions,
            args=(str(tmp_path), w_id, missions_per_worker),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=30)
        assert not p.is_alive(), "Worker process timed out!"

    # Verify all missions are present, readable, and non-corrupted
    store = MissionStore(tmp_path)
    records = store.list_all(limit=total_expected + 50)
    assert len(records) == total_expected

    # Zero stray temp files
    tmp_files = list(tmp_path.glob("*.tmp")) + list(tmp_path.glob(".*.tmp"))
    assert len(tmp_files) == 0


def test_multiprocess_concurrent_writes_same_mission(tmp_path: Path):
    """Verify multiple processes updating the same mission ID without corruption or sharing lock deadlocks."""
    shared_mission_id = "m_shared_contest"
    store = MissionStore(tmp_path)
    init_rec = MissionRecord(mission_id=shared_mission_id, objective="Concurrent Contest")
    store.save(init_rec)

    num_workers = 3
    updates_per_worker = 5

    processes = []
    for w_id in range(num_workers):
        p = multiprocessing.Process(
            target=_worker_write_same_mission,
            args=(str(tmp_path), shared_mission_id, w_id, updates_per_worker),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=30)
        assert not p.is_alive(), "Contested worker process timed out!"

    # Must be valid, readable JSON
    final = store.get(shared_mission_id)
    assert final is not None
    assert final.mission_id == shared_mission_id
    assert len(final.timeline) >= updates_per_worker


def test_multiprocess_mutate_no_lost_updates(tmp_path: Path):
    """Verify that transactional mutate() under inter-process lock prevents 100% of lost updates."""
    shared_mission_id = "m_locked_contest"
    store = MissionStore(tmp_path)
    init_rec = MissionRecord(mission_id=shared_mission_id, objective="Locked Contest")
    store.save(init_rec)

    num_workers = 3
    updates_per_worker = 5
    total_expected = num_workers * updates_per_worker

    processes = []
    for w_id in range(num_workers):
        p = multiprocessing.Process(
            target=_worker_mutate_mission,
            args=(str(tmp_path), shared_mission_id, w_id, updates_per_worker),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=30)
        assert not p.is_alive(), "Mutate worker process timed out!"

    final = store.get(shared_mission_id)
    assert final is not None
    assert final.mission_id == shared_mission_id
    # Every single mutation must be preserved — exactly total_expected!
    assert len(final.timeline) == total_expected
    assert len(final.deliverables) == total_expected


def test_concurrency_conflict_error_on_stale_expected_version(tmp_path: Path):
    """Verify that saving with expected_version raises ConcurrencyConflictError when a stale update occurs."""
    store = MissionStore(tmp_path)
    rec = MissionRecord("m_occ", objective="OCC Test")
    store.save(rec)
    initial_version = rec.version

    # Writer A reads at initial version
    rec_a = store.get("m_occ")
    assert rec_a.version == initial_version

    # Writer B updates to next version
    rec_b = store.get("m_occ")
    rec_b.add_timeline_event("b_update", "Writer B update")
    store.save(rec_b)
    assert store.get("m_occ").version > initial_version

    # Writer A attempts to save with stale expected_version -> raises ConcurrencyConflictError
    rec_a.add_timeline_event("a_update", "Writer A update")
    with pytest.raises(ConcurrencyConflictError):
        store.save(rec_a, expected_version=initial_version)


def test_corrupted_json_handling(tmp_path: Path):
    """Verify that corrupt or partially written JSON is handled gracefully without crashing get()."""
    store = MissionStore(tmp_path)

    # Write truncated/invalid JSON
    bad_file = tmp_path / "m_corrupt.json"
    bad_file.write_text('{"mission_id": "m_corrupt", "objective": "truncated...', encoding="utf-8")

    rec = store.get("m_corrupt")
    assert rec is None

    # Normal list_all skips corrupt file without crashing
    good_rec = MissionRecord(mission_id="m_good", objective="Good Mission")
    store.save(good_rec)

    all_recs = store.list_all()
    assert len(all_recs) == 1
    assert all_recs[0].mission_id == "m_good"


def test_cleanup_temp_files(tmp_path: Path):
    """Verify cleanup_temp_files deletes stale temporary files left behind."""
    store = MissionStore(tmp_path)

    # Create dummy stale temp files
    stale_tmp1 = tmp_path / ".m_old.1234.5678.abcd.tmp"
    stale_tmp1.write_text("partial data", encoding="utf-8")
    stale_tmp2 = tmp_path / "old_explicit.tmp"
    stale_tmp2.write_text("partial data", encoding="utf-8")

    # Set access/mod time to 1 hour ago
    old_time = time.time() - 3600
    os.utime(stale_tmp1, (old_time, old_time))
    os.utime(stale_tmp2, (old_time, old_time))

    cleaned = store.cleanup_temp_files(max_age_seconds=60.0)
    assert cleaned == 2
    assert not stale_tmp1.exists()
    assert not stale_tmp2.exists()


def test_state_reconciliation_with_verified_deliverables(tmp_path: Path):
    """Verify interrupted subtask is reconciled to COMPLETED when expected deliverables exist on disk."""
    store = MissionStore(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    deliverable_path = workspace / "reports" / "summary.md"
    deliverable_path.parent.mkdir(parents=True, exist_ok=True)
    deliverable_path.write_text("# Deliverable Content\nCompleted before crash.", encoding="utf-8")

    # Subtask was RUNNING when process crashed, but deliverable was successfully written
    st1 = MissionSubtask(
        subtask_id="st_interrupted_ok",
        title="Generate Summary Report",
        status=SubtaskStatus.RUNNING,
        assigned_agent="developer",
        expected_deliverables=[str(deliverable_path)],
    )
    plan = MissionPlan(objective="Reconciliation Test 1", subtasks=[st1])
    rec = MissionRecord(
        mission_id="m_reconcile_ok",
        objective="Reconciliation Test 1",
        state=MissionState.EXECUTE,
        plan=plan,
    )
    store.save(rec)

    # Reconcile
    reconciled = store.reconcile_interrupted_missions(workspace_root=workspace)
    assert len(reconciled) == 1

    updated = store.get("m_reconcile_ok")
    assert updated is not None
    # Subtask verified and transitioned to COMPLETED
    assert updated.plan.subtasks[0].status == SubtaskStatus.COMPLETED
    assert str(deliverable_path) in updated.deliverables
    # Since all subtasks are COMPLETED, mission advances to VERIFY
    assert updated.state == MissionState.VERIFY


def test_state_reconciliation_with_ambiguous_unverified_subtask(tmp_path: Path):
    """Verify interrupted subtask without deliverables is marked AMBIGUOUS_EXECUTION rather than blindly rerun."""
    store = MissionStore(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    missing_deliv = workspace / "non_existent_output.json"

    st_ambiguous = MissionSubtask(
        subtask_id="st_ambiguous_1",
        title="Execute Critical Mutating Shell Script",
        status=SubtaskStatus.RUNNING,
        assigned_agent="developer",
        required_tools=["execute_command"],
        expected_deliverables=[str(missing_deliv)],
    )
    plan = MissionPlan(objective="Reconciliation Test Ambiguous", subtasks=[st_ambiguous])
    rec = MissionRecord(
        mission_id="m_reconcile_ambiguous",
        objective="Reconciliation Test Ambiguous",
        state=MissionState.EXECUTE,
        plan=plan,
    )
    store.save(rec)

    reconciled = store.reconcile_interrupted_missions(workspace_root=workspace)
    assert len(reconciled) == 1

    updated = store.get("m_reconcile_ambiguous")
    assert updated is not None
    # Subtask must NOT be reset to PENDING or blindly rerun
    assert updated.plan.subtasks[0].status == SubtaskStatus.FAILED
    assert "AMBIGUOUS_EXECUTION" in updated.plan.subtasks[0].error
    # Mission paused for human/system inspection
    assert updated.state in (MissionState.PAUSED, MissionState.PARTIALLY_COMPLETED)
    assert updated.context.get("requires_reconciliation") is True


def test_reconciliation_is_idempotent_and_safe_to_run_repeatedly(tmp_path: Path):
    """Verify repeated reconciliation runs produce stable state without duplicate events or unintended resets."""
    store = MissionStore(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    # 1. Mission with 1 COMPLETED subtask and 1 RUNNING ambiguous subtask
    s1 = MissionSubtask(
        "s1_done",
        "Step 1 Already Done",
        status=SubtaskStatus.COMPLETED,
        result_output="Pre-crash result",
    )
    s2 = MissionSubtask(
        "s2_interrupted",
        "Step 2 Mutating Step",
        status=SubtaskStatus.RUNNING,
        assigned_agent="developer",
    )
    plan = MissionPlan(objective="Idempotent Reconciliation", subtasks=[s1, s2])
    rec = MissionRecord(
        mission_id="m_idempotent_rec",
        objective="Idempotent Reconciliation",
        state=MissionState.EXECUTE,
        plan=plan,
    )
    store.save(rec)

    # First reconciliation pass
    r1 = store.reconcile_interrupted_missions(workspace_root=workspace)
    assert len(r1) == 1
    m1 = store.get("m_idempotent_rec")
    assert m1.plan.subtasks[0].status == SubtaskStatus.COMPLETED
    assert m1.plan.subtasks[1].status == SubtaskStatus.FAILED
    assert m1.state == MissionState.PARTIALLY_COMPLETED
    t1_len = len(m1.timeline)

    # Second reconciliation pass
    r2 = store.reconcile_interrupted_missions(workspace_root=workspace)
    # No in-flight / RUNNING missions remain
    assert len(r2) == 0

    m2 = store.get("m_idempotent_rec")
    # State and timeline must be completely stable
    assert m2.plan.subtasks[0].status == SubtaskStatus.COMPLETED
    assert m2.plan.subtasks[1].status == SubtaskStatus.FAILED
    assert m2.state == MissionState.PARTIALLY_COMPLETED
    assert len(m2.timeline) == t1_len


def test_mission_engine_resume_reconciles_before_execution(tmp_path: Path):
    """Verify MissionExecutionEngine.resume_mission reconciles stranded RUNNING subtasks."""
    store = MissionStore(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    # Mission with stranded RUNNING subtask
    st = MissionSubtask("st_stranded", "Stranded Step", status=SubtaskStatus.RUNNING)
    plan = MissionPlan("Plan Stranded", subtasks=[st])
    rec = MissionRecord(mission_id="m_engine_resume", objective="Engine Resume Test", state=MissionState.PAUSED, plan=plan)
    store.save(rec)

    engine = MissionExecutionEngine(store=store)
    resumed = engine.resume_mission("m_engine_resume")

    # The stranded step was reconciled before execution, preventing deadlock
    assert resumed.plan.subtasks[0].status != SubtaskStatus.RUNNING
    engine.shutdown(timeout=1.0)
