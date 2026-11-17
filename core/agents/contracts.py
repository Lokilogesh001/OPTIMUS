"""core/agents/contracts.py — Typed Contracts & Failure Policies for OPTIMUS Specialized Agents.

Defines:
- AgentInputContract: Strongly typed input parameters, context boundaries, and tool scopes.
- AgentOutputContract: Strongly typed outputs, deliverables, side effects, and evidence.
- AgentFailurePolicy: Explicit failure handling strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentFailurePolicy(str, Enum):
    """Failure handling strategies for specialized agent execution."""
    FAIL_FAST = "FAIL_FAST"          # Immediately stop workflow and fail subtask
    FALLBACK_LOGICAL = "FALLBACK"    # Fall back to logical explanation without throwing error
    RETRY = "RETRY"                  # Allow controlled turn retries within limits


@dataclass
class AgentInputContract:
    """Strongly typed input specification delivered to a specialized agent."""
    task_id: str
    subtask_id: str
    goal: str
    instructions: str
    context_data: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    workspace_root: str = "."
    timeout_seconds: float = 60.0
    failure_policy: AgentFailurePolicy = AgentFailurePolicy.FAIL_FAST

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "goal": self.goal,
            "instructions": self.instructions,
            "context_data": self.context_data,
            "allowed_tools": self.allowed_tools,
            "workspace_root": self.workspace_root,
            "timeout_seconds": self.timeout_seconds,
            "failure_policy": self.failure_policy.value,
        }


@dataclass
class AgentOutputContract:
    """Strongly typed result contract returned by a specialized agent."""
    subtask_id: str
    agent_name: str
    status: str = "success"  # "success", "failed", "error"
    summary: str = ""
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == "success" and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "agent_name": self.agent_name,
            "status": self.status,
            "summary": self.summary,
            "deliverables": self.deliverables,
            "side_effects": self.side_effects,
            "evidence": self.evidence,
            "execution_time_ms": self.execution_time_ms,
            "error": self.error,
        }
