"""core/planning/events.py — Planning & Optimization Lifecycle Events for OPTIMUS.

Dispatched through the Central Orchestrator's EventBus for telemetry, audit logging,
and UI updates in the Control Center.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.planning.types import FailureType, OutcomeType, PlanRiskLevel, StrategyType


@dataclass(frozen=True)
class PlanningEvent:
    """Base event for all planning subsystem activities."""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PlanFormulatedEvent(PlanningEvent):
    """Fired when an initial or revised adaptive plan is created."""
    plan_id: str = ""
    goal: str = ""
    version: int = 1
    strategy: StrategyType = StrategyType.CONSERVATIVE_SEQUENTIAL
    risk_level: PlanRiskLevel = PlanRiskLevel.LOW
    step_count: int = 0
    complexity_score: float = 1.0


@dataclass(frozen=True)
class PlanValidationFailedEvent(PlanningEvent):
    """Fired when plan graph or security validation fails."""
    plan_id: str = ""
    goal: str = ""
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplanTriggeredEvent(PlanningEvent):
    """Fired when step failure forces diagnostic replanning."""
    plan_id: str = ""
    failed_step_id: str = ""
    failure_type: FailureType = FailureType.UNKNOWN
    old_version: int = 1
    new_version: int = 2
    reason: str = ""
    requires_approval: bool = False


@dataclass(frozen=True)
class ExecutionOutcomeRecordedEvent(PlanningEvent):
    """Fired when physical outcome is verified and recorded into OutcomeStore."""
    outcome_id: str = ""
    plan_id: str = ""
    goal: str = ""
    outcome: OutcomeType = OutcomeType.VERIFIED_SUCCESS
    duration_seconds: float = 0.0
    verified_evidence_count: int = 0


@dataclass(frozen=True)
class OptimizationRecommendedEvent(PlanningEvent):
    """Fired when the optimizer identifies an evidence-based improvement."""
    recommendation_id: str = ""
    target_type: str = ""
    description: str = ""
    confidence_score: float = 0.0


@dataclass(frozen=True)
class OptimizationAppliedEvent(PlanningEvent):
    """Fired when an authorized optimization is applied to runtime."""
    recommendation_id: str = ""
    target_type: str = ""
    description: str = ""


@dataclass(frozen=True)
class OptimizationRolledBackEvent(PlanningEvent):
    """Fired when an applied optimization is rolled back."""
    recommendation_id: str = ""
    target_type: str = ""
    description: str = ""
