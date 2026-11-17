"""core/workflows/events.py — Typed Workflow Lifecycle Events for OPTIMUS.

Dispatched across the central EventBus for UI observability, desktop bridge notifications,
and audit trail persistence without exposing credentials or secret tokens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.orchestrator.events import OrchestratorEvent


@dataclass
class WorkflowCreatedEvent(OrchestratorEvent):
    workflow_id: str = ""
    name: str = ""
    step_count: int = 0


@dataclass
class WorkflowStartedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowPausedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    reason: str = ""


@dataclass
class WorkflowResumedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""


@dataclass
class WorkflowCompletedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    duration_seconds: float = 0.0


@dataclass
class WorkflowFailedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    error: str = ""
    failed_steps: list[str] = field(default_factory=list)


@dataclass
class WorkflowCancelledEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    reason: str = ""


@dataclass
class WorkflowStepStartedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    step_id: str = ""
    assigned_agent: str = ""
    attempt: int = 1


@dataclass
class WorkflowStepCompletedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    step_id: str = ""
    duration_seconds: float = 0.0
    summary: str = ""


@dataclass
class WorkflowStepFailedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    step_id: str = ""
    error: str = ""
    attempt: int = 1
    will_retry: bool = False


@dataclass
class WorkflowStepSkippedEvent(OrchestratorEvent):
    execution_id: str = ""
    workflow_id: str = ""
    step_id: str = ""
    reason: str = ""


@dataclass
class ApprovalRequestedEvent(OrchestratorEvent):
    approval_id: str = ""
    execution_id: str = ""
    workflow_id: str = ""
    step_id: str = ""
    reason: str = ""
    required_role: str = "admin"
    expires_at: float = field(default_factory=time.time)


@dataclass
class ApprovalResolvedEvent(OrchestratorEvent):
    approval_id: str = ""
    execution_id: str = ""
    step_id: str = ""
    status: str = "APPROVED"  # APPROVED, REJECTED, EXPIRED
    approver: str = ""
    reason: str = ""


@dataclass
class WorkflowRecoveryStartedEvent(OrchestratorEvent):
    recovered_count: int = 0
    executions: list[str] = field(default_factory=list)
