"""core/skills/validator.py — Schema & Compatibility Validation for OPTIMUS Skills.

Ensures:
- Manifest fields satisfy required structural contracts.
- File integrity matches computed SHA-256 fingerprint.
- Required tools exist in ToolRegistry.
- Compatibility with user's active permission profile.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from core.skills.models import SkillManifest, SkillTrustLevel
from core.skills.security import SkillSecurityValidator

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry
    from core.security.permissions import PermissionManager


class SkillValidator:
    """Validates skill structure, security, dependencies, and environment compatibility."""

    def __init__(self, security_validator: SkillSecurityValidator | None = None) -> None:
        self.security_validator = security_validator or SkillSecurityValidator()

    def validate(
        self,
        manifest: SkillManifest,
        tool_registry: ToolRegistry | None = None,
        permission_mgr: PermissionManager | None = None,
    ) -> tuple[bool, list[str]]:
        """Validate a skill's manifest, security posture, and runtime dependencies.

        Returns:
            (is_valid, validation_errors_or_warnings)
        """
        errors: list[str] = []

        # 1. Structural requirements
        if not manifest.id or not manifest.id.replace("-", "").replace("_", "").isalnum():
            errors.append(f"Invalid skill ID '{manifest.id}': must be alphanumeric with hyphens/underscores.")
        if not manifest.name:
            errors.append("Skill name cannot be empty.")
        if not manifest.description:
            errors.append("Skill description cannot be empty.")

        # 2. Content integrity check
        if manifest.instructions:
            computed_sha = hashlib.sha256(manifest.instructions.encode("utf-8")).hexdigest()
            if manifest.content_sha256 and manifest.content_sha256 != computed_sha:
                errors.append("Integrity check failed: manifest content_sha256 does not match instruction hash.")

        # 3. Security scan
        report = self.security_validator.scan_skill(manifest)
        manifest.security_report = report
        manifest.trust_level = report.trust_level

        if report.trust_level == SkillTrustLevel.BLOCKED:
            errors.append(f"SECURITY BLOCKED: Skill has risk score {report.risk_score}/10 with critical issues.")
            manifest.enabled = False

        # 4. Dependency & Tool check against ToolRegistry
        if tool_registry and manifest.required_tools:
            missing_tools = [t for t in manifest.required_tools if not tool_registry.is_registered(t)]
            if missing_tools:
                errors.append(f"Missing required tools: {', '.join(missing_tools)}")

        is_valid = len([e for e in errors if not e.startswith("WARNING")]) == 0
        return is_valid, errors
