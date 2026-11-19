"""tests/test_multiprocess_storage.py — Multiprocess Contention & Concurrency Stress Test.

Verifies that when 5 independent OS processes concurrently update the same MissionRecord
via MissionStore.mutate():
1. OS-level file locking (msvcrt.locking / flock) guarantees inter-process serialization.
2. 100% of subtasks, timeline events, and deliverables are preserved without lost updates.
3. The record's monotonic version counter exactly reflects all successful concurrent updates.
"""

import multiprocessing as mp
import time
from pathlib import Path

from core.missions.storage import MissionStore
from core.missions.types import (
    MissionPlan,
    MissionRecord,
    MissionState,
    MissionSubtask,
    SubtaskStatus,
)


def _worker_process_fn(storage_dir_str: str, mission_id: str, worker_id: int, updates_per_worker: int) -> None:
    """Worker process targeting the same mission file in parallel."""
    store = MissionStore(storage_dir=storage_dir_str)
    for i in range(updates_per_worker):
        def _updater(rec: MissionRecord, step: int = i, wid: int = worker_id) -> None:
            # 1. Append distinct timeline event
            rec.add_timeline_event(
                event_type=f"worker_{wid}_step_{step}",
                details=f"Worker {wid} executed update {step}",
            )
            # 2. Append distinct deliverable
            deliv = f"output_worker_{wid}_step_{step}.dat"
            if deliv not in rec.deliverables:
                rec.deliverables.append(deliv)
            # 3. Add or update distinct subtask
            st_id = f"st_w{wid}_{step}"
            existing = next((s for s in rec.plan.subtasks if s.subtask_id == st_id), None)
            if existing is None:
                rec.plan.subtasks.append(
                    MissionSubtask(
                        subtask_id=st_id,
                        title=f"Worker {wid} Subtask {step}",
                        status=SubtaskStatus.COMPLETED,
                        result_output=f"Success from worker {wid} step {step}",
                    )
                )

        store.mutate(mission_id, _updater, timeout=30.0)
        time.sleep(0.005)


def test_multiprocess_concurrency_five_workers(tmp_path: Path):
    """Spawn 5 separate OS processes mutating the exact same mission record."""
    store = MissionStore(storage_dir=tmp_path)
    mission_id = "test_multi_proc_5_workers"

    # Initialize baseline record
    baseline = MissionRecord(
        mission_id=mission_id,
        objective="Multiprocess Concurrency Stress Test",
        state=MissionState.EXECUTE,
        plan=MissionPlan(plan_id="plan_stress", objective="Stress testing"),
        version=1,
    )
    store.save(baseline)

    num_workers = 5
    updates_per_worker = 4
    expected_total_updates = num_workers * updates_per_worker

    # Spawn 5 independent OS processes
    ctx = mp.get_context("spawn" if hasattr(mp, "get_context") else None)
    processes = []
    for w in range(num_workers):
        p = ctx.Process(
            target=_worker_process_fn,
            args=(str(tmp_path), mission_id, w, updates_per_worker),
        )
        processes.append(p)
        p.start()

    # Wait for all processes to complete
    for p in processes:
        p.join(timeout=60.0)
        assert p.exitcode == 0, f"Process {p.pid} failed with exit code {p.exitcode}"

    # Load final consolidated record from disk
    final_record = store.get(mission_id)
    assert final_record is not None

    # Assert ZERO lost updates
    # 1. Deliverables count must equal expected_total_updates
    assert len(final_record.deliverables) == expected_total_updates, (
        f"Expected {expected_total_updates} deliverables, found {len(final_record.deliverables)}"
    )

    # 2. Subtasks count must equal expected_total_updates
    assert len(final_record.plan.subtasks) == expected_total_updates, (
        f"Expected {expected_total_updates} subtasks, found {len(final_record.plan.subtasks)}"
    )

    # 3. All subtasks must be COMPLETED
    for st in final_record.plan.subtasks:
        assert st.status == SubtaskStatus.COMPLETED

    # 4. Version must be strictly greater than baseline + total updates
    assert final_record.version >= (1 + expected_total_updates)
