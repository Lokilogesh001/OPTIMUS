"""core/planning/goal_analyzer.py — Intelligent Goal Interpretation & Decomposition for OPTIMUS.

Translates complex natural-language directives into structured decompositions:
- Identifies required agent capabilities and tool permissions.
- Deconstructs multi-stage instructions into ordered subtasks.
- Discovers dependencies and parallelizable subtasks.
- Detects destructive actions and enforces mandatory human approval gates.
- Evaluates complexity and resource duration estimates.
"""

from __future__ import annotations

import re
from typing import Any

from core.planning.types import GoalCategory, GoalDecomposition, PlanRiskLevel, PlanStep

_DESTRUCTIVE_PATTERNS = [
    re.compile(r"\b(delete|remove|rm|drop|erase|format|overwrite|truncate|kill|destroy|purge)\b", re.IGNORECASE),
    re.compile(r"\b(drop\s+table|rm\s+-rf|del\s+/f)\b", re.IGNORECASE),
]

_CATEGORY_KEYWORDS = {
    GoalCategory.CODE_MODIFICATION: [
        "code", "refactor", "bug", "git", "test", "lint", "function", "python",
        "script", "class", "syntax", "compile", "patch", "module",
    ],
    GoalCategory.SYSTEM_OPS: [
        "cpu", "ram", "memory", "process", "disk", "storage", "hardware",
        "diagnostic", "service", "kill", "reboot", "port", "network", "system",
    ],
    GoalCategory.DATA_PIPELINE: [
        "csv", "json", "table", "metrics", "calculate", "clean", "dataframe",
        "dataset", "aggregate", "statistics", "report", "transform",
    ],
    GoalCategory.RESEARCH: [
        "search", "find", "query", "research", "documentation", "fetch",
        "lookup", "explore", "investigate",
    ],
}


