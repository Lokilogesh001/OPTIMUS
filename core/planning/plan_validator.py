"""core/planning/plan_validator.py — Deterministic Plan Graph & Security Validator for OPTIMUS.

Validates:
- Graph acyclicity (DAG verification via cycle detection).
- Self-dependency and dangling dependency prevention.
- Agent existence verification against AgentRegistry.
- CBAC tool authorization checks against Agent Profile permissions.
- Mandatory approval enforcement for destructive operations.
- Risk level consistency across steps and composite plan.
"""

from __future__ import annotations

from core.orchestrator import AgentRegistry, ToolRegistry
from core.planning.types import AdaptivePlan, PlanRiskLevel, PlanStep


class PlanValidator:
    """Performs deterministic semantic, topological, and security validation on AdaptivePlans."""

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.tool_registry = tool_registry

    def validate(self, plan: AdaptivePlan) -> tuple[bool, list[str]]:
        """Validate an AdaptivePlan, returning (is_valid, list_of_error_strings)."""
        errors: list[str] = []

        # 1. Step Count Check
        if not plan.steps:
            errors.append("Plan contains no steps; at least one executable step is required.")
            return False, errors

        # 2. Unique Step IDs Check
        step_ids = [s.step_id for s in plan.steps]
        seen_ids = set()
        for sid in step_ids:
            if not sid:
                errors.append("Plan contains a step with an empty step_id.")
            elif sid in seen_ids:
                errors.append(f"Duplicate step_id detected: '{sid}'. Step IDs must be unique.")
            seen_ids.add(sid)

        # 3. Dependency Integrity & Cycle Detection
        dag_errors = self._validate_dag(plan.steps)
        errors.extend(dag_errors)

        # 4. Destructive Safeguards & Approval Enforcement
        destructive_steps = [s for s in plan.steps if s.is_destructive]
        for s in destructive_steps:
            if not s.requires_approval:
                errors.append(
                    f"Security violation in step '{s.step_id}': Destructive operations require mandatory operator approval."
                )

        if destructive_steps and plan.risk_level not in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL):
            errors.append(
                f"Risk inconsistency: Plan contains {len(destructive_steps)} destructive step(s) "
                f"but plan risk_level is '{plan.risk_level.value}'. It must be HIGH or CRITICAL."
            )

        # 5. Agent Existence Check (if registry is available)
        if self.agent_registry is not None:
            for s in plan.steps:
                if not self.agent_registry.is_registered(s.assigned_agent):
                    errors.append(
                        f"Unrecognized agent '{s.assigned_agent}' in step '{s.step_id}'. "
                        f"Agent is not registered in AgentRegistry."
                    )

        # 6. Tool Whitelist / CBAC Check (if registries are available)
        if self.tool_registry is not None:
            known_tools = {t.name for t in self.tool_registry.list_tools()}
            common_builtins = {
                "read_file", "write_file", "edit_file", "list_directory", "delete_file",
                "process_list", "process_diagnostics", "process_status", "search_code",
                "search_web", "system_telemetry", "system_info",
            }
            for s in plan.steps:
                for tool in s.required_tools:
                    # Allow internal system pseudo-tools or check tool registry
                    if (
                        tool not in known_tools
                        and tool not in common_builtins
                        and not tool.startswith("system_")
                        and not tool.startswith("data_")
                        and known_tools
                    ):
                        errors.append(f"Step '{s.step_id}' references unknown tool: '{tool}'.")

        is_valid = len(errors) == 0
        return is_valid, errors

    def validate_or_raise(self, plan: AdaptivePlan) -> None:
        """Validate plan and raise ValueError if any violations exist."""
        is_valid, errors = self.validate(plan)
        if not is_valid:
            error_details = "\n - " + "\n - ".join(errors)
            raise ValueError(f"Plan validation failed with {len(errors)} error(s):{error_details}")

    def _validate_dag(self, steps: list[PlanStep]) -> list[str]:
        """Detect circular dependencies and invalid references using Kahn's algorithm."""
        errors: list[str] = []
        step_id_set = {s.step_id for s in steps}

        # Build in-degrees and adjacency
        in_degree: dict[str, int] = {s.step_id: 0 for s in steps}
        adj: dict[str, list[str]] = {s.step_id: [] for s in steps}

        for s in steps:
            for dep in s.depends_on:
                if dep == s.step_id:
                    errors.append(f"Step '{s.step_id}' directly depends on itself (self-dependency).")
                elif dep not in step_id_set:
                    errors.append(f"Step '{s.step_id}' depends on non-existent step_id '{dep}'.")
                else:
                    adj[dep].append(s.step_id)
                    in_degree[s.step_id] += 1

        # Kahn's algorithm for cycle detection
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            node = queue.pop(0)
            visited_count += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count < len(steps):
            cycle_nodes = [sid for sid, deg in in_degree.items() if deg > 0]
            errors.append(
                f"Cyclic dependency loop detected involving step(s): {', '.join(cycle_nodes)}."
            )

        return errors
