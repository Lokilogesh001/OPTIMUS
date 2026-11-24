"""core/application_intelligence/ufo_integrator.py — Microsoft UFO² Trajectory Quarantining Integrator.

Converts multi-application interaction trajectories from Microsoft UFO² into
learned workflows, enforcing that all unverified trajectories remain strictly
quarantined in state UNVERIFIED in the WorkflowStore until reviewed and promoted by the operator.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from core.application_intelligence.types import LearnedWorkflow
from core.application_intelligence.workflow_store import WorkflowStore

logger = logging.getLogger("optimus.application_intelligence.ufo_integrator")


class UFOQuarantineIntegrator:
    """Imports trajectories from UFO² runs and quarantines them as UNVERIFIED."""

    def __init__(self, store: Optional[WorkflowStore] = None):
        self.store = store or WorkflowStore()

    def import_ufo_trajectory(
        self,
        task_name: str,
        target_app: str,
        actions: List[Dict[str, Any]],
        recovery_hints: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LearnedWorkflow:
        """Converts raw UFO² trajectory into a quarantined LearnedWorkflow."""
        import uuid
        wf_id = f"ufo_{uuid.uuid4().hex[:12]}"

        # Sanitization: Ensure all action records are clean
        sanitized_actions = []
        for act in actions:
            clean_act = {
                "step": act.get("step", len(sanitized_actions) + 1),
                "operation": act.get("operation", "interact"),
                "control": act.get("control", ""),
                "parameters": act.get("parameters", {}),
            }
            sanitized_actions.append(clean_act)

        # INVARIANT: UFO² learned workflows MUST enter in UNVERIFIED state
        workflow = LearnedWorkflow(
            workflow_id=wf_id,
            target_app=target_app,
            goal=task_name,
            action_sequence=sanitized_actions,
            validation_state="UNVERIFIED",  # Strictly quarantined
            recovery_hints=recovery_hints or [],
            expected_outcome=f"Completed {task_name} via UFO² trajectory",
            metadata=metadata or {"source": "microsoft_ufo2_host_agent"},
        )

        self.store.save(workflow)
        logger.info(
            "Imported UFO² trajectory '%s' for '%s' as QUARANTINED (UNVERIFIED) workflow %s",
            task_name, target_app, wf_id
        )
        return workflow

    def promote_workflow(self, workflow_id: str, new_state: str = "VERIFIED_SKILL") -> bool:
        """Operator promotion of a quarantined workflow."""
        wf = self.store.get(workflow_id)
        if not wf:
            return False
        wf.validation_state = new_state
        self.store.save(wf)
        logger.info("Promoted workflow %s to state '%s'", workflow_id, new_state)
        return True
