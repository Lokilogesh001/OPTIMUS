"""core/proactive/types.py — Data contracts and models for Proactive Personal Intelligence.

Implements data models for:
- Recommendations (7 canonical categories, priority, evidence, governance)
- Intelligent Schedules (one-time, recurring, cron/intervals, persistence)
- Condition Monitors (system vitals, service status, files, missions)
- Notifications (priority, delivery states, evidence, actions)
- Action Governance (Level 1: Recommend -> Level 4: Approval Required)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecommendationCategory(str, Enum):
    PENDING_MISSION = "PENDING_MISSION"
    PROJECT_FOLLOWUP = "PROJECT_FOLLOWUP"
    SCHEDULED_TASK = "SCHEDULED_TASK"
    CONDITION_ALERT = "CONDITION_ALERT"
    RECOVERY_SUGGESTION = "RECOVERY_SUGGESTION"
    SYSTEM_HEALTH = "SYSTEM_HEALTH"
    NEXT_ACTION = "NEXT_ACTION"


class RecommendationPriority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RecommendationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SNOOZED = "SNOOZED"
    DISMISSED = "DISMISSED"
    EXPIRED = "EXPIRED"


class ActionGovernanceLevel(str, Enum):
    LEVEL_1_RECOMMEND = "LEVEL_1_RECOMMEND"           # Suggestion only; zero execution
    LEVEL_2_PREPARE = "LEVEL_2_PREPARE"               # Gather telemetry, stage non-destructive plan
    LEVEL_3_EXECUTE = "LEVEL_3_EXECUTE"               # Perform authorized low-risk action
    LEVEL_4_APPROVAL_REQUIRED = "LEVEL_4_APPROVAL_REQUIRED"  # Require explicit human approval


class ScheduleType(str, Enum):
    ONE_TIME = "ONE_TIME"
    RECURRING = "RECURRING"


class ScheduleFrequency(str, Enum):
    ONCE = "ONCE"
    INTERVAL = "INTERVAL"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    CRON = "CRON"


class ScheduleStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class MonitorStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    TRIGGERED = "TRIGGERED"
    DISABLED = "DISABLED"


@dataclass
class Recommendation:
    """A proactive next-action or advisory recommendation."""
    recommendation_id: str = field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:10]}")
    category: RecommendationCategory = RecommendationCategory.NEXT_ACTION
    title: str = ""
    description: str = ""
    reason: str = ""
    source_evidence: dict[str, Any] = field(default_factory=dict)
    priority: RecommendationPriority = RecommendationPriority.MEDIUM
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    suggested_action: dict[str, Any] = field(default_factory=dict)
    governance_level: ActionGovernanceLevel = ActionGovernanceLevel.LEVEL_1_RECOMMEND
    required_permission: str = "READ_ONLY"
    status: RecommendationStatus = RecommendationStatus.ACTIVE
    snoozed_until: float | None = None

    def is_expired(self, current_time: float | None = None) -> bool:
        now = current_time or time.time()
        if self.expires_at and now >= self.expires_at:
            return True
        return False

    def is_snoozed(self, current_time: float | None = None) -> bool:
        now = current_time or time.time()
        if self.status == RecommendationStatus.SNOOZED and self.snoozed_until and now < self.snoozed_until:
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "category": self.category.value,
            "title": self.title,
            "description": self.description,
            "reason": self.reason,
            "source_evidence": self.source_evidence,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "suggested_action": self.suggested_action,
            "governance_level": self.governance_level.value,
            "required_permission": self.required_permission,
            "status": self.status.value,
            "snoozed_until": self.snoozed_until,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recommendation:
        return cls(
            recommendation_id=data.get("recommendation_id", f"rec_{uuid.uuid4().hex[:10]}"),
            category=RecommendationCategory(data.get("category", RecommendationCategory.NEXT_ACTION.value)),
            title=data.get("title", ""),
            description=data.get("description", ""),
            reason=data.get("reason", ""),
            source_evidence=dict(data.get("source_evidence", {})),
            priority=RecommendationPriority(data.get("priority", RecommendationPriority.MEDIUM.value)),
            created_at=float(data.get("created_at", time.time())),
            expires_at=data.get("expires_at"),
            suggested_action=dict(data.get("suggested_action", {})),
            governance_level=ActionGovernanceLevel(data.get("governance_level", ActionGovernanceLevel.LEVEL_1_RECOMMEND.value)),
            required_permission=data.get("required_permission", "READ_ONLY"),
            status=RecommendationStatus(data.get("status", RecommendationStatus.ACTIVE.value)),
            snoozed_until=data.get("snoozed_until"),
        )


@dataclass
class ScheduleRecord:
    """A scheduled task definition with persistent state."""
    schedule_id: str = field(default_factory=lambda: f"sch_{uuid.uuid4().hex[:10]}")
    name: str = ""
    description: str = ""
    schedule_type: ScheduleType = ScheduleType.ONE_TIME
    frequency: ScheduleFrequency = ScheduleFrequency.ONCE
    interval_seconds: float = 0.0
    cron_expr: str = ""
    run_at: float = field(default_factory=time.time)
    next_run_at: float | None = None
    last_run_at: float | None = None
    action_type: str = "mission"                       # "mission" | "tool" | "workflow"
    target: str = ""                                  # objective or tool name
    payload: dict[str, Any] = field(default_factory=dict)
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    execution_count: int = 0
    max_executions: int | None = None
    last_result: dict[str, Any] = field(default_factory=dict)
    governance_level: ActionGovernanceLevel = ActionGovernanceLevel.LEVEL_3_EXECUTE
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "name": self.name,
            "description": self.description,
            "schedule_type": self.schedule_type.value,
            "frequency": self.frequency.value,
            "interval_seconds": self.interval_seconds,
            "cron_expr": self.cron_expr,
            "run_at": self.run_at,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "action_type": self.action_type,
            "target": self.target,
            "payload": self.payload,
            "status": self.status.value,
            "execution_count": self.execution_count,
            "max_executions": self.max_executions,
            "last_result": self.last_result,
            "governance_level": self.governance_level.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleRecord:
        return cls(
            schedule_id=data.get("schedule_id", f"sch_{uuid.uuid4().hex[:10]}"),
            name=data.get("name", ""),
            description=data.get("description", ""),
            schedule_type=ScheduleType(data.get("schedule_type", ScheduleType.ONE_TIME.value)),
            frequency=ScheduleFrequency(data.get("frequency", ScheduleFrequency.ONCE.value)),
            interval_seconds=float(data.get("interval_seconds", 0.0)),
            cron_expr=data.get("cron_expr", ""),
            run_at=float(data.get("run_at", time.time())),
            next_run_at=data.get("next_run_at"),
            last_run_at=data.get("last_run_at"),
            action_type=data.get("action_type", "mission"),
            target=data.get("target", ""),
            payload=dict(data.get("payload", {})),
            status=ScheduleStatus(data.get("status", ScheduleStatus.ACTIVE.value)),
            execution_count=int(data.get("execution_count", 0)),
            max_executions=data.get("max_executions"),
            last_result=dict(data.get("last_result", {})),
            governance_level=ActionGovernanceLevel(data.get("governance_level", ActionGovernanceLevel.LEVEL_3_EXECUTE.value)),
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass
class ConditionMonitorRecord:
    """An event and condition monitor definition."""
    monitor_id: str = field(default_factory=lambda: f"mon_{uuid.uuid4().hex[:10]}")
    name: str = ""
    description: str = ""
    condition_type: str = "metric"                     # "metric" | "service" | "file" | "mission"
    target: str = ""                                  # e.g. "cpu", "ram", "disk", "ollama", "/path"
    operator: str = ">="                              # ">=", "<=", "==", "!=", "exists", "missing"
    threshold: Any = 90.0
    evaluation_interval: float = 30.0                 # seconds between checks
    policy: str = "ON_EDGE"                           # ON_EDGE (trigger on transition) | CONTINUOUS
    last_evaluated_at: float | None = None
    last_triggered_at: float | None = None
    trigger_count: int = 0
    status: MonitorStatus = MonitorStatus.ACTIVE
    governance_level: ActionGovernanceLevel = ActionGovernanceLevel.LEVEL_1_RECOMMEND
    enabled: bool = True
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor_id": self.monitor_id,
            "name": self.name,
            "description": self.description,
            "condition_type": self.condition_type,
            "target": self.target,
            "operator": self.operator,
            "threshold": self.threshold,
            "evaluation_interval": self.evaluation_interval,
            "policy": self.policy,
            "last_evaluated_at": self.last_evaluated_at,
            "last_triggered_at": self.last_triggered_at,
            "trigger_count": self.trigger_count,
            "status": self.status.value,
            "governance_level": self.governance_level.value,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConditionMonitorRecord:
        return cls(
            monitor_id=data.get("monitor_id", f"mon_{uuid.uuid4().hex[:10]}"),
            name=data.get("name", ""),
            description=data.get("description", ""),
            condition_type=data.get("condition_type", "metric"),
            target=data.get("target", ""),
            operator=data.get("operator", ">="),
            threshold=data.get("threshold", 90.0),
            evaluation_interval=float(data.get("evaluation_interval", 30.0)),
            policy=data.get("policy", "ON_EDGE"),
            last_evaluated_at=data.get("last_evaluated_at"),
            last_triggered_at=data.get("last_triggered_at"),
            trigger_count=int(data.get("trigger_count", 0)),
            status=MonitorStatus(data.get("status", MonitorStatus.ACTIVE.value)),
            governance_level=ActionGovernanceLevel(data.get("governance_level", ActionGovernanceLevel.LEVEL_1_RECOMMEND.value)),
            enabled=bool(data.get("enabled", True)),
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass
class NotificationRecord:
    """A user notification delivered to the Notification Center."""
    notification_id: str = field(default_factory=lambda: f"notif_{uuid.uuid4().hex[:10]}")
    title: str = ""
    message: str = ""
    category: str = "RECOMMENDATION"                 # RECOMMENDATION, SCHEDULE, ALERT, APPROVAL, MISSION, HEALTH
    priority: RecommendationPriority = RecommendationPriority.MEDIUM
    source: str = "proactive_engine"
    created_at: float = field(default_factory=time.time)
    read: bool = False
    dismissed: bool = False
    snoozed_until: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation_id: str | None = None
    actions: list[dict[str, str]] = field(default_factory=list)

    def is_visible(self, current_time: float | None = None) -> bool:
        if self.dismissed:
            return False
        now = current_time or time.time()
        if self.snoozed_until and now < self.snoozed_until:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "title": self.title,
            "message": self.message,
            "category": self.category,
            "priority": self.priority.value,
            "source": self.source,
            "created_at": self.created_at,
            "read": self.read,
            "dismissed": self.dismissed,
            "snoozed_until": self.snoozed_until,
            "evidence": self.evidence,
            "recommendation_id": self.recommendation_id,
            "actions": self.actions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NotificationRecord:
        return cls(
            notification_id=data.get("notification_id", f"notif_{uuid.uuid4().hex[:10]}"),
            title=data.get("title", ""),
            message=data.get("message", ""),
            category=data.get("category", "RECOMMENDATION"),
            priority=RecommendationPriority(data.get("priority", RecommendationPriority.MEDIUM.value)),
            source=data.get("source", "proactive_engine"),
            created_at=float(data.get("created_at", time.time())),
            read=bool(data.get("read", False)),
            dismissed=bool(data.get("dismissed", False)),
            snoozed_until=data.get("snoozed_until"),
            evidence=dict(data.get("evidence", {})),
            recommendation_id=data.get("recommendation_id"),
            actions=list(data.get("actions", [])),
        )
