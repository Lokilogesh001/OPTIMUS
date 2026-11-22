"""core/application_intelligence/learning_engine.py — Autonomous Application Learning Engine.

Discovers, inspects, and synthesizes reusable workflows for unfamiliar desktop applications.
Enforces a Strict Two-Phase Boundary (CRIT-5):
- Phase 1: Passive Zero-Click Exploration (Tree extraction only)
- Phase 2: Active Goal-Directed Bounded Execution with Negative Control Filtering

Reconciles HIGH-5 (Learned workflows start as UNVERIFIED, no premature self-promotion).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from core.application_intelligence.capabilities import (
    is_control_destructive,
    is_control_safe_for_exploration,
)
from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.registry import ApplicationRegistry
from core.application_intelligence.types import LearnedWorkflow
from core.outcome import untrusted

logger = logging.getLogger("optimus.application_intelligence.learning_engine")


class ApplicationLearningEngine:
    """Autonomous learner for discovering and synthesizing reusable application workflows."""

    def __init__(self, workflow_store: Any | None = None) -> None:
        self.workflow_store = workflow_store

    def passive_explore_app(self, app_name_or_query: str) -> dict[str, Any]:
        """Phase 1: Passive Zero-Click Exploration of an unfamiliar application.

        Extracts the accessibility UI tree, menu bar items, buttons, and input fields
        WITHOUT performing any physical clicks (reconciles CRIT-5).
        """
        registry = ApplicationRegistry.instance()
        launch_res = registry.launch_or_activate(app_name_or_query)
        if not launch_res.get("success"):
            return {
                "success": False,
                "error": launch_res.get("error", f"Could not launch or activate '{app_name_or_query}'"),
                "discovered_controls": [],
            }

        time.sleep(0.5)

        # Inspect UI tree via core.screenview (CRIT-3)
        from core.screenview import look
        view = look()

        discovered_controls = []
        for el in (view.elements if hasattr(view, "elements") else []):
            label = el.name or ""
            role = el.role.value if hasattr(el.role, "value") else str(el.role)
            auto_id = getattr(el, "automation_id", "") or ""

            # Check safety
            is_destructive = is_control_destructive(label, auto_id, role)
            is_safe = is_control_safe_for_exploration(label, auto_id, role)

            discovered_controls.append({
                "label": untrusted("ui", label),
                "role": role,
                "automation_id": auto_id,
                "is_destructive": is_destructive,
                "is_safe_for_action": is_safe,
            })

        logger.info(
            "Passive exploration completed for %s: Discovered %d controls (Zero Clicks issued)",
            app_name_or_query,
            len(discovered_controls),
        )

        return {
            "success": True,
            "app": app_name_or_query,
            "window_title": untrusted("window", getattr(view, "title", getattr(view, "active_window", ""))),
            "control_count": len(discovered_controls),
            "controls": discovered_controls,
            "mode": "passive_zero_click",
        }

    def learn_workflow_for_goal(
        self,
        app_name: str,
        user_goal: str,
        max_steps: int = 5,
    ) -> LearnedWorkflow:
        """Phase 2: Active Goal-Directed Workflow Synthesis with strict negative control filtering.

        Synthesizes a proposed action sequence matching an explicit user objective.
        Hard-blocks any destructive actions and registers the workflow as UNVERIFIED (HIGH-5).
        """
        clean_goal = user_goal.strip()
        app_reg = ApplicationRegistry.instance()
        app = app_reg.find_app(app_name)
        target_name = app.name if app else app_name

        # 1. Passive exploration first
        explore_data = self.passive_explore_app(app_name)
        controls = explore_data.get("controls", [])

        # 2. Match goal words to available safe controls
        goal_words = set(re.findall(r"\w+", clean_goal.lower()))
        action_sequence = []

        for c in controls:
            if not c["is_safe_for_action"]:
                # NEVER include destructive controls in learned workflows (CRIT-5)
                continue

            c_words = set(re.findall(r"\w+", c["label"].lower()))
            if goal_words.intersection(c_words):
                action_sequence.append({
                    "step": len(action_sequence) + 1,
                    "target_element": c["label"],
                    "role": c["role"],
                    "action": "click" if "button" in c["role"].lower() or "menu" in c["role"].lower() else "inspect",
                    "precondition": f"window_active('{target_name}')",
                    "expected_state": f"element_interacted('{c['label']}')",
                })
                if len(action_sequence) >= max_steps:
                    break

        # If no specific controls match, synthesize standard canonical pathway
        if not action_sequence:
            action_sequence.append({
                "step": 1,
                "target_element": "File",
                "role": "menuitem",
                "action": "click",
                "precondition": f"window_active('{target_name}')",
                "expected_state": "menu_opened('File')",
            })
            action_sequence.append({
                "step": 2,
                "target_element": clean_goal.split()[-1].capitalize(),
                "role": "menuitem",
                "action": "click",
                "precondition": "menu_opened('File')",
                "expected_state": f"dialog_or_action_triggered('{clean_goal}')",
            })

        # 3. Create LearnedWorkflow with validation_state = UNVERIFIED (reconciles HIGH-5)
        workflow_id = f"wf_{target_name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}"
        workflow = LearnedWorkflow(
            workflow_id=workflow_id,
            target_app=target_name,
            goal=clean_goal,
            action_sequence=action_sequence,
            validation_state="UNVERIFIED",  # Enforce HIGH-5: Must NOT self-promote to VERIFIED
            preconditions=[f"app_installed('{target_name}')"],
            recovery_hints=["press_escape_on_failure", "restore_main_window"],
            created_at=time.time(),
        )

        # 4. Save to WorkflowStore if provided
        if self.workflow_store:
            self.workflow_store.save_workflow(workflow)

        logger.info(
            "Synthesized learned workflow '%s' for goal '%s' with %d steps (State: UNVERIFIED)",
            workflow_id,
            clean_goal,
            len(action_sequence),
        )
        return workflow
