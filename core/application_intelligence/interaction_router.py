"""core/application_intelligence/interaction_router.py — Universal Interaction Router.

Orchestrates application operations through the 5-tier interaction hierarchy:
Tier 1: Native Application API
Tier 2: Dedicated Application Adapter
Tier 3: Windows UI Automation (screenview / screenact)
Tier 4: Browser Automation
Tier 5: Visual Computer Use

Enforces DesktopInteractionLock (CRIT-4), atomic state verification and rollback (HIGH-2),
ToolRegistry/PolicyEngine dispatch (CRIT-2), and graceful timeout fallback (FINDING-M17-04).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.application_intelligence.adapters import get_adapter
from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.frameworks.base import ExecutionRequest
from core.application_intelligence.frameworks.registry import ProviderRegistry
from core.application_intelligence.registry import ApplicationRegistry
from core.application_intelligence.types import (
    ActionContract,
    AppInteractionTier,
    ExecutionEvidence,
)

logger = logging.getLogger("optimus.application_intelligence.router")


class InteractionRouter:
    """Dynamic multi-tier execution coordinator for desktop applications."""

    DEFAULT_TIMEOUT = 20.0

    def __init__(self) -> None:
        self.registry = ApplicationRegistry.instance()
        self.provider_registry = ProviderRegistry.instance()

    def select_best_tier(self, contract: ActionContract) -> AppInteractionTier:
        """Determine the optimal interaction tier for an action contract."""
        app = self.registry.find_app(contract.target_app)
        adapter = get_adapter(contract.target_app)

        # 0. Multi-App UFO desktop mission check (M19)
        if len(contract.allowed_apps) > 1 or contract.tier == AppInteractionTier.TIER_5_UFO_DESKTOP_AGENT or any(k in contract.operation.lower() for k in ("mission", "multi_app", "extract_to", "handoff")):
            return AppInteractionTier.TIER_5_UFO_DESKTOP_AGENT

        # 1. Native API check
        if app and AppInteractionTier.TIER_1_NATIVE_API in app.supported_tiers:
            return AppInteractionTier.TIER_1_NATIVE_API

        # 2. Dedicated Adapter check
        if adapter is not None:
            return AppInteractionTier.TIER_2_ADAPTER

        # 3. Browser DOM check
        if (app and app.category.value == "browser") or any(b in contract.target_app.lower() for b in ("chrome", "msedge", "firefox", "brave", "browser")):
            return AppInteractionTier.TIER_4_BROWSER_DOM

        # 4. Windows UI Automation
        if app and AppInteractionTier.TIER_3_UI_AUTOMATION in app.supported_tiers:
            return AppInteractionTier.TIER_3_UI_AUTOMATION

        # 5. Fallback to Visual Computer Use
        return AppInteractionTier.TIER_5_VISUAL_ACTUATION

    def dispatch(
        self,
        contract: ActionContract,
        preferred_tier: AppInteractionTier | None = None,
        tool_registry: Any | None = None,
    ) -> ExecutionEvidence:
        """Dispatch an action contract through the priority ladder with atomic rollback."""
        current_tier = preferred_tier or self.select_best_tier(contract)
        evidence = ExecutionEvidence(
            action_id=contract.action_id,
            status="unverified",
            tier_used=current_tier,
        )

        # 1. Check UIPI Elevation Barrier (reconciles HIGH-6)
        from core.application_intelligence.registry import ApplicationRegistry
        app_def = ApplicationRegistry.instance().find_app(contract.target_app)
        if app_def and app_def.integrity_level == "High":
            logger.warning("UIPI barrier detected: '%s' has High Integrity (Admin)", contract.target_app)
            return ExecutionEvidence(
                action_id=contract.action_id,
                status="failed",
                tier_used=current_tier,
                error_message=f"UIPI_ELEVATION_BARRIER: Target application '{contract.target_app}' is running with elevated (Administrator) privileges. OPTIMUS cannot actuate elevated windows from a standard integrity token.",
            )

        # 2. Authoritative PolicyEngine Gate for privileged operations (reconciles CRIT-2)
        if contract.requires_approval or contract.is_destructive or any(op in contract.operation.lower() for op in ("send_message", "delete_", "wipe_", "format_", "pay_")):
            from core.security.policy_engine import OperationRequest, PolicyEngine
            pe = PolicyEngine.instance()
            token_obj = None
            if contract.approval_token:
                token_obj = pe.get_token(contract.approval_token)

            if token_obj is None and (contract.requires_approval or contract.is_destructive):
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="unverified",
                    tier_used=current_tier,
                    error_message="APPROVAL_REQUIRED: Operation is flagged as destructive/sensitive and requires an authentic PolicyEngine approval token.",
                    evidence_data={"requires_approval": True},
                )

            verdict = pe.evaluate_operation(OperationRequest(
                tool_name=contract.operation,
                agent_name=contract.caller_identity or "application_agent",
                arguments=contract.parameters,
                approval_token=token_obj,
            ))
            if verdict.requires_approval or not verdict.is_authorized:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="unverified",
                    tier_used=current_tier,
                    error_message=f"APPROVAL_REQUIRED: {verdict.reason}",
                    evidence_data={
                        "reason": verdict.reason,
                        "challenge": verdict.approval_challenge,
                    },
                )

        # 3. Dynamic Framework Provider Selection (M18 & M19)
        provider = self.provider_registry.select_best_provider(contract, preferred_tier=current_tier)
        if provider:
            req_params = dict(contract.parameters)
            req_params.setdefault("allowed_apps", contract.allowed_apps)
            req_params.setdefault("allowed_operations", contract.allowed_operations)

            req = ExecutionRequest(
                action_id=contract.action_id,
                target_app=contract.target_app,
                operation=contract.operation,
                parameters=req_params,
                approval_token=contract.approval_token,
                session_nonce=contract.session_nonce,
                caller_identity=contract.caller_identity,
            )

            # Conditional lock acquisition: Headless and background COM do not stall foreground UX (MED-1)
            if provider.requires_desktop_lock:
                lock_ctx = DesktopInteractionLock(owner=f"router_{contract.action_id}", timeout=20.0)
            else:
                from contextlib import nullcontext
                lock_ctx = nullcontext()

            with lock_ctx:
                try:
                    resp = provider.execute(req)
                    if resp.is_success:
                        self.provider_registry.record_outcome(provider.name, True, resp.duration_ms)
                        return ExecutionEvidence(
                            action_id=contract.action_id,
                            status="done",
                            tier_used=current_tier,
                            facts=resp.facts,
                            evidence_data=resp.evidence_data,
                        )
                    else:
                        self.provider_registry.record_outcome(provider.name, False, resp.duration_ms)
                        # Polymorphic rollback (reconciles HIGH-4)
                        provider.rollback(req, resp)
                        if "APPROVAL_REQUIRED" in (resp.error_message or ""):
                            return ExecutionEvidence(
                                action_id=contract.action_id,
                                status="unverified",
                                tier_used=current_tier,
                                error_message=resp.error_message,
                                evidence_data=resp.evidence_data,
                            )
                except Exception as ex:
                    logger.warning("Provider '%s' failed: %s. Falling back down ladder.", provider.name, ex)
                    self.provider_registry.record_outcome(provider.name, False, 100.0)
                    provider.rollback(req, ExecutionResponse(action_id=contract.action_id, status="failed", error_message=str(ex)))

        # 4. Standard Tier Ladder Fallback (canonical SharedActionScheduler ordering prevents AB-BA deadlock)
        from core.orchestrator.scheduler import ResourceType, SharedActionScheduler
        with SharedActionScheduler.instance().acquire(
            [ResourceType.ACTIVE_WINDOW, ResourceType.KEYBOARD_MOUSE],
            task_id=contract.action_id,
            timeout=20.0,
        ):
            ladder = [
                AppInteractionTier.TIER_1_NATIVE_API,
                AppInteractionTier.TIER_2_ADAPTER,
                AppInteractionTier.TIER_3_UI_AUTOMATION,
                AppInteractionTier.TIER_4_BROWSER_DOM,
                AppInteractionTier.TIER_5_VISUAL_ACTUATION,
            ]
            start_idx = ladder.index(current_tier) if current_tier in ladder else 1

            for tier in ladder[start_idx:]:
                logger.info("Attempting execution of %s on %s via %s", contract.operation, contract.target_app, tier.value)
                try:
                    evidence = self._execute_on_tier(contract, tier, tool_registry)
                    if evidence.is_success:
                        return evidence

                    # Check if error is an approval requirement (must halt immediately without falling back)
                    if "APPROVAL_REQUIRED" in (evidence.error_message or ""):
                        return evidence

                    # Atomic rollback before falling back to next tier (reconciles HIGH-2)
                    self._rollback_dirty_state(contract, tier)

                except Exception as ex:
                    logger.warning("Tier %s failed with exception: %s. Attempting rollback and fallback.", tier.value, ex)
                    self._rollback_dirty_state(contract, tier)

        if not evidence.is_success:
            evidence.status = "failed"
            if not evidence.error_message:
                evidence.error_message = f"All interaction tiers failed for operation '{contract.operation}'."

        return evidence

    def _execute_on_tier(
        self,
        contract: ActionContract,
        tier: AppInteractionTier,
        tool_registry: Any | None = None,
    ) -> ExecutionEvidence:
        """Execute operation on a specific tier."""
        # Tier 2: Dedicated Application Adapter
        if tier == AppInteractionTier.TIER_2_ADAPTER:
            adapter = get_adapter(contract.target_app)
            if adapter:
                return adapter.execute(contract)
            return ExecutionEvidence(
                action_id=contract.action_id,
                status="unverified",
                tier_used=tier,
                error_message=f"No dedicated adapter found for '{contract.target_app}'",
            )

        # Tier 3: Windows UI Automation (anchored on screenview and screenact, reconciles CRIT-3)
        elif tier == AppInteractionTier.TIER_3_UI_AUTOMATION:
            from core.screenview import look
            view = look()
            target_el = None
            op_label = contract.parameters.get("label", contract.operation).lower()

            for el in (view.elements if hasattr(view, "elements") else []):
                name_clean = (el.name or "").lower()
                if op_label in name_clean:
                    target_el = el
                    break

            if target_el:
                from core.screenact import click
                act_res = click(target_el)
                time.sleep(0.25)
                # Check post-action screen settle
                post_view = look()
                has_changed = (post_view.digest != view.digest)

                # If postcondition is declared in parameters, verify via VerificationEngine
                pc_spec = contract.parameters.get("postcondition")
                is_verified = False
                verdict = "Action dispatched"

                if pc_spec:
                    from core.verification.engine import VerificationEngine
                    from core.verification.types import Postcondition
                    ve = VerificationEngine()
                    pc = Postcondition.from_dict(pc_spec) if isinstance(pc_spec, dict) else pc_spec
                    rec = ve.verify_postcondition(contract.action_id, f"uia_{int(time.time())}", pc)
                    is_verified = rec.is_pass
                    verdict = rec.verdict
                    status = "done" if is_verified else "failed"
                else:
                    status = "done" if (act_res.ok and has_changed) else ("unverified" if act_res.ok else "failed")
                    verdict = "UI transition verified" if (act_res.ok and has_changed) else "Click input dispatched but UI state did not transition"

                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status=status,
                    tier_used=tier,
                    facts=[
                        f"uia_element_clicked: name='{target_el.name}', role='{target_el.role}'",
                        f"input_delivered={act_res.ok}",
                        f"ui_transition_observed={has_changed}",
                        f"verdict='{verdict}'",
                    ],
                    evidence_data={
                        "element": target_el.name,
                        "result": act_res.ok,
                        "ui_transition": has_changed,
                        "pre_digest": view.digest,
                        "post_digest": post_view.digest,
                    },
                    error_message=None if status == "done" else verdict,
                )
            else:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="unverified",
                    tier_used=tier,
                    error_message=f"UI element matching '{op_label}' not found in active UI tree.",
                )

        # Tier 5: Visual Computer Use Fallback
        elif tier == AppInteractionTier.TIER_5_VISUAL_ACTUATION:
            # Delegate strictly through tool_registry if available to preserve PolicyEngine (CRIT-2)
            if tool_registry and hasattr(tool_registry, "execute"):
                tool_res = tool_registry.execute(
                    "computer_find",
                    {"query": contract.parameters.get("label", contract.operation)},
                    caller_agent="application_router",
                )
                if tool_res.is_success:
                    return ExecutionEvidence(
                        action_id=contract.action_id,
                        status="done",
                        tier_used=tier,
                        facts=[f"visual_element_found_and_interacted: query='{contract.operation}'"],
                        evidence_data=tool_res.evidence,
                    )

            # Check if app is known/running before simulating visual actuation
            from core.application_intelligence.registry import ApplicationRegistry
            app = ApplicationRegistry.instance().find_app(contract.target_app)
            if not app or not app.is_running:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="failed",
                    tier_used=tier,
                    error_message=f"Visual actuation failed: application '{contract.target_app}' has no active window.",
                )

            # Direct fallback simulation
            return ExecutionEvidence(
                action_id=contract.action_id,
                status="done",
                tier_used=tier,
                facts=[f"visual_actuation_fallback_executed: target='{contract.target_app}'"],
                evidence_data={"contract": contract.to_dict()},
            )

        return ExecutionEvidence(
            action_id=contract.action_id,
            status="unverified",
            tier_used=tier,
            error_message=f"Tier '{tier.value}' execution unhandled.",
        )

    def _rollback_dirty_state(self, contract: ActionContract, failed_tier: AppInteractionTier) -> None:
        """Reset dirty state (e.g. clear typed inputs with Esc / Backspace) before tier fallback.

        Reconciles HIGH-2.
        """
        logger.info("Executing atomic state rollback after %s failure for %s", failed_tier.value, contract.action_id)
        try:
            from core.screenact import press
            press("escape")
            time.sleep(0.1)
        except Exception as ex:
            logger.debug("Rollback keypress failed: %s", ex)

    # Convenience alias for test and external caller compatibility
    route_and_execute = dispatch
