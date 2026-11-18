"""core/skills/loader.py — Dynamic Skill Prompt Injection & Context Loader for OPTIMUS.

Formats and injects selected skills into:
- AgentInputContract instructions.
- System prompt contexts for specialized agents.
- Task execution environments.
"""

from __future__ import annotations

import threading

from core.agents.contracts import AgentInputContract
from core.skills.models import SkillManifest
from core.skills.selector import SelectedSkillSet


class SkillLoader:
    """Manages the in-memory formatting and injection of skills into agent execution contexts."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._lock = threading.RLock()

    def format_skill_prompt(self, skill: SkillManifest) -> str:
        """Render a single skill into standard agent instruction block."""
        with self._lock:
            if self._cache.get(skill.id):
                return self._cache[skill.id]

            rendered = (
                f"\n## [ACTIVATED SKILL: {skill.name.upper()}]\n"
                f"> Provenance: {skill.provenance} | Category: {skill.category.value} | Trust: {skill.trust_level.value}\n"
                f"> Description: {skill.description}\n\n"
                f"{skill.instructions.strip()}\n"
                f"## [END SKILL: {skill.name.upper()}]\n"
            )
            self._cache[skill.id] = rendered
            return rendered

    def inject_into_contract(
        self,
        contract: AgentInputContract,
        skill_set: SelectedSkillSet,
    ) -> AgentInputContract:
        """Inject selected skills directly into the instructions and context_data of an AgentInputContract."""
        if not skill_set.skills:
            return contract

        blocks = [self.format_skill_prompt(s) for s in skill_set.skills]
        injected_instructions = contract.instructions + "\n\n" + "\n\n".join(blocks)

        new_context = dict(contract.context_data or {})
        new_context["active_skills"] = [s.id for s in skill_set.skills]
        new_context["skill_selection_rationale"] = skill_set.selection_rationale

        # Merge allowed tools
        merged_tools = list(set(contract.allowed_tools + [t for s in skill_set.skills for t in s.required_tools]))

        return AgentInputContract(
            task_id=contract.task_id,
            subtask_id=contract.subtask_id,
            goal=contract.goal,
            instructions=injected_instructions,
            context_data=new_context,
            allowed_tools=merged_tools,
            workspace_root=contract.workspace_root,
            timeout_seconds=contract.timeout_seconds,
            failure_policy=contract.failure_policy,
        )

    def clear_cache(self) -> None:
        """Invalidate rendered prompt cache."""
        with self._lock:
            self._cache.clear()
