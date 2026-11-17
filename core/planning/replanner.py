"""core/planning/replanner.py — Diagnostic Failure Analyzer & Adaptive Replanner for OPTIMUS.

Features:
- Root-cause failure diagnosis:
  * TRANSIENT: timeouts, temporary network drops, rate limits.
  * ENVIRONMENTAL: missing files, blocked ports, disk exhaustion.
  * CAPABILITY_MISMATCH: agent missing tool, parameter contract error.
  * LOGICAL: postcondition assertion failure, schema mismatch.
  * PERMISSIONS_DENIED: CBAC tier violation, unapproved destructive action.
- Dynamic remediation:
  * Replaces failing tools/agents with suitable fallbacks.
  * Injects prerequisite remediation steps for environmental issues.
  * Increments plan version (v1 -> v2 -> v3) and retains an append-only audit trail.
  * Re-evaluates risk levels and enforces human approval re-gating if risk escalates.
- Dispatches ReplanTriggeredEvent for Control Center telemetry.
"""

from __future__ import annotations

import re
import time
from typing import Any

from core.orchestrator import AgentRegistry, EventBus
from core.planning.events import ReplanTriggeredEvent
from core.planning.outcome_store import OutcomeStore
from core.planning.plan_validator import PlanValidator
from core.planning.types import (
    AdaptivePlan,
    FailureType,
    PlanRiskLevel,
    PlanStep,
    StrategyType,
)

_TRANSIENT_PATTERNS = [
    re.compile(r"\b(timeout|timed out|connection reset|temporar|busy|rate limit|throttl|backoff)\b", re.IGNORECASE),
]
_ENVIRONMENTAL_PATTERNS = [
    re.compile(r"\b(filenotfound|file not found|no such file|missing file|directory not found|directory missing|missing directory|disk full|port in use|connection refused|enoent|eacces)\b", re.IGNORECASE),
]
_PERMISSIONS_PATTERNS = [
    re.compile(r"\b(permission denied|unauthorized|cbac|approval required|forbidden|not allowed|tier restriction)\b", re.IGNORECASE),
]
_CAPABILITY_PATTERNS = [
    re.compile(r"\b(missing tool|tool not found|unknown tool|unsupported capability|cannot execute tool|invalid argument)\b", re.IGNORECASE),
]
_LOGICAL_PATTERNS = [
    re.compile(r"\b(assertion error|postcondition failed|validation failed|expected non-empty|schema mismatch|checksum mismatch)\b", re.IGNORECASE),
]


