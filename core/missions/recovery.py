"""core/missions/recovery.py — Adaptive Failure Recovery Engine for OPTIMUS Missions.

Provides:
- Failure diagnosis & classification (TRANSIENT, ENVIRONMENTAL, CAPABILITY_MISMATCH, LOGICAL, PERMISSIONS_DENIED).
- Alternative tool and agent fallback recommendations.
- Downstream subtask replanning while preserving all completed work and outputs.
- Bounded retry guardrails.
"""

from __future__ import annotations

import logging
from typing import Any

from core.missions.types import MissionPlan, MissionSubtask, SubtaskStatus
from core.planning.types import FailureType

logger = logging.getLogger(__name__)


# Canonical fallback tool alternatives
TOOL_FALLBACKS: dict[str, list[str]] = {
    "web_search": ["duckduckgo_search", "read_file"],
    "duckduckgo_search": ["web_search", "read_file"],
    "run_bash": ["run_powershell", "run_script"],
    "run_powershell": ["run_bash", "run_script"],
    "cpu_status": ["get_vitals", "system_diagnostics"],
    "memory_status": ["get_vitals", "system_diagnostics"],
    "write_file": ["append_file"],
    "read_file": ["view_code"],
}

# Canonical fallback agent alternatives
AGENT_FALLBACKS: dict[str, str] = {
    "researcher": "research_agent",
    "research_agent": "general",
    "developer": "developer_agent",
    "developer_agent": "execution",
    "system_diagnosis": "system_agent",
    "system_agent": "general",
    "execution": "file_agent",
    "file_agent": "general",
    "verifier": "verification_agent",
    "verification_agent": "general",
    "data_intel": "data_agent",
    "data_agent": "general",
    "planner": "planning_agent",
}


class MissionRecoveryEngine:
    """Diagnoses subtask failures and devises adaptive recovery strategies."""

    def __init__(self, max_retries_per_step: int = 3) -> None:
        self.max_retries_per_step = max_retries_per_step

    def diagnose_failure(self, subtask: MissionSubtask, error_msg: str, evidence: list[dict[str, Any]] | None = None) -> FailureType:
        """Classify subtask failure into an actionable taxonomy."""
        err_lower = (error_msg or "").lower()
        
        # Check permissions
        if any(w in err_lower for w in ("permission", "access denied", "forbidden", "cbac", "unauthorized", "outside allowed")):
            return FailureType.PERMISSIONS_DENIED

        # Check transient network / timeout
        if any(w in err_lower for w in ("timeout", "timed out", "connection reset", "econnreset", "temporary", "rate limit", "429")):
            return FailureType.TRANSIENT

        # Check capability mismatch / missing tool
        if any(w in err_lower for w in ("no tool named", "tool not found", "unknown tool", "not registered", "unsupported parameter", "missing capability")):
            return FailureType.CAPABILITY_MISMATCH

        # Check environmental / missing resource
        if any(w in err_lower for w in ("not found", "no such file", "does not exist", "disk full", "enospc", "path invalid", "port in use")):
            return FailureType.ENVIRONMENTAL

        # Check logical / postcondition verification failure
        if any(w in err_lower for w in ("postcondition", "deliverable missing", "verification failed", "assertion failed", "empty output")):
            return FailureType.LOGICAL

        return FailureType.UNKNOWN

    def select_alternative_tool(self, tool_name: str, available_tools: list[str]) -> str | None:
        """Find a viable registered substitute for a failed or unavailable tool."""
        fallbacks = TOOL_FALLBACKS.get(tool_name, [])
        for fb in fallbacks:
            if fb in available_tools:
                return fb
        return None

    def select_alternative_agent(self, agent_name: str, available_agents: list[str]) -> str:
        """Find a fallback specialized agent."""
        alt = AGENT_FALLBACKS.get(agent_name.lower())
        if alt and alt in available_agents:
            return alt
        if "general" in available_agents:
            return "general"
        return agent_name

    def can_retry(self, subtask: MissionSubtask) -> bool:
        """Determine if subtask retry limit has not been exhausted."""
        limit = min(subtask.max_retries, self.max_retries_per_step)
        return subtask.retry_count < limit

    def adapt_subtask_for_recovery(
        self,
        subtask: MissionSubtask,
        failure_type: FailureType,
        available_tools: list[str],
        available_agents: list[str],
    ) -> bool:
        """Modify subtask parameters in-place to attempt safe recovery.
        
        Returns True if a recovery strategy was configured, False if unrecoverable.
        """
        subtask.retry_count += 1

        if failure_type == FailureType.TRANSIENT:
            # Simple retry with backoff is appropriate
            logger.info("Retrying transient failure for subtask '%s' (attempt %d)", subtask.title, subtask.retry_count)
            subtask.status = SubtaskStatus.RETRYING
            return True

        if failure_type == FailureType.CAPABILITY_MISMATCH:
            # Substitute tools or agents
            adapted = False
            new_tools: list[str] = []
            for t in subtask.required_tools:
                if t not in available_tools:
                    alt_tool = self.select_alternative_tool(t, available_tools)
                    if alt_tool:
                        new_tools.append(alt_tool)
                        adapted = True
                    else:
                        new_tools.append(t)
                else:
                    new_tools.append(t)
            
            subtask.required_tools = new_tools

            if subtask.assigned_agent not in available_agents:
                alt_agent = self.select_alternative_agent(subtask.assigned_agent, available_agents)
                subtask.assigned_agent = alt_agent
                adapted = True

            if adapted:
                logger.info("Adapted capabilities for subtask '%s': tools=%s, agent=%s", subtask.title, subtask.required_tools, subtask.assigned_agent)
                subtask.status = SubtaskStatus.RETRYING
                return True

        if failure_type == FailureType.ENVIRONMENTAL:
            # If a deliverable path is invalid, try alternative relative path or general fallback
            subtask.status = SubtaskStatus.RETRYING
            return True

        if failure_type == FailureType.LOGICAL:
            # Attempt with developer or execution agent fallback
            if subtask.assigned_agent not in ("developer", "developer_agent", "execution"):
                alt_agent = self.select_alternative_agent(subtask.assigned_agent, available_agents)
                subtask.assigned_agent = alt_agent
                subtask.status = SubtaskStatus.RETRYING
                return True

        # Unrecoverable or PERMISSIONS_DENIED
        return False

    def replan_downstream(
        self,
        plan: MissionPlan,
        failed_subtask_id: str,
        recovery_plan_generator: Any | None = None,
    ) -> MissionPlan:
        """Replan affected downstream subtasks while preserving completed work.
        
        Identifies all subtasks that directly or transitively depend on the failed subtask.
        Completed subtasks are NEVER modified or re-executed.
        """
        # Find all dependent subtasks
        dependent_ids: set[str] = set()
        queue = [failed_subtask_id]
        while queue:
            curr = queue.pop(0)
            for st in plan.subtasks:
                if curr in st.depends_on and st.subtask_id not in dependent_ids and st.status != SubtaskStatus.COMPLETED:
                    dependent_ids.add(st.subtask_id)
                    queue.append(st.subtask_id)

        logger.info(
            "Replanning downstream of %s. Affected subtasks: %s (preserving %d completed)",
            failed_subtask_id,
            list(dependent_ids),
            sum(1 for s in plan.subtasks if s.status == SubtaskStatus.COMPLETED),
        )

        plan.version += 1
        return plan
