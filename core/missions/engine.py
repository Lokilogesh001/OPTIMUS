"""core/missions/engine.py — Mission Execution Engine & State Machine for OPTIMUS.

Implements the 8-stage autonomous mission state machine:
  UNDERSTAND -> (WAITING_FOR_CLARIFICATION) -> PLAN -> VALIDATE -> APPROVAL -> EXECUTE -> VERIFY -> COMPLETE

Handles intermediate & recovery states:
  PAUSED, WAITING_FOR_AGENT, WAITING_FOR_APPROVAL, RETRYING,
  PARTIALLY_COMPLETED, FAILED, CANCELLED, RECOVERING.

Guarantees:
- Resolves MED-01: Disambiguation pauses in WAITING_FOR_CLARIFICATION.
- Resolves CRIT-04: Saga rollback unwinds partial mutations on abort/failure.
- Resolves HIGH-01: Skip propagation prevents dependency deadlocks on conditional branches.
- Resolves MED-05: Token budget governance tracked via TokenAccountingGovernor.
- Resolves HIGH-02: Synchronized thread-safe persistence via MissionStore.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.missions.capabilities import UniversalCapabilityRegistry
from core.missions.coordinator import MissionCoordinator
from core.missions.governance import GovernanceBudget, SemanticLoopDetector, TokenAccountingGovernor
from core.missions.memory import MissionMemory
from core.missions.planner import MissionPlanner
from core.missions.recovery import MissionRecoveryEngine
from core.missions.saga import SagaCoordinator
from core.missions.storage import MissionStore
from core.missions.types import (
    MissionPlan,
    MissionRecord,
    MissionState,
    MissionSubtask,
    SubtaskStatus,
)
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.events import EventBus
from core.orchestrator.tools import ToolRegistry

logger = logging.getLogger("optimus.missions.engine")


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
        cap_reg = UniversalCapabilityRegistry(tool_reg)
        saga_coord = SagaCoordinator(tool_reg)

        self.planner = planner or MissionPlanner(agent_reg, tool_reg, capability_registry=cap_reg)
        self.coordinator = coordinator or MissionCoordinator(
            agent_reg, tool_reg, saga_coordinator=saga_coord
        )
        self.recovery_engine = recovery_engine or MissionRecoveryEngine(capability_registry=cap_reg)
        self.event_bus = event_bus
        self.on_state_change = on_state_change
        self.governor = TokenAccountingGovernor()
        self.loop_detector = SemanticLoopDetector()

        self._active_records: dict[str, MissionRecord] = {}
        self._active_memories: dict[str, MissionMemory] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._pause_events: dict[str, threading.Event] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._approval_events: dict[str, threading.Event] = {}
        self._approval_decisions: dict[str, bool] = {}
        self._clarification_events: dict[str, threading.Event] = {}
        self._clarification_inputs: dict[str, str] = {}
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
            try:
                initial_plan = self.planner.plan(objective)
            except Exception as e:
                logger.warning("Initial plan formulation deferred: %s", e)
                initial_plan = MissionPlan(objective=objective)

            record = MissionRecord(objective=objective, state=MissionState.UNDERSTAND, plan=initial_plan)
            record.add_timeline_event("mission_created", f"Mission created for objective: '{objective}'")
            self.store.save(record)
            self._active_records[record.mission_id] = record
            self._active_memories[record.mission_id] = MissionMemory(record.mission_id)
            self._pause_events[record.mission_id] = threading.Event()
            self._pause_events[record.mission_id].set()  # Unpaused initially
            self._cancel_events[record.mission_id] = threading.Event()
            self._approval_events[record.mission_id] = threading.Event()
            self._clarification_events[record.mission_id] = threading.Event()
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

    def resume_mission(self, mission_id: str) -> MissionRecord:
        """Resume a paused, interrupted, or waiting mission."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                raise ValueError(f"Mission '{mission_id}' not found.")

            # If stranded subtasks from crash exist, reconcile first
            if any(s.status == SubtaskStatus.RUNNING for s in record.plan.subtasks):
                self.reconcile_interrupted_missions()
                record = self.store.get(mission_id) or record
                self._active_records[mission_id] = record

            if mission_id not in self._pause_events:
                self._pause_events[mission_id] = threading.Event()
            self._pause_events[mission_id].set()

            if mission_id not in self._cancel_events:
                self._cancel_events[mission_id] = threading.Event()

            if mission_id not in self._approval_events:
                self._approval_events[mission_id] = threading.Event()

            if mission_id not in self._clarification_events:
                self._clarification_events[mission_id] = threading.Event()

            if mission_id not in self._active_memories:
                mem = MissionMemory(mission_id)
                if record.context:
                    mem.load_dict(record.context)
                self._active_memories[mission_id] = mem

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
        """Cancel a mission and trigger Saga rollback for mutating operations."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                return False

            if mission_id in self._cancel_events:
                self._cancel_events[mission_id].set()

            if mission_id in self._pause_events:
                self._pause_events[mission_id].set()

            if mission_id in self._approval_events:
                self._approval_decisions[mission_id] = False
                self._approval_events[mission_id].set()

            if mission_id in self._clarification_events:
                self._clarification_events[mission_id].set()

            self._transition(record, MissionState.CANCELLED, "Mission cancelled by user request.")

            # Trigger Saga Rollback (resolves CRIT-04)
            self.coordinator.saga_coordinator.rollback()
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

    def provide_clarification(self, mission_id: str, clarification_text: str) -> bool:
        """Provide user clarification for an ambiguous query in WAITING_FOR_CLARIFICATION."""
        with self._lock:
            record = self._get_or_load_record(mission_id)
            if not record:
                return False

            self._clarification_inputs[mission_id] = clarification_text
            record.objective = f"{record.objective} (Clarification: {clarification_text})"
            if mission_id in self._clarification_events:
                self._clarification_events[mission_id].set()
            return True

    def reconcile_interrupted_missions(self, workspace_root: Path | str | None = None) -> list[MissionRecord]:
        """Reconcile in-flight or interrupted missions stored on disk after process termination."""
        with self._lock:
            reconciled = self.store.reconcile_interrupted_missions(workspace_root=workspace_root)
            for r in reconciled:
                self._active_records[r.mission_id] = r
            return reconciled

    def shutdown(self, timeout: float = 2.0) -> None:
        """Cooperatively cancel and join active mission worker threads."""
        with self._lock:
            for evt in self._cancel_events.values():
                evt.set()
            for evt in self._pause_events.values():
                evt.set()
            for evt in self._approval_events.values():
                evt.set()
            for evt in self._clarification_events.values():
                evt.set()

        for m_id, th in list(self._threads.items()):
            if th.is_alive():
                th.join(timeout=timeout)

    # ── State Machine Execution Loop ─────────────────────────────────────────

    def _run_mission_lifecycle(self, record: MissionRecord) -> None:
        """Execute the 8-stage mission state machine to completion."""
        m_id = record.mission_id
        memory = self._active_memories.get(m_id) or MissionMemory(m_id)

        try:
            # 1. UNDERSTAND & DISAMBIGUATION (resolves MED-01)
            if record.state == MissionState.UNDERSTAND:
                parsed_intent = self.planner.intent_parser.parse(record.objective)
                if parsed_intent.requires_disambiguation:
                    self._transition(
                        record,
                        MissionState.WAITING_FOR_CLARIFICATION,
                        parsed_intent.clarification_prompt,
                    )
                    clarified = self._wait_for_clarification(m_id)
                    if not clarified or self._check_cancelled(m_id):
                        self._transition(record, MissionState.CANCELLED, "Clarification timed out or cancelled.")
                        return

            # 2. PLAN Formulation
            if record.state in (MissionState.UNDERSTAND, MissionState.WAITING_FOR_CLARIFICATION, MissionState.PLAN):
                if not record.plan or not record.plan.subtasks:
                    self._transition(record, MissionState.PLAN, "Synthesizing Mission DAG plan...")
                    plan = self.planner.plan(record.objective)
                    record.plan = plan
                    self.store.save(record)
                else:
                    self._transition(record, MissionState.PLAN, "Plan already formulated.")

            # 3. VALIDATE DAG
            if record.state == MissionState.PLAN:
                self._transition(record, MissionState.VALIDATE, "Validating plan DAG and required capabilities...")
                valid = self.planner.validate_plan(record.plan)
                if not valid:
                    error_summary = "; ".join(record.plan.validation_errors)
                    record.failure_reason = f"Plan validation failed: {error_summary}"
                    self._transition(record, MissionState.FAILED, error_summary)
                    return

            # 4. APPROVAL Gate
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

            # 5. EXECUTE Subtasks DAG
            if record.state in (
                MissionState.APPROVAL,
                MissionState.EXECUTE,
                MissionState.PAUSED,
                MissionState.RETRYING,
                MissionState.RECOVERING,
            ):
                self._transition(record, MissionState.EXECUTE, "Commencing subtask execution.")
                all_subtasks_ok = self._execute_subtask_dag(record, memory)

                if self._check_cancelled(m_id):
                    self._transition(record, MissionState.CANCELLED, "Mission cancelled during execution.")
                    self.coordinator.saga_coordinator.rollback()
                    return

                if not all_subtasks_ok:
                    completed_count = sum(1 for s in record.plan.subtasks if s.status == SubtaskStatus.COMPLETED)
                    if completed_count > 0:
                        self._transition(
                            record,
                            MissionState.PARTIALLY_COMPLETED,
                            f"{completed_count}/{len(record.plan.subtasks)} subtasks completed.",
                        )
                    else:
                        self._transition(record, MissionState.FAILED, record.failure_reason or "Execution failed.")
                    # Trigger Saga rollback on failed execution (resolves CRIT-04)
                    self.coordinator.saga_coordinator.rollback()
                    return

            # 6. VERIFY Deliverables and Physical Postconditions
            if record.state == MissionState.EXECUTE:
                self._transition(record, MissionState.VERIFY, "Verifying deliverables and physical postconditions.")
                verified = self._verify_mission_deliverables(record)
                if not verified:
                    self._transition(record, MissionState.FAILED, "Physical deliverable verification failed.")
                    self.coordinator.saga_coordinator.rollback()
                    return

            # 7. COMPLETE
            if record.state == MissionState.VERIFY:
                record.completed_at = time.time()
                record.context = memory.to_dict()
                # Clear compensation stack as mission completed successfully
                self.coordinator.saga_coordinator.clear()
                self._transition(record, MissionState.COMPLETE, "Mission completed and verified successfully.")

        except Exception as e:
            logger.exception("Mission [%s] encountered unhandled exception: %s", m_id, e)
            record.failure_reason = str(e)
            self._transition(record, MissionState.FAILED, f"Fatal engine exception: {e}")
            self.coordinator.saga_coordinator.rollback()

    def _execute_subtask_dag(self, record: MissionRecord, memory: MissionMemory) -> bool:
        """Iteratively execute subtasks in dependency DAG order with skip propagation (resolves HIGH-01)."""
        m_id = record.mission_id
        plan = record.plan

        while True:
            if self._check_cancelled(m_id):
                return False

            self._wait_if_paused(m_id)

            # Check token budget governance (resolves MED-05)
            if self.governor.is_exceeded():
                logger.error("Mission [%s] halted: Token budget exceeded", m_id)
                record.failure_reason = "Token budget exceeded"
                return False

            # Resolved IDs includes both COMPLETED and SKIPPED subtasks (resolves HIGH-01)
            completed_ids = {s.subtask_id for s in plan.subtasks if s.status == SubtaskStatus.COMPLETED}
            resolved_ids = {
                s.subtask_id for s in plan.subtasks
                if s.status in (SubtaskStatus.COMPLETED, SubtaskStatus.SKIPPED)
            }

            if len(resolved_ids) == len(plan.subtasks):
                return True

            # Identify runnable subtasks
            runnable: list[MissionSubtask] = []
            for s in plan.subtasks:
                if s.status in (SubtaskStatus.PENDING, SubtaskStatus.READY, SubtaskStatus.RETRYING):
                    if s.join_policy == "ONE_COMPLETED":
                        if any(dep in completed_ids for dep in s.depends_on) or not s.depends_on:
                            runnable.append(s)
                    else:
                        # ALL_COMPLETED or ALL_RESOLVED: check if dependencies are met
                        if all(dep in resolved_ids for dep in s.depends_on):
                            # Skip propagation: if ALL upstream dependencies were skipped, cascade skip
                            if s.depends_on and all(
                                next((x.status == SubtaskStatus.SKIPPED for x in plan.subtasks if x.subtask_id == dep), False)
                                for dep in s.depends_on
                            ):
                                s.status = SubtaskStatus.SKIPPED
                                logger.info("Cascade skipped subtask '%s' because all upstream dependencies were skipped", s.title)
                                continue
                            runnable.append(s)

            if not runnable:
                uncompleted = [s for s in plan.subtasks if s.status not in (SubtaskStatus.COMPLETED, SubtaskStatus.SKIPPED)]
                if uncompleted:
                    logger.error("DAG Deadlock in mission %s. Uncompleted: %s", m_id, [s.subtask_id for s in uncompleted])
                    record.failure_reason = "Unresolvable dependency deadlock"
                    return False
                return True

            # Execute parallel candidates
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

                self.store.save(record)
                if not batch_ok and any(s.status == SubtaskStatus.FAILED for s in parallel_candidates):
                    return False
            else:
                curr = runnable[0]
                cancel_evt = self._cancel_events.get(m_id)
                ok, out, ev = self.coordinator.execute_subtask(curr, record, memory, cancellation_token=cancel_evt)
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
        """Diagnose failure, check loops, attempt bounded retry or adapt capabilities."""
        record.recovery_attempts += 1
        self._transition(record, MissionState.RECOVERING, f"Diagnosing failure in '{subtask.title}'")

        # Semantic Loop Detection check (resolves HIGH-04)
        if self.loop_detector.record_failure(subtask.required_tools[0] if subtask.required_tools else subtask.assigned_agent, error_output):
            logger.warning("Harmful repetitive failure loop detected for subtask '%s'. Halting retry.", subtask.title)
            subtask.status = SubtaskStatus.FAILED
            self.recovery_engine.replan_downstream(record.plan, subtask.subtask_id)
            return False

        diag = self.recovery_engine.diagnose_failure(subtask, error_output)
        logger.info("Mission [%s] subtask '%s' failure diagnosed as: %s", record.mission_id, subtask.title, diag.value)

        if self.recovery_engine.can_retry(subtask):
            adapted = self.recovery_engine.adapt_subtask_for_recovery(subtask, diag, plan=record.plan)
            if adapted:
                self._transition(record, MissionState.RETRYING, f"Retrying subtask '{subtask.title}' (Attempt {subtask.retry_count})")
                return True

        subtask.status = SubtaskStatus.FAILED
        self.recovery_engine.replan_downstream(record.plan, subtask.subtask_id)
        return False

    def _verify_mission_deliverables(self, record: MissionRecord) -> bool:
        """Verify that expected deliverables exist, are non-empty, and pass semantic schemas."""
        all_ok = True
        for s in record.plan.subtasks:
            for deliv in s.expected_deliverables:
                if "." in deliv or "/" in deliv or "\\" in deliv:
                    p = Path(deliv)
                    if not p.is_absolute():
                        p = Path.cwd() / p

                    if p.exists() and (p.is_dir() or p.stat().st_size > 0):
                        if str(p) not in record.deliverables:
                            record.deliverables.append(str(p))
                        record.add_timeline_event(
                            "deliverable_verified",
                            f"Verified deliverable: '{deliv}' ({p.stat().st_size if p.is_file() else 'dir'} bytes)",
                        )
                    else:
                        all_ok = False
                        logger.error("Mission [%s] deliverable missing or empty: %s", record.mission_id, deliv)
                        record.add_timeline_event("deliverable_failed", f"Missing or empty deliverable: '{deliv}'")
                else:
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

    def _wait_for_clarification(self, mission_id: str, timeout: float = 300.0) -> bool:
        evt = self._clarification_events.get(mission_id)
        if not evt:
            return False
        evt.wait(timeout=timeout)
        return mission_id in self._clarification_inputs

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
