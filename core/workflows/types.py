"""core/workflows/types.py — Data Contracts, Enums & Models for OPTIMUS Autonomous Workflow Engine.

Defines:
- WorkflowStatus, StepStatus, ApprovalStatus: Lifecycle state machines.
- StepCondition, ConditionType: Structured, safe condition specifications.
- RetryPolicy: Exponential backoff configuration.
- ApprovalDecision: Human-in-the-loop audit records.
- WorkflowStepDefinition, WorkflowDefinition: Typed workflow schemas.
- StepExecutionRecord, WorkflowExecutionRecord: Persistent runtime execution models.
- Specialized workflow exception hierarchy.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    """Lifecycle status of a workflow execution instance."""
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, Enum):
    """Lifecycle status of an individual workflow step."""
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    AMBIGUOUS_EXECUTION = "AMBIGUOUS_EXECUTION"


class ApprovalStatus(str, Enum):
    """Status of a human authorization request."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ConditionType(str, Enum):
    """Supported safe, deterministic condition evaluation modes."""
    STEP_STATUS = "STEP_STATUS"
    OUTPUT_EQUALS = "OUTPUT_EQUALS"
    OUTPUT_CONTAINS = "OUTPUT_CONTAINS"
    EXPRESSION = "EXPRESSION"
    ARTIFACT_EXISTS = "ARTIFACT_EXISTS"
    APPROVAL_RESOLVED = "APPROVAL_RESOLVED"


@dataclass
class RetryPolicy:
    """Configurable exponential retry parameters for steps."""
    max_retries: int = 3
    backoff_factor: float = 2.0
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "backoff_factor": self.backoff_factor,
            "initial_delay_seconds": self.initial_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RetryPolicy:
        if not data:
            return cls()
        return cls(
            max_retries=int(data.get("max_retries", 3)),
            backoff_factor=float(data.get("backoff_factor", 2.0)),
            initial_delay_seconds=float(data.get("initial_delay_seconds", 1.0)),
            max_delay_seconds=float(data.get("max_delay_seconds", 30.0)),
        )


@dataclass
class StepCondition:
    """Deterministic, structured conditional rule (safe from arbitrary code execution)."""
    condition_type: ConditionType = ConditionType.STEP_STATUS
    step_id: str = ""
    field_name: str = ""
    operator: str = "eq"  # "eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "is_true", "is_false"
    expected_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_type": self.condition_type.value,
            "step_id": self.step_id,
            "field_name": self.field_name,
            "operator": self.operator,
            "expected_value": self.expected_value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StepCondition | None:
        if not data:
            return None
        return cls(
            condition_type=ConditionType(data.get("condition_type", "STEP_STATUS")),
            step_id=str(data.get("step_id", "")),
            field_name=str(data.get("field_name", "")),
            operator=str(data.get("operator", "eq")),
            expected_value=data.get("expected_value"),
        )


@dataclass
class ApprovalDecision:
    """Audit record capturing a human authorization request and decision."""
    approval_id: str
    workflow_id: str
    execution_id: str
    step_id: str
    reason: str
    required_role: str = "admin"
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 3600.0)
    resolved_at: float | None = None
    approver: str | None = None
    decision_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "workflow_id": self.workflow_id,
            "execution_id": self.execution_id,
            "step_id": self.step_id,
            "reason": self.reason,
            "required_role": self.required_role,
            "status": self.status.value,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "resolved_at": self.resolved_at,
            "approver": self.approver,
            "decision_reason": self.decision_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalDecision:
        d = dict(data)
        d["status"] = ApprovalStatus(d.get("status", "PENDING"))
        return cls(**d)


