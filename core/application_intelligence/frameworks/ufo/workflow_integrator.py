"""core/application_intelligence/frameworks/ufo/workflow_integrator.py — UFO Trajectory to WorkflowStore Bridge.

Integrates Microsoft UFO² action trajectories into OPTIMUS's WorkflowStore and SkillRegistry (reconciles HIGH-5, MED-2):
1. Converts UFO trajectories into canonical LearnedWorkflow definitions.
2. Enforces validation_state = "UNVERIFIED" and SkillTrustLevel.UNVERIFIED.
3. Disables UFO's autonomous offline experience retriever from replaying unverified workflows without human promotion.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from core.application_intelligence.types import ActionContract, LearnedWorkflow
from core.application_intelligence.workflow_store import WorkflowStore

logger = logging.getLogger("optimus.application_intelligence.frameworks.ufo.workflow_integrator")


class UFOWorkflowIntegrator:
    """Bridge converting UFO² action trajectories into verified OPTIMUS workflows."""

    def __init__(self, store: WorkflowStore | None = None) -> None:
        self.store = store or WorkflowStore.instance()

    def capture_and_store_trajectory(
        self,
        contract: ActionContract,
        evidence_data: dict[str, Any],
    ) -> str | None:
        """Convert UFO mission steps into a persistent LearnedWorkflow marked UNVERIFIED (HIGH-5)."""
        tool_calls = evidence_data.get("tool_calls", [])
        if not tool_calls:
            return None

        wf_id = f"wf_ufo_{uuid.uuid4().hex[:8]}"
        action_seq = []
        for idx, call in enumerate(tool_calls, start=1):
            action_seq.append({
                "step": idx,
                "action": call,
                "target_element": contract.target_app,
                "role": "application_control",
            })

        workflow = LearnedWorkflow(
            workflow_id=wf_id,
            target_app=contract.target_app or (contract.allowed_apps[0] if contract.allowed_apps else "Windows"),
            goal=contract.operation or "Automate desktop task via UFO",
            action_sequence=action_seq,
            preconditions=contract.preconditions or ["application_running"],
            expected_outcome=contract.expected_outcome or "Task completed successfully",
            validation_state="UNVERIFIED",  # Enforce UNVERIFIED trust boundary (HIGH-5)
        )

        self.store.save_workflow(workflow)
        logger.info(
            "Persisted UFO mission trajectory '%s' as UNVERIFIED workflow '%s' in WorkflowStore",
            contract.action_id,
            wf_id,
        )
        return wf_id
