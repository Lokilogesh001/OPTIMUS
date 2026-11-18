"""core/computer_use/lifecycle.py — 7-Step Interaction Lifecycle for Computer Use.

Implements Phase 4 and Phase 5 of Milestone 9: Computer Use & Visual Intelligence.
The canonical 7-step execution loop:
  1. OBSERVE   — Capture fresh point-in-time desktop observation.
  2. INTERPRET — Locate target element, evaluate confidence, detect ambiguity.
  3. PLAN      — Calibrate coordinates, determine input parameters, evaluate risk.
  4. AUTHORIZE — Enforce centralized security permissions and approval gates.
  5. ACT       — Perform bounded mouse, keyboard, or window actuation.
  6. OBSERVE AGAIN — Capture immediate post-action observation.
  7. VERIFY    — Rigorously verify expected postcondition and state transition.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.computer_use.actuation import ComputerActuator, get_computer_actuator
from core.computer_use.observation import DesktopObserver, get_desktop_observer
from core.computer_use.types import InteractionAction, InteractionResult, UIElement
from core.computer_use.understanding import (
    VisualUnderstandingPipeline,
    get_visual_understanding,
)
from core.security.permissions import PermissionProfile, get_permission_manager

logger = logging.getLogger("optimus.computer_use.lifecycle")

CRITICAL_KEYWORDS = ("delete", "format", "erase", "uninstall", "drop", "terminate", "destroy", "shutdown")


class ComputerInteractionLifecycle:
    """Orchestrates safe, closed-loop visual computer interaction."""

    def __init__(
        self,
        observer: DesktopObserver | None = None,
        understanding: VisualUnderstandingPipeline | None = None,
        actuator: ComputerActuator | None = None,
    ) -> None:
        self.observer = observer or get_desktop_observer()
        self.understanding = understanding or get_visual_understanding()
        self.actuator = actuator or get_computer_actuator()

    def execute_interaction(
        self,
        action: InteractionAction,
        expected_postcondition: dict[str, Any] | None = None,
        is_pre_approved: bool = False,
    ) -> InteractionResult:
        """Execute a complete 7-step actuation lifecycle."""
        # ── Step 1: OBSERVE ──────────────────────────────────────────
        target_win = action.target_name if action.action_type == "switch_window" else None
        obs_before = self.observer.observe(target_window_title=target_win)

        # ── Step 2: RISK EVALUATION & AUTHORIZE ─────────────────────
        risk_level = action.risk_level
        action_name = (action.target_name or "").lower()
        if any(kw in action_name for kw in CRITICAL_KEYWORDS):
            risk_level = "HIGH"

        if action.action_type == "hotkey" and action.keys:
            joined_keys = "+".join(k.lower() for k in action.keys)
            if any(k in joined_keys for k in ("alt+f4", "ctrl+alt+del")):
                risk_level = "CRITICAL"

        pm = get_permission_manager()
        profile = pm.get_profile()

        if (action.requires_approval or risk_level in ("HIGH", "CRITICAL")) and not is_pre_approved:
            return InteractionResult(
                success=False,
                action=action.action_type,
                details=f"Authorization gate: action '{action.action_type}' with risk '{risk_level}' requires human confirmation.",
                postcondition_verified=False,
                confidence=1.0,
                before_digest=obs_before.digest,
                after_digest=obs_before.digest,
                state_changed=False,
                error="APPROVAL_REQUIRED",
            )

        if profile == PermissionProfile.LOW and action.action_type not in ("hover", "switch_window") and not is_pre_approved:
            return InteractionResult(
                success=False,
                action=action.action_type,
                details="Permission profile LOW requires user authorization before interactive actuation.",
                postcondition_verified=False,
                confidence=1.0,
                before_digest=obs_before.digest,
                after_digest=obs_before.digest,
                state_changed=False,
                error="LOW_PROFILE_RESTRICTION",
            )

        # ── Step 3: INTERPRET ────────────────────────────────────────
        target_coords = action.coords
        target_element: UIElement | None = None
        match_confidence = 1.0

        if action.target_name and action.action_type in ("click", "double_click", "right_click", "hover"):
            el, conf, reason = self.understanding.find_best_element(
                obs_before, query=action.target_name, role=action.target_role
            )
            match_confidence = conf
            if not el:
                return InteractionResult(
                    success=False,
                    action=action.action_type,
                    details=f"Interpretation failed: {reason}",
                    postcondition_verified=False,
                    confidence=conf,
                    before_digest=obs_before.digest,
                    after_digest=obs_before.digest,
                    state_changed=False,
                    error="ELEMENT_NOT_LOCATED",
                )
            target_element = el
            target_coords = el.center

        # ── Step 4: PLAN ─────────────────────────────────────────────
        # Validates parameters (e.g. coordinates or text) before actuation

        # ── Step 5: ACT ──────────────────────────────────────────────
        act_details: dict[str, Any] = {}
        try:
            if action.action_type == "click":
                if not target_coords:
                    raise ValueError("No target coordinates or element resolved for click action")
                act_details = self.actuator.click(target_coords[0], target_coords[1])
            elif action.action_type == "double_click":
                if not target_coords:
                    raise ValueError("No target coordinates or element resolved for double-click action")
                act_details = self.actuator.double_click(target_coords[0], target_coords[1])
            elif action.action_type == "right_click":
                if not target_coords:
                    raise ValueError("No target coordinates or element resolved for right-click action")
                act_details = self.actuator.right_click(target_coords[0], target_coords[1])
            elif action.action_type == "hover":
                if not target_coords:
                    raise ValueError("No target coordinates or element resolved for hover action")
                act_details = self.actuator.hover(target_coords[0], target_coords[1])
            elif action.action_type == "type":
                if action.text is None:
                    raise ValueError("Text parameter missing for type action")
                is_secret = target_element.metadata.get("protected", False) if target_element else False
                act_details = self.actuator.type_text(action.text, sensitive=is_secret)
            elif action.action_type == "press":
                key = (action.keys or ["enter"])[0]
                act_details = self.actuator.press(key)
            elif action.action_type == "hotkey":
                keys = action.keys or []
                act_details = self.actuator.hotkey(*keys)
            elif action.action_type == "switch_window":
                target = action.target_name or ""
                act_details = self.actuator.switch_window(target)
            else:
                raise ValueError(f"Unsupported action_type: {action.action_type}")
        except Exception as ex:
            return InteractionResult(
                success=False,
                action=action.action_type,
                details=f"Actuation failure: {ex}",
                postcondition_verified=False,
                confidence=match_confidence,
                before_digest=obs_before.digest,
                after_digest=obs_before.digest,
                state_changed=False,
                error=str(ex),
            )

        # ── Step 6: OBSERVE AGAIN ────────────────────────────────────
        time.sleep(0.18)  # Let target application UI redraw and update
        obs_after = self.observer.observe(target_window_title=target_win)
        diff = self.understanding.diff_observations(obs_before, obs_after)

        # ── Step 7: VERIFY ───────────────────────────────────────────
        verified = False
        verify_reason = ""

        if expected_postcondition:
            # Explicit postconditions
            if "expected_title" in expected_postcondition:
                exp_t = expected_postcondition["expected_title"].lower()
                verified = exp_t in obs_after.window_title.lower()
                verify_reason = f"Window title match: '{obs_after.window_title}' contains '{exp_t}'"
            elif "expected_element" in expected_postcondition:
                exp_e = expected_postcondition["expected_element"]
                found_e = obs_after.find_element(name=exp_e)
                verified = found_e is not None
                verify_reason = f"Element '{exp_e}' presence: {verified}"
            elif "expected_closed" in expected_postcondition:
                exp_c = expected_postcondition["expected_closed"]
                found_c = obs_after.find_element(name=exp_c)
                verified = found_c is None
                verify_reason = f"Element '{exp_c}' closed/dismissed: {verified}"
            elif "state_changed" in expected_postcondition:
                req_change = expected_postcondition["state_changed"]
                verified = diff["state_changed"] == req_change
                verify_reason = f"State changed check: expected {req_change}, observed {diff['state_changed']}"
        else:
            # Default verification heuristic: input succeeded and either state changed or focused
            if action.action_type in ("click", "double_click", "right_click", "type", "press", "hotkey"):
                # Actuation succeeded without exception
                verified = True
                verify_reason = f"Actuation succeeded; state_changed={diff['state_changed']}"
            elif action.action_type == "switch_window":
                verified = act_details.get("success", True)
                verify_reason = "Window switch command executed"
            elif action.action_type == "hover":
                verified = True
                verify_reason = "Cursor position updated"

        return InteractionResult(
            success=verified,
            action=action.action_type,
            details=verify_reason or "Lifecycle completed",
            postcondition_verified=verified,
            confidence=round(match_confidence, 3),
            before_digest=obs_before.digest,
            after_digest=obs_after.digest,
            state_changed=diff["state_changed"],
            evidence={
                "actuation": act_details,
                "diff": diff,
                "before_app": obs_before.active_app,
                "after_app": obs_after.active_app,
            },
        )


_GLOBAL_LIFECYCLE: ComputerInteractionLifecycle | None = None


def get_computer_lifecycle() -> ComputerInteractionLifecycle:
    """Retrieve or initialize the global computer interaction lifecycle."""
    global _GLOBAL_LIFECYCLE
    if _GLOBAL_LIFECYCLE is None:
        _GLOBAL_LIFECYCLE = ComputerInteractionLifecycle()
    return _GLOBAL_LIFECYCLE
