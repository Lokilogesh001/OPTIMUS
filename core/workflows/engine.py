"""core/workflows/engine.py — Autonomous Workflow Execution Engine for OPTIMUS.

The central workflow execution engine coordinating:
- Deterministic DAG dependency resolution.
- Human authorization gates (never bypassed by LLM responses).
- Step dispatch strictly through the Central Orchestrator's delegation pipeline.
- Physical postcondition validation via VerificationEngine.
- Context isolation via SharedContextBus.
- Crash recovery and ambiguous execution reconciliation.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from core.orchestrator.events import EventBus
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import Subtask
from core.workflows.approval import ApprovalManager
from core.workflows.conditions import SafeConditionEvaluator
from core.workflows.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowCreatedEvent,
    WorkflowFailedEvent,
    WorkflowPausedEvent,
    WorkflowRecoveryStartedEvent,
    WorkflowResumedEvent,
    WorkflowStartedEvent,
    WorkflowStepCompletedEvent,
    WorkflowStepFailedEvent,
    WorkflowStepStartedEvent,
)
from core.workflows.resolver import DependencyResolver
from core.workflows.storage import WorkflowStore
from core.workflows.types import (
    ApprovalStatus,
    StepExecutionRecord,
    StepStatus,
    WorkflowDefinition,
    WorkflowError,
    WorkflowExecutionRecord,
    WorkflowStatus,
    WorkflowStepDefinition,
    new_execution_id,
)

logger = logging.getLogger("optimus.workflows.engine")


class WorkflowEngine:
    """Coordinates declarative DAG workflow execution within OPTIMUS."""

    def __init__(
        self,
        orchestrator: CentralOrchestrator,
        store: WorkflowStore | None = None,
        event_bus: EventBus | None = None,
        approval_manager: ApprovalManager | None = None,
        allowed_roots: list[str] | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.store = store or WorkflowStore()
        self.event_bus = event_bus or orchestrator.event_bus or EventBus()
        self.approval_manager = approval_manager or ApprovalManager()
        self.allowed_roots = allowed_roots or orchestrator.allowed_roots
        self._lock = threading.RLock()
        self._active_threads: dict[str, threading.Thread] = {}
        self._cancelled_executions: set[str] = set()

    # ── Workflow Definition Registration ─────────────────────────────────────

    def register_workflow(self, definition: WorkflowDefinition) -> None:
        """Validate, register, and persist a declarative workflow definition."""
        with self._lock:
            # 1. Structural DAG validation
            DependencyResolver.validate_workflow(definition)

            # 2. Persist to disk
            self.store.save_definition(definition)

            # 3. Emit lifecycle event
            self.event_bus.publish(WorkflowCreatedEvent(
                workflow_id=definition.workflow_id,
                name=definition.name,
                step_count=len(definition.steps),
            ))

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        """Retrieve a registered workflow definition."""
        return self.store.get_definition(workflow_id)

    def list_workflows(self) -> list[WorkflowDefinition]:
        """List all registered workflow definitions."""
        return self.store.list_definitions()

    # ── Workflow Execution Lifecycle ─────────────────────────────────────────

    def start_workflow(
        self,
        workflow_id: str,
        parameters: dict[str, Any] | None = None,
        depth: int = 0,
    ) -> WorkflowExecutionRecord:
        """Instantiate and start a new workflow execution instance."""
        with self._lock:
            definition = self.store.get_definition(workflow_id)
            if not definition:
                raise KeyError(f"Workflow '{workflow_id}' is not registered.")

            if depth > definition.max_depth:
                raise WorkflowError(
                    f"Nested workflow depth limit exceeded ({depth} > {definition.max_depth})."
                )

            # Bind and merge default parameters
            exec_params = dict(definition.parameters)
            if parameters:
                exec_params.update(parameters)

            exec_id = new_execution_id()
            now = time.time()

            # Initialize step states
            step_states: dict[str, StepExecutionRecord] = {}
            for step in definition.steps:
                step_states[step.step_id] = StepExecutionRecord(
                    step_id=step.step_id,
                    status=StepStatus.PENDING,
                )

            execution = WorkflowExecutionRecord(
                execution_id=exec_id,
                workflow_id=workflow_id,
                status=WorkflowStatus.RUNNING,
                parameters=exec_params,
                context=dict(exec_params),
                step_states=step_states,
                created_at=now,
                started_at=now,
                depth=depth,
            )

            # Initialize context bus isolation for execution
            if hasattr(self.orchestrator, "memory_manager"):
                self.orchestrator.memory_manager.context_bus.isolate_task(exec_id)

            self.store.save_execution(execution)

            self.event_bus.publish(WorkflowStartedEvent(
                execution_id=exec_id,
                workflow_id=workflow_id,
                parameters=exec_params,
            ))

            return execution

    def step_workflow(self, execution_id: str) -> WorkflowExecutionRecord:
        """Advance the workflow DAG: resolve ready steps, evaluate conditions, and dispatch."""
        with self._lock:
            execution = self.store.get_execution(execution_id)
            if not execution:
                raise KeyError(f"Execution '{execution_id}' not found.")

            if execution.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
                WorkflowStatus.PAUSED,
            ):
                return execution

            definition = self.store.get_definition(execution.workflow_id)
            if not definition:
                raise KeyError(f"Workflow definition '{execution.workflow_id}' missing.")

            # Check expiration on any pending approvals
            self.approval_manager.check_expirations()
            approvals_map = {a.approval_id: a for a in self.approval_manager.list_approvals(execution_id)}

            # Sync steps in WAITING_APPROVAL with approval records
            for s_id, s_rec in execution.step_states.items():
                if s_rec.status == StepStatus.WAITING_APPROVAL:
                    app = None
                    if s_rec.approval_id:
                        app = self.approval_manager.get_approval(s_rec.approval_id) or self.store.load_approval(s_rec.approval_id)
                    if not app:
                        app = next((a for a in approvals_map.values() if a.step_id == s_id), None)
                    if app:
                        if app.status == ApprovalStatus.APPROVED:
                            s_rec.status = StepStatus.READY
                        elif app.status == ApprovalStatus.REJECTED:
                            s_rec.status = StepStatus.FAILED
                            s_rec.error = f"Authorization REJECTED: {app.decision_reason or 'No reason provided'}"
                        elif app.status == ApprovalStatus.EXPIRED:
                            s_rec.status = StepStatus.FAILED
                            s_rec.error = "Human approval expired before authorization."

            # If execution was WAITING_APPROVAL but no approvals are pending, resume RUNNING
            if execution.status == WorkflowStatus.WAITING_APPROVAL:
                if not self.approval_manager.list_pending(execution_id):
                    execution.status = WorkflowStatus.RUNNING

            # Query ready steps from dependency resolver
            ready_steps = DependencyResolver.get_ready_steps(
                workflow=definition,
                step_states=execution.step_states,
                context=execution.context,
                approvals=approvals_map,
                allowed_roots=self.allowed_roots,
            )

            # Check if workflow is waiting for an approval
            pending_approvals = self.approval_manager.list_pending(execution_id)
            if pending_approvals:
                execution.status = WorkflowStatus.WAITING_APPROVAL
                self.store.save_execution(execution)
                return execution

            # If no steps ready, evaluate terminal status
            if not ready_steps:
                self._evaluate_terminal_state(execution, definition)
                self.store.save_execution(execution)
                return execution

            # Process ready steps
            for step_def in ready_steps:
                if execution_id in self._cancelled_executions:
                    execution.status = WorkflowStatus.CANCELLED
                    self.store.save_execution(execution)
                    return execution

                step_rec = execution.step_states[step_def.step_id]

                # Check if step requires human authorization gate
                if (step_def.requires_approval or step_def.is_destructive) and step_rec.status != StepStatus.READY:
                    existing_app = next(
                        (a for a in approvals_map.values() if a.step_id == step_def.step_id),
                        None,
                    )
                    if not existing_app:
                        # Request approval
                        reason = f"Human authorization required for step '{step_def.name}'."
                        if step_def.is_destructive:
                            reason = f"DESTRUCTIVE ACTION: {reason}"
                        app = self.approval_manager.request_approval(
                            workflow_id=execution.workflow_id,
                            execution_id=execution_id,
                            step_id=step_def.step_id,
                            reason=reason,
                            required_role=step_def.approval_role,
                            timeout_seconds=step_def.approval_timeout_seconds,
                        )
                        self.store.save_approval(app)
                        step_rec.status = StepStatus.WAITING_APPROVAL
                        step_rec.approval_id = app.approval_id
                        execution.status = WorkflowStatus.WAITING_APPROVAL

                        self.event_bus.publish(ApprovalRequestedEvent(
                            approval_id=app.approval_id,
                            execution_id=execution_id,
                            workflow_id=execution.workflow_id,
                            step_id=step_def.step_id,
                            reason=reason,
                            required_role=step_def.approval_role,
                            expires_at=app.expires_at,
                        ))
                        continue
                    elif existing_app.status == ApprovalStatus.PENDING:
                        step_rec.status = StepStatus.WAITING_APPROVAL
                        execution.status = WorkflowStatus.WAITING_APPROVAL
                        continue
                    elif existing_app.status in (ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED):
                        step_rec.status = StepStatus.FAILED
                        step_rec.error = f"Authorization {existing_app.status.value}: {existing_app.decision_reason or 'No reason provided'}"
                        continue

                # Execute step
                self._execute_single_step(execution, step_def, step_rec)

            self._evaluate_terminal_state(execution, definition)
            self.store.save_execution(execution)
            return execution

    def _execute_single_step(
        self,
        execution: WorkflowExecutionRecord,
        step_def: WorkflowStepDefinition,
        step_rec: StepExecutionRecord,
    ) -> None:
        """Execute a step via CentralOrchestrator and verify postconditions."""
        step_rec.status = StepStatus.RUNNING
        step_rec.started_at = time.time()
        step_rec.attempts += 1

        self.event_bus.publish(WorkflowStepStartedEvent(
            execution_id=execution.execution_id,
            workflow_id=execution.workflow_id,
            step_id=step_def.step_id,
            assigned_agent=step_def.assigned_agent,
            attempt=step_rec.attempts,
        ))

        # Check for nested workflow invocation
        if step_def.nested_workflow_id:
            try:
                child_exec = self.start_workflow(
                    workflow_id=step_def.nested_workflow_id,
                    parameters=execution.context,
                    depth=execution.depth + 1,
                )
                child_exec = self.run_workflow_sync(
                    workflow_id=step_def.nested_workflow_id,
                    parameters=execution.context,
                )
                if child_exec.status == WorkflowStatus.COMPLETED:
                    step_rec.status = StepStatus.COMPLETED
                    step_rec.finished_at = time.time()
                    step_rec.duration_seconds = step_rec.finished_at - step_rec.started_at
                    step_rec.outputs = dict(child_exec.context)
                    step_rec.summary = f"Nested workflow '{step_def.nested_workflow_id}' completed successfully."
                    execution.context.update(child_exec.context)
                else:
                    step_rec.status = StepStatus.FAILED
                    step_rec.error = child_exec.error or "Nested workflow execution failed."
                return
            except Exception as e:
                step_rec.status = StepStatus.FAILED
                step_rec.error = f"Nested workflow execution error: {e}"
                return

        # Prepare interpolated instructions
        interpolated_desc = SafeConditionEvaluator.interpolate_text(
            step_def.description or step_def.name,
            execution.context,
            execution.step_states,
        )

        # Prepare effective allowed tools from agent profile if not overridden
        prof = self.orchestrator.agent_registry.get(step_def.assigned_agent)
        effective_allowed_tools = list(step_def.allowed_tools) if step_def.allowed_tools else (list(prof.allowed_tools) if prof else [])

        # Build Subtask contract for CentralOrchestrator delegation
        subtask = Subtask(
            subtask_id=f"st_{step_def.step_id}_{execution.execution_id[:8]}",
            task_id=execution.execution_id,
            index=1,
            title=step_def.name,
            description=interpolated_desc,
            assigned_agent=step_def.assigned_agent,
            allowed_tools=effective_allowed_tools,
            expected_deliverables=step_def.expected_deliverables,
            timeout_seconds=step_def.timeout_seconds,
        )

        # Retry loop with exponential backoff
        retry = step_def.retry_policy
        delay = retry.initial_delay_seconds

        while step_rec.attempts <= retry.max_retries:
            try:
                # Dispatch to Central Orchestrator
                success, summary, side_effects = self.orchestrator._execute_subtask(
                    subtask=subtask,
                    workflow_context={"goal": interpolated_desc, "context": execution.context},
                    task_id=execution.execution_id,
                )

                step_rec.summary = summary
                step_rec.side_effects = side_effects

                # Verification of physical deliverables
                is_verified, verdict, evidence = self.orchestrator._verify_deliverables(
                    subtask=subtask,
                    side_effects=side_effects,
                    output_summary=summary,
                )
                step_rec.verification_verdict = verdict
            except Exception as exc:
                success = False
                summary = f"Execution exception: {exc}"
                side_effects = []
                is_verified = False
                verdict = str(exc)
                evidence = {}

            if success and is_verified:
                step_rec.status = StepStatus.COMPLETED
                step_rec.finished_at = time.time()
                step_rec.duration_seconds = step_rec.finished_at - step_rec.started_at
                step_rec.outputs = {"summary": summary, "evidence": evidence}

                # Publish to workflow execution context
                execution.context[step_def.step_id] = {"output": summary}
                execution.context.update(step_rec.outputs)

                # Context bus promotion
                mem_mgr = getattr(self.orchestrator, "memory_manager", None)
                if mem_mgr and hasattr(mem_mgr, "context_bus") and mem_mgr.context_bus:
                    mem_mgr.context_bus.publish_subtask_output(
                        task_id=execution.execution_id,
                        subtask_id=subtask.subtask_id,
                        agent_name=step_def.assigned_agent,
                        key=step_def.step_id,
                        output=summary,
                        is_verified=is_verified,
                        verification_verdict=verdict,
                    )

                self.event_bus.publish(WorkflowStepCompletedEvent(
                    execution_id=execution.execution_id,
                    workflow_id=execution.workflow_id,
                    step_id=step_def.step_id,
                    duration_seconds=step_rec.duration_seconds,
                    summary=summary[:160],
                ))
                return
            else:
                err_msg = f"Execution failed: {summary}" if not success else f"Verification failed: {verdict}"
                step_rec.error = err_msg

                if step_rec.attempts < retry.max_retries:
                    self.event_bus.publish(WorkflowStepFailedEvent(
                        execution_id=execution.execution_id,
                        workflow_id=execution.workflow_id,
                        step_id=step_def.step_id,
                        error=err_msg,
                        attempt=step_rec.attempts,
                        will_retry=True,
                    ))
                    time.sleep(delay)
                    delay = min(delay * retry.backoff_factor, retry.max_delay_seconds)
                    step_rec.attempts += 1
                else:
                    break

        # Max retries exhausted -> Mark FAILED
        step_rec.status = StepStatus.FAILED
        step_rec.finished_at = time.time()
        step_rec.duration_seconds = step_rec.finished_at - step_rec.started_at
        self.event_bus.publish(WorkflowStepFailedEvent(
            execution_id=execution.execution_id,
            workflow_id=execution.workflow_id,
            step_id=step_def.step_id,
            error=step_rec.error or "Unknown step failure",
            attempt=step_rec.attempts,
            will_retry=False,
        ))

    def _evaluate_terminal_state(
        self,
        execution: WorkflowExecutionRecord,
        definition: WorkflowDefinition,
    ) -> None:
        """Check whether all steps in the DAG have reached terminal status."""
        terminal_states = (StepStatus.COMPLETED, StepStatus.SKIPPED, StepStatus.FAILED)
        all_terminal = all(rec.status in terminal_states for rec in execution.step_states.values())

        if all_terminal:
            now = time.time()
            execution.finished_at = now
            duration = now - (execution.started_at or now)

            failed_steps = [
                sid for sid, rec in execution.step_states.items()
                if rec.status == StepStatus.FAILED
            ]

            if failed_steps:
                execution.status = WorkflowStatus.FAILED
                execution.error = f"Workflow failed at step(s): {', '.join(failed_steps)}"
                self.event_bus.publish(WorkflowFailedEvent(
                    execution_id=execution.execution_id,
                    workflow_id=execution.workflow_id,
                    error=execution.error,
                    failed_steps=failed_steps,
                ))
            else:
                execution.status = WorkflowStatus.COMPLETED
                self.event_bus.publish(WorkflowCompletedEvent(
                    execution_id=execution.execution_id,
                    workflow_id=execution.workflow_id,
                    duration_seconds=round(duration, 2),
                ))

    # ── Synchronous & Asynchronous Runners ───────────────────────────────────

    def run_workflow_sync(
        self,
        workflow_id: str,
        parameters: dict[str, Any] | None = None,
        max_iterations: int = 200,
    ) -> WorkflowExecutionRecord:
        """Run workflow to completion synchronously."""
        execution = self.start_workflow(workflow_id, parameters)
        for _ in range(max_iterations):
            execution = self.step_workflow(execution.execution_id)
            if execution.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
                WorkflowStatus.WAITING_APPROVAL,
                WorkflowStatus.PAUSED,
            ):
                break
        return execution

    def run_workflow_async(
        self,
        workflow_id: str,
        parameters: dict[str, Any] | None = None,
    ) -> str:
        """Run workflow in a detached background worker thread."""
        execution = self.start_workflow(workflow_id, parameters)
        exec_id = execution.execution_id

        def _worker():
            try:
                self.run_workflow_sync(workflow_id, parameters)
            except Exception as e:
                logger.error(f"Async workflow worker error for {exec_id}: {e}")
            finally:
                self._active_threads.pop(exec_id, None)

        t = threading.Thread(target=_worker, name=f"wf_worker_{exec_id}", daemon=True)
        self._active_threads[exec_id] = t
        t.start()
        return exec_id

    # ── Human Approval Interface ─────────────────────────────────────────────

    def approve_step(self, approval_id: str, approver: str, reason: str = "Approved.") -> WorkflowExecutionRecord:
        """Approve a pending human gate and immediately advance execution."""
        with self._lock:
            decision = self.approval_manager.approve(approval_id, approver=approver, reason=reason)
            self.store.save_approval(decision)

            execution = self.store.get_execution(decision.execution_id)
            if not execution:
                raise KeyError(f"Execution '{decision.execution_id}' not found.")

            step_rec = execution.step_states.get(decision.step_id)
            if step_rec:
                step_rec.status = StepStatus.READY

            if execution.status == WorkflowStatus.WAITING_APPROVAL:
                execution.status = WorkflowStatus.RUNNING

            self.store.save_execution(execution)

            self.event_bus.publish(ApprovalResolvedEvent(
                approval_id=approval_id,
                execution_id=decision.execution_id,
                step_id=decision.step_id,
                status="APPROVED",
                approver=approver,
                reason=reason,
            ))

            return self.step_workflow(decision.execution_id)

    def reject_step(self, approval_id: str, approver: str, reason: str = "Rejected.") -> WorkflowExecutionRecord:
        """Reject a pending human gate and fail the associated step."""
        with self._lock:
            decision = self.approval_manager.reject(approval_id, approver=approver, reason=reason)
            self.store.save_approval(decision)

            execution = self.store.get_execution(decision.execution_id)
            if not execution:
                raise KeyError(f"Execution '{decision.execution_id}' not found.")

            step_rec = execution.step_states.get(decision.step_id)
            if step_rec:
                step_rec.status = StepStatus.FAILED
                step_rec.error = f"Human authorization denied by {approver}: {reason}"

            execution.status = WorkflowStatus.FAILED
            execution.error = f"Step '{decision.step_id}' rejected by {approver}"
            self.store.save_execution(execution)

            self.event_bus.publish(ApprovalResolvedEvent(
                approval_id=approval_id,
                execution_id=decision.execution_id,
                step_id=decision.step_id,
                status="REJECTED",
                approver=approver,
                reason=reason,
            ))

            return execution

    # ── Pause, Resume & Cancellation ─────────────────────────────────────────

    def pause_workflow(self, execution_id: str, reason: str = "") -> WorkflowExecutionRecord:
        """Pause execution between safe step boundaries."""
        with self._lock:
            execution = self.store.get_execution(execution_id)
            if not execution:
                raise KeyError(f"Execution '{execution_id}' not found.")

            execution.status = WorkflowStatus.PAUSED
            self.store.save_execution(execution)
            self.event_bus.publish(WorkflowPausedEvent(
                execution_id=execution_id,
                workflow_id=execution.workflow_id,
                reason=reason,
            ))
            return execution

    def resume_workflow(self, execution_id: str) -> WorkflowExecutionRecord:
        """Resume execution from safe checkpoint."""
        with self._lock:
            execution = self.store.get_execution(execution_id)
            if not execution:
                raise KeyError(f"Execution '{execution_id}' not found.")

            execution.status = WorkflowStatus.RUNNING
            self.store.save_execution(execution)
            self.event_bus.publish(WorkflowResumedEvent(
                execution_id=execution_id,
                workflow_id=execution.workflow_id,
            ))
            return self.step_workflow(execution_id)

    def cancel_workflow(self, execution_id: str, reason: str = "Cancelled by user.") -> WorkflowExecutionRecord:
        """Halt execution permanently."""
        with self._lock:
            self._cancelled_executions.add(execution_id)
            execution = self.store.get_execution(execution_id)
            if not execution:
                raise KeyError(f"Execution '{execution_id}' not found.")

            execution.status = WorkflowStatus.CANCELLED
            execution.error = reason
            self.store.save_execution(execution)
            self.event_bus.publish(WorkflowCancelledEvent(
                execution_id=execution_id,
                workflow_id=execution.workflow_id,
                reason=reason,
            ))
            return execution

    # ── Crash Recovery & Reconciliation ──────────────────────────────────────

    def recover_interrupted_workflows(self) -> list[WorkflowExecutionRecord]:
        """Scan persistence layer after crash/restart and reconcile in-flight workflows."""
        with self._lock:
            recovered = self.store.reconcile_interrupted_executions()
            self.event_bus.publish(WorkflowRecoveryStartedEvent(
                recovered_count=len(recovered),
                executions=[r.execution_id for r in recovered],
            ))
            return recovered
