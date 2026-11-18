"""core/skills/executor.py — Controlled Skill Execution Engine for OPTIMUS.

Enforces:
- Central tool routing through ToolRegistry (no unvetted shell or network access).
- Permission policy enforcement via PermissionManager on every tool invocation.
- Strict isolation: a skill cannot independently grant permissions or self-authorize.
- Execution telemetry recording into SkillRegistry.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from core.security.permissions import PermissionManager
from core.skills.models import (
    SkillExecutionRecord,
    SkillManifest,
    SkillTrustLevel,
)
from core.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry, ToolResult


class SkillExecutor:
    """Manages secure application of skills within existing OPTIMUS capability boundaries."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        permission_mgr: PermissionManager | None = None,
    ) -> None:
        self.registry = registry or SkillRegistry.instance()
        self.permission_mgr = permission_mgr or PermissionManager.instance()

    def execute_skill_action(
        self,
        skill: SkillManifest,
        tool_name: str,
        arguments: dict[str, Any],
        tool_registry: ToolRegistry,
        caller_agent: str = "orchestrator",
        task_id: str = "task_skill",
        context: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Execute a tool call requested by a skill, enforcing central permission and audit boundaries."""
        start_time = time.perf_counter()

        # 1. Trust Gate: Blocked skills cannot execute tools
        if skill.trust_level == SkillTrustLevel.BLOCKED:
            from core.orchestrator.tools import ToolResult
            res = ToolResult(
                status="failed",
                error=f"SKILL_BLOCKED: Skill '{skill.id}' is blocked due to security findings.",
            )
            self._record_telemetry(skill.id, task_id, caller_agent, start_time, "blocked", res.error)
            return res

        # 2. Disabled Gate
        if not skill.enabled:
            from core.orchestrator.tools import ToolResult
            res = ToolResult(
                status="failed",
                error=f"SKILL_DISABLED: Skill '{skill.id}' is currently disabled in registry.",
            )
            self._record_telemetry(skill.id, task_id, caller_agent, start_time, "failed", res.error)
            return res

        # 3. Route through central ToolRegistry which enforces PermissionManager
        call_ctx = dict(context or {})
        call_ctx["invoked_by_skill"] = skill.id

        tool_result = tool_registry.execute(
            tool_name=tool_name,
            arguments=arguments,
            caller_agent=caller_agent,
            context=call_ctx,
        )

        status_str = "success" if tool_result.is_success else "failed"
        summary = f"Invoked tool '{tool_name}' via skill '{skill.id}': {status_str}"
        self._record_telemetry(skill.id, task_id, caller_agent, start_time, status_str, summary)

        return tool_result

    def _record_telemetry(
        self,
        skill_id: str,
        task_id: str,
        caller_agent: str,
        start_time: float,
        status: str,
        summary: str,
    ) -> None:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        rec = SkillExecutionRecord(
            skill_id=skill_id,
            task_id=task_id,
            caller_agent=caller_agent,
            timestamp=time.time(),
            status=status,
            duration_ms=duration_ms,
            summary=summary,
        )
        self.registry.record_execution(rec)
