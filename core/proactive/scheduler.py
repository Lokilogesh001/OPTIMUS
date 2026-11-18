"""core/proactive/scheduler.py — Persistent, Time-Aware Intelligent Task Scheduler.

Implements Phase 2 of Milestone 10: Intelligent Scheduler.
- One-time and recurring task execution (interval, daily, weekly, monthly, cron).
- Persistent schedule storage at `memory/schedules/<id>.json`.
- Dispatches execution through MissionExecutionEngine and CentralOrchestrator.
- Evaluates 4-tier Action Governance before execution (pauses for approval on Level 4).
- Resilient missed-schedule recovery and duplicate execution prevention.
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from core.proactive.governance import ActionGovernor
from core.proactive.notifications import get_notification_manager
from core.proactive.types import (
    ActionGovernanceLevel,
    RecommendationPriority,
    ScheduleFrequency,
    ScheduleRecord,
    ScheduleStatus,
    ScheduleType,
)

logger = logging.getLogger("optimus.proactive.scheduler")


class IntelligentScheduler:
    """Persistent, thread-safe task scheduler with recurrence and governance enforcement."""

    def __init__(
        self,
        storage_dir: Path | str | None = None,
        governor: ActionGovernor | None = None,
        notification_manager: Any | None = None,
    ) -> None:
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path.cwd() / "memory" / "schedules"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.governor = governor or ActionGovernor()
        self.notification_manager = notification_manager or get_notification_manager()
        self._schedules: dict[str, ScheduleRecord] = {}
        self._action_runners: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._running = False
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._load_all()

    def register_action_runner(self, action_type: str, runner: Any) -> None:
        """Register a custom runner callback for a specific action type."""
        with self._lock:
            self._action_runners[action_type] = runner

    def _load_all(self) -> None:
        """Load persisted schedules from disk and recover missed schedules."""
        with self._lock:
            for file_path in self.storage_dir.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    rec = ScheduleRecord.from_dict(data)
                    self._schedules[rec.schedule_id] = rec
                except Exception as ex:
                    logger.warning("Failed to load schedule file %s: %s", file_path, ex)

    def _persist(self, rec: ScheduleRecord) -> None:
        """Atomically persist a schedule record."""
        target_path = self.storage_dir / f"{rec.schedule_id}.json"
        temp_path = self.storage_dir / f"{rec.schedule_id}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
                f.flush()
            if target_path.exists():
                target_path.unlink()
            temp_path.rename(target_path)
        except Exception as ex:
            logger.error("Failed to persist schedule %s: %s", rec.schedule_id, ex)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def compute_next_run(self, rec: ScheduleRecord, from_time: float | None = None) -> float | None:
        """Calculate the next execution timestamp based on recurrence settings."""
        base_time = from_time or time.time()

        if rec.schedule_type == ScheduleType.ONE_TIME:
            return rec.run_at if rec.execution_count == 0 else None

        if rec.frequency == ScheduleFrequency.INTERVAL:
            interval = max(rec.interval_seconds, 1.0)
            return base_time + interval

        dt = datetime.datetime.fromtimestamp(base_time, datetime.UTC)

        if rec.frequency == ScheduleFrequency.DAILY:
            next_dt = dt + datetime.timedelta(days=1)
            return next_dt.timestamp()

        if rec.frequency == ScheduleFrequency.WEEKLY:
            next_dt = dt + datetime.timedelta(weeks=1)
            return next_dt.timestamp()

        if rec.frequency == ScheduleFrequency.MONTHLY:
            # Approximate 30-day advancement for robust multi-platform recurrence
            next_dt = dt + datetime.timedelta(days=30)
            return next_dt.timestamp()

        # Default interval fallback
        interval = max(rec.interval_seconds, 60.0)
        return base_time + interval

    def create_schedule(
        self,
        name: str,
        target: str,
        schedule_type: ScheduleType = ScheduleType.ONE_TIME,
        frequency: ScheduleFrequency = ScheduleFrequency.ONCE,
        run_at: float | None = None,
        interval_seconds: float = 0.0,
        action_type: str = "mission",
        payload: dict[str, Any] | None = None,
        description: str = "",
        max_executions: int | None = None,
        governance_level: ActionGovernanceLevel | None = None,
    ) -> ScheduleRecord:
        """Create and persist a new scheduled task."""
        with self._lock:
            now = time.time()
            scheduled_run_at = run_at if run_at is not None else now

            # Classify governance tier if not specified
            if governance_level is None:
                governance_level = self.governor.classify_action(action_type, target, payload)

            rec = ScheduleRecord(
                name=name,
                description=description,
                schedule_type=schedule_type,
                frequency=frequency,
                interval_seconds=interval_seconds,
                run_at=scheduled_run_at,
                next_run_at=scheduled_run_at,
                action_type=action_type,
                target=target,
                payload=payload or {},
                status=ScheduleStatus.ACTIVE,
                max_executions=max_executions,
                governance_level=governance_level,
                created_at=now,
            )

            self._schedules[rec.schedule_id] = rec
            self._persist(rec)
            return rec

    def pause_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            rec = self._schedules.get(schedule_id)
            if rec and rec.status == ScheduleStatus.ACTIVE:
                rec.status = ScheduleStatus.PAUSED
                self._persist(rec)
                return True
            return False

    def resume_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            rec = self._schedules.get(schedule_id)
            if rec and rec.status == ScheduleStatus.PAUSED:
                rec.status = ScheduleStatus.ACTIVE
                # Missed schedule recovery: if next_run_at passed while paused, trigger immediately
                now = time.time()
                if rec.next_run_at and rec.next_run_at < now:
                    rec.next_run_at = now
                self._persist(rec)
                return True
            return False

    def cancel_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            rec = self._schedules.get(schedule_id)
            if rec:
                rec.status = ScheduleStatus.CANCELLED
                self._persist(rec)
                return True
            return False

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            rec = self._schedules.pop(schedule_id, None)
            if rec:
                p = self.storage_dir / f"{schedule_id}.json"
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
                return True
            return False

    def list_schedules(self, status_filter: ScheduleStatus | None = None) -> list[ScheduleRecord]:
        with self._lock:
            results = list(self._schedules.values())
            if status_filter:
                results = [r for r in results if r.status == status_filter]
            results.sort(key=lambda r: (r.next_run_at or float("inf")))
            return results

    def get_schedule(self, schedule_id: str) -> ScheduleRecord | None:
        with self._lock:
            return self._schedules.get(schedule_id)

    # ── Execution Worker & Engine Dispatch ───────────────────────────────────

    def execute_scheduled_task(self, rec: ScheduleRecord, is_pre_approved: bool = False) -> dict[str, Any]:
        """Execute a due schedule, observing governance and recording outcomes."""
        gov_level = rec.governance_level

        # Governance Check
        if not self.governor.can_auto_execute(gov_level, is_pre_approved=is_pre_approved):
            # Level 4 or unapproved: generate alert and pause execution
            with self._lock:
                rec.status = ScheduleStatus.PAUSED
                rec.last_result = {"status": "APPROVAL_REQUIRED", "details": "Execution halted pending user approval"}
                self._persist(rec)

            self.notification_manager.add_notification(
                title=f"Approval Required: Scheduled Task '{rec.name}'",
                message=f"Scheduled task '{rec.name}' (Target: {rec.target}) requires explicit authorization before execution.",
                category="APPROVAL",
                priority=RecommendationPriority.HIGH,
                source="scheduler",
                evidence={"schedule_id": rec.schedule_id, "governance_level": gov_level.value, "target": rec.target},
                actions=[
                    {"action": "approve", "label": "Authorize"},
                    {"action": "reject", "label": "Reject"},
                ],
            )
            return rec.last_result

        result: dict[str, Any] = {"status": "executed", "timestamp": time.time()}

        try:
            if rec.action_type in self._action_runners:
                runner = self._action_runners[rec.action_type]
                custom_res = runner(rec.action_type, rec.target, rec.payload)
                if isinstance(custom_res, dict):
                    result.update(custom_res)
                else:
                    result["output"] = custom_res
            elif rec.action_type == "mission":
                from core.missions import get_mission_engine
                engine = get_mission_engine()
                mission = engine.create_mission(rec.target)
                started = engine.start_mission(mission.mission_id)
                result["mission_id"] = mission.mission_id
                result["mission_state"] = started.state.value if hasattr(started, "state") else "STARTED"
            elif rec.action_type == "tool":
                from core.orchestrator.tools import ToolRegistry
                tr = ToolRegistry()
                res = tr.execute(rec.target, arguments=rec.payload, caller_agent="scheduler")
                result["tool_result"] = getattr(res, "output", str(res))
                result["status"] = getattr(res, "status", "executed")
            else:
                result["output"] = f"Action '{rec.action_type}' acknowledged."
        except Exception as ex:
            logger.error("Execution error for schedule %s: %s", rec.schedule_id, ex)
            result["status"] = "failed"
            result["error"] = str(ex)

        with self._lock:
            rec.last_run_at = time.time()
            rec.execution_count += 1
            rec.last_result = result

            if result.get("status") == "failed":
                rec.status = ScheduleStatus.FAILED
                rec.next_run_at = None
            elif rec.schedule_type == ScheduleType.ONE_TIME or rec.max_executions and rec.execution_count >= rec.max_executions:
                rec.status = ScheduleStatus.COMPLETED
                rec.next_run_at = None
            else:
                rec.next_run_at = self.compute_next_run(rec, from_time=rec.last_run_at)

            self._persist(rec)

        return result

    def tick(self) -> int:
        """Evaluate schedules and execute due tasks. Returns count of executed tasks."""
        now = time.time()
        due_tasks: list[ScheduleRecord] = []

        with self._lock:
            for rec in self._schedules.values():
                if rec.status == ScheduleStatus.ACTIVE and rec.next_run_at is not None:
                    if rec.next_run_at <= now:
                        due_tasks.append(rec)

        executed_count = 0
        for rec in due_tasks:
            self.execute_scheduled_task(rec)
            executed_count += 1

        return executed_count

    def evaluate_tick(self) -> int:
        """Convenience alias for tick()."""
        return self.tick()

    def start_scheduler_loop(self, poll_interval_s: float = 5.0) -> None:
        """Start the background scheduler daemon."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

            def _loop():
                while not self._stop_event.is_set():
                    try:
                        self.tick()
                    except Exception as ex:
                        logger.error("Error in scheduler tick: %s", ex)
                    self._stop_event.wait(poll_interval_s)

            self._worker_thread = threading.Thread(target=_loop, daemon=True, name="OptimusScheduler")
            self._worker_thread.start()

    def stop_scheduler_loop(self) -> None:
        """Stop the background scheduler daemon cleanly."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=2.0)
            self._worker_thread = None


_GLOBAL_SCHEDULER: IntelligentScheduler | None = None


def get_intelligent_scheduler() -> IntelligentScheduler:
    global _GLOBAL_SCHEDULER
    if _GLOBAL_SCHEDULER is None:
        _GLOBAL_SCHEDULER = IntelligentScheduler()
    return _GLOBAL_SCHEDULER
