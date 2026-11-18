"""core/skills/loader.py — Dynamic Skill Prompt Injection & Context Loader for OPTIMUS.

Formulates and injects selected skills into:
- AgentInputContract instructions.
- System prompt contexts for specialized agents.
- Task execution environments.

Optimizations:
- Lazy loading: Native skills are read from disk only on demand.
- Bounded LRU Cache: Prevents memory growth during extended runs.
- Adaptive Memory Relief: Flushes and compacts cached instructions under host memory pressure.
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Any

from core.agents.contracts import AgentInputContract
from core.skills.models import SkillManifest
from core.skills.selector import SelectedSkillSet

logger = logging.getLogger("optimus.skills.loader")


class SkillLoader:
    """Manages the in-memory formatting and injection of skills with bounded LRU caching."""

    def __init__(self, max_cache_size: int = 15) -> None:
        self.max_cache_size = max_cache_size
        self._cache: collections.OrderedDict[str, str] = collections.OrderedDict()
        self._lock = threading.RLock()

        # Connect to AdaptiveResourceManager for memory pressure relief
        try:
            from core.runtime.resources import AdaptiveResourceManager
            AdaptiveResourceManager.instance().register_pressure_relief_handler(self._on_pressure_relief)
        except Exception:
            pass

    def _on_pressure_relief(self, pressure: Any) -> None:
        """Evict cache aggressively when host memory pressure rises."""
        from core.runtime.resources import MemoryPressureLevel
        with self._lock:
            if pressure in (MemoryPressureLevel.HIGH, MemoryPressureLevel.CRITICAL):
                logger.info("SkillLoader evicting cache under %s memory pressure.", pressure.value)
                self._cache.clear()
            elif pressure == MemoryPressureLevel.MODERATE:
                # Retain only the most recent 3 skills
                while len(self._cache) > 3:
                    self._cache.popitem(last=False)

    def format_skill_prompt(self, skill: SkillManifest) -> str:
        """Render a single skill into a standard agent instruction block with LRU caching."""
        with self._lock:
            if skill.id in self._cache:
                self._cache.move_to_end(skill.id)
                return self._cache[skill.id]

            # If installed natively, read directly from native installation directory to ensure source independence
            instructions = skill.instructions
            if skill.is_installed and skill.installed_path:
                from pathlib import Path
                entry_file = Path(skill.installed_path) / skill.entrypoint_file
                if entry_file.is_file():
                    try:
                        instructions = entry_file.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

            installed_badge = " [NATIVE]" if skill.is_installed else ""
            rendered = (
                f"\n## [ACTIVATED SKILL: {skill.name.upper()}{installed_badge}]\n"
                f"> Provenance: {skill.provenance} | Category: {skill.category.value} | Trust: {skill.trust_level.value}\n"
                f"> Description: {skill.description}\n\n"
                f"{instructions.strip()}\n"
                f"## [END SKILL: {skill.name.upper()}]\n"
            )

            # Evict oldest entry if exceeding cache limit
            if len(self._cache) >= self.max_cache_size:
                self._cache.popitem(last=False)

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
