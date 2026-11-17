"""core/planning/types.py — Data Contracts & Enums for OPTIMUS Adaptive Planning Subsystem.

Defines:
- GoalCategory, StrategyType, PlanRiskLevel, FailureType, OutcomeType.
- GoalDecomposition: Goal interpretation and decomposition schema.
- PlanStep: Granular executable plan step with dependencies, postconditions, and approvals.
- AdaptivePlan: Versioned execution DAG with replanning history.
- ExecutionOutcome: Cryptographically verifiable execution outcome record.
- OptimizationRecommendation: Measurable evidence-based recommendation with rollback payload.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GoalCategory(str, Enum):
    """Broad domain classification of requested goals."""
    ANALYSIS = "ANALYSIS"
    SYSTEM_OPS = "SYSTEM_OPS"
    CODE_MODIFICATION = "CODE_MODIFICATION"
    DATA_PIPELINE = "DATA_PIPELINE"
    RESEARCH = "RESEARCH"
    RECOVERY = "RECOVERY"
    GENERAL = "GENERAL"


class StrategyType(str, Enum):
    """Adaptive execution strategy profiles."""
    CONSERVATIVE_SEQUENTIAL = "CONSERVATIVE_SEQUENTIAL"
    FAST_PARALLEL = "FAST_PARALLEL"
    SPECIALIZED_MINIMAL = "SPECIALIZED_MINIMAL"
    ROBUST_FAULT_TOLERANT = "ROBUST_FAULT_TOLERANT"


class PlanRiskLevel(str, Enum):
    """Risk tiers governing approval requirements."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FailureType(str, Enum):
    """Categorization of step execution failures for diagnostic replanning."""
    TRANSIENT = "TRANSIENT"                      # Network glitch, timeout, temporary rate limit
    ENVIRONMENTAL = "ENVIRONMENTAL"              # File missing, disk full, port blocked
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"  # Agent lacks required tool or parameter
    LOGICAL = "LOGICAL"                          # Semantic error, postcondition failed
    PERMISSIONS_DENIED = "PERMISSIONS_DENIED"    # CBAC tier restriction, unapproved action
    UNKNOWN = "UNKNOWN"


class OutcomeType(str, Enum):
    """Authoritative outcome classifications for evidence recording."""
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    INCOMPLETE = "INCOMPLETE"
    AMBIGUOUS_EXECUTION = "AMBIGUOUS_EXECUTION"
    USER_CANCELLED = "USER_CANCELLED"


@dataclass
class GoalDecomposition:
    """Interpretation and capability decomposition of a user goal."""
    goal: str
    category: GoalCategory = GoalCategory.GENERAL
    complexity_score: float = 1.0  # 1.0 (trivial) to 10.0 (highly complex composite)
    risk_level: PlanRiskLevel = PlanRiskLevel.LOW
    required_capabilities: list[str] = field(default_factory=list)
    estimated_duration_seconds: float = 5.0
    is_destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "category": self.category.value,
            "complexity_score": round(self.complexity_score, 2),
            "risk_level": self.risk_level.value,
            "required_capabilities": list(self.required_capabilities),
            "estimated_duration_seconds": round(self.estimated_duration_seconds, 2),
            "is_destructive": self.is_destructive,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalDecomposition:
        return cls(
            goal=data.get("goal", ""),
            category=GoalCategory(data.get("category", "GENERAL")),
            complexity_score=float(data.get("complexity_score", 1.0)),
            risk_level=PlanRiskLevel(data.get("risk_level", "LOW")),
            required_capabilities=list(data.get("required_capabilities", [])),
            estimated_duration_seconds=float(data.get("estimated_duration_seconds", 5.0)),
            is_destructive=bool(data.get("is_destructive", False)),
        )