class GoalAnalyzer:
    """Decomposes and analyzes complex user goals deterministically."""

    def analyze(self, goal: str) -> GoalDecomposition:
        """Analyze a goal string and produce a structured GoalDecomposition."""
        clean_goal = goal.strip()
        lower_goal = clean_goal.lower()

        # 1. Determine Category
        category = self._infer_category(lower_goal)

        # 2. Check Destructive Risk
        is_destructive = any(p.search(clean_goal) for p in _DESTRUCTIVE_PATTERNS)
        risk_level = PlanRiskLevel.LOW
        if is_destructive:
            risk_level = PlanRiskLevel.HIGH
            if any(k in lower_goal for k in ("all", "root", "system", "database", "disk", "format")):
                risk_level = PlanRiskLevel.CRITICAL
        elif any(k in lower_goal for k in ("modify", "edit", "update", "patch", "install", "stop")):
            risk_level = PlanRiskLevel.MEDIUM

        # 3. Required Capabilities
        caps = self._extract_capabilities(category, lower_goal)

        # 4. Complexity & Duration Estimation
        steps_preview = self.decompose_into_steps(clean_goal, category)
        step_count = len(steps_preview)
        unique_agents = len(set(s.assigned_agent for s in steps_preview))

        complexity = 1.0 + (step_count - 1) * 1.5 + (unique_agents - 1) * 2.0
        if is_destructive:
            complexity += 2.0
        complexity = min(10.0, max(1.0, complexity))

        estimated_duration = 4.0 + step_count * 6.0
        if complexity > 6.0:
            estimated_duration += 10.0

        return GoalDecomposition(
            goal=clean_goal,
            category=category,
            complexity_score=complexity,
            risk_level=risk_level,
            required_capabilities=caps,
            estimated_duration_seconds=estimated_duration,
            is_destructive=is_destructive,
        )

    def _infer_category(self, lower_goal: str) -> GoalCategory:
        scores: dict[GoalCategory, int] = {cat: 0 for cat in _CATEGORY_KEYWORDS}
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in lower_goal:
                    scores[cat] += 1

        best_cat = max(scores, key=lambda k: scores[k])
        if scores[best_cat] > 0:
            return best_cat
        return GoalCategory.GENERAL

    def _extract_capabilities(self, category: GoalCategory, lower_goal: str) -> list[str]:
        caps = []
        if category == GoalCategory.CODE_MODIFICATION:
            caps.extend(["code_intelligence", "filesystem_read", "filesystem_write"])
        elif category == GoalCategory.SYSTEM_OPS:
            caps.extend(["system_diagnosis", "process_inspection"])
        elif category == GoalCategory.DATA_PIPELINE:
            caps.extend(["data_intelligence", "data_validation"])
        elif category == GoalCategory.RESEARCH:
            caps.extend(["research", "codebase_search"])
        else:
            caps.extend(["general_execution"])

        if any(p.search(lower_goal) for p in _DESTRUCTIVE_PATTERNS):
            caps.append("destructive_operations")

        return list(dict.fromkeys(caps))

    def decompose_into_steps(self, goal: str, category: GoalCategory) -> list[PlanStep]:
        """Decompose a goal into concrete PlanStep objects with dependency resolution."""
        # Split composite goals on sequential conjunctions
        split_pattern = re.compile(r"\s+(?:and\s+then|then|after\s+that|followed\s+by)\s+", re.IGNORECASE)
        segments = split_pattern.split(goal.strip())

        if len(segments) <= 1:
            # Check for simple "and" split if two independent clauses exist
            and_clauses = [c.strip() for c in goal.split(" and ") if len(c.strip().split()) >= 3]
            if len(and_clauses) > 1:
                segments = and_clauses
            else:
                segments = [goal.strip()]

        steps: list[PlanStep] = []
        for idx, segment in enumerate(segments):
            step_id = f"step_{idx + 1}"
            seg_lower = segment.lower()
            seg_cat = self._infer_category(seg_lower) if len(segments) > 1 else category

            assigned_agent = self._select_agent(seg_cat)
            required_tools = self._select_tools(seg_cat, seg_lower)
            is_destr = any(p.search(segment) for p in _DESTRUCTIVE_PATTERNS)

            # Dependency mapping: if multi-stage, subsequent steps depend on preceding step
            depends_on = [f"step_{idx}"] if idx > 0 and not ("concurrently" in seg_lower or "parallel" in seg_lower) else []
            is_parallel = len(depends_on) == 0 and len(segments) > 1

            postconditions = self._infer_postconditions(segment, seg_cat)
            deliverables = self._infer_deliverables(segment)

            steps.append(
                PlanStep(
                    step_id=step_id,
                    title=f"Execute: {segment[:50]}",
                    description=segment,
                    assigned_agent=assigned_agent,
                    required_tools=required_tools,
                    depends_on=depends_on,
                    is_parallelizable=is_parallel,
                    is_destructive=is_destr,
                    requires_approval=is_destr,
                    approval_role="admin" if is_destr else "operator",
                    expected_deliverables=deliverables,
                    postconditions=postconditions,
                    retry_count=2,
                    timeout_seconds=60.0,
                )
            )

        return steps

    def _select_agent(self, category: GoalCategory) -> str:
        if category == GoalCategory.CODE_MODIFICATION:
            return "code_intelligence"
        elif category == GoalCategory.SYSTEM_OPS:
            return "system_diagnosis"
        elif category == GoalCategory.DATA_PIPELINE:
            return "data_intelligence"
        elif category == GoalCategory.RESEARCH:
            return "research"
        return "execution"

    def _select_tools(self, category: GoalCategory, seg_lower: str) -> list[str]:
        tools = []
        if category == GoalCategory.CODE_MODIFICATION:
            tools.extend(["read_file", "edit_file", "list_directory"])
        elif category == GoalCategory.SYSTEM_OPS:
            tools.extend(["system_telemetry", "process_list"])
        elif category == GoalCategory.DATA_PIPELINE:
            tools.extend(["read_file", "write_file"])
        elif category == GoalCategory.RESEARCH:
            tools.extend(["search_code", "read_file"])
        else:
            tools.extend(["read_file", "system_telemetry"])

        if any(p.search(seg_lower) for p in _DESTRUCTIVE_PATTERNS):
            tools.append("delete_file")

        return list(dict.fromkeys(tools))

    def _infer_postconditions(self, segment: str, category: GoalCategory) -> list[dict[str, Any]]:
        pcs = []
        seg_lower = segment.lower()
        if "file" in seg_lower or "script" in seg_lower or "report" in seg_lower:
            pcs.append({
                "type": "FILESYSTEM",
                "spec": {"non_empty": True},
                "description": "Output deliverable file must exist and be non-empty.",
            })
        if category == GoalCategory.DATA_PIPELINE:
            pcs.append({
                "type": "DATA",
                "spec": {"valid_json_or_csv": True},
                "description": "Structured data output must adhere to schema.",
            })
        return pcs

    def _infer_deliverables(self, segment: str) -> list[str]:
        delivs = []
        words = segment.split()
        for w in words:
            clean_w = w.strip(".,'\"()")
            if any(clean_w.endswith(ext) for ext in (".py", ".json", ".csv", ".txt", ".md", ".log")):
                delivs.append(clean_w)
        return delivs

