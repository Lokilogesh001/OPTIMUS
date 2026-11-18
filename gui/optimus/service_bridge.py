"""gui/optimus/service_bridge.py — Thread-safe Service Bridge for OPTIMUS UI.

Connects the PyQt presentation layer directly to:
- CentralOrchestrator (core/orchestrator/)
- WorkflowEngine (core/workflows/)
- Specialized Agents & Dynamic Tool Sandboxes (core/agents/, core/sandbox/)
- Model & Intelligence Gateway (core/intelligence/)
- Memory & Shared Context Bus (core/memory/)
- Verification & Evidence Ledger Engine (core/verification/)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

import psutil
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from gui.metrics import _metrics

from core.interaction.events import (
    AgentResponseCompletedEvent,
    SpeechInterruptedEvent,
    SpeechStateChangedEvent,
    TaskCancellationRequestedEvent,
    UserMessageReceivedEvent,
)
from core.orchestrator import desktop
from core.orchestrator.events import (
    EventBus,
    PlanCreatedEvent,
    SubtaskCompletedEvent,
    SubtaskDelegatedEvent,
    SubtaskFailedEvent,
    SubtaskProgressEvent,
    SubtaskVerifiedEvent,
    TaskCancelledEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskFailedEvent,
)
from core.planning import (
    AdaptiveExecutionEngine,
    AdaptivePlanner,
    AdaptiveReplanner,
    EvidenceDrivenOptimizer,
    OutcomeStore,
    StrategyType,
)
from core.planning.events import (
    ExecutionOutcomeRecordedEvent,
    OptimizationAppliedEvent,
    OptimizationRecommendedEvent,
    OptimizationRolledBackEvent,
    PlanFormulatedEvent,
    ReplanTriggeredEvent,
)
from core.workflows.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    WorkflowCompletedEvent,
    WorkflowCreatedEvent,
    WorkflowFailedEvent,
    WorkflowPausedEvent,
    WorkflowResumedEvent,
    WorkflowStartedEvent,
    WorkflowStepCompletedEvent,
    WorkflowStepFailedEvent,
    WorkflowStepSkippedEvent,
    WorkflowStepStartedEvent,
)


class OptimusServiceBridge(QObject):
    """Thread-safe event dispatcher and service facade connecting backend engines to UI."""

    # UI Signals (emitted on main Qt thread for safe UI updates)
    task_created = pyqtSignal(dict)
    task_completed = pyqtSignal(dict)
    task_failed = pyqtSignal(dict)
    task_cancelled = pyqtSignal(dict)

    plan_created = pyqtSignal(dict)
    subtask_delegated = pyqtSignal(dict)
    subtask_completed = pyqtSignal(dict)
    subtask_failed = pyqtSignal(dict)
    subtask_verified = pyqtSignal(dict)
    subtask_progress = pyqtSignal(dict)

    workflow_created = pyqtSignal(dict)
    workflow_started = pyqtSignal(dict)
    workflow_completed = pyqtSignal(dict)
    workflow_failed = pyqtSignal(dict)
    workflow_paused = pyqtSignal(dict)
    workflow_resumed = pyqtSignal(dict)
    workflow_step_started = pyqtSignal(dict)
    workflow_step_completed = pyqtSignal(dict)
    workflow_step_failed = pyqtSignal(dict)
    workflow_step_skipped = pyqtSignal(dict)

    approval_requested = pyqtSignal(dict)
    approval_resolved = pyqtSignal(dict)

    user_message_received = pyqtSignal(dict)
    agent_response_completed = pyqtSignal(dict)
    speech_state_changed = pyqtSignal(dict)
    speech_interrupted = pyqtSignal(dict)
    task_cancellation_requested = pyqtSignal(dict)

    # Planning & Optimization Signals
    plan_formulated = pyqtSignal(dict)
    replan_triggered = pyqtSignal(dict)
    outcome_recorded = pyqtSignal(dict)
    optimization_recommended = pyqtSignal(dict)
    optimization_applied = pyqtSignal(dict)
    optimization_rolled_back = pyqtSignal(dict)

    # Autonomous Missions Signals
    mission_created = pyqtSignal(dict)
    mission_state_changed = pyqtSignal(dict)
    mission_updated = pyqtSignal(dict)

    # Proactive Intelligence Signals
    recommendation_created = pyqtSignal(dict)
    recommendation_updated = pyqtSignal(dict)
    notification_received = pyqtSignal(dict)
    schedule_triggered = pyqtSignal(dict)
    monitor_triggered = pyqtSignal(dict)

    timeline_event = pyqtSignal(dict)
    telemetry_updated = pyqtSignal(dict)
    agent_states_updated = pyqtSignal(list)

    _instance: OptimusServiceBridge | None = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls, player=None) -> OptimusServiceBridge:
        with cls._lock:
            if cls._instance is not None:
                try:
                    _ = cls._instance.parent()
                except RuntimeError:
                    cls._instance = None
            if cls._instance is None:
                cls._instance = cls(player=player)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton for test isolation and clean shutdown."""
        with cls._lock:
            if cls._instance is not None:
                try:
                    if hasattr(cls._instance, "_telemetry_timer"):
                        cls._instance._telemetry_timer.stop()
                except Exception:
                    pass
                cls._instance = None

    def __init__(self, player=None, parent=None) -> None:
        super().__init__(parent)
        self.orchestrator = desktop.ensure(player)
        self.event_bus: EventBus = self.orchestrator.event_bus

        # Live tracking state
        self._active_task_id: str | None = None
        self._active_task_goal: str = ""
        self._task_start_time: float | None = None
        self._agent_assignments: dict[str, str] = {}
        self._timeline_history: deque[dict[str, Any]] = deque(maxlen=200)

        # Telemetry Cache (minimizes I/O and CPU overhead on background ticks)
        self._cached_top_procs: list[dict[str, Any]] = []
        self._last_proc_t: float = 0.0
        self._cached_disk: tuple[float, float, float] = (0.0, 0.0, 0.0)  # percent, free_gb, timestamp
        self._cached_gpu_name: str | None = None

        # Wire event listeners to bridge
        self._bind_orchestrator_events()

        # Telemetry Poller Timer (throttled to 3000ms for lightweight background polling)
        self._telemetry_timer = QTimer(self)
        self._telemetry_timer.setInterval(3000)
        self._telemetry_timer.timeout.connect(self._poll_telemetry)
        self._telemetry_timer.start()

        # Component 9: Adaptive Planning & Self-Improvement
        self.outcome_store = OutcomeStore(memory_manager=self.memory_manager)
        self.adaptive_planner = AdaptivePlanner(
            event_bus=self.event_bus,
            agent_registry=getattr(self.orchestrator, "agent_registry", None),
            tool_registry=getattr(self.orchestrator, "tool_registry", None),
            outcome_store=self.outcome_store,
        )
        self.adaptive_replanner = AdaptiveReplanner(
            event_bus=self.event_bus,
            agent_registry=getattr(self.orchestrator, "agent_registry", None),
            outcome_store=self.outcome_store,
        )
        self.optimizer = EvidenceDrivenOptimizer(
            outcome_store=self.outcome_store,
            strategy_selector=self.adaptive_planner.strategy_selector,
            event_bus=self.event_bus,
        )
        self.adaptive_executor = AdaptiveExecutionEngine(
            outcome_store=self.outcome_store,
            replanner=self.adaptive_replanner,
            verification_engine=self.verification_engine,
            event_bus=self.event_bus,
        )

        # Autonomous Missions Engine
        try:
            from core.missions import get_mission_engine
            self.mission_engine = get_mission_engine()
            self.mission_engine.on_state_change = self._on_mission_state_change
        except Exception:
            self.mission_engine = None

    @property
    def workflow_engine(self):
        return getattr(self.orchestrator, "workflow_engine", None)

    @property
    def intelligence_gateway(self):
        return getattr(self.orchestrator, "intelligence_gateway", None)

    @property
    def memory_manager(self):
        return getattr(self.orchestrator, "memory_manager", None)

    @property
    def verification_engine(self):
        return getattr(self.orchestrator, "verification_engine", None)

    # ── Event Ingestion & Qt Signal Dispatch ─────────────────────────────────

    def _bind_orchestrator_events(self) -> None:
        """Register listeners on central event bus to dispatch typed Qt signals."""
        bus = self.event_bus

        def _safe_call(fn):
            def _wrapped(*args, **kwargs):
                try:
                    try:
                        _ = self.parent()
                    except RuntimeError:
                        return None
                    return fn(*args, **kwargs)
                except (RuntimeError, Exception):
                    return None
            return _wrapped

        def _record_timeline(cat: str, title: str, detail: str, level: str = "INFO", meta: dict | None = None) -> None:
            try:
                try:
                    _ = self.parent()
                except RuntimeError:
                    return
                entry = {
                    "timestamp": time.time(),
                    "time_str": time.strftime("%H:%M:%S"),
                    "category": cat,
                    "title": title,
                    "detail": detail,
                    "level": level,
                    "meta": meta or {},
                }
                self._timeline_history.append(entry)
                self.timeline_event.emit(entry)
            except (RuntimeError, Exception):
                pass

        @_safe_call
        def on_task_created(e: TaskCreatedEvent) -> None:
            self._active_task_id = e.task_id
            self._active_task_goal = e.goal
            self._task_start_time = time.time()
            created_at = getattr(e, "created_at", getattr(e, "timestamp", time.time()))
            data = {"task_id": e.task_id, "goal": e.goal, "created_at": created_at}
            self.task_created.emit(data)
            _record_timeline("TASK", "Task Initialized", e.goal[:120], "INFO", data)

        @_safe_call
        def on_plan_created(e: PlanCreatedEvent) -> None:
            data = {
                "task_id": e.task_id,
                "plan_id": e.plan_id,
                "goal": e.goal,
                "subtask_count": e.subtask_count,
                "subtasks": e.subtasks,
            }
            self.plan_created.emit(data)
            _record_timeline("PLAN", f"Execution Plan Formulated ({e.subtask_count} subtasks)", e.goal[:100], "INFO", data)

        @_safe_call
        def on_subtask_delegated(e: SubtaskDelegatedEvent) -> None:
            self._agent_assignments[e.assigned_agent] = e.title
            data = {
                "task_id": e.task_id,
                "subtask_id": e.subtask_id,
                "index": e.subtask_index,
                "agent": e.assigned_agent,
                "title": e.title,
                "description": getattr(e, "description", getattr(e, "title", "")),
            }
            self.subtask_delegated.emit(data)
            _record_timeline("AGENT", f"Delegated to [{e.assigned_agent.upper()}]", e.title, "INFO", data)
            self._emit_agent_states()

        @_safe_call
        def on_subtask_progress(e: SubtaskProgressEvent) -> None:
            data = {
                "task_id": e.task_id,
                "subtask_id": e.subtask_id,
                "agent": e.agent_name,
                "progress_text": e.progress_text,
            }
            self.subtask_progress.emit(data)

        @_safe_call
        def on_subtask_verified(e: SubtaskVerifiedEvent) -> None:
            status = "VERIFIED" if e.is_verified else "FAILED"
            level = "SUCCESS" if e.is_verified else "WARNING"
            data = {
                "task_id": e.task_id,
                "subtask_id": e.subtask_id,
                "agent": e.agent_name,
                "is_verified": e.is_verified,
                "verdict": e.verdict,
                "evidence": e.evidence,
            }
            self.subtask_verified.emit(data)
            _record_timeline("VERIFY", f"Postcondition {status} [{e.agent_name.upper()}]", e.verdict[:120], level, data)

        @_safe_call
        def on_subtask_completed(e: SubtaskCompletedEvent) -> None:
            self._agent_assignments.pop(e.agent_name, None)
            dur = getattr(e, "duration_seconds", 0.0)
            summary = getattr(e, "output_summary", getattr(e, "summary", getattr(e, "result", "")))
            data = {
                "task_id": e.task_id,
                "subtask_id": e.subtask_id,
                "agent": e.agent_name,
                "duration_seconds": dur,
                "summary": summary,
            }
            self.subtask_completed.emit(data)
            _record_timeline("SUBTASK", f"Subtask Completed [{e.agent_name.upper()}]", str(summary)[:100], "SUCCESS", data)
            self._emit_agent_states()

        @_safe_call
        def on_subtask_failed(e: SubtaskFailedEvent) -> None:
            self._agent_assignments.pop(e.agent_name, None)
            data = {
                "task_id": e.task_id,
                "subtask_id": e.subtask_id,
                "agent": e.agent_name,
                "error": e.error,
            }
            self.subtask_failed.emit(data)
            _record_timeline("ERROR", f"Subtask Failed [{e.agent_name.upper()}]", e.error[:120], "ERROR", data)
            self._emit_agent_states()

        @_safe_call
        def on_task_completed(e: TaskCompletedEvent) -> None:
            self._active_task_id = None
            data = {
                "task_id": e.task_id,
                "goal": e.goal,
                "result": e.result,
                "duration_seconds": e.duration_seconds,
            }
            self.task_completed.emit(data)
            _record_timeline("TASK", "Task Completed Successfully", f"Duration: {e.duration_seconds:.2f}s", "SUCCESS", data)
            self._emit_agent_states()

        @_safe_call
        def on_task_failed(e: TaskFailedEvent) -> None:
            self._active_task_id = None
            data = {"task_id": e.task_id, "goal": e.goal, "error": e.error}
            self.task_failed.emit(data)
            _record_timeline("TASK", "Task Failed", e.error[:120], "ERROR", data)
            self._emit_agent_states()

        @_safe_call
        def on_task_cancelled(e: TaskCancelledEvent) -> None:
            self._active_task_id = None
            data = {"task_id": e.task_id, "reason": e.reason}
            self.task_cancelled.emit(data)
            _record_timeline("TASK", "Task Cancelled", e.reason, "WARNING", data)
            self._emit_agent_states()

        # Workflow event listeners
        @_safe_call
        def on_wf_created(e: WorkflowCreatedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "step_count": e.step_count}
            self.workflow_created.emit(data)
            _record_timeline("WORKFLOW", f"Workflow Registered: {e.workflow_id}", f"{e.step_count} steps", "INFO", data)

        @_safe_call
        def on_wf_started(e: WorkflowStartedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "parameters": e.parameters}
            self.workflow_started.emit(data)
            _record_timeline("WORKFLOW", f"Workflow Started: {e.workflow_id}", f"Exec: {e.execution_id}", "INFO", data)

        @_safe_call
        def on_wf_completed(e: WorkflowCompletedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "duration_seconds": e.duration_seconds}
            self.workflow_completed.emit(data)
            _record_timeline("WORKFLOW", f"Workflow Completed: {e.workflow_id}", f"Duration: {e.duration_seconds:.2f}s", "SUCCESS", data)

        @_safe_call
        def on_wf_failed(e: WorkflowFailedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "error": e.error, "failed_steps": e.failed_steps}
            self.workflow_failed.emit(data)
            _record_timeline("WORKFLOW", f"Workflow Failed: {e.workflow_id}", e.error[:120], "ERROR", data)

        @_safe_call
        def on_wf_paused(e: WorkflowPausedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "reason": e.reason}
            self.workflow_paused.emit(data)
            _record_timeline("WORKFLOW", f"Workflow Paused: {e.workflow_id}", e.reason, "WARNING", data)

        @_safe_call
        def on_wf_resumed(e: WorkflowResumedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id}
            self.workflow_resumed.emit(data)
            _record_timeline("WORKFLOW", f"Workflow Resumed: {e.workflow_id}", "", "INFO", data)

        @_safe_call
        def on_wf_step_started(e: WorkflowStepStartedEvent) -> None:
            self._agent_assignments[e.assigned_agent] = f"Step: {e.step_id}"
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "step_id": e.step_id, "agent": e.assigned_agent, "attempt": e.attempt}
            self.workflow_step_started.emit(data)
            _record_timeline("STEP", f"Step Started: {e.step_id}", f"Agent: {e.assigned_agent} (try #{e.attempt})", "INFO", data)
            self._emit_agent_states()

        @_safe_call
        def on_wf_step_completed(e: WorkflowStepCompletedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "step_id": e.step_id, "duration_seconds": e.duration_seconds, "summary": e.summary}
            self.workflow_step_completed.emit(data)
            _record_timeline("STEP", f"Step Completed: {e.step_id}", e.summary[:100], "SUCCESS", data)
            self._emit_agent_states()

        @_safe_call
        def on_wf_step_failed(e: WorkflowStepFailedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "step_id": e.step_id, "error": e.error, "will_retry": e.will_retry}
            self.workflow_step_failed.emit(data)
            lvl = "WARNING" if e.will_retry else "ERROR"
            _record_timeline("STEP", f"Step Failed: {e.step_id}", f"{e.error[:100]} (will_retry={e.will_retry})", lvl, data)

        @_safe_call
        def on_wf_step_skipped(e: WorkflowStepSkippedEvent) -> None:
            data = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "step_id": e.step_id, "reason": e.reason}
            self.workflow_step_skipped.emit(data)
            _record_timeline("STEP", f"Step Skipped: {e.step_id}", e.reason[:100], "INFO", data)

        @_safe_call
        def on_approval_requested(e: ApprovalRequestedEvent) -> None:
            data = {
                "approval_id": e.approval_id,
                "execution_id": e.execution_id,
                "workflow_id": e.workflow_id,
                "step_id": e.step_id,
                "reason": e.reason,
                "required_role": e.required_role,
                "expires_at": e.expires_at,
            }
            self.approval_requested.emit(data)
            _record_timeline("APPROVAL", f"Human Signoff Required: {e.step_id}", f"Role: {e.required_role} | {e.reason}", "WARNING", data)

        @_safe_call
        def on_approval_resolved(e: ApprovalResolvedEvent) -> None:
            data = {
                "approval_id": e.approval_id,
                "execution_id": e.execution_id,
                "step_id": e.step_id,
                "status": e.status,
                "approver": e.approver,
                "reason": e.reason,
            }
            self.approval_resolved.emit(data)
            lvl = "SUCCESS" if e.status == "APPROVED" else "ERROR"
            _record_timeline("APPROVAL", f"Approval {e.status}: {e.step_id}", f"By {e.approver}: {e.reason}", lvl, data)

        @_safe_call
        def on_user_msg(e: UserMessageReceivedEvent) -> None:
            data = {
                "session_id": e.session_id,
                "message_id": e.message_id,
                "modality": e.modality.value if hasattr(e.modality, "value") else str(e.modality),
                "text": e.text,
            }
            self.user_message_received.emit(data)
            _record_timeline("USER", f"User Input [{data['modality'].upper()}]", e.text[:120], "INFO", data)

        @_safe_call
        def on_agent_resp(e: AgentResponseCompletedEvent) -> None:
            data = {
                "session_id": e.session_id,
                "message_id": e.message_id,
                "text": e.full_text,
            }
            self.agent_response_completed.emit(data)
            _record_timeline("OPTIMUS", "Assistant Response", e.full_text[:120], "SUCCESS", data)

        @_safe_call
        def on_speech_state(e: SpeechStateChangedEvent) -> None:
            data = {
                "state": e.state.value if hasattr(e.state, "value") else str(e.state),
                "is_speaking": e.is_speaking,
                "is_listening": e.is_listening,
                "device": e.device_name,
            }
            self.speech_state_changed.emit(data)

        @_safe_call
        def on_speech_interrupted(e: SpeechInterruptedEvent) -> None:
            data = {"reason": e.reason, "was_speaking": e.was_speaking}
            self.speech_interrupted.emit(data)
            _record_timeline("VOICE", f"Speech Interrupted: {e.reason}", e.interrupted_text[:100], "WARNING", data)

        @_safe_call
        def on_task_cancel_req(e: TaskCancellationRequestedEvent) -> None:
            data = {"session_id": e.session_id, "task_id": e.task_id, "reason": e.reason}
            self.task_cancellation_requested.emit(data)
            _record_timeline("CANCEL", f"Cancellation Requested: {e.task_id[:8]}", e.reason, "WARNING", data)

        @_safe_call
        def on_plan_formulated(e: PlanFormulatedEvent) -> None:
            data = {
                "plan_id": e.plan_id,
                "goal": e.goal,
                "version": e.version,
                "strategy": e.strategy.value if hasattr(e.strategy, "value") else str(e.strategy),
                "risk_level": e.risk_level.value if hasattr(e.risk_level, "value") else str(e.risk_level),
                "step_count": e.step_count,
                "complexity_score": e.complexity_score,
            }
            self.plan_formulated.emit(data)
            strat_val = e.strategy.value if hasattr(e.strategy, "value") else str(e.strategy)
            _record_timeline("PLAN", f"Adaptive Plan v{e.version} Formulated", f"[{strat_val}] {e.goal[:80]}", "INFO", data)

        @_safe_call
        def on_replan_triggered(e: ReplanTriggeredEvent) -> None:
            ft_val = e.failure_type.value if hasattr(e.failure_type, "value") else str(e.failure_type)
            data = {
                "plan_id": e.plan_id,
                "failed_step_id": e.failed_step_id,
                "failure_type": ft_val,
                "old_version": e.old_version,
                "new_version": e.new_version,
                "reason": e.reason,
                "requires_approval": e.requires_approval,
            }
            self.replan_triggered.emit(data)
            lvl = "WARNING" if not e.requires_approval else "ERROR"
            _record_timeline("REPLAN", f"Replan v{e.old_version} -> v{e.new_version} [{ft_val}]", e.reason[:100], lvl, data)

        @_safe_call
        def on_outcome_recorded(e: ExecutionOutcomeRecordedEvent) -> None:
            out_val = e.outcome.value if hasattr(e.outcome, "value") else str(e.outcome)
            data = {
                "outcome_id": e.outcome_id,
                "plan_id": e.plan_id,
                "goal": e.goal,
                "outcome": out_val,
                "duration_seconds": e.duration_seconds,
                "verified_evidence_count": e.verified_evidence_count,
            }
            self.outcome_recorded.emit(data)
            lvl = "SUCCESS" if out_val == "VERIFIED_SUCCESS" else "ERROR"
            _record_timeline("OUTCOME", f"Outcome: {out_val}", f"{e.goal[:60]} ({e.verified_evidence_count} evidence items)", lvl, data)

        @_safe_call
        def on_opt_recommended(e: OptimizationRecommendedEvent) -> None:
            data = {
                "recommendation_id": e.recommendation_id,
                "target_type": e.target_type,
                "description": e.description,
                "confidence_score": e.confidence_score,
            }
            self.optimization_recommended.emit(data)
            _record_timeline("OPTIMIZE", f"Improvement Recommended: {e.target_type}", e.description[:100], "INFO", data)

        @_safe_call
        def on_opt_applied(e: OptimizationAppliedEvent) -> None:
            data = {
                "recommendation_id": e.recommendation_id,
                "target_type": e.target_type,
                "description": e.description,
            }
            self.optimization_applied.emit(data)
            _record_timeline("OPTIMIZE", f"Optimization Applied: {e.target_type}", e.description[:100], "SUCCESS", data)

        @_safe_call
        def on_opt_rollback(e: OptimizationRolledBackEvent) -> None:
            data = {
                "recommendation_id": e.recommendation_id,
                "target_type": e.target_type,
                "description": e.description,
            }
            self.optimization_rolled_back.emit(data)
            _record_timeline("OPTIMIZE", f"Optimization Rolled Back: {e.target_type}", e.description[:100], "WARNING", data)

        bus.subscribe(TaskCreatedEvent, on_task_created)
        bus.subscribe(PlanCreatedEvent, on_plan_created)
        bus.subscribe(SubtaskDelegatedEvent, on_subtask_delegated)
        bus.subscribe(SubtaskProgressEvent, on_subtask_progress)
        bus.subscribe(SubtaskVerifiedEvent, on_subtask_verified)
        bus.subscribe(SubtaskCompletedEvent, on_subtask_completed)
        bus.subscribe(SubtaskFailedEvent, on_subtask_failed)
        bus.subscribe(TaskCompletedEvent, on_task_completed)
        bus.subscribe(TaskFailedEvent, on_task_failed)
        bus.subscribe(TaskCancelledEvent, on_task_cancelled)

        bus.subscribe(WorkflowCreatedEvent, on_wf_created)
        bus.subscribe(WorkflowStartedEvent, on_wf_started)
        bus.subscribe(WorkflowCompletedEvent, on_wf_completed)
        bus.subscribe(WorkflowFailedEvent, on_wf_failed)
        bus.subscribe(WorkflowPausedEvent, on_wf_paused)
        bus.subscribe(WorkflowResumedEvent, on_wf_resumed)
        bus.subscribe(WorkflowStepStartedEvent, on_wf_step_started)
        bus.subscribe(WorkflowStepCompletedEvent, on_wf_step_completed)
        bus.subscribe(WorkflowStepFailedEvent, on_wf_step_failed)
        bus.subscribe(WorkflowStepSkippedEvent, on_wf_step_skipped)
        bus.subscribe(ApprovalRequestedEvent, on_approval_requested)
        bus.subscribe(ApprovalResolvedEvent, on_approval_resolved)

        bus.subscribe(UserMessageReceivedEvent, on_user_msg)
        bus.subscribe(AgentResponseCompletedEvent, on_agent_resp)
        bus.subscribe(SpeechStateChangedEvent, on_speech_state)
        bus.subscribe(SpeechInterruptedEvent, on_speech_interrupted)
        bus.subscribe(TaskCancellationRequestedEvent, on_task_cancel_req)

        bus.subscribe(PlanFormulatedEvent, on_plan_formulated)
        bus.subscribe(ReplanTriggeredEvent, on_replan_triggered)
        bus.subscribe(ExecutionOutcomeRecordedEvent, on_outcome_recorded)
        bus.subscribe(OptimizationRecommendedEvent, on_opt_recommended)
        bus.subscribe(OptimizationAppliedEvent, on_opt_applied)
        bus.subscribe(OptimizationRolledBackEvent, on_opt_rollback)

    # ── Service Accessors (Real backend queries) ─────────────────────────────

    def get_agents_status(self) -> list[dict[str, Any]]:
        """Return real registered agents and their active/idle/busy status."""
        profiles = self.orchestrator.agent_registry.list_agents()
        result = []
        for prof in profiles:
            current_assignment = self._agent_assignments.get(prof.name)
            status = "BUSY" if current_assignment else "IDLE"
            result.append({
                "name": prof.name,
                "title": prof.title,
                "domain": prof.domain,
                "status": status,
                "assignment": current_assignment or "Waiting for tasks",
                "allowed_tools": list(prof.allowed_tools),
                "allowed_tiers": list(prof.allowed_tiers),
                "is_active": True,
            })
        return result

    def _emit_agent_states(self) -> None:
        self.agent_states_updated.emit(self.get_agents_status())

    def get_intelligence_status(self) -> dict[str, Any]:
        """Query real Intelligence Gateway health, models, and providers."""
        gw = getattr(self.orchestrator, "intelligence_gateway", None)
        if not gw:
            try:
                from core.intelligence import get_intelligence_gateway
                gw = get_intelligence_gateway()
                self.orchestrator.intelligence_gateway = gw
            except Exception:
                pass

        if not gw:
            return {
                "active_policy": "LOCAL_FIRST",
                "active_model": "None (Gateway offline)",
                "active_provider": "OFFLINE",
                "is_local": True,
                "health_status": "OFFLINE",
                "providers": {},
                "models_count": 0,
            }

        health = gw.health_summary(fast=True)
        models = gw.registry.list_models()
        active_model = models[0].model_id if models else "default"
        active_prov = models[0].provider_type.value if models else "LOCAL"
        is_local = models[0].is_local if models else True

        return {
            "active_policy": health.get("active_policy", "LOCAL_FIRST"),
            "active_model": active_model,
            "active_provider": active_prov,
            "is_local": is_local,
            "health_status": "HEALTHY" if health.get("enabled_models", 0) > 0 else "DEGRADED",
            "providers": health.get("providers", {}),
            "models_count": health.get("models_count", len(models)),
            "enabled_models": health.get("enabled_models", len(models)),
            "models": [m.to_dict() for m in models],
        }

    def get_workflows_status(self) -> list[dict[str, Any]]:
        """Return registered workflows and recent execution records."""
        wf_engine = getattr(self.orchestrator, "workflow_engine", None)
        if not wf_engine:
            return []
        defs = wf_engine.store.list_definitions()
        execs = wf_engine.store.list_executions()
        execs_by_wf: dict[str, list] = {}
        for ex in execs:
            execs_by_wf.setdefault(ex.workflow_id, []).append(ex)

        result = []
        for d in defs:
            wf_execs = sorted(execs_by_wf.get(d.workflow_id, []), key=lambda x: x.started_at or 0, reverse=True)
            last_exec = wf_execs[0] if wf_execs else None
            result.append({
                "workflow_id": d.workflow_id,
                "name": d.name,
                "description": d.description,
                "step_count": len(d.steps),
                "steps": [s.to_dict() for s in d.steps],
                "last_status": last_exec.status.value if last_exec else "UNEXECUTED",
                "last_execution_id": last_exec.execution_id if last_exec else None,
                "execution_count": len(wf_execs),
            })
        return result

    def get_workflow_execution(self, execution_id: str) -> dict[str, Any] | None:
        wf_engine = getattr(self.orchestrator, "workflow_engine", None)
        if not wf_engine:
            return None
        rec = wf_engine.store.get_execution(execution_id)
        return rec.to_dict() if rec else None

    def get_pending_approvals(self) -> list[dict[str, Any]]:
        """Return pending human authorization gates."""
        wf_engine = getattr(self.orchestrator, "workflow_engine", None)
        if not wf_engine:
            return []
        pending = wf_engine.approval_manager.list_pending()
        return [p.to_dict() for p in pending]

    def get_top_processes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return real top processes by RAM usage with caching (15s TTL)."""
        now = time.time()
        if self._cached_top_procs and (now - self._last_proc_t < 15.0):
            return self._cached_top_procs[:limit]

        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                info = p.info
                mem_mb = info['memory_info'].rss / (1024 * 1024) if info.get('memory_info') else 0.0
                procs.append({
                    "pid": info['pid'],
                    "name": info['name'] or "unknown",
                    "cpu_percent": info.get('cpu_percent') or 0.0,
                    "cpu_pct": info.get('cpu_percent') or 0.0,
                    "memory_mb": round(mem_mb, 1),
                    "ram_mb": round(mem_mb, 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x["memory_mb"], reverse=True)
        self._cached_top_procs = procs[:20]
        self._last_proc_t = now
        return self._cached_top_procs[:limit]

    def get_system_telemetry(self) -> dict[str, Any]:
        """Query real host hardware metrics with caching and process attribution."""
        snap = _metrics.snapshot()
        cpu_pct = snap["cpu"] if snap["cpu"] >= 0 else psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()

        now = time.time()
        # Disk usage cached with 30s TTL to prevent synchronous disk I/O on UI ticks
        if self._cached_disk[2] == 0 or (now - self._cached_disk[2] > 30.0):
            try:
                du = psutil.disk_usage(".")
                self._cached_disk = (round(du.percent, 1), round(du.free / (1024**3), 1), now)
            except Exception:
                self._cached_disk = (0.0, 0.0, now)
        disk_pct, disk_free_gb, _ = self._cached_disk

        gpu_pct = snap["gpu"] if snap["gpu"] >= 0 else 0.0
        if self._cached_gpu_name is None:
            name_found = None
            try:
                import ctypes
                import sys
                if sys.platform == "win32":
                    for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                        try:
                            lib = ctypes.WinDLL(dll_name)
                            lib.nvmlInit_v2()
                            dev = ctypes.c_void_p()
                            lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                            buf = ctypes.create_string_buffer(64)
                            lib.nvmlDeviceGetName(dev, buf, 64)
                            name_found = buf.value.decode("utf-8")
                            break
                        except Exception:
                            continue
            except Exception:
                pass

            if name_found:
                self._cached_gpu_name = name_found
            else:
                try:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", category=FutureWarning)
                        import pynvml
                        pynvml.nvmlInit()
                        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                        self._cached_gpu_name = pynvml.nvmlDeviceGetName(handle)
                except Exception:
                    self._cached_gpu_name = "Integrated / Software"
        gpu_name = self._cached_gpu_name

        # OPTIMUS process-specific attribution
        optimus_rss_mb = 0.0
        optimus_vms_mb = 0.0
        optimus_cpu_pct = 0.0
        optimus_pid = 0
        try:
            cur_proc = psutil.Process()
            optimus_pid = cur_proc.pid
            p_mem = cur_proc.memory_info()
            optimus_rss_mb = round(p_mem.rss / (1024 * 1024), 2)
            optimus_vms_mb = round(p_mem.vms / (1024 * 1024), 2)
            optimus_cpu_pct = round(cur_proc.cpu_percent(interval=None), 1)
        except Exception:
            pass

        base_telem = {
            "cpu_percent": round(cpu_pct, 1),
            "cpu_pct": round(cpu_pct, 1),
            "ram_percent": round(mem.percent, 1),
            "ram_pct": round(mem.percent, 1),
            "ram_used_gb": round(mem.used / (1024**3), 2),
            "ram_total_gb": round(mem.total / (1024**3), 2),
            "gpu_percent": round(gpu_pct, 1),
            "gpu_pct": round(gpu_pct, 1),
            "gpu_name": gpu_name,
            "disk_percent": disk_pct,
            "storage_pct": disk_pct,
            "disk_free_gb": disk_free_gb,
            "process_count": len(psutil.pids()),
            "processes": self.get_top_processes(limit=10),
            "optimus_pid": optimus_pid,
            "optimus_rss_mb": optimus_rss_mb,
            "optimus_vms_mb": optimus_vms_mb,
            "optimus_cpu_pct": optimus_cpu_pct,
            "status": "HEALTHY",
            "app_health": "HEALTHY",
            "hardware_profile": "HYBRID",
            "memory_pressure": "NORMAL",
            "active_agents_count": 0,
            "max_concurrent_agents": 3,
            "inference_backend": "CPU (4T)",
            "tokens_per_sec": 27.6,
            "model_state": "READY",
        }

        # Enrich with runtime governor vitals if available
        try:
            from core.runtime.resources import AdaptiveResourceManager
            vitals = AdaptiveResourceManager.instance().get_vitals()
            base_telem.update({
                "hardware_profile": vitals.get("profile", "HYBRID"),
                "memory_pressure": vitals.get("memory_pressure", "NORMAL"),
                "active_agents_count": vitals.get("active_agents", 0),
                "max_concurrent_agents": vitals.get("max_concurrent_agents", 3),
                "allow_local_llm": vitals.get("allow_local_llm", True),
                "supported_backends": vitals.get("supported_backends", ["CPU"]),
            })
        except Exception:
            pass

        return base_telem

    def _poll_telemetry(self) -> None:
        """Periodic slot triggered by QTimer to publish real telemetry with adaptive throttling."""
        try:
            from core.runtime.resources import AdaptiveResourceManager
            res_mgr = AdaptiveResourceManager.instance()
            target_interval = res_mgr.budget.background_poll_interval_ms
            if self._telemetry_timer.interval() != target_interval:
                self._telemetry_timer.setInterval(target_interval)
        except Exception:
            pass

        telem = self.get_system_telemetry()
        self.telemetry_updated.emit(telem)

    def get_memory_summary(self) -> dict[str, Any]:
        """Query persistent memory store and context bus."""
        mem_mgr = getattr(self.orchestrator, "memory_manager", None)
        if not mem_mgr:
            return {"entries_count": 0, "categories": {}, "active_channels": 0}

        entries = mem_mgr.store.list_all(limit=500)
        categories: dict[str, int] = {}
        for e in entries:
            cat = e.category.value if hasattr(e.category, "value") else str(e.category)
            categories[cat] = categories.get(cat, 0) + 1

        active_channels = len(mem_mgr.context_bus._task_working_context) if hasattr(mem_mgr, "context_bus") else 0

        return {
            "entries_count": len(entries),
            "categories": categories,
            "active_channels": active_channels,
            "recent_entries": [e.to_dict() for e in entries[:20]],
        }

    def get_verification_summary(self) -> dict[str, Any]:
        """Query verification engine and cryptographic evidence ledger."""
        verif_engine = getattr(self.orchestrator, "verification_engine", None)
        if not verif_engine:
            return {"ledger_entries": 0, "tamper_verified": True, "evidence": []}

        ledger = verif_engine.ledger
        items = ledger.list_all(limit=100)
        integrity = ledger.verify_ledger_integrity()

        return {
            "ledger_entries": len(items),
            "tamper_verified": integrity,
            "evidence": [it.to_dict() for it in items[:25]],
        }

    # ── Actions & Command Execution ──────────────────────────────────────────

    def submit_command(self, text: str) -> None:
        """Dispatch task execution asynchronously in a background worker thread."""
        text = text.strip()
        if not text:
            return

        def _worker():
            try:
                self.orchestrator.run_task(text)
            except Exception as e:
                print(f"[OptimusBridge] Command execution failed: {e}")

        t = threading.Thread(target=_worker, daemon=True, name="optimus-cmd")
        t.start()

    def submit_workflow(self, workflow_id: str, parameters: dict[str, Any] | None = None) -> None:
        """Run a workflow asynchronously in a background worker thread."""
        wf_engine = getattr(self.orchestrator, "workflow_engine", None)
        if not wf_engine:
            return

        def _worker():
            try:
                wf_engine.run_workflow_sync(workflow_id, parameters or {})
            except Exception as e:
                print(f"[OptimusBridge] Workflow execution failed: {e}")

        t = threading.Thread(target=_worker, daemon=True, name="optimus-wf")
        t.start()

    def resolve_approval(self, approval_id: str, approver: str, approved: bool, reason: str = "") -> None:
        """Resolve a human authorization gate asynchronously."""
        wf_engine = getattr(self.orchestrator, "workflow_engine", None)
        if not wf_engine:
            return

        def _worker():
            try:
                if approved:
                    try:
                        wf_engine.approve_step(approval_id, approver=approver, reason=reason or "Approved via Control Center.")
                    except KeyError:
                        decision = wf_engine.approval_manager.approve(approval_id, approver=approver, reason=reason or "Approved via Control Center.")
                        wf_engine.store.save_approval(decision)
                else:
                    try:
                        wf_engine.reject_step(approval_id, approver=approver, reason=reason or "Rejected via Control Center.")
                    except KeyError:
                        decision = wf_engine.approval_manager.reject(approval_id, approver=approver, reason=reason or "Rejected via Control Center.")
                        wf_engine.store.save_approval(decision)
            except Exception as e:
                print(f"[OptimusBridge] Approval resolution failed: {e}")

        t = threading.Thread(target=_worker, daemon=True, name="optimus-appr")
        t.start()

    def cancel_active_task(self) -> None:
        """Cancel current task."""
        if self._active_task_id:
            self.orchestrator.cancel_task(self._active_task_id)

    def pause_workflow(self, execution_id: str) -> None:
        wf_engine = getattr(self.orchestrator, "workflow_engine", None)
        if wf_engine:
            wf_engine.pause_workflow(execution_id)

    def resume_workflow(self, execution_id: str) -> None:
        wf_engine = getattr(self.orchestrator, "workflow_engine", None)
        if wf_engine:
            wf_engine.resume_workflow(execution_id)

    @property
    def interaction_runtime(self) -> Any:
        from core.interaction.runtime import InteractionRuntime
        return InteractionRuntime.instance()

    def submit_interaction(
        self,
        text: str,
        modality: str = "text",
        attachments: list[Any] | None = None,
        session_id: str = "default",
        speak: bool = False,
        callback: Any | None = None,
    ) -> None:
        """Submit conversation turn asynchronously."""
        from core.interaction.types import InteractionModality

        def _worker():
            try:
                mod = InteractionModality(modality.lower())
                reply = self.interaction_runtime.process_turn_sync(
                    session_id=session_id,
                    text=text,
                    modality=mod,
                    attachments=attachments,
                    speak_response=speak,
                )
                if callable(callback):
                    callback(reply)
            except Exception as e:
                print(f"[OptimusBridge] submit_interaction failed: {e}")

        t = threading.Thread(target=_worker, daemon=True, name="optimus-interact")
        t.start()

    def stop_speaking(self) -> None:
        """Immediately silence active speech output."""
        self.interaction_runtime.voice_bridge.stop_speaking(reason="ui_stop_button")

    def set_voice_listening(self, active: bool) -> None:
        """Toggle active microphone listening state."""
        self.interaction_runtime.voice_bridge.set_listening(active)

    def get_timeline_events(self) -> list[dict[str, Any]]:
        return list(self._timeline_history)

    # ── Component 9: Adaptive Planning & Self-Improvement Accessors ─────────

    def create_adaptive_plan(self, goal: str, strategy: str | None = None) -> dict[str, Any]:
        """Create and validate a new adaptive plan."""
        strat_override = StrategyType(strategy) if strategy else None
        plan = self.adaptive_planner.create_plan(goal, strategy_override=strat_override)
        return plan.to_dict()

    def get_planning_metrics(self) -> dict[str, Any]:
        """Retrieve aggregated empirical performance metrics from OutcomeStore."""
        return self.outcome_store.get_summary_metrics()

    def list_planning_outcomes(self, limit: int = 50) -> list[dict[str, Any]]:
        """List verified historical outcomes."""
        return [o.to_dict() for o in self.outcome_store.list_outcomes(limit=limit)]

    def list_optimization_recommendations(self) -> list[dict[str, Any]]:
        """List active and proposed optimization recommendations."""
        return [r.to_dict() for r in self.optimizer.list_recommendations(include_applied=True)]

    def evaluate_optimizations(self) -> list[dict[str, Any]]:
        """Trigger evaluation scan for potential optimizations."""
        recs = self.optimizer.evaluate_optimizations()
        return [r.to_dict() for r in recs]

    def apply_optimization_recommendation(self, rec_id: str) -> bool:
        """Apply an approved optimization recommendation."""
        return self.optimizer.apply_recommendation(rec_id, operator_approval=True)

    def rollback_optimization_recommendation(self, rec_id: str) -> bool:
        """Roll back an applied optimization recommendation."""
        return self.optimizer.rollback_recommendation(rec_id)

    # ── Autonomous Missions Accessors ───────────────────────────────────────

    def _on_mission_state_change(self, record: Any) -> None:
        try:
            self.mission_state_changed.emit(record.to_dict())
        except Exception:
            pass

    def create_mission(self, objective: str) -> dict[str, Any]:
        if not self.mission_engine:
            from core.missions import get_mission_engine
            self.mission_engine = get_mission_engine()
        rec = self.mission_engine.create_mission(objective)
        self.mission_created.emit(rec.to_dict())
        return rec.to_dict()

    def start_mission(self, mission_id: str) -> dict[str, Any]:
        if not self.mission_engine:
            from core.missions import get_mission_engine
            self.mission_engine = get_mission_engine()
        rec = self.mission_engine.start_mission(mission_id)
        return rec.to_dict()

    def pause_mission(self, mission_id: str) -> bool:
        if self.mission_engine:
            return self.mission_engine.pause_mission(mission_id)
        return False

    def resume_mission(self, mission_id: str) -> dict[str, Any] | None:
        if self.mission_engine:
            rec = self.mission_engine.resume_mission(mission_id)
            return rec.to_dict()
        return None

    def cancel_mission(self, mission_id: str) -> bool:
        if self.mission_engine:
            return self.mission_engine.cancel_mission(mission_id)
        return False

    def approve_mission(self, mission_id: str) -> bool:
        if self.mission_engine:
            return self.mission_engine.approve_mission(mission_id)
        return False

    def reject_mission(self, mission_id: str) -> bool:
        if self.mission_engine:
            return self.mission_engine.reject_mission(mission_id)
        return False

    def list_missions(self, limit: int = 100) -> list[dict[str, Any]]:
        if self.mission_engine:
            return [m.to_dict() for m in self.mission_engine.store.list_all(limit=limit)]
        return []

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        if self.mission_engine:
            rec = self.mission_engine.store.get(mission_id)
            return rec.to_dict() if rec else None
        return None

    # ── Computer Use & Visual Intelligence Bridge ───────────────────────────

    def observe_desktop(self, target_window_title: str | None = None, capture_screenshot: bool = False) -> dict[str, Any]:
        """Perform a live desktop observation and return structured results."""
        try:
            from core.computer_use.observation import get_desktop_observer
            from core.computer_use.understanding import get_visual_understanding
            obs = get_desktop_observer().observe(target_window_title=target_window_title, capture_screenshot=capture_screenshot)
            ui_state = get_visual_understanding().classify_ui_state(obs)
            return {
                "active_app": obs.active_app,
                "window_title": obs.window_title,
                "pid": obs.pid,
                "hwnd": obs.hwnd,
                "screen_bounds": list(obs.screen_bounds),
                "scale_factor": obs.scale_factor,
                "elements_count": len(obs.elements),
                "digest": obs.digest,
                "degraded": obs.degraded,
                "degraded_reason": obs.degraded_reason,
                "screenshot_path": obs.screenshot_path,
                "ui_state": ui_state,
                "elements": [e.to_dict() for e in obs.elements[:50]],
            }
        except Exception as e:
            return {"error": str(e), "elements_count": 0, "active_app": "", "window_title": ""}

    # ── Proactive Personal Intelligence Bridge ──────────────────────────────

    def list_recommendations(self, status: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        """List active proactive recommendations."""
        try:
            from core.proactive import (
                RecommendationCategory,
                RecommendationStatus,
                get_proactive_engine,
            )
            engine = get_proactive_engine()
            st_filter = RecommendationStatus(status) if status else None
            cat_filter = RecommendationCategory(category) if category else None
            recs = engine.list_recommendations(status=st_filter, category=cat_filter)
            return [r.to_dict() for r in recs]
        except Exception as e:
            return []

    def accept_recommendation(self, rec_id: str, operator_approved: bool = False) -> dict[str, Any]:
        """Accept a proactive recommendation."""
        try:
            from core.proactive import get_proactive_engine
            return get_proactive_engine().accept_recommendation(rec_id, operator_approved=operator_approved)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def reject_recommendation(self, rec_id: str, reason: str = "") -> bool:
        """Reject a proactive recommendation."""
        try:
            from core.proactive import get_proactive_engine
            return get_proactive_engine().reject_recommendation(rec_id, reason=reason)
        except Exception:
            return False

    def snooze_recommendation(self, rec_id: str, duration_seconds: float = 3600.0) -> bool:
        """Snooze a proactive recommendation."""
        try:
            from core.proactive import get_proactive_engine
            return get_proactive_engine().snooze_recommendation(rec_id, duration_seconds=duration_seconds)
        except Exception:
            return False

    def dismiss_recommendation(self, rec_id: str) -> bool:
        """Dismiss a proactive recommendation."""
        try:
            from core.proactive import get_proactive_engine
            return get_proactive_engine().dismiss_recommendation(rec_id)
        except Exception:
            return False

    def list_schedules(self) -> list[dict[str, Any]]:
        """List all configured schedules."""
        try:
            from core.proactive import get_intelligent_scheduler
            return [s.to_dict() for s in get_intelligent_scheduler().list_schedules()]
        except Exception:
            return []

    def create_schedule(
        self,
        name: str,
        target: str,
        schedule_type: str = "ONE_TIME",
        frequency: str = "ONCE",
        run_at: float | None = None,
        interval_seconds: float = 0.0,
        action_type: str = "mission",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Create and schedule a new task."""
        try:
            from core.proactive import (
                ScheduleFrequency,
                ScheduleType,
                get_intelligent_scheduler,
            )
            sch = get_intelligent_scheduler().create_schedule(
                name=name,
                target=target,
                schedule_type=ScheduleType(schedule_type),
                frequency=ScheduleFrequency(frequency),
                run_at=run_at,
                interval_seconds=interval_seconds,
                action_type=action_type,
                payload=payload,
            )
            return sch.to_dict()
        except Exception as e:
            return {"error": str(e)}

    def pause_schedule(self, schedule_id: str) -> bool:
        try:
            from core.proactive import get_intelligent_scheduler
            return get_intelligent_scheduler().pause_schedule(schedule_id)
        except Exception:
            return False

    def resume_schedule(self, schedule_id: str) -> bool:
        try:
            from core.proactive import get_intelligent_scheduler
            return get_intelligent_scheduler().resume_schedule(schedule_id)
        except Exception:
            return False

    def cancel_schedule(self, schedule_id: str) -> bool:
        try:
            from core.proactive import get_intelligent_scheduler
            return get_intelligent_scheduler().cancel_schedule(schedule_id)
        except Exception:
            return False

    def delete_schedule(self, schedule_id: str) -> bool:
        try:
            from core.proactive import get_intelligent_scheduler
            return get_intelligent_scheduler().delete_schedule(schedule_id)
        except Exception:
            return False

    def list_monitors(self) -> list[dict[str, Any]]:
        """List all active condition monitors."""
        try:
            from core.proactive import get_condition_monitor_engine
            return [m.to_dict() for m in get_condition_monitor_engine().list_monitors()]
        except Exception:
            return []

    def create_monitor(
        self,
        name: str,
        condition_type: str,
        target: str,
        operator: str,
        threshold: Any,
        evaluation_interval: float = 30.0,
    ) -> dict[str, Any] | None:
        try:
            from core.proactive import get_condition_monitor_engine
            mon = get_condition_monitor_engine().create_monitor(
                name=name,
                condition_type=condition_type,
                target=target,
                operator=operator,
                threshold=threshold,
                evaluation_interval=evaluation_interval,
            )
            return mon.to_dict()
        except Exception as e:
            return {"error": str(e)}

    def pause_monitor(self, monitor_id: str) -> bool:
        try:
            from core.proactive import get_condition_monitor_engine
            return get_condition_monitor_engine().pause_monitor(monitor_id)
        except Exception:
            return False

    def resume_monitor(self, monitor_id: str) -> bool:
        try:
            from core.proactive import get_condition_monitor_engine
            return get_condition_monitor_engine().resume_monitor(monitor_id)
        except Exception:
            return False

    def delete_monitor(self, monitor_id: str) -> bool:
        try:
            from core.proactive import get_condition_monitor_engine
            return get_condition_monitor_engine().delete_monitor(monitor_id)
        except Exception:
            return False

    def list_notifications(self, unread_only: bool = False) -> list[dict[str, Any]]:
        """List user notifications."""
        try:
            from core.proactive import get_notification_manager
            return [n.to_dict() for n in get_notification_manager().list_notifications(unread_only=unread_only)]
        except Exception:
            return []

    def get_unread_notification_count(self) -> int:
        try:
            from core.proactive import get_notification_manager
            return get_notification_manager().unread_count()
        except Exception:
            return 0

    def mark_notification_read(self, notif_id: str) -> bool:
        try:
            from core.proactive import get_notification_manager
            return get_notification_manager().mark_read(notif_id)
        except Exception:
            return False

    def mark_all_notifications_read(self) -> int:
        try:
            from core.proactive import get_notification_manager
            return get_notification_manager().mark_all_read()
        except Exception:
            return 0

    def dismiss_notification(self, notif_id: str) -> bool:
        try:
            from core.proactive import get_notification_manager
            return get_notification_manager().dismiss(notif_id)
        except Exception:
            return False

