"""core/missions/types.py — Type definitions and data contracts for OPTIMUS Missions.

Defines:
- MissionState: Complete lifecycle states and operational execution states.
- MissionSubtask: Discrete executable unit with dependencies, concurrency, and postconditions.
- MissionPlan: Structured DAG with resource estimates, validation, and risk analysis.
- MissionRecord: Full mission execution record with timeline, deliverables, and evidence.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MissionState(str, Enum):
    """Lifecycle and operational states of an autonomous mission."""
    UNDERSTAND = "UNDERSTAND"
    PLAN = "PLAN"
    VALIDATE = "VALIDATE"
    APPROVAL = "APPROVAL"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    COMPLETE = "COMPLETE"

    # Operational intermediate & terminal states
    PAUSED = "PAUSED"
    WAITING_FOR_AGENT = "WAITING_FOR_AGENT"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RETRYING = "RETRYING"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RECOVERING = "RECOVERING"


class SubtaskStatus(str, Enum):
    """Execution status of an individual mission subtask."""
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    RETRYING = "RETRYING"


@dataclass
class MissionSubtask:
    """A granular unit of work within an autonomous mission."""
    subtask_id: str
    title: str
    description: str = ""
    assigned_agent: str = "general"
    required_tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    parallelizable: bool = False
    requires_approval: bool = False
    approval_role: str = "user"
    expected_deliverables: list[str] = field(default_factory=list)
    postconditions: list[dict[str, Any]] = field(default_factory=list)
    status: SubtaskStatus = SubtaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    result_output: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "title": self.title,
            "description": self.description,
            "assigned_agent": self.assigned_agent,
            "required_tools": list(self.required_tools),
            "depends_on": list(self.depends_on),
            "parallelizable": self.parallelizable,
            "requires_approval": self.requires_approval,
            "approval_role": self.approval_role,
            "expected_deliverables": list(self.expected_deliverables),
            "postconditions": list(self.postconditions),
            "status": self.status.value if isinstance(self.status, SubtaskStatus) else str(self.status),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "result_output": self.result_output,
            "evidence": list(self.evidence),
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionSubtask:
        raw_status = data.get("status", SubtaskStatus.PENDING.value)
        try:
            status = SubtaskStatus(raw_status)
        except ValueError:
            status = SubtaskStatus.PENDING

        return cls(
            subtask_id=data.get("subtask_id", f"subtask_{uuid.uuid4().hex[:8]}"),
            title=data.get("title", "Untitled Subtask"),
            description=data.get("description", ""),
            assigned_agent=data.get("assigned_agent", "general"),
            required_tools=list(data.get("required_tools", [])),
            depends_on=list(data.get("depends_on", [])),
            parallelizable=bool(data.get("parallelizable", False)),
            requires_approval=bool(data.get("requires_approval", False)),
            approval_role=data.get("approval_role", "user"),
            expected_deliverables=list(data.get("expected_deliverables", [])),
            postconditions=list(data.get("postconditions", [])),
            status=status,
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
            result_output=str(data.get("result_output", "")),
            evidence=list(data.get("evidence", [])),
            error=str(data.get("error", "")),
            started_at=float(data.get("started_at", 0.0)),
            completed_at=float(data.get("completed_at", 0.0)),
        )


@dataclass
class MissionPlan:
    """Structured dependency graph for an autonomous mission."""
    plan_id: str = field(default_factory=lambda: f"mplan_{uuid.uuid4().hex[:8]}")
    objective: str = ""
    subtasks: list[MissionSubtask] = field(default_factory=list)
    version: int = 1
    validated: bool = False
    validation_errors: list[str] = field(default_factory=list)
    estimated_resources: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    risk_level: str = "LOW"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "objective": self.objective,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "version": self.version,
            "validated": self.validated,
            "validation_errors": list(self.validation_errors),
            "estimated_resources": dict(self.estimated_resources),
            "requires_approval": self.requires_approval,
            "risk_level": self.risk_level,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionPlan:
        subtasks_raw = data.get("subtasks", [])
        subtasks = [MissionSubtask.from_dict(s) if isinstance(s, dict) else s for s in subtasks_raw]
        return cls(
            plan_id=data.get("plan_id", f"mplan_{uuid.uuid4().hex[:8]}"),
            objective=data.get("objective", ""),
            subtasks=subtasks,
            version=int(data.get("version", 1)),
            validated=bool(data.get("validated", False)),
            validation_errors=list(data.get("validation_errors", [])),
            estimated_resources=dict(data.get("estimated_resources", {})),
            requires_approval=bool(data.get("requires_approval", False)),
            risk_level=str(data.get("risk_level", "LOW")),
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass
class MissionRecord:
    """Persistent execution record and audit trail of an autonomous mission."""
    mission_id: str = field(default_factory=lambda: f"mission_{uuid.uuid4().hex[:10]}")
    objective: str = ""
    state: MissionState = MissionState.UNDERSTAND
    plan: MissionPlan = field(default_factory=MissionPlan)
    deliverables: list[str] = field(default_factory=list)
    evidence_ledger: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    failure_reason: str | None = None
    recovery_attempts: int = 0
    version: int = 1
    context: dict[str, Any] = field(default_factory=dict)

    def add_timeline_event(self, event_type: str, details: str, data: dict[str, Any] | None = None) -> None:
        """Append an entry to the mission execution timeline."""
        entry = {
            "timestamp": time.time(),
            "event_type": event_type,
            "details": details,
            "data": data or {},
        }
        self.timeline.append(entry)
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "objective": self.objective,
            "state": self.state.value if isinstance(self.state, MissionState) else str(self.state),
            "plan": self.plan.to_dict(),
            "deliverables": list(self.deliverables),
            "evidence_ledger": list(self.evidence_ledger),
            "timeline": list(self.timeline),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "failure_reason": self.failure_reason,
            "recovery_attempts": self.recovery_attempts,
            "version": self.version,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionRecord:
        raw_state = data.get("state", MissionState.UNDERSTAND.value)
        try:
            state = MissionState(raw_state)
        except ValueError:
            state = MissionState.UNDERSTAND

        plan_raw = data.get("plan", {})
        plan = MissionPlan.from_dict(plan_raw) if isinstance(plan_raw, dict) else plan_raw

        return cls(
            mission_id=data.get("mission_id", f"mission_{uuid.uuid4().hex[:10]}"),
            objective=data.get("objective", ""),
            state=state,
            plan=plan,
            deliverables=list(data.get("deliverables", [])),
            evidence_ledger=list(data.get("evidence_ledger", [])),
            timeline=list(data.get("timeline", [])),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            completed_at=data.get("completed_at"),
            failure_reason=data.get("failure_reason"),
            recovery_attempts=int(data.get("recovery_attempts", 0)),
            version=int(data.get("version", 1)),
            context=dict(data.get("context", {})),
        )
