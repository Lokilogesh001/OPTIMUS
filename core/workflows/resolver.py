"""core/workflows/resolver.py — Deterministic DAG Dependency Resolver for OPTIMUS Workflows.

Implements:
- Topological sort and DAG validation (Kahn's Algorithm).
- Circular dependency detection and rejection (CircularDependencyError).
- Missing dependency detection and rejection (MissingDependencyError).
- Parallel execution stage calculation.
- Dynamic step readiness and conditional evaluation.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from core.workflows.conditions import SafeConditionEvaluator
from core.workflows.types import (
    ApprovalDecision,
    CircularDependencyError,
    MissingDependencyError,
    StepExecutionRecord,
    StepStatus,
    WorkflowDefinition,
    WorkflowStepDefinition,
    WorkflowValidationError,
)


class DependencyResolver:
    """Validates DAG topologies and determines step execution readiness."""

    @staticmethod
    def validate_workflow(workflow: WorkflowDefinition) -> None:
        """Validate workflow structure, step IDs, and dependency integrity."""
        if not workflow.workflow_id:
            raise WorkflowValidationError("Workflow must have a non-empty workflow_id.")
        if not workflow.steps:
            raise WorkflowValidationError("Workflow must define at least one step.")

        step_ids: set[str] = set()
        for step in workflow.steps:
            if not step.step_id:
                raise WorkflowValidationError("Step must have a non-empty step_id.")
            if step.step_id in step_ids:
                raise WorkflowValidationError(f"Duplicate step_id detected: '{step.step_id}'.")
            step_ids.add(step.step_id)

        # Verify dependency targets exist
        for step in workflow.steps:
            for dep in step.depends_on:
                if dep == step.step_id:
                    raise CircularDependencyError(
                        f"Step '{step.step_id}' cannot depend on itself (self-cycle detected)."
                    )
                if dep not in step_ids:
                    raise MissingDependencyError(
                        f"Step '{step.step_id}' depends on non-existent step '{dep}'."
                    )

        # Cycle detection via topological sort
        DependencyResolver.compute_execution_stages(workflow)

    @staticmethod
    def compute_execution_stages(workflow: WorkflowDefinition) -> list[list[str]]:
        """Calculate parallel execution stages (layers) using Kahn's algorithm."""
        step_ids = {s.step_id for s in workflow.steps}
        in_degree: dict[str, int] = {sid: 0 for sid in step_ids}
        dependents: dict[str, list[str]] = defaultdict(list)

        for step in workflow.steps:
            for dep in step.depends_on:
                dependents[dep].append(step.step_id)
                in_degree[step.step_id] += 1

        # Queue for nodes with in-degree 0
        queue: deque[str] = deque([sid for sid, deg in in_degree.items() if deg == 0])
        stages: list[list[str]] = []
        visited_count = 0

        while queue:
            current_stage: list[str] = []
            level_size = len(queue)

            for _ in range(level_size):
                curr = queue.popleft()
                current_stage.append(curr)
                visited_count += 1

                for child in dependents[curr]:
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        queue.append(child)

            stages.append(sorted(current_stage))

        if visited_count != len(step_ids):
            # Nodes remaining with in-degree > 0 indicate a cycle
            unresolved = [sid for sid, deg in in_degree.items() if deg > 0]
            raise CircularDependencyError(
                f"Circular dependency detected involving steps: {sorted(unresolved)}"
            )

        return stages

    @staticmethod
    def get_ready_steps(
        workflow: WorkflowDefinition,
        step_states: dict[str, StepExecutionRecord],
        context: dict[str, Any],
        approvals: dict[str, ApprovalDecision] | None = None,
        allowed_roots: list[str] | None = None,
    ) -> list[WorkflowStepDefinition]:
        """Determine which steps are ready to be scheduled or marked as skipped."""
        ready: list[WorkflowStepDefinition] = []

        for step in workflow.steps:
            rec = step_states.get(step.step_id)
            if not rec or rec.status not in (StepStatus.PENDING, StepStatus.READY):
                continue

            if rec.status == StepStatus.READY:
                ready.append(step)
                continue

            # Check if all upstream dependencies have resolved
            deps_completed = True
            deps_failed = False

            for dep_id in step.depends_on:
                dep_rec = step_states.get(dep_id)
                if not dep_rec:
                    deps_completed = False
                    break
                if dep_rec.status in (StepStatus.PENDING, StepStatus.READY, StepStatus.RUNNING, StepStatus.WAITING_APPROVAL):
                    deps_completed = False
                    break
                if dep_rec.status in (StepStatus.FAILED, StepStatus.AMBIGUOUS_EXECUTION):
                    deps_failed = True

            if not deps_completed:
                continue

            # Check conditions
            if step.condition:
                condition_met = SafeConditionEvaluator.evaluate(
                    condition=step.condition,
                    context=context,
                    step_states=step_states,
                    approvals=approvals,
                    allowed_roots=allowed_roots,
                )
                if not condition_met:
                    # Mark step as explicitly SKIPPED
                    rec.status = StepStatus.SKIPPED
                    rec.summary = "Step skipped: conditional gate evaluated to False."
                    continue

            # If dependencies failed and step condition did not explicitly handle failures
            if deps_failed and not (step.condition and step.condition.expected_value == "FAILED"):
                rec.status = StepStatus.SKIPPED
                rec.summary = "Step skipped due to upstream dependency failure."
                continue

            ready.append(step)

        return ready
