"""core/skills/selector.py — Intelligent Context-Aware Skill Selector for OPTIMUS.

Implements:
- Goal & Intent Matching across task specifications.
- Agent Role Compatibility (aligning skills with specialized agent profiles).
- Permission Profile Gating (verifying tool tiers).
- Conflict Detection & Controlled Composition.
- Bounded Context Injection (avoids overloading context windows).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.security.permissions import PermissionManager, PermissionProfile
from core.skills.models import (
    SkillCategory,
    SkillManifest,
    SkillTrustLevel,
)
from core.skills.registry import SkillRegistry

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry


# Category affinities by specialized agent name
AGENT_CATEGORY_AFFINITY: dict[str, list[SkillCategory]] = {
    "developer_agent": [SkillCategory.ENGINEERING, SkillCategory.QUALITY, SkillCategory.UI_DESIGN],
    "developer": [SkillCategory.ENGINEERING, SkillCategory.QUALITY, SkillCategory.UI_DESIGN],
    "code_intelligence": [SkillCategory.ENGINEERING, SkillCategory.QUALITY],
    "system_diagnosis": [SkillCategory.SECURITY, SkillCategory.DEVOPS],
    "researcher": [SkillCategory.GENERAL, SkillCategory.SECURITY],
    "research_agent": [SkillCategory.GENERAL, SkillCategory.SECURITY],
    "verification_agent": [SkillCategory.QUALITY, SkillCategory.SECURITY],
    "verifier": [SkillCategory.QUALITY, SkillCategory.SECURITY],
    "execution_agent": [SkillCategory.WORKFLOW, SkillCategory.DEVOPS],
    "execution": [SkillCategory.WORKFLOW, SkillCategory.DEVOPS],
}


@dataclass
class SelectedSkillSet:
    """The resolved collection of compatible skills to inject into agent context."""
    task_id: str
    goal: str
    agent_name: str
    skills: list[SkillManifest] = field(default_factory=list)
    combined_instructions: str = ""
    selection_rationale: list[str] = field(default_factory=list)

    @property
    def skill_ids(self) -> list[str]:
        return [s.id for s in self.skills]


class SkillSelector:
    """Selects the optimal subset of skills tailored to the task and agent capabilities."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        permission_mgr: PermissionManager | None = None,
    ) -> None:
        self.registry = registry or SkillRegistry.instance()
        self.permission_mgr = permission_mgr or PermissionManager.instance()

    def select_skills_for_task(
        self,
        task_id: str,
        goal: str,
        agent_name: str = "developer_agent",
        tool_registry: ToolRegistry | None = None,
        max_skills: int = 3,
        explicit_skill_ids: list[str] | None = None,
    ) -> SelectedSkillSet:
        """Intelligently score, filter, and compose compatible skills for a task."""
        candidates = self.registry.list_skills(enabled_only=True)
        active_profile = self.permission_mgr.get_profile()

        selected: list[SkillManifest] = []
        rationales: list[str] = []

        # 1. Handle Explicit Requests
        if explicit_skill_ids:
            for sid in explicit_skill_ids:
                skill = self.registry.get(sid)
                if skill and skill.enabled and skill.trust_level != SkillTrustLevel.BLOCKED:
                    selected.append(skill)
                    rationales.append(f"Explicitly requested skill '{skill.id}'.")
            if len(selected) >= max_skills:
                return self._assemble_skill_set(task_id, goal, agent_name, selected[:max_skills], rationales)

        # 2. Score Remaining Candidates
        scores: list[tuple[float, SkillManifest, str]] = []
        goal_tokens = set(re.findall(r"\w+", goal.lower()))
        affinities = AGENT_CATEGORY_AFFINITY.get(agent_name.lower(), [SkillCategory.GENERAL])

        for skill in candidates:
            if skill in selected:
                continue

            # Must not be blocked
            if skill.trust_level == SkillTrustLevel.BLOCKED:
                continue

            score = 0.0
            reasons = []

            # A. Tag & Token Overlap
            skill_tokens = set(re.findall(r"\w+", f"{skill.name} {skill.description} {' '.join(skill.tags)}".lower()))
            overlap = goal_tokens.intersection(skill_tokens)
            if overlap:
                score += len(overlap) * 2.5
                reasons.append(f"Matches task keywords: {', '.join(list(overlap)[:3])}")

            # B. Agent Role Affinity
            if skill.category in affinities:
                score += 3.0
                reasons.append(f"Aligned with agent category '{skill.category.value}'")

            # C. Trust Bonus
            if skill.trust_level == SkillTrustLevel.VERIFIED:
                score += 2.0
            elif skill.trust_level == SkillTrustLevel.TRUSTED:
                score += 1.0

            # C2. Native Installed Library Priority
            if skill.is_installed:
                score += 5.0
                reasons.append("Natively installed in OPTIMUS library")

            # D. Tool Compatibility Gate
            if tool_registry and skill.required_tools:
                missing = [t for t in skill.required_tools if not tool_registry.is_registered(t)]
                if missing:
                    score -= 10.0  # Heavy penalty if required tools missing

            # E. Profile-Based Filtering
            if active_profile == PermissionProfile.LOW and skill.category in (SkillCategory.DEVOPS, SkillCategory.WORKFLOW):
                score -= 4.0  # Discourage destructive/execution workflows on LOW profile

            if score > 0.0:
                scores.append((score, skill, "; ".join(reasons)))

        # Sort descending by score
        scores.sort(key=lambda x: x[0], reverse=True)

        # 3. Conflict Detection and Composition
        for score_val, candidate, reason in scores:
            if len(selected) >= max_skills:
                break

            # Conflict check against already selected
            has_conflict = False
            for s in selected:
                if s.id in candidate.incompatible_with or candidate.id in s.incompatible_with:
                    has_conflict = True
                    break

            if not has_conflict:
                selected.append(candidate)
                rationales.append(f"Selected '{candidate.id}' (score={score_val:.1f}): {reason}")

        return self._assemble_skill_set(task_id, goal, agent_name, selected, rationales)

    def _assemble_skill_set(
        self,
        task_id: str,
        goal: str,
        agent_name: str,
        skills: list[SkillManifest],
        rationales: list[str],
    ) -> SelectedSkillSet:
        """Compose skill markdown instructions into a unified injection context."""
        instruction_blocks: list[str] = []
        for s in skills:
            header = f"\n<!-- SKILL: {s.name} (id: {s.id}, category: {s.category.value}) -->\n"
            instruction_blocks.append(header + s.instructions.strip())

        combined = "\n\n".join(instruction_blocks)
        return SelectedSkillSet(
            task_id=task_id,
            goal=goal,
            agent_name=agent_name,
            skills=skills,
            combined_instructions=combined,
            selection_rationale=rationales,
        )
