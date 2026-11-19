"""core/missions/engine.py — Mission Execution Engine & State Machine for OPTIMUS.

Implements the 7-stage autonomous mission state machine:
  UNDERSTAND -> PLAN -> VALIDATE -> APPROVAL -> EXECUTE -> VERIFY -> COMPLETE

Handles intermediate & recovery states:
  PAUSED, WAITING_FOR_AGENT, WAITING_FOR_APPROVAL, RETRYING,
  PARTIALLY_COMPLETED, FAILED, CANCELLED, RECOVERING.

Guarantees:
- Non-blocking execution thread with pause, resume, and cancellation.
- Continuous persistence via MissionStore after each transition and subtask.
- Physical postcondition and deliverable verification before completion.
- Seamless crash recovery and resumption of interrupted missions.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from core.missions.coordinator import MissionCoordinator
from core.missions.memory import MissionMemory
from core.missions.planner import MissionPlanner
from core.missions.recovery import MissionRecoveryEngine
from core.missions.storage import MissionStore
from core.missions.types import (
    MissionRecord,
    MissionState,
    MissionSubtask,
    SubtaskStatus,
)
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.events import EventBus
from core.orchestrator.tools import ToolRegistry

logger = logging.getLogger(__name__)


class MissionExecutionEngine:
    """The central state machine and lifecycle manager for autonomous multi-step missions."""

    def __init__(
        self,
        store: MissionStore | None = None,
        planner: MissionPlanner | None = None,
        coordinator: MissionCoordinator | None = None,
        recovery_engine: MissionRecoveryEngine | None = None,
        event_bus: EventBus | None = None,
        on_state_change: Callable[[MissionRecord], None] | None = None,
    ) -> None:
        self.store = store or MissionStore()
        agent_reg = AgentRegistry()
        from core.agents.registry_ext import register_specialized_agents
        register_specialized_agents(agent_reg)
        tool_reg = ToolRegistry()
        self.planner = planner or MissionPlanner(agent_reg, tool_reg)
        self.coordinator = coordinator or MissionCoordinator(agent_reg, tool_reg)
        self.recovery_engine = recovery_engine or MissionRecoveryEngine()
        self.event_bus = event_bus
        self.on_state_change = on_state_change

        self._active_records: dict[str, MissionRecord] = {}
        self._active_memories: dict[str, MissionMemory] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._pause_events: dict[str, threading.Event] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._approval_events: dict[str, threading.Event] = {}
        self._approval_decisions: dict[str, bool] = {}
        self._lock = threading.RLock()

    # ── State Transition Helper ──────────────────────────────────────────────

    def _transition(self, record: MissionRecord, new_state: MissionState, detail: str = "") -> None:
        """Atomically transition state, record timeline event, persist to store, and notify callbacks."""
        with self._lock:
            old_state = record.state
            record.state = new_state
            record.updated_at = time.time()
            record.add_timeline_event(
                event_type="state_transition",
                details=f"Transitioned from {old_state.value} to {new_state.value}. {detail}".strip(),
                data={"from_state": old_state.value, "to_state": new_state.value},
            )
            self.store.save(record)

        logger.info("Mission [%s] state: %s -> %s (%s)", record.mission_id, old_state.value, new_state.value, detail)
        if self.on_state_change:
            try:
                self.on_state_change(record)
            except Exception as e:
                logger.error("Error in on_state_change callback: %s", e)

    # ── Mission Lifecycle Entry Points ───────────────────────────────────────

    def create_mission(self, objective: str) -> MissionRecord:
        """Create and persist a new mission record in UNDERSTAND state."""
        with self._lock:
            record = MissionRecord(objective=objective, state=MissionState.UNDERSTAND)
            record.add_timeline_event("mission_created", f"Mission created for objective: '{objective}'")
            self.store.save(record)
            self._active_records[record.mission_id] = record
            self._active_memories[record.mission_id] = MissionMemory(record.mission_id)
            self._pause_events[record.mission_id] = threading.Event()
            self._pause_events[record.mission_id].set()  # Unpaused initially
            self._cancel_events[record.mission_id] = threading.Event()
            self._approval_events[record.mission_id] = threading.Event()
            return record

    def start_mission(self, mission_id: str) -> MissionRecord:
        """Launch autonomous execution of a created or ready mission in a background thread."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                raise ValueError(f"Mission '{mission_id}' not found.")

            if record.mission_id in self._threads and self._threads[record.mission_id].is_alive():
                logger.warning("Mission '%s' is already executing.", mission_id)
                return record

            thread = threading.Thread(
                target=self._run_mission_lifecycle,
                args=(record,),
                name=f"OptimusMissionThread-{mission_id}",
                daemon=True,
            )
            self._threads[mission_id] = thread
            thread.start()
            return record

    def pause_mission(self, mission_id: str) -> bool:
        """Pause a currently running mission."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record or record.state not in (MissionState.EXECUTE, MissionState.WAITING_FOR_AGENT, MissionState.RETRYING):
                return False

            if mission_id in self._pause_events:
                self._pause_events[mission_id].clear()
            self._transition(record, MissionState.PAUSED, "Mission paused by user request.")
            return True

    def reconcile_interrupted_missions(self, workspace_root: Path | str | None = None) -> list[MissionRecord]:
        """Reconcile in-flight or interrupted missions stored on disk after process termination."""
        with self._lock:
            reconciled = self.store.reconcile_interrupted_missions(workspace_root=workspace_root)
            for r in reconciled:
                self._active_records[r.mission_id] = r
            return reconciled

    def resume_mission(self, mission_id: str) -> MissionRecord:
        """Resume a paused, interrupted, or waiting mission."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                raise ValueError(f"Mission '{mission_id}' not found.")

            # If mission has stranded RUNNING subtasks from a crash, reconcile them first
            if any(s.status == SubtaskStatus.RUNNING for s in record.plan.subtasks):
                self.reconcile_interrupted_missions()
                record = self.store.get(mission_id) or record
                self._active_records[mission_id] = record

            # Ensure event flags exist
            if mission_id not in self._pause_events:
                self._pause_events[mission_id] = threading.Event()
            self._pause_events[mission_id].set()

            if mission_id not in self._cancel_events:
                self._cancel_events[mission_id] = threading.Event()

            if mission_id not in self._approval_events:
                self._approval_events[mission_id] = threading.Event()

            # Ensure memory bus exists
            if mission_id not in self._active_memories:
                mem = MissionMemory(mission_id)
                if record.context:
                    mem.load_dict(record.context)
                self._active_memories[mission_id] = mem

            # If background thread died (e.g. app restart), launch fresh worker thread
            if mission_id not in self._threads or not self._threads[mission_id].is_alive():
                thread = threading.Thread(
                    target=self._run_mission_lifecycle,
                    args=(record,),
                    name=f"OptimusMissionThread-{mission_id}",
                    daemon=True,
                )
                self._threads[mission_id] = thread
                thread.start()

            return record

    def cancel_mission(self, mission_id: str) -> bool:
        """Cancel a mission."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                return False

            if mission_id in self._cancel_events:
                self._cancel_events[mission_id].set()

            # If paused, unblock pause event so worker thread can terminate
            if mission_id in self._pause_events:
                self._pause_events[mission_id].set()

            # If waiting for approval, unblock approval event
            if mission_id in self._approval_events:
                self._approval_decisions[mission_id] = False
                self._approval_events[mission_id].set()

            self._transition(record, MissionState.CANCELLED, "Mission cancelled by user request.")
            return True

    def approve_mission(self, mission_id: str) -> bool:
        """Grant approval for a mission in WAITING_FOR_APPROVAL."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                return False

            self._approval_decisions[mission_id] = True
            if mission_id in self._approval_events:
                self._approval_events[mission_id].set()
            return True

    def reject_mission(self, mission_id: str) -> bool:
        """Deny approval for a mission in WAITING_FOR_APPROVAL."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                return False

            self._approval_decisions[mission_id] = False
            if mission_id in self._approval_events:
                self._approval_events[mission_id].set()
            return True

    def shutdown(self, timeout: float = 2.0) -> None:
        """Cooperatively cancel and join all active mission background worker threads."""
        with self._lock:
            for evt in self._cancel_events.values():
                evt.set()
            for evt in self._pause_events.values():
                evt.set()
            for evt in self._approval_events.values():
                evt.set()

        for m_id, th in list(self._threads.items()):
            if th.is_alive():
                th.join(timeout=timeout)

    # ── State Machine Execution Loop ─────────────────────────────────────────

    def _run_mission_lifecycle(self, record: MissionRecord) -> None:
        """Execute the 7-stage mission state machine to completion."""
        m_id = record.mission_id
        memory = self._active_memories.get(m_id) or MissionMemory(m_id)

        try:
            # 1. UNDERSTAND -> PLAN
            if record.state in (MissionState.UNDERSTAND, MissionState.PLAN):
                if not record.plan or not record.plan.subtasks:
                    self._transition(record, MissionState.PLAN, "Synthesizing Mission DAG plan...")
                    plan = self.planner.plan(record.objective)
                    record.plan = plan
                    self.store.save(record)
                else:
                    self._transition(record, MissionState.PLAN, "Plan already formulated.")

            # 2. VALIDATE
            if record.state == MissionState.PLAN:
                self._transition(record, MissionState.VALIDATE, "Validating plan DAG and required capabilities...")
                valid = self.planner.validate_plan(record.plan)
                if not valid:
                    error_summary = "; ".join(record.plan.validation_errors)
                    record.failure_reason = f"Plan validation failed: {error_summary}"
                    self._transition(record, MissionState.FAILED, error_summary)
                    return

            # 3. APPROVAL Gate
            if record.state == MissionState.VALIDATE:
                if record.plan.requires_approval:
                    self._transition(record, MissionState.WAITING_FOR_APPROVAL, "Plan requires user authorization.")
                    approved = self._wait_for_approval(m_id)
                    if not approved:
                        self._transition(record, MissionState.CANCELLED, "Mission rejected by user.")
                        return
                    self._transition(record, MissionState.APPROVAL, "Authorization granted.")
                else:
                    self._transition(record, MissionState.APPROVAL, "No elevated approval needed.")

            # 4. EXECUTE Subtasks
            if record.state in (MissionState.APPROVAL, MissionState.EXECUTE, MissionState.PAUSED, MissionState.RETRYING, MissionState.RECOVERING):
                self._transition(record, MissionState.EXECUTE, "Commencing subtask execution.")
                all_subtasks_ok = self._execute_subtask_dag(record, memory)

                if self._check_cancelled(m_id):
                    self._transition(record, MissionState.CANCELLED, "Mission cancelled during execution.")
                    return

                if not all_subtasks_ok:
                    completed_count = sum(1 for s in record.plan.subtasks if s.status == SubtaskStatus.COMPLETED)
                    if completed_count > 0:
                        self._transition(record, MissionState.PARTIALLY_COMPLETED, f"{completed_count}/{len(record.plan.subtasks)} subtasks completed.")
                    else:
                        self._transition(record, MissionState.FAILED, record.failure_reason or "Execution failed.")
                    return

            # 5. VERIFY Deliverables and Postconditions
            if record.state == MissionState.EXECUTE:
                self._transition(record, MissionState.VERIFY, "Verifying deliverables and physical postconditions.")
                verified = self._verify_mission_deliverables(record)
                if not verified:
                    self._transition(record, MissionState.FAILED, "Physical deliverable verification failed.")
                    return

            # 6. COMPLETE
            if record.state == MissionState.VERIFY:
                record.completed_at = time.time()
                # Persist final shared context
                record.context = memory.to_dict()
                self._transition(record, MissionState.COMPLETE, "Mission completed and verified successfully.")

        except Exception as e:
            logger.exception("Mission [%s] encountered unhandled exception: %s", m_id, e)
            record.failure_reason = str(e)
            self._transition(record, MissionState.FAILED, f"Fatal engine exception: {e}")

    def _execute_subtask_dag(self, record: MissionRecord, memory: MissionMemory) -> bool:
        """Iteratively execute subtasks in dependency DAG order with concurrency support."""
        m_id = record.mission_id
        plan = record.plan

        while True:
            # Check cancellation
            if self._check_cancelled(m_id):
                return False

            # Check pause
            self._wait_if_paused(m_id)

            # Determine completed subtask IDs
            completed_ids = {s.subtask_id for s in plan.subtasks if s.status == SubtaskStatus.COMPLETED}

            # Check if all subtasks are finished
            if len(completed_ids) == len(plan.subtasks):
                return True

            # Find ready subtasks whose dependencies are all satisfied
            runnable: list[MissionSubtask] = []
            for s in plan.subtasks:
                if (
                    s.status in (SubtaskStatus.PENDING, SubtaskStatus.READY, SubtaskStatus.RETRYING)
                    and all(dep in completed_ids for dep in s.depends_on)
                ):
                    runnable.append(s)

            if not runnable:
                # Deadlock check: remaining uncompleted subtasks have unsatisfied dependencies
                uncompleted = [s for s in plan.subtasks if s.status != SubtaskStatus.COMPLETED]
                logger.error("DAG Deadlock in mission %s. Uncompleted: %s", m_id, [s.subtask_id for s in uncompleted])
                record.failure_reason = "Unresolvable dependency deadlock"
                return False

            # Check if multiple independent runnable subtasks can run in parallel
            parallel_candidates = [s for s in runnable if s.parallelizable]
            if len(parallel_candidates) > 1:
                batch_results = self.coordinator.execute_concurrent_group(parallel_candidates, record, memory)
                batch_ok = True
                for s in parallel_candidates:
                    res_ok, out, ev = batch_results.get(s.subtask_id, (False, "Timeout", []))
                    record.evidence_ledger.extend(ev)
                    if not res_ok:
                        batch_ok = False
                        self._handle_subtask_failure(record, s, out, memory)
                
                # Persist progress
                self.store.save(record)
                if not batch_ok and any(s.status == SubtaskStatus.FAILED for s in parallel_candidates):
                    return False
            else:
                # Run single subtask
                curr = runnable[0]
                ok, out, ev = self.coordinator.execute_subtask(curr, record, memory)
                record.evidence_ledger.extend(ev)
                self.store.save(record)

                if not ok:
                    recovered = self._handle_subtask_failure(record, curr, out, memory)
                    if not recovered:
                        record.failure_reason = f"Subtask '{curr.title}' failed: {out}"
                        return False

        return True

    def _handle_subtask_failure(
        self,
        record: MissionRecord,
        subtask: MissionSubtask,
        error_output: str,
        memory: MissionMemory,
    ) -> bool:
        """Diagnose failure, attempt bounded retry or adapt capabilities."""
        record.recovery_attempts += 1
        self._transition(record, MissionState.RECOVERING, f"Diagnosing failure in '{subtask.title}'")

        diag = self.recovery_engine.diagnose_failure(subtask, error_output)
        logger.info("Mission [%s] subtask '%s' failure diagnosed as: %s", record.mission_id, subtask.title, diag.value)

        available_tools = [t.name for t in self.coordinator.tool_registry.list_tools()]
        available_agents = [a.name for a in self.coordinator.agent_registry.list_agents()]

        if self.recovery_engine.can_retry(subtask):
            adapted = self.recovery_engine.adapt_subtask_for_recovery(subtask, diag, available_tools, available_agents)
            if adapted:
                self._transition(record, MissionState.RETRYING, f"Retrying subtask '{subtask.title}' (Attempt {subtask.retry_count})")
                return True

        # Unrecoverable for this subtask
        subtask.status = SubtaskStatus.FAILED
        self.recovery_engine.replan_downstream(record.plan, subtask.subtask_id)
        return False

    def _verify_mission_deliverables(self, record: MissionRecord) -> bool:
        """Verify that expected deliverables (files and memory contexts) exist and are non-empty."""
        all_ok = True
        for s in record.plan.subtasks:
            for deliv in s.expected_deliverables:
                # If deliverable looks like a file path (has extension or / or \)
                if "." in deliv or "/" in deliv or "\\" in deliv:
                    p = Path(deliv)
                    if not p.is_absolute():
                        p = Path.cwd() / p
                    
                    if p.exists() and p.stat().st_size > 0:
                        if str(p) not in record.deliverables:
                            record.deliverables.append(str(p))
                        record.add_timeline_event("deliverable_verified", f"Verified deliverable: '{deliv}' ({p.stat().st_size} bytes)")
                    else:
                        all_ok = False
                        logger.error("Mission [%s] deliverable missing or empty: %s", record.mission_id, deliv)
                        record.add_timeline_event("deliverable_failed", f"Missing or empty deliverable: '{deliv}'")
                else:
                    # In-memory context deliverable
                    if s.result_output and len(s.result_output.strip()) > 0:
                        record.add_timeline_event("deliverable_verified", f"Verified context deliverable: '{deliv}'")
                    else:
                        all_ok = False
                        logger.error("Mission [%s] context deliverable empty: %s", record.mission_id, deliv)
                        record.add_timeline_event("deliverable_failed", f"Empty context deliverable: '{deliv}'")

        return all_ok

    # ── Flow Control Helpers ─────────────────────────────────────────────────

    def _wait_if_paused(self, mission_id: str) -> None:
        evt = self._pause_events.get(mission_id)
        if evt:
            evt.wait()

    def _check_cancelled(self, mission_id: str) -> bool:
        evt = self._cancel_events.get(mission_id)
        return evt.is_set() if evt else False

    def _wait_for_approval(self, mission_id: str, timeout: float = 300.0) -> bool:
        evt = self._approval_events.get(mission_id)
        if not evt:
            return False
        evt.wait(timeout=timeout)
        return self._approval_decisions.get(mission_id, False)

    def _get_or_load_record(self, mission_id: str) -> MissionRecord | None:
        if mission_id in self._active_records:
            return self._active_records[mission_id]
        rec = self.store.get(mission_id)
        if rec:
            self._active_records[mission_id] = rec
        return rec


_global_engine: MissionExecutionEngine | None = None
_engine_lock = threading.RLock()


def get_mission_engine() -> MissionExecutionEngine:
    """Retrieve or initialize the global MissionExecutionEngine instance."""
    global _global_engine
    with _engine_lock:
        if _global_engine is None:
            _global_engine = MissionExecutionEngine()
        return _global_engine