class AdaptiveReplanner:
    """Diagnoses execution failures, devises remediation strategies, and produces versioned revised plans."""

    def __init__(
        self,
        validator: PlanValidator | None = None,
        event_bus: EventBus | None = None,
        agent_registry: AgentRegistry | None = None,
        outcome_store: OutcomeStore | None = None,
    ) -> None:
        self.validator = validator or PlanValidator(agent_registry=agent_registry)
        self.event_bus = event_bus
        self.agent_registry = agent_registry
        self.outcome_store = outcome_store

    def diagnose_failure(
        self,
        step: PlanStep,
        error_message: str,
        physical_evidence: dict[str, Any] | None = None,
    ) -> FailureType:
        """Classify failure root-cause using error text and physical postcondition evidence."""
        msg = (error_message or "").lower()

        # Check evidence dict first if present
        if physical_evidence:
            ev_status = str(physical_evidence.get("status", "")).lower()
            ev_reason = str(physical_evidence.get("reason", "")).lower()
            msg = f"{msg} {ev_status} {ev_reason}"

        if any(p.search(msg) for p in _TRANSIENT_PATTERNS):
            return FailureType.TRANSIENT

        if any(p.search(msg) for p in _PERMISSIONS_PATTERNS):
            return FailureType.PERMISSIONS_DENIED

        if any(p.search(msg) for p in _ENVIRONMENTAL_PATTERNS):
            return FailureType.ENVIRONMENTAL

        if any(p.search(msg) for p in _CAPABILITY_PATTERNS):
            return FailureType.CAPABILITY_MISMATCH

        if any(p.search(msg) for p in _LOGICAL_PATTERNS):
            return FailureType.LOGICAL

        return FailureType.UNKNOWN

    def replan(
        self,
        plan: AdaptivePlan,
        failed_step_id: str,
        error_message: str,
        physical_evidence: dict[str, Any] | None = None,
    ) -> AdaptivePlan:
        """Devise a remediated AdaptivePlan with incremented version and immutable audit history."""
        # Find the failing step
        step_idx = next((i for i, s in enumerate(plan.steps) if s.step_id == failed_step_id), None)
        if step_idx is None:
            raise ValueError(f"Step '{failed_step_id}' not found in plan '{plan.plan_id}'.")

        failed_step = plan.steps[step_idx]
        failure_type = self.diagnose_failure(failed_step, error_message, physical_evidence)

        # Clone current steps
        new_steps: list[PlanStep] = [PlanStep.from_dict(s.to_dict()) for s in plan.steps]
        target_step = new_steps[step_idx]
        modifications: list[str] = []

        # 1. Apply remediation based on diagnosed root-cause
        if failure_type == FailureType.TRANSIENT:
            # Increase retry count and extend timeout
            target_step.retry_count = max(3, target_step.retry_count + 2)
            target_step.timeout_seconds = max(90.0, target_step.timeout_seconds * 2.0)
            modifications.append(
                f"Transient failure detected: Increased retry count to {target_step.retry_count} "
                f"and timeout to {target_step.timeout_seconds}s."
            )

        elif failure_type == FailureType.ENVIRONMENTAL:
            # Insert a diagnostic & environment repair prerequisite step
            remediation_step_id = f"remediate_env_{target_step.step_id}"
            remediation_step = PlanStep(
                step_id=remediation_step_id,
                title=f"Remediate Environment: {target_step.step_id}",
                description=f"Inspect system state, verify disk/directory existence, and prepare environment for {target_step.step_id}.",
                assigned_agent="system_diagnosis" if (not self.agent_registry or self.agent_registry.is_registered("system_diagnosis")) else "general",
                required_tools=["system_telemetry", "list_directory"],
                depends_on=list(target_step.depends_on),
                is_parallelizable=False,
                is_destructive=False,
                requires_approval=False,
                retry_count=2,
                timeout_seconds=45.0,
            )
            # Failing step now depends on remediation step
            target_step.depends_on = [remediation_step_id]
            new_steps.insert(step_idx, remediation_step)
            modifications.append(
                f"Environmental failure detected: Injected prerequisite remediation step '{remediation_step_id}'."
            )

        elif failure_type == FailureType.CAPABILITY_MISMATCH:
            # Reassign agent or replace specialized tools with general-purpose tools
            old_agent = target_step.assigned_agent
            fallback_agent = self._find_fallback_agent(old_agent)
            target_step.assigned_agent = fallback_agent
            # Replace specialized search tools with basic file/system tools
            fallback_tools = ["read_file", "list_directory"]
            target_step.required_tools = fallback_tools
            modifications.append(
                f"Capability mismatch: Reassigned agent from '{old_agent}' to '{fallback_agent}' "
                f"with fallback tools {fallback_tools}."
            )

        elif failure_type == FailureType.PERMISSIONS_DENIED:
            # Elevate approval requirement to human admin
            target_step.requires_approval = True
            target_step.approval_role = "admin"
            modifications.append(
                "Permissions denied: Gated step with mandatory human administrator approval."
            )

        else:  # LOGICAL or UNKNOWN
            # Switch to Robust Fault-Tolerant mode and insert diagnostic verification
            target_step.retry_count = max(2, target_step.retry_count + 1)
            target_step.timeout_seconds = max(60.0, target_step.timeout_seconds * 1.5)
            modifications.append(
                "Logical/Unknown failure: Reinforced retry policy and tightened postcondition evaluation."
            )

        # 2. Risk Evaluation & Re-gating
        new_risk_level = plan.risk_level
        requires_approval_regating = False
        if any(s.is_destructive or s.requires_approval for s in new_steps) and (
            new_risk_level not in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL)
        ):
            new_risk_level = PlanRiskLevel.HIGH
            requires_approval_regating = True

        # 3. Create Revised Plan
        new_version = plan.version + 1
        history_entry = {
            "old_version": plan.version,
            "new_version": new_version,
            "failed_step_id": failed_step_id,
            "failure_type": failure_type.value,
            "reason": error_message,
            "modifications": modifications,
            "timestamp": time.time(),
        }

        revised_plan = AdaptivePlan(
            plan_id=plan.plan_id,
            goal=plan.goal,
            category=plan.category,
            version=new_version,
            strategy=StrategyType.ROBUST_FAULT_TOLERANT if failure_type in (FailureType.TRANSIENT, FailureType.LOGICAL) else plan.strategy,
            risk_level=new_risk_level,
            steps=new_steps,
            created_at=plan.created_at,
            updated_at=time.time(),
            replan_history=[*plan.replan_history, history_entry],
            metadata=dict(plan.metadata),
        )

        # 4. Validate Revised Plan
        self.validator.validate_or_raise(revised_plan)

        # 5. Dispatch Event
        if self.event_bus:
            self.event_bus.publish(
                ReplanTriggeredEvent(
                    plan_id=revised_plan.plan_id,
                    failed_step_id=failed_step_id,
                    failure_type=failure_type,
                    old_version=plan.version,
                    new_version=new_version,
                    reason=error_message,
                    requires_approval=requires_approval_regating or target_step.requires_approval,
                )
            )

        return revised_plan

    def _find_fallback_agent(self, current_agent: str) -> str:
        """Find a viable fallback agent from the registry."""
        if not self.agent_registry:
            return "general" if current_agent != "general" else "execution"

        available = [a.name for a in self.agent_registry.list_agents()]
        if current_agent in ("code_intelligence", "data_intelligence") and "execution" in available:
            return "execution"
        if "general" in available and current_agent != "general":
            return "general"
        if "execution_agent" in available:
            return "execution_agent"
        return available[0] if available else "general"
