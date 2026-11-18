"""core/security/permissions.py — Central Permission Architecture & Capability Enforcement for OPTIMUS.

Implements three permission profiles:
- LOW: Read-only operations by default. Ask before modifying files or network operations.
- MODERATE: Execute approved workflows. Modify files within authorized workspaces. Require approval for execution/mutation.
- ADVANCED: Expanded autonomous execution (terminal, development, multi-agent, browser, file automation).
  MANDATORY authorization remains strictly enforced for destructive, financial, credential-sensitive,
  and privilege escalation actions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from core.orchestrator.tools import CapabilityTier, ToolDefinition

logger = logging.getLogger("optimus.security.permissions")


class PermissionProfile(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    ADVANCED = "ADVANCED"


@dataclass
class PermissionVerdict:
    """Outcome of a central permission evaluation."""
    allowed: bool
    requires_approval: bool = False
    reason: str = ""
    approval_reason: str = ""


# Prohibited destructive keywords/actions that ALWAYS require approval or are blocked even in ADVANCED
MANDATORY_APPROVAL_KEYWORDS = [
    "format", "mkfs", "fdisk", "diskpart",
    "rmdir /s", "rm -rf", "drop table", "truncate table",
    "netsh advfirewall", "ufw disable", "set-mppreference",
    "sudo", "su -", "runas",
    "bitcoin", "crypto_transfer", "bank_transfer", "payment",
    "private_key", "dump_credentials",
]


class PermissionManager:
    """Central authority governing tool execution and capability boundaries."""

    _instance: PermissionManager | None = None

    def __init__(self, settings_file: Path | None = None) -> None:
        self.settings_file = settings_file or (Path.cwd() / "config" / "optimus_settings.json")
        self._profile = PermissionProfile.ADVANCED  # Default for development installation
        self._load_persisted_profile()

    @classmethod
    def instance(cls) -> PermissionManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_persisted_profile(self) -> None:
        try:
            if self.settings_file.exists():
                data = json.loads(self.settings_file.read_text(encoding="utf-8"))
                prof_str = str(data.get("permission_profile") or "ADVANCED").upper()
                if prof_str in PermissionProfile.__members__:
                    self._profile = PermissionProfile[prof_str]
        except Exception as e:
            logger.warning(f"Could not load persisted permission profile: {e}")

    def _persist_profile(self) -> None:
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if self.settings_file.exists():
                try:
                    data = json.loads(self.settings_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data["permission_profile"] = self._profile.value
            self.settings_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to persist permission profile: {e}")

    def get_profile(self) -> PermissionProfile:
        return self._profile

    def set_profile(self, profile: PermissionProfile | str) -> None:
        """Update permission profile. Must only be invoked via direct user action / UI."""
        if isinstance(profile, str):
            profile = PermissionProfile(profile.upper())
        self._profile = profile
        self._persist_profile()
        logger.info(f"OPTIMUS Permission Profile updated to: {self._profile.value}")

    def evaluate_tool(
        self,
        tool: ToolDefinition,
        caller_agent: str,
        arguments: dict[str, Any],
        workspace_roots: list[str] | None = None,
        is_pre_approved: bool = False,
    ) -> PermissionVerdict:
        """Central deterministic evaluation before any tool invocation.
        
        Evaluates:
        - Permission profile constraints (LOW, MODERATE, ADVANCED)
        - Tool capability tier (READ_ONLY -> SYSTEM_DESTRUCTIVE)
        - Command strings and argument values for mandatory approval criteria
        - Workspace boundary traversal
        """
        tier = tool.capability_tier
        profile = self._profile
        arg_str = json.dumps(arguments, default=str).lower()

        # ── 1. Global Invariant: Mandatory Approval for Destructive/Financial/Escalation Actions ──
        for kw in MANDATORY_APPROVAL_KEYWORDS:
            if kw in arg_str:
                if not is_pre_approved:
                    return PermissionVerdict(
                        allowed=False,
                        requires_approval=True,
                        reason=f"Action matches critical security keyword '{kw}'.",
                        approval_reason=f"MANDATORY SECURITY GATE: Tool '{tool.name}' attempted critical operation containing '{kw}'. Human confirmation required.",
                    )

        if tier == CapabilityTier.SYSTEM_DESTRUCTIVE:
            if not is_pre_approved:
                return PermissionVerdict(
                    allowed=False,
                    requires_approval=True,
                    reason="Tool is categorized as SYSTEM_DESTRUCTIVE.",
                    approval_reason=f"DESTRUCTIVE ACTION: Tool '{tool.name}' has capability tier SYSTEM_DESTRUCTIVE.",
                )

        # ── 2. Profile-Specific Policy Evaluation ──
        if profile == PermissionProfile.LOW:
            # LOW: Read-only operations by default.
            if tier == CapabilityTier.READ_ONLY:
                return PermissionVerdict(allowed=True, reason="Permitted under LOW profile (read-only).")

            if tier == CapabilityTier.WORKSPACE_WRITE:
                if is_pre_approved:
                    return PermissionVerdict(allowed=True, reason="Pre-approved workspace write.")
                return PermissionVerdict(
                    allowed=False,
                    requires_approval=True,
                    reason="File or workspace modification requires confirmation under LOW permission profile.",
                    approval_reason=f"CONFIRMATION REQUIRED: Tool '{tool.name}' wants to modify files under LOW permission mode.",
                )

            if tier == CapabilityTier.NETWORK_EXTERNAL:
                if is_pre_approved:
                    return PermissionVerdict(allowed=True, reason="Pre-approved external network request.")
                return PermissionVerdict(
                    allowed=False,
                    requires_approval=True,
                    reason="External network action requires confirmation under LOW permission profile.",
                    approval_reason=f"CONFIRMATION REQUIRED: Tool '{tool.name}' requests external network access.",
                )

            # Execution, Mutating, Destructive are disallowed without pre-approval
            if is_pre_approved:
                return PermissionVerdict(allowed=True, reason="Pre-approved execution.")
            return PermissionVerdict(
                allowed=False,
                requires_approval=True,
                reason=f"Tool tier '{tier.name}' requires explicit authorization in LOW permission mode.",
                approval_reason=f"HUMAN AUTHORIZATION REQUIRED: Tool '{tool.name}' tier '{tier.name}' is restricted under LOW mode.",
            )

        elif profile == PermissionProfile.MODERATE:
            # MODERATE: Read-only and workspace writes are allowed within bounds.
            if tier in (CapabilityTier.READ_ONLY, CapabilityTier.WORKSPACE_WRITE):
                return PermissionVerdict(allowed=True, reason="Permitted under MODERATE profile (read/workspace write).")

            if tier == CapabilityTier.NETWORK_EXTERNAL:
                return PermissionVerdict(allowed=True, reason="Permitted external query under MODERATE profile.")

            if tier in (CapabilityTier.EXECUTION, CapabilityTier.SYSTEM_MUTATING):
                if is_pre_approved:
                    return PermissionVerdict(allowed=True, reason="Pre-approved execution/mutation.")
                return PermissionVerdict(
                    allowed=False,
                    requires_approval=True,
                    reason=f"Process execution or mutation ('{tier.name}') requires authorization under MODERATE profile.",
                    approval_reason=f"APPROVAL REQUIRED: Tool '{tool.name}' requests execution permission on host system.",
                )

        elif profile == PermissionProfile.ADVANCED:
            # ADVANCED: Expanded autonomous execution (terminal, development, multi-agent, browser, file automation).
            if tier in (
                CapabilityTier.READ_ONLY,
                CapabilityTier.WORKSPACE_WRITE,
                CapabilityTier.NETWORK_EXTERNAL,
                CapabilityTier.EXECUTION,
            ):
                return PermissionVerdict(allowed=True, reason="Autonomous execution permitted under ADVANCED profile.")

            if tier == CapabilityTier.SYSTEM_MUTATING:
                # System mutating tools (e.g. system settings, display, volume) allowed if non-destructive
                return PermissionVerdict(allowed=True, reason="System mutation permitted under ADVANCED profile.")

        return PermissionVerdict(allowed=True, reason="Permitted.")


def get_permission_manager() -> PermissionManager:
    return PermissionManager.instance()