@dataclass
class WorkflowStepDefinition:
    """Immutable specification for a single step within a workflow definition."""
    step_id: str
    name: str
    description: str = ""
    assigned_agent: str = "general"
    required_capabilities: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    condition: StepCondition | None = None
    requires_approval: bool = False
    approval_role: str = "admin"
    approval_timeout_seconds: float = 3600.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: float = 120.0
    expected_deliverables: list[str] = field(default_factory=list)
    is_destructive: bool = False
    nested_workflow_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "assigned_agent": self.assigned_agent,
            "required_capabilities": self.required_capabilities,
            "allowed_tools": self.allowed_tools,
            "inputs": self.inputs,
            "depends_on": self.depends_on,
            "condition": self.condition.to_dict() if self.condition else None,
            "requires_approval": self.requires_approval,
            "approval_role": self.approval_role,
            "approval_timeout_seconds": self.approval_timeout_seconds,
            "retry_policy": self.retry_policy.to_dict(),
            "timeout_seconds": self.timeout_seconds,
            "expected_deliverables": self.expected_deliverables,
            "is_destructive": self.is_destructive,
            "nested_workflow_id": self.nested_workflow_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStepDefinition:
        d = dict(data)
        d["condition"] = StepCondition.from_dict(d.get("condition"))
        d["retry_policy"] = RetryPolicy.from_dict(d.get("retry_policy"))
        return cls(**d)


@dataclass
class WorkflowDefinition:
    """Declarative DAG workflow template specification."""
    workflow_id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    parameters: dict[str, Any] = field(default_factory=dict)
    steps: list[WorkflowStepDefinition] = field(default_factory=list)
    max_depth: int = 3
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "parameters": self.parameters,
            "steps": [s.to_dict() for s in self.steps],
            "max_depth": self.max_depth,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowDefinition:
        d = dict(data)
        d["steps"] = [WorkflowStepDefinition.from_dict(s) for s in d.get("steps", [])]
        return cls(**d)


@dataclass
class StepExecutionRecord:
    """Runtime execution state and deliverable accounting for a workflow step."""
    step_id: str
    status: StepStatus = StepStatus.PENDING
    started_at: float | None = None
    finished_at: float | None = None
    duration_seconds: float = 0.0
    attempts: int = 0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    verification_verdict: str = ""
    error: str | None = None
    approval_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "attempts": self.attempts,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "summary": self.summary,
            "side_effects": self.side_effects,
            "verification_verdict": self.verification_verdict,
            "error": self.error,
            "approval_id": self.approval_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepExecutionRecord:
        d = dict(data)
        d["status"] = StepStatus(d.get("status", "PENDING"))
        return cls(**d)


@dataclass
class WorkflowExecutionRecord:
    """Persistent execution instance capturing full workflow lifecycle."""
    execution_id: str
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    parameters: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    step_states: dict[str, StepExecutionRecord] = field(default_factory=dict)
    current_position: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    checkpoint_id: str | None = None
    recovery_metadata: dict[str, Any] = field(default_factory=dict)
    depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "parameters": self.parameters,
            "context": self.context,
            "step_states": {sid: rec.to_dict() for sid, rec in self.step_states.items()},
            "current_position": self.current_position,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "checkpoint_id": self.checkpoint_id,
            "recovery_metadata": self.recovery_metadata,
            "depth": self.depth,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowExecutionRecord:
        d = dict(data)
        d["status"] = WorkflowStatus(d.get("status", "PENDING"))
        raw_steps = d.get("step_states", {})
        d["step_states"] = {sid: StepExecutionRecord.from_dict(rec) for sid, rec in raw_steps.items()}
        return cls(**d)


def new_execution_id(prefix: str = "wf_exec") -> str:
    """Generate a unique timestamped execution ID."""
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


# ── Specialized Workflow Exceptions ──────────────────────────────────────────

class WorkflowError(Exception):
    """Base exception for workflow subsystem."""


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow definition is structurally invalid."""


class CircularDependencyError(WorkflowValidationError):
    """Raised when step dependencies form an illegal cycle."""


class MissingDependencyError(WorkflowValidationError):
    """Raised when a step depends on a non-existent step."""


class ApprovalRequiredError(WorkflowError):
    """Raised when execution halts waiting for human authorization."""


class AmbiguousExecutionError(WorkflowError):
    """Raised when an interrupted step may have produced side effects requiring manual review."""
