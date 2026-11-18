"""core/proactive/governance.py — 4-Level Action Governance & Consequential Action Gating.

Implements Phase 5 of Milestone 10: Recommendation & Action Governance.
Strictly enforces:
- Level 1 (Recommend): Advisory next-action, zero execution.
- Level 2 (Prepare): Gather information, stage plan, zero mutation.
- Level 3 (Execute): Autonomous low-risk execution within granted permissions.
- Level 4 (Approval Required): Mandatory pause requiring explicit human authorization.
"""

from __future__ import annotations

import json
from typing import Any

from core.proactive.types import ActionGovernanceLevel
from core.security.permissions import PermissionProfile, get_permission_manager

CONSEQUENTIAL_KEYWORDS = (
    "delete", "remove", "rmdir", "unlink", "drop", "truncate", "format",
    "erase", "destroy", "uninstall", "shutdown", "reboot", "kill",
    "password", "secret", "token", "credential", "private_key",
    "purchase", "buy", "pay", "checkout", "transfer", "financial",
)

PREPARATION_KEYWORDS = (
    "inspect", "analyze", "gather", "plan", "telemetry", "check", "scan",
    "measure", "read", "list", "observe", "status",
)


class ActionGovernor:
    """Classifies actions into the 4 governance tiers and enforces safety gates."""

    @classmethod
    def classify_action(
        cls,
        action_type: str,
        target: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[ActionGovernanceLevel, str]:
        """Classify any proposed scheduled or recommended action into its appropriate governance level."""
        combined_text = f"{action_type} {target} {json.dumps(payload or {}, default=str)}".lower()

        # Level 4: Consequential / Destructive / Financial / Credential Operations
        for kw in CONSEQUENTIAL_KEYWORDS:
            if kw in combined_text:
                return (
                    ActionGovernanceLevel.LEVEL_4_APPROVAL_REQUIRED,
                    f"Action matches critical security keyword '{kw}'. Explicit operator approval is mandatory.",
                )

        # Level 2: Pure Preparation / Telemetry / Non-destructive analysis
        if any(kw in combined_text for kw in PREPARATION_KEYWORDS) and "write" not in combined_text:
            return (
                ActionGovernanceLevel.LEVEL_2_PREPARE,
                "Information gathering and non-destructive preparation only.",
            )

        # Level 3 vs Level 1 based on permission profile
        pm = get_permission_manager()
        profile = pm.get_profile()

        if profile == PermissionProfile.LOW:
            # Under LOW, mutations are recommendation-only
            if any(k in action_type for k in ("write", "modify", "execute", "run", "interact")):
                return (
                    ActionGovernanceLevel.LEVEL_1_RECOMMEND,
                    "Permission profile LOW restricts automatic execution; action framed as advisory recommendation.",
                )

        # Level 3: Autonomous execution of approved low-risk actions
        return (
            ActionGovernanceLevel.LEVEL_3_EXECUTE,
            "Authorized low-risk autonomous execution within active permission profile.",
        )

    @classmethod
    def can_auto_execute(
        cls,
        level: ActionGovernanceLevel,
        is_pre_approved: bool = False,
    ) -> bool:
        """Evaluate if an action at the specified governance level can proceed without interactive approval."""
        if level == ActionGovernanceLevel.LEVEL_1_RECOMMEND:
            return False  # Advisory only
        if level == ActionGovernanceLevel.LEVEL_2_PREPARE:
            return True   # Safe non-destructive staging
        if level == ActionGovernanceLevel.LEVEL_3_EXECUTE:
            return True   # Authorized execution
        if level == ActionGovernanceLevel.LEVEL_4_APPROVAL_REQUIRED:
            return is_pre_approved
        return False


_GLOBAL_GOVERNOR: ActionGovernor | None = None


def get_action_governor() -> ActionGovernor:
    global _GLOBAL_GOVERNOR
    if _GLOBAL_GOVERNOR is None:
        _GLOBAL_GOVERNOR = ActionGovernor()
    return _GLOBAL_GOVERNOR
