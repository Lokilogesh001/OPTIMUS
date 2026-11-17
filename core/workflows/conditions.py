"""core/workflows/conditions.py — Safe & Deterministic Condition Evaluator for OPTIMUS Workflows.

CRITICAL SECURITY CONSTRAINT:
Arbitrary Python or shell code execution is strictly prohibited. All conditions
are parsed as typed structured descriptors against context variables, step outputs,
artifact existence, or approval decisions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.workflows.types import (
    ApprovalDecision,
    ConditionType,
    StepCondition,
    StepExecutionRecord,
)


class SafeConditionEvaluator:
    """Evaluates workflow conditional gates deterministically without eval() or shell execution."""

    @staticmethod
    def evaluate(
        condition: StepCondition | None,
        context: dict[str, Any],
        step_states: dict[str, StepExecutionRecord],
        approvals: dict[str, ApprovalDecision] | None = None,
        allowed_roots: list[str] | None = None,
    ) -> bool:
        """Evaluate a StepCondition against runtime state, returning True if condition is met."""
        if condition is None:
            return True  # Unconditional step

        c_type = condition.condition_type

        # 1. Step Status Condition
        if c_type == ConditionType.STEP_STATUS:
            target_step = step_states.get(condition.step_id)
            if not target_step:
                return False
            expected_status = str(condition.expected_value or "COMPLETED").upper()
            return target_step.status.value == expected_status

        # 2. Output Equals Condition
        if c_type == ConditionType.OUTPUT_EQUALS:
            val = SafeConditionEvaluator._resolve_field(condition, context, step_states)
            return val == condition.expected_value

        # 3. Output Contains Condition
        if c_type == ConditionType.OUTPUT_CONTAINS:
            val = SafeConditionEvaluator._resolve_field(condition, context, step_states)
            if isinstance(val, (list, tuple, set)):
                return condition.expected_value in val
            if isinstance(val, str):
                return str(condition.expected_value) in val
            if isinstance(val, dict):
                return str(condition.expected_value) in val
            return False

        # 4. Structured Typed Expression
        if c_type == ConditionType.EXPRESSION:
            val = SafeConditionEvaluator._resolve_field(condition, context, step_states)
            exp = condition.expected_value
            op = condition.operator.lower()

            if op == "eq":
                return val == exp
            elif op == "neq":
                return val != exp
            elif op == "gt":
                try:
                    return float(val) > float(exp)
                except (ValueError, TypeError):
                    return False
            elif op == "gte":
                try:
                    return float(val) >= float(exp)
                except (ValueError, TypeError):
                    return False
            elif op == "lt":
                try:
                    return float(val) < float(exp)
                except (ValueError, TypeError):
                    return False
            elif op == "lte":
                try:
                    return float(val) <= float(exp)
                except (ValueError, TypeError):
                    return False
            elif op == "in":
                return val in exp if isinstance(exp, (list, tuple, set, dict)) else False
            elif op == "contains":
                return str(exp) in str(val) if val is not None else False
            elif op == "is_true":
                return bool(val) is True
            elif op == "is_false":
                return bool(val) is False
            return False

        # 5. Artifact Exists Condition
        if c_type == ConditionType.ARTIFACT_EXISTS:
            file_path_str = str(condition.expected_value or condition.field_name or "")
            if not file_path_str:
                return False
            p = Path(file_path_str)
            if not p.is_absolute() and allowed_roots:
                p = (Path(allowed_roots[0]) / p).resolve()
            else:
                p = p.resolve()
            return p.is_file() and p.stat().st_size > 0

        # 6. Approval Resolved Condition
        if c_type == ConditionType.APPROVAL_RESOLVED:
            if not approvals:
                return False
            # Find approval matching step_id
            for app in approvals.values():
                if app.step_id == condition.step_id:
                    expected = str(condition.expected_value or "APPROVED").upper()
                    return app.status.value == expected
            return False

        return False

    @staticmethod
    def _resolve_field(
        condition: StepCondition,
        context: dict[str, Any],
        step_states: dict[str, StepExecutionRecord],
    ) -> Any:
        """Safely extract field value from step outputs or global context."""
        step_id = condition.step_id
        field_name = condition.field_name

        if step_id and step_id in step_states:
            step_record = step_states[step_id]
            if field_name in step_record.outputs:
                return step_record.outputs[field_name]
            if field_name == "summary":
                return step_record.summary
            if field_name == "status":
                return step_record.status.value
            if not field_name:
                return step_record.outputs

        if field_name in context:
            return context[field_name]

        return None

    @staticmethod
    def interpolate_text(
        text: str,
        context: dict[str, Any],
        step_states: dict[str, StepExecutionRecord],
    ) -> str:
        """Substitute `{{variable}}` and `{{step_id.key}}` placeholders safely."""
        if not text or "{{" not in text:
            return text

        def _replace(match: re.Match) -> str:
            raw_key = match.group(1).strip()
            parts = raw_key.split(".")

            if len(parts) == 1:
                # Top-level context lookup
                return str(context.get(parts[0], match.group(0)))

            if len(parts) == 2:
                prefix, attr = parts[0], parts[1]
                if prefix in ("context", "inputs", "params"):
                    return str(context.get(attr, match.group(0)))
                if prefix in step_states:
                    rec = step_states[prefix]
                    if attr in rec.outputs:
                        return str(rec.outputs[attr])
                    if attr == "summary":
                        return str(rec.summary)
                    if attr == "status":
                        return str(rec.status.value)

            return match.group(0)

        return re.sub(r"\{\{([^}]+)\}\}", _replace, text)
