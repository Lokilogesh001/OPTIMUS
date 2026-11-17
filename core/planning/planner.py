"""core/planning/planner.py — Adaptive Execution Planner for OPTIMUS.

Coordinates:
- GoalAnalyzer: Goal interpretation, complexity scoring, subtask decomposition.
- StrategySelector: Dynamic selection between Conservative Sequential, Fast Parallel,
  Specialized Minimal, and Robust Fault-Tolerant profiles.
- PlanValidator: Graph acyclicity, CBAC tool whitelisting, destructive safety gates.
- EventBus: Dispatches PlanFormulatedEvent and PlanValidationFailedEvent.
- Component 6 Interoperability: Exports AdaptivePlan to WorkflowDefinition.
- Component 1 Interoperability: Exports AdaptivePlan to Orchestrator subtask directives.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from core.orchestrator import AgentRegistry, EventBus, ToolRegistry
from core.planning.events import PlanFormulatedEvent, PlanValidationFailedEvent
from core.planning.goal_analyzer import GoalAnalyzer
from core.planning.outcome_store import OutcomeStore
from core.planning.plan_validator import PlanValidator
from core.planning.strategy_selector import StrategySelector
from core.planning.types import (
    AdaptivePlan,
    GoalDecomposition,
    PlanRiskLevel,
    PlanStep,
    StrategyType,
)
from core.workflows.types import WorkflowDefinition, WorkflowStepDefinition


class AdaptivePlanner:
    """Central planning coordinator that formulates, validates, and adapts execution plans."""

    def __init__(
        self,
        analyzer: GoalAnalyzer | None = None,
        strategy_selector: StrategySelector | None = None,
        validator: PlanValidator | None = None,
        event_bus: EventBus | None = None,
        agent_registry: AgentRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        outcome_store: OutcomeStore | None = None,
    ) -> None:
        self.analyzer = analyzer or GoalAnalyzer()
        self.outcome_store = outcome_store
        self.strategy_selector = strategy_selector or StrategySelector(outcome_store=self.outcome_store)
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry
        self.validator = validator or PlanValidator(agent_registry=agent_registry, tool_registry=tool_registry)
        self.event_bus = event_bus

    def create_plan(
        self,
        goal: str,
        strategy_override: StrategyType | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AdaptivePlan:
        """Formulate, optimize, and validate an AdaptivePlan from a user goal."""
        if not goal or not goal.strip():
            raise ValueError("Goal string cannot be empty.")

        # 1. Goal Analysis & Capability Decomposition
        decomp: GoalDecomposition = self.analyzer.analyze(goal)
        raw_steps: list[PlanStep] = self.analyzer.decompose_into_steps(goal, decomp.category)

        # 2. Strategy Selection
        strategy = strategy_override or self.strategy_selector.select_strategy(decomp, raw_steps)

        # 3. Strategy-Specific Step Shaping
        shaped_steps = self._shape_steps_by_strategy(raw_steps, strategy, decomp)

        # 4. Enforce Consistent Plan Risk Level
        risk_level = decomp.risk_level
        if any(s.is_destructive for s in shaped_steps) and risk_level not in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL):
            risk_level = PlanRiskLevel.HIGH

        plan = AdaptivePlan(
            plan_id=f"plan-{uuid.uuid4().hex[:10]}",
            goal=goal.strip(),
            category=decomp.category,
            version=1,
            strategy=strategy,
            risk_level=risk_level,
            steps=shaped_steps,
            created_at=time.time(),
            updated_at=time.time(),
            replan_history=[],
            metadata={
                "complexity_score": decomp.complexity_score,
                "estimated_duration_seconds": decomp.estimated_duration_seconds,
                "required_capabilities": decomp.required_capabilities,
                **(metadata or {}),
            },
        )

        # 5. Deterministic Validation
        is_valid, errors = self.validator.validate(plan)
        if not is_valid:
            if self.event_bus:
                self.event_bus.publish(
                    PlanValidationFailedEvent(
                        plan_id=plan.plan_id,
                        goal=plan.goal,
                        errors=tuple(errors),
                    )
                )
            raise ValueError(f"Plan validation failed: {'; '.join(errors)}")

        # 6. Dispatch Formulated Event
        if self.event_bus:
            self.event_bus.publish(
                PlanFormulatedEvent(
                    plan_id=plan.plan_id,
                    goal=plan.goal,
                    version=plan.version,
                    strategy=plan.strategy,
                    risk_level=plan.risk_level,
                    step_count=len(plan.steps),
                    complexity_score=decomp.complexity_score,
                )
            )

        return plan

    def _shape_steps_by_strategy(
        self,
        steps: list[PlanStep],
        strategy: StrategyType,
        decomp: GoalDecomposition,
    ) -> list[PlanStep]:
        """Adjust step dependencies, parallel flags, retries, and timeouts based on strategy."""
        shaped: list[PlanStep] = []
        for idx, step in enumerate(steps):
            s_dict = step.to_dict()
            s_copy = PlanStep.from_dict(s_dict)

            if strategy == StrategyType.CONSERVATIVE_SEQUENTIAL:
                # Force strictly linear dependencies
                if idx > 0:
                    s_copy.depends_on = [steps[idx - 1].step_id]
                else:
                    s_copy.depends_on = []
                s_copy.is_parallelizable = False

            elif strategy == StrategyType.FAST_PARALLEL:
                # Preserve existing dependencies; if no dependencies, allow parallel
                if not s_copy.depends_on and len(steps) > 1:
                    s_copy.is_parallelizable = True

            elif strategy == StrategyType.ROBUST_FAULT_TOLERANT:
                # Bolster retry limits and timeouts
                s_copy.retry_count = max(3, s_copy.retry_count + 1)
                s_copy.timeout_seconds = max(90.0, s_copy.timeout_seconds * 1.5)

            elif strategy == StrategyType.SPECIALIZED_MINIMAL:
                # Streamlined execution
                s_copy.retry_count = 1
                s_copy.timeout_seconds = 30.0

            shaped.append(s_copy)

        return shaped

    def export_to_workflow(self, plan: AdaptivePlan) -> WorkflowDefinition:
        """Export AdaptivePlan to Component 6 WorkflowDefinition for DAG execution."""
        wf_steps: list[WorkflowStepDefinition] = []
        for s in plan.steps:
            wf_step = WorkflowStepDefinition(
                step_id=s.step_id,
                name=s.title,
                description=s.description,
                assigned_agent=s.assigned_agent,
                required_capabilities=[],
                allowed_tools=list(s.required_tools),
                inputs={},
                depends_on=list(s.depends_on),
                condition=None,
                requires_approval=s.requires_approval,
                approval_role=s.approval_role,
                approval_timeout_seconds=300.0,
                retry_policy={
                    "max_retries": s.retry_count,
                    "backoff_factor": 1.5,
                },
                timeout_seconds=s.timeout_seconds,
                expected_deliverables=list(s.expected_deliverables),
                is_destructive=s.is_destructive,
                nested_workflow_id=None,
            )
            wf_steps.append(wf_step)

        return WorkflowDefinition(
            workflow_id=f"wf-{plan.plan_id}",
            name=f"Workflow: {plan.goal[:40]}",
            description=f"Generated from AdaptivePlan {plan.plan_id} (strategy: {plan.strategy.value})",
            version=plan.version,
            parameters={},
            steps=wf_steps,
            max_depth=3,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    def export_to_orchestrator_subtasks(self, plan: AdaptivePlan) -> list[dict[str, Any]]:
        """Export AdaptivePlan to Component 1 Orchestrator subtask specifications."""
        subtasks: list[dict[str, Any]] = []
        for s in plan.steps:
            subtasks.append({
                "subtask_id": s.step_id,
                "title": s.title,
                "description": s.description,
                "agent_name": s.assigned_agent,
                "tools": list(s.required_tools),
                "dependencies": list(s.depends_on),
                "is_destructive": s.is_destructive,
                "requires_approval": s.requires_approval,
                "expected_deliverables": list(s.expected_deliverables),
                "timeout_seconds": s.timeout_seconds,
            })
        return subtasks
