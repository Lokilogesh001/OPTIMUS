"""core/application_intelligence/workflow_store.py — Persistent Store for Learned Application Workflows.

Bridges learned workflows directly into OPTIMUS's existing SkillRegistry and SkillManifest
without creating a competing skill subsystem.
Enforces SkillTrustLevel.UNVERIFIED on all newly synthesized workflows (reconciles HIGH-5).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from core.application_intelligence.types import LearnedWorkflow
from core.skills.models import SkillCategory, SkillManifest, SkillTrustLevel, SkillType
from core.skills.registry import SkillRegistry

logger = logging.getLogger("optimus.application_intelligence.workflow_store")


class WorkflowStore:
    """Persistent storage and SkillRegistry bridge for learned application workflows."""

    _instance: WorkflowStore | None = None
    _lock = threading.RLock()

    def __init__(self, storage_file: Path | str | None = None) -> None:
        if storage_file:
            self.storage_file = Path(storage_file)
        else:
            self.storage_file = Path.cwd() / "config" / "learned_application_workflows.json"
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)

        self._workflows: dict[str, LearnedWorkflow] = {}
        self.skill_registry = SkillRegistry.instance()
        self._load()

    @classmethod
    def instance(cls, storage_file: Path | str | None = None) -> WorkflowStore:
        """Global thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(storage_file=storage_file)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _load(self) -> None:
        """Load stored workflows from disk."""
        if self.storage_file.is_file():
            try:
                data = json.loads(self.storage_file.read_text(encoding="utf-8"))
                for item in data.get("workflows", []):
                    wf = LearnedWorkflow.from_dict(item)
                    self._workflows[wf.workflow_id] = wf
                logger.debug("Loaded %d learned workflows from disk", len(self._workflows))
            except Exception as ex:
                logger.warning("Failed loading learned workflows: %s", ex)

    def _save(self) -> None:
        """Save stored workflows to disk."""
        try:
            payload = {
                "saved_at": time.time(),
                "workflows": [w.to_dict() for w in self._workflows.values()],
            }
            self.storage_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as ex:
            logger.warning("Failed saving learned workflows: %s", ex)

    def save_workflow(self, workflow: LearnedWorkflow) -> None:
        """Save a learned workflow and register into SkillRegistry as UNVERIFIED."""
        with self._lock:
            # Reconciles HIGH-5: Newly saved workflows are ALWAYS UNVERIFIED initially
            workflow.validation_state = "UNVERIFIED"
            self._workflows[workflow.workflow_id] = workflow
            self._save()

            # Bridge into SkillRegistry as SkillManifest (reconciles HIGH-5)
            # Reconciles HIGH-5: Newly saved workflows are ALWAYS UNVERIFIED initially
            trust = SkillTrustLevel.UNVERIFIED
            instructions = (
                f"# Autonomous Application Workflow: {workflow.target_app} — {workflow.goal}\n\n"
                f"**Application:** {workflow.target_app}\n"
                f"**Goal:** {workflow.goal}\n"
                f"**Validation State:** {workflow.validation_state}\n\n"
                f"## Action Sequence\n"
            )
            for step in workflow.action_sequence:
                instructions += f"- Step {step.get('step')}: {step.get('action')} on `{step.get('target_element')}` ({step.get('role')})\n"

            manifest = SkillManifest(
                id=workflow.workflow_id,
                name=f"{workflow.target_app}: {workflow.goal}",
                description=f"Autonomously synthesized workflow for {workflow.target_app} to {workflow.goal}.",
                skill_type=SkillType.TOOL_BASED,
                category=SkillCategory.WORKFLOW,
                trust_level=trust,
                instructions=instructions,
                tags=[workflow.target_app.lower(), "learned_workflow", "application_intelligence"],
                metadata={
                    "target_app": workflow.target_app,
                    "action_sequence": workflow.action_sequence,
                    "validation_state": workflow.validation_state,
                    "preconditions": workflow.preconditions,
                },
            )
            self.skill_registry.register(manifest, overwrite=True)
            logger.info("Registered learned workflow '%s' into SkillRegistry with trust=%s", workflow.workflow_id, trust.value)

    def promote_workflow(self, workflow_id: str, human_confirmed: bool = False) -> bool:
        """Promote a verified workflow to TRUSTED after explicit human confirmation (HIGH-5)."""
        if not human_confirmed:
            logger.warning("Attempted workflow promotion without explicit human confirmation for '%s'", workflow_id)
            return False
        with self._lock:
            wf = self._workflows.get(workflow_id)
            if not wf:
                return False
            wf.validation_state = "VERIFIED_SKILL"
            manifest = self.skill_registry.get(workflow_id)
            if manifest:
                manifest.trust_level = SkillTrustLevel.TRUSTED
                self.skill_registry.register(manifest, overwrite=True)
            self._save()
            logger.info("Workflow '%s' promoted to TRUSTED with human confirmation", workflow_id)
            return True

    def get_workflow(self, workflow_id: str) -> LearnedWorkflow | None:
        """Fetch a learned workflow by ID."""
        with self._lock:
            return self._workflows.get(workflow_id)

    def list_workflows(self, app_name: str | None = None) -> list[LearnedWorkflow]:
        """List learned workflows, optionally filtered by application name."""
        with self._lock:
            if not app_name:
                return list(self._workflows.values())
            clean = app_name.lower().strip()
            return [w for w in self._workflows.values() if clean in w.target_app.lower()]

    def record_execution(self, workflow_id: str, success: bool) -> None:
        """Record an execution outcome for a learned workflow."""
        with self._lock:
            wf = self._workflows.get(workflow_id)
            if wf:
                wf.execution_count += 1
                if success:
                    wf.success_count += 1
                self._save()
