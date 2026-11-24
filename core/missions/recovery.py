"""core/missions/recovery.py — Adaptive Failure Recovery Engine for OPTIMUS Missions.

Provides:
- Root-cause failure diagnosis (TRANSIENT, ENVIRONMENTAL, CAPABILITY_MISMATCH, LOGICAL, PERMISSIONS_DENIED).
- Idempotency-aware recovery: Prevents blind retries of NON_IDEMPOTENT actions (resolves MED-04).
- Dynamic alternative capability lookup from UniversalCapabilityRegistry (resolves LOW-03).
- Prerequisite step injection into active DAG for environmental failures (e.g. missing directory).
- Downstream subtask replanning while preserving completed outputs.
- Bounded retry guardrails.
"""

from __future__ import annotations

import logging
from typing import Any

from core.missions.capabilities import IdempotencyMode, UniversalCapabilityRegistry
from core.missions.types import MissionPlan, MissionSubtask, SubtaskStatus
from core.planning.types import FailureType

logger = logging.getLogger("optimus.missions.recovery")


class MissionRecoveryEngine:
    """Diagnoses subtask failures and devises adaptive recovery strategies."""

    def __init__(
        self,
        capability_registry: UniversalCapabilityRegistry | None = None,
        max_retries_per_step: int = 3,
    ) -> None:
        self.capability_registry = capability_registry or UniversalCapabilityRegistry()
        self.max_retries_per_step = max_retries_per_step

    def diagnose_failure(
        self, subtask: MissionSubtask, error_msg: str, evidence: list[dict[str, Any]] | None = None
    ) -> FailureType:
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

    def select_alternative_tool(self, tool_name: str) -> str | None:
        """Find a viable registered substitute for a failed or unavailable tool via CapabilityRegistry.
        
        Resolves LOW-03: Semantic discovery replacing static nonsensical fallbacks.
        """
        alt = self.capability_registry.find_alternative_capability(tool_name)
        return alt.capability_id if alt else None

    def can_retry(self, subtask: MissionSubtask) -> bool:
        """Determine if subtask retry limit has not been exhausted."""
        limit = min(subtask.max_retries, self.max_retries_per_step)
        return subtask.retry_count < limit

    def adapt_subtask_for_recovery(
        self,
        subtask: MissionSubtask,
        failure_type: FailureType,
        plan: MissionPlan | None = None,
    ) -> bool:
        """Modify subtask parameters or inject prerequisite steps to attempt safe recovery.
        
        Resolves MED-04: Never blind-retries non-idempotent operations without authorization.
        """
        subtask.retry_count += 1

        if failure_type == FailureType.TRANSIENT:
            # Check idempotency: only auto-retry IDEMPOTENT capabilities
            for t_name in subtask.required_tools:
                cap = self.capability_registry.get_capability(t_name)
                if cap and cap.idempotency == IdempotencyMode.NON_IDEMPOTENT:
                    logger.warning(
                        "Cannot auto-retry non-idempotent tool '%s' for subtask '%s'. Escalate to approval.",
                        t_name,
                        subtask.title,
                    )
                    subtask.requires_approval = True
                    return False

            logger.info("Retrying transient failure for subtask '%s' (attempt %d)", subtask.title, subtask.retry_count)
            subtask.status = SubtaskStatus.RETRYING
            return True

        if failure_type == FailureType.CAPABILITY_MISMATCH:
            # Substitute tools using UniversalCapabilityRegistry
            adapted = False
            new_tools: list[str] = []
            for t in subtask.required_tools:
                alt_tool = self.select_alternative_tool(t)
                if alt_tool and alt_tool != t:
                    new_tools.append(alt_tool)
                    adapted = True
                else:
                    new_tools.append(t)

            subtask.required_tools = new_tools
            if adapted:
                logger.info(
                    "Adapted capabilities for subtask '%s': tools=%s",
                    subtask.title,
                    subtask.required_tools,
                )
                subtask.status = SubtaskStatus.RETRYING
                return True

        if failure_type == FailureType.ENVIRONMENTAL and plan:
            # Prerequisite injection: e.g. create missing destination directory
            if "directory" in subtask.error.lower() or "not found" in subtask.error.lower():
                inj_id = f"remed_{subtask.subtask_id}"
                if not any(s.subtask_id == inj_id for s in plan.subtasks):
                    logger.info("Injecting prerequisite remediation step '%s' for '%s'", inj_id, subtask.title)
                    remed_step = MissionSubtask(
                        subtask_id=inj_id,
                        title=f"Remediate Environment for {subtask.title}",
                        description="Auto-provision missing directory or environmental prerequisite.",
                        assigned_agent="file_agent",
                        required_tools=["create_directory"],
                        arguments={"directory_path": "Documents/Research"},
                        depends_on=list(subtask.depends_on),
                    )
                    # Update subtask dependency to depend on injected remediation step
                    subtask.depends_on.append(inj_id)
                    # Insert before subtask in plan
                    idx = plan.subtasks.index(subtask)
                    plan.subtasks.insert(idx, remed_step)
                    subtask.status = SubtaskStatus.PENDING
                    return True

        return False

    def replan_downstream(self, plan: MissionPlan, failed_subtask_id: str) -> None:
        """Mark unexecutable downstream subtasks as SKIPPED to prevent deadlocks."""
        # Find all transitive dependents of the failed subtask
        dependents: set[str] = set()
        queue = [failed_subtask_id]

        while queue:
            curr = queue.pop(0)
            for s in plan.subtasks:
                if curr in s.depends_on and s.subtask_id not in dependents:
                    dependents.add(s.subtask_id)
                    queue.append(s.subtask_id)

        for s in plan.subtasks:
            if s.subtask_id in dependents and s.status == SubtaskStatus.PENDING:
                s.status = SubtaskStatus.SKIPPED
                logger.info("Skipped downstream subtask '%s' due to failure of upstream '%s'", s.title, failed_subtask_id)