@dataclass
class PlanStep:
    """Individual unit of work in an adaptive plan."""
    step_id: str
    title: str
    description: str = ""
    assigned_agent: str = "general"
    required_tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    is_parallelizable: bool = False
    is_destructive: bool = False
    requires_approval: bool = False
    approval_role: str = "admin"
    expected_deliverables: list[str] = field(default_factory=list)
    postconditions: list[dict[str, Any]] = field(default_factory=list)
    retry_count: int = 2
    timeout_seconds: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "description": self.description,
            "assigned_agent": self.assigned_agent,
            "required_tools": list(self.required_tools),
            "depends_on": list(self.depends_on),
            "is_parallelizable": self.is_parallelizable,
            "is_destructive": self.is_destructive,
            "requires_approval": self.requires_approval,
            "approval_role": self.approval_role,
            "expected_deliverables": list(self.expected_deliverables),
            "postconditions": list(self.postconditions),
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        return cls(
            step_id=data.get("step_id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            assigned_agent=data.get("assigned_agent", "general"),
            required_tools=list(data.get("required_tools", [])),
            depends_on=list(data.get("depends_on", [])),
            is_parallelizable=bool(data.get("is_parallelizable", False)),
            is_destructive=bool(data.get("is_destructive", False)),
            requires_approval=bool(data.get("requires_approval", False)),
            approval_role=data.get("approval_role", "admin"),
            expected_deliverables=list(data.get("expected_deliverables", [])),
            postconditions=list(data.get("postconditions", [])),
            retry_count=int(data.get("retry_count", 2)),
            timeout_seconds=float(data.get("timeout_seconds", 60.0)),
        )


@dataclass
class AdaptivePlan:
    """Full execution plan graph with version tracking and replan history."""
    plan_id: str = field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:10]}")
    goal: str = ""
    category: GoalCategory = GoalCategory.GENERAL
    version: int = 1
    strategy: StrategyType = StrategyType.CONSERVATIVE_SEQUENTIAL
    risk_level: PlanRiskLevel = PlanRiskLevel.LOW
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    replan_history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "category": self.category.value,
            "version": self.version,
            "strategy": self.strategy.value,
            "risk_level": self.risk_level.value,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "replan_history": list(self.replan_history),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptivePlan:
        steps_raw = data.get("steps", [])
        steps = [PlanStep.from_dict(s) if isinstance(s, dict) else s for s in steps_raw]
        return cls(
            plan_id=data.get("plan_id", f"plan-{uuid.uuid4().hex[:10]}"),
            goal=data.get("goal", ""),
            category=GoalCategory(data.get("category", "GENERAL")),
            version=int(data.get("version", 1)),
            strategy=StrategyType(data.get("strategy", "CONSERVATIVE_SEQUENTIAL")),
            risk_level=PlanRiskLevel(data.get("risk_level", "LOW")),
            steps=steps,
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            replan_history=list(data.get("replan_history", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ExecutionOutcome:
    """Persistent outcome record backed by verified physical evidence."""
    outcome_id: str = field(default_factory=lambda: f"out-{uuid.uuid4().hex[:10]}")
    plan_id: str = ""
    goal: str = ""
    goal_category: GoalCategory = GoalCategory.GENERAL
    strategy: StrategyType = StrategyType.CONSERVATIVE_SEQUENTIAL
    outcome: OutcomeType = OutcomeType.VERIFIED_SUCCESS
    duration_seconds: float = 0.0
    verified_evidence_count: int = 0
    failed_step_id: str | None = None
    failure_type: FailureType | None = None
    recovery_attempts: int = 0
    agents_used: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "plan_id": self.plan_id,
            "goal": self.goal,
            "goal_category": self.goal_category.value,
            "strategy": self.strategy.value,
            "outcome": self.outcome.value,
            "duration_seconds": round(self.duration_seconds, 2),
            "verified_evidence_count": self.verified_evidence_count,
            "failed_step_id": self.failed_step_id,
            "failure_type": self.failure_type.value if self.failure_type else None,
            "recovery_attempts": self.recovery_attempts,
            "agents_used": list(self.agents_used),
            "tools_used": list(self.tools_used),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionOutcome:
        ft = FailureType(data["failure_type"]) if data.get("failure_type") else None
        return cls(
            outcome_id=data.get("outcome_id", f"out-{uuid.uuid4().hex[:10]}"),
            plan_id=data.get("plan_id", ""),
            goal=data.get("goal", ""),
            goal_category=GoalCategory(data.get("goal_category", "GENERAL")),
            strategy=StrategyType(data.get("strategy", "CONSERVATIVE_SEQUENTIAL")),
            outcome=OutcomeType(data.get("outcome", "VERIFIED_SUCCESS")),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            verified_evidence_count=int(data.get("verified_evidence_count", 0)),
            failed_step_id=data.get("failed_step_id"),
            failure_type=ft,
            recovery_attempts=int(data.get("recovery_attempts", 0)),
            agents_used=list(data.get("agents_used", [])),
            tools_used=list(data.get("tools_used", [])),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class OptimizationRecommendation:
    """Actionable recommendation for improving agent/tool/strategy choices."""
    recommendation_id: str = field(default_factory=lambda: f"rec-{uuid.uuid4().hex[:8]}")
    target_type: str = "strategy_selection"  # "agent_selection", "tool_selection", "strategy_selection"
    target_key: str = ""                     # e.g., "code_intelligence", "CONSERVATIVE_SEQUENTIAL"
    description: str = ""
    observed_samples: int = 0
    confidence_score: float = 0.0            # 0.0 to 1.0
    evidence_summary: str = ""
    proposed_change: dict[str, Any] = field(default_factory=dict)
    is_applied: bool = False
    applied_at: float | None = None
    revert_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "target_type": self.target_type,
            "target_key": self.target_key,
            "description": self.description,
            "observed_samples": self.observed_samples,
            "confidence_score": round(self.confidence_score, 2),
            "evidence_summary": self.evidence_summary,
            "proposed_change": dict(self.proposed_change),
            "is_applied": self.is_applied,
            "applied_at": self.applied_at,
            "revert_payload": dict(self.revert_payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OptimizationRecommendation:
        return cls(
            recommendation_id=data.get("recommendation_id", f"rec-{uuid.uuid4().hex[:8]}"),
            target_type=data.get("target_type", "strategy_selection"),
            target_key=data.get("target_key", ""),
            description=data.get("description", ""),
            observed_samples=int(data.get("observed_samples", 0)),
            confidence_score=float(data.get("confidence_score", 0.0)),
            evidence_summary=data.get("evidence_summary", ""),
            proposed_change=dict(data.get("proposed_change", {})),
            is_applied=bool(data.get("is_applied", False)),
            applied_at=data.get("applied_at"),
            revert_payload=dict(data.get("revert_payload", {})),
        )
