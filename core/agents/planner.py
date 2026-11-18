"""core/agents/planner.py — Planning and Strategy Specialist Agent for OPTIMUS.

Provides:
- Strategic goal decomposition into actionable dependency graphs (DAGs).
- Resource requirement and risk estimation.
- Tool capability matching and specialized agent routing.
- Adaptive plan validation and replan recommendations.
"""

from __future__ import annotations

import time

from core.agents.base import SpecializedAgent
from core.agents.contracts import AgentInputContract, AgentOutputContract
from core.orchestrator.agents import AgentCapability, AgentProfile
from core.orchestrator.tools import CapabilityTier, ToolRegistry


def create_planning_agent_profile() -> AgentProfile:
    """Create the canonical Planning Agent profile."""
    return AgentProfile(
        name="planning_agent",
        title="Mission & Strategy Planning Specialist",
        description="Analyzes complex high-level objectives, decomposes goals into structured DAG plans, assigns specialized agents, estimates resources, and identifies approval gates.",
        capability=AgentCapability(
            domain="planning",
            allowed_tools=[
                "read_file",
                "list_directory",
                "calculator",
            ],
            allowed_tiers=[CapabilityTier.READ_ONLY],
            max_turns=5,
            timeout_seconds=120.0,
        ),
        instructions=(
            "You are the OPTIMUS Planning Agent. Decompose complex objectives into clear, "
            "actionable subtasks with dependencies, concurrency, resource estimates, and success criteria."
        ),
    )


class PlanningAgent(SpecializedAgent):
    """Specialized agent performing goal analysis and mission DAG decomposition."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_planning_agent_profile())

    def execute(
        self,
        contract: AgentInputContract,
        tool_registry: ToolRegistry,
    ) -> AgentOutputContract:
        """Decompose requested objective into a structured plan."""
        start_time = time.time()
        goal = contract.subtask_description or contract.goal

        try:
            from core.missions.planner import MissionPlanner
            planner = MissionPlanner(tool_registry=tool_registry)
            plan = planner.plan(goal)

            summary_lines = [
                f"### Mission Plan Formulated: {plan.plan_id}",
                f"- Objective: {plan.objective}",
                f"- Subtasks Count: {len(plan.subtasks)}",
                f"- Concurrency Supported: {any(s.parallelizable for s in plan.subtasks)}",
                f"- Risk Level: {plan.risk_level}",
                f"- Requires User Approval: {plan.requires_approval}",
                f"- Estimated Duration: {plan.estimated_resources.get('estimated_duration_seconds', 0)}s",
                "\n#### Planned Subtasks:",
            ]
            for s in plan.subtasks:
                deps = f" (depends on: {', '.join(s.depends_on)})" if s.depends_on else ""
                summary_lines.append(f"  {s.subtask_id}: [{s.assigned_agent}] {s.title}{deps}")

            output_text = "\n".join(summary_lines)
            return AgentOutputContract(
                subtask_id=contract.subtask_id,
                agent_name=self.name,
                is_success=True,
                output=output_text,
                deliverables=[f"plan:{plan.plan_id}"],
                execution_time_seconds=time.time() - start_time,
                metadata={"plan": plan.to_dict()},
            )
        except Exception as e:
            return AgentOutputContract(
                subtask_id=contract.subtask_id,
                agent_name=self.name,
                is_success=False,
                output="",
                error=f"PlanningAgent error: {e}",
                execution_time_seconds=time.time() - start_time,
            )
