"""core/missions/storage.py — Multiprocess-Safe Persistence and Recovery Store for OPTIMUS Missions.

Persists MissionRecord objects to disk at `memory/missions/<mission_id>.json` using:
- Unique per-write temporary files (`.<mission_id>.<pid>.<tid>.<nonce>.tmp`) to prevent collisions across processes.
- Atomic `os.replace()` without an unlink-before-rename race window.
- Bounded retries with exponential backoff for transient Windows file sharing violations.
- Safe temporary-file cleanup in all execution paths.
- Mandatory inter-process file locking and optimistic concurrency versioning to prevent lost updates.
- Automatic timeline and evidence conflict resolution across concurrent writers.
- Robust state reconciliation for interrupted and ambiguous mission executions.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from core.missions.types import MissionRecord, MissionState, SubtaskStatus

logger = logging.getLogger(__name__)

_thread_local = threading.local()


def _get_held_locks() -> set[str]:
    """Retrieve the set of mission lock IDs currently held by the active thread."""
    if not hasattr(_thread_local, "held_locks"):
        _thread_local.held_locks = set()
    return _thread_local.held_locks


class ConcurrencyConflictError(RuntimeError):
    """Raised when an update conflicts with a concurrent modification to the same mission."""


class MissionStore:
    """Thread-safe, multiprocess-safe atomic disk persistence store for autonomous missions."""

    def __init__(self, storage_dir: Path | str | None = None) -> None:
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path.cwd() / "memory" / "missions"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.cleanup_temp_files(max_age_seconds=600.0)

    @contextmanager
    def lock_mission(self, mission_id: str, timeout: float = 10.0) -> Generator[None, None, None]:
        """Acquire an exclusive OS-level inter-process lock on a mission file."""
        held = _get_held_locks()
        if mission_id in held:
            yield
            return

        lock_path = self.storage_dir / f".{mission_id}.lock"
        with open(lock_path, "a+b") as f:
            start = time.perf_counter()
            acquired = False
            try:
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            f.seek(0)
                            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        held.add(mission_id)
                        break
                    except (OSError, PermissionError):
                        if (time.perf_counter() - start) >= timeout:
                            raise TimeoutError(
                                f"Timed out waiting for file lock on mission '{mission_id}' after {timeout:.1f}s"
                            ) from None
                        time.sleep(0.01 + (secrets.randbelow(15) / 1000.0))
                yield
            finally:
                if acquired:
                    held.discard(mission_id)
                    try:
                        if os.name == "nt":
                            import msvcrt
                            f.seek(0)
                            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass

    def mutate(
        self,
        mission_id: str,
        updater: Callable[[MissionRecord], None],
        timeout: float = 10.0,
    ) -> MissionRecord:
        """Atomically read, mutate, and save a mission record under an exclusive inter-process lock."""
        with self.lock_mission(mission_id, timeout=timeout):
            rec = self.get(mission_id)
            if rec is None:
                raise KeyError(f"Mission '{mission_id}' does not exist in storage.")
            updater(rec)
            self.save(rec, auto_merge=True)
            return rec

    def save(
        self,
        record: MissionRecord,
        expected_version: int | None = None,
        auto_merge: bool = True,
        max_retries: int = 6,
    ) -> None:
        """Atomically persist a MissionRecord to disk with version tracking and conflict resolution."""
        with self._lock, self.lock_mission(record.mission_id):
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            target_path = self.storage_dir / f"{record.mission_id}.json"

            # Check existing state on disk for concurrency resolution
            if target_path.exists():
                disk_record = self.get(record.mission_id)
                if disk_record is not None:
                    if expected_version is not None and disk_record.version != expected_version:
                        raise ConcurrencyConflictError(
                            f"Stale update detected on mission '{record.mission_id}': "
                            f"expected version {expected_version}, but found {disk_record.version}"
                        )

                    # Auto-merge concurrent modifications to prevent lost updates
                    if auto_merge and (
                        disk_record.version > record.version
                        or (disk_record.updated_at > record.created_at and disk_record.version >= record.version)
                    ):
                        # 1. Merge timeline events
                        existing_keys = {
                            (e.get("timestamp"), e.get("event_type"), e.get("details"))
                            for e in record.timeline
                        }
                        for event in disk_record.timeline:
                            key = (event.get("timestamp"), event.get("event_type"), event.get("details"))
                            if key not in existing_keys:
                                record.timeline.append(event)
                                existing_keys.add(key)
                        record.timeline.sort(key=lambda e: float(e.get("timestamp", 0.0)))

                        # 2. Merge deliverables
                        record.deliverables = list(dict.fromkeys(record.deliverables + disk_record.deliverables))

                        # 3. Merge evidence ledger
                        existing_ev = {json.dumps(ev, sort_keys=True) for ev in record.evidence_ledger}
                        for ev in disk_record.evidence_ledger:
                            ev_key = json.dumps(ev, sort_keys=True)
                            if ev_key not in existing_ev:
                                record.evidence_ledger.append(ev)
                                existing_ev.add(ev_key)

                        # 4. Monotonically preserve terminal subtask states
                        disk_st_map = {s.subtask_id: s for s in disk_record.plan.subtasks}
                        for s in record.plan.subtasks:
                            disk_st = disk_st_map.get(s.subtask_id)
                            if disk_st and disk_st.status == SubtaskStatus.COMPLETED and s.status != SubtaskStatus.COMPLETED:
                                s.status = SubtaskStatus.COMPLETED
                                s.result_output = s.result_output or disk_st.result_output
                                s.completed_at = s.completed_at or disk_st.completed_at

                        record.version = max(record.version, disk_record.version) + 1
                    else:
                        record.version = max(record.version, disk_record.version) + 1
            else:
                record.version = max(record.version, 1)

            # Unique per-write temporary file path to prevent multiprocess collisions
            pid = os.getpid()
            tid = threading.get_ident()
            nonce = secrets.token_hex(4)
            temp_path = self.storage_dir / f".{record.mission_id}.{pid}.{tid}.{nonce}.tmp"

            payload = record.to_dict()
            try:
                content = json.dumps(payload, indent=2, ensure_ascii=False)
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic replacement with exponential backoff for Windows sharing violations
                base_delay = 0.02
                for attempt in range(max_retries):
                    try:
                        os.replace(temp_path, target_path)
                        break
                    except (PermissionError, FileExistsError, OSError) as err:
                        if attempt == max_retries - 1:
                            logger.error(
                                "Failed to replace mission file %s -> %s after %d retries: %s",
                                temp_path, target_path, max_retries, err,
                            )
                            raise
                        delay = base_delay * (2 ** attempt) + (secrets.randbelow(15) / 1000.0)
                        time.sleep(delay)
            except Exception as e:
                logger.error("Failed to save mission %s: %s", record.mission_id, e)
                raise
            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def get(self, mission_id: str, max_retries: int = 4) -> MissionRecord | None:
        """Load a MissionRecord by its ID with retry tolerance for transient file sharing locks."""
        with self._lock:
            target_path = self.storage_dir / f"{mission_id}.json"
            if not target_path.exists():
                return None

            base_delay = 0.02
            for attempt in range(max_retries):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return MissionRecord.from_dict(data)
                except (PermissionError, OSError) as e:
                    if attempt == max_retries - 1:
                        logger.error("Failed to read mission record at %s (sharing violation): %s", target_path, e)
                        return None
                    delay = base_delay * (2 ** attempt) + (secrets.randbelow(10) / 1000.0)
                    time.sleep(delay)
                except json.JSONDecodeError as je:
                    logger.error("Corrupted mission record JSON at %s: %s", target_path, je)
                    return None
                except (ValueError, KeyError, TypeError) as e:
                    logger.error("Error deserializing mission record at %s: %s", target_path, e)
                    return None
            return None

    def list_all(self, limit: int = 100) -> list[MissionRecord]:
        """List all stored missions, ordered most recently updated first."""
        with self._lock:
            records: list[MissionRecord] = []
            for file_path in self.storage_dir.glob("*.json"):
                # Skip temp files or hidden files
                if file_path.name.startswith("."):
                    continue
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    records.append(MissionRecord.from_dict(data))
                except (json.JSONDecodeError, ValueError, KeyError, TypeError, OSError) as e:
                    logger.warning("Skipping unreadable mission file %s: %s", file_path, e)

            records.sort(key=lambda r: r.updated_at, reverse=True)
            return records[:limit]

    def find_resumable(self) -> list[MissionRecord]:
        """Find missions that were interrupted or are waiting to be resumed."""
        with self._lock:
            all_missions = self.list_all(limit=500)
            resumable_states = {
                MissionState.PAUSED,
                MissionState.WAITING_FOR_APPROVAL,
                MissionState.WAITING_FOR_AGENT,
                MissionState.RETRYING,
                MissionState.RECOVERING,
                MissionState.EXECUTE,
                MissionState.PLAN,
                MissionState.UNDERSTAND,
            }
            return [m for m in all_missions if m.state in resumable_states]

    def delete(self, mission_id: str, max_retries: int = 4) -> bool:
        """Delete a mission record and its lock file from storage with retry tolerance for file locks."""
        with self._lock:
            target_path = self.storage_dir / f"{mission_id}.json"
            lock_path = self.storage_dir / f".{mission_id}.lock"
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

            if not target_path.exists():
                return False

            base_delay = 0.02
            for attempt in range(max_retries):
                try:
                    target_path.unlink()
                    return True
                except (PermissionError, OSError) as e:
                    if attempt == max_retries - 1:
                        logger.error("Failed to delete mission %s: %s", mission_id, e)
                        return False
                    time.sleep(base_delay * (2 ** attempt))
            return False

    def cleanup_temp_files(self, max_age_seconds: float = 300.0) -> int:
        """Safely clean up orphaned temporary files left behind by killed processes."""
        with self._lock:
            cleaned = 0
            now = time.time()
            # Look for both explicit .tmp and hidden .*.tmp files
            for p in list(self.storage_dir.glob("*.tmp")) + list(self.storage_dir.glob(".*.tmp")):
                try:
                    if (now - p.stat().st_mtime) >= max_age_seconds:
                        p.unlink(missing_ok=True)
                        cleaned += 1
                except OSError:
                    pass
            return cleaned

    # ── State Reconciliation & Ambiguous Execution Recovery ─────────────────

    def reconcile_interrupted_missions(
        self,
        workspace_root: Path | str | None = None,
    ) -> list[MissionRecord]:
        """Scan persistence layer after restart and reconcile in-flight or interrupted missions.

        Guarantees:
        - Interrupted subtasks with verified physical deliverables on disk are marked COMPLETED.
        - Interrupted subtasks with unverified side effects or missing deliverables are marked FAILED
          with an AMBIGUOUS_EXECUTION diagnosis, requiring explicit inspection rather than blind replay.
        - Preserves completed work and existing deliverables (idempotency safeguard).
        - Safe to run repeatedly without mutating already reconciled missions.
        """
        with self._lock:
            root = Path(workspace_root) if workspace_root else Path.cwd()
            reconciled: list[MissionRecord] = []
            all_missions = self.list_all(limit=1000)

            for record in all_missions:
                # Check if mission has active in-flight state or any RUNNING subtask
                is_in_flight_state = record.state in (
                    MissionState.EXECUTE,
                    MissionState.RETRYING,
                    MissionState.RECOVERING,
                )
                has_running_subtask = any(
                    s.status == SubtaskStatus.RUNNING for s in record.plan.subtasks
                )

                if not (is_in_flight_state or has_running_subtask):
                    continue

                has_ambiguous = False

                for subtask in record.plan.subtasks:
                    if subtask.status == SubtaskStatus.RUNNING:
                        # Process terminated while this subtask was in-flight
                        file_delivs = [
                            Path(d) if Path(d).is_absolute() else root / d
                            for d in subtask.expected_deliverables
                            if ("." in d or "/" in d or "\\" in d)
                        ]
                        started = subtask.started_at or 0.0
                        deliverables_verified = (
                            len(file_delivs) > 0
                            and all(
                                p.exists()
                                and p.stat().st_size > 0
                                and (started <= 0 or p.stat().st_mtime >= (started - 1.0))
                                for p in file_delivs
                            )
                        )

                        if deliverables_verified:
                            subtask.status = SubtaskStatus.COMPLETED
                            subtask.completed_at = time.time()
                            subtask.result_output = "Reconciled to COMPLETED: verified deliverables exist on disk."
                            for p in file_delivs:
                                if str(p) not in record.deliverables:
                                    record.deliverables.append(str(p))
                            record.add_timeline_event(
                                event_type="subtask_reconciled",
                                details=f"Subtask '{subtask.title}' reconciled to COMPLETED (deliverables verified on disk).",
                                data={"subtask_id": subtask.subtask_id},
                            )
                        else:
                            # Ambiguous outcome: side effects could be partially executed or unverified
                            subtask.status = SubtaskStatus.FAILED
                            subtask.error = (
                                "AMBIGUOUS_EXECUTION: Process terminated mid-execution; outcome unverified. "
                                "Requires verification rather than blind automatic replay."
                            )
                            record.add_timeline_event(
                                event_type="ambiguous_subtask_detected",
                                details=f"Subtask '{subtask.title}' flagged as AMBIGUOUS_EXECUTION (outcome unverified).",
                                data={"subtask_id": subtask.subtask_id},
                            )
                            has_ambiguous = True

                # Reconcile mission record lifecycle state
                all_completed = (
                    len(record.plan.subtasks) > 0
                    and all(s.status == SubtaskStatus.COMPLETED for s in record.plan.subtasks)
                )

                if all_completed:
                    record.state = MissionState.VERIFY
                    record.add_timeline_event(
                        event_type="mission_reconciled",
                        details="All subtasks completed; advanced mission to VERIFY stage.",
                    )
                elif has_ambiguous or any(s.status == SubtaskStatus.FAILED for s in record.plan.subtasks):
                    completed_count = sum(1 for s in record.plan.subtasks if s.status == SubtaskStatus.COMPLETED)
                    if completed_count > 0:
                        record.state = MissionState.PARTIALLY_COMPLETED
                    else:
                        record.state = MissionState.PAUSED
                    record.context["requires_reconciliation"] = True
                    record.context["reconciled_at"] = time.time()
                    record.failure_reason = "Mission interrupted by process termination with unverified/ambiguous subtask outcomes."
                    record.add_timeline_event(
                        event_type="mission_reconciled",
                        details=f"Interrupted mission reconciled to {record.state.value} (ambiguous steps require inspection).",
                    )
                else:
                    record.state = MissionState.PAUSED
                    record.context["reconciled_at"] = time.time()
                    record.add_timeline_event(
                        event_type="mission_reconciled",
                        details="Interrupted mission reconciled to PAUSED state.",
                    )

                self.save(record, auto_merge=True)
                reconciled.append(record)

            return reconciled
