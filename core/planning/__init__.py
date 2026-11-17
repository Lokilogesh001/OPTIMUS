"""core/planning — Adaptive Planning & Evidence-Driven Self-Improvement Subsystem for OPTIMUS.

Provides:
- Goal interpretation, complexity estimation, and structured subtask decomposition.
- Dynamic execution strategy selection (Conservative Sequential, Fast Parallel,
  Specialized Minimal, Robust Fault-Tolerant).
- Deterministic DAG validation, agent checking, CBAC whitelisting, and safety gates.
- Failure diagnosis and self-healing versioned replanning.
- Persistent verified physical outcome storage and empirical telemetry.
- Evidence-driven self-improvement recommendations with operator approval and rollback.
- Adaptive execution engine with postcondition verification.
"""

from __future__ import annotations

from core.planning.adaptive_executor import AdaptiveExecutionEngine
from core.planning.events import (
    ExecutionOutcomeRecordedEvent,
    OptimizationAppliedEvent,
    OptimizationRecommendedEvent,
    OptimizationRolledBackEvent,
    PlanFormulatedEvent,
    PlanningEvent,
    PlanValidationFailedEvent,
    ReplanTriggeredEvent,
)
from core.planning.goal_analyzer import GoalAnalyzer
from core.planning.optimizer import EvidenceDrivenOptimizer
from core.planning.outcome_store import OutcomeStore
from core.planning.plan_validator import PlanValidator
from core.planning.planner import AdaptivePlanner
from core.planning.replanner import AdaptiveReplanner
from core.planning.strategy_selector import StrategySelector
from core.planning.types import (
    AdaptivePlan,
    ExecutionOutcome,
    FailureType,
    GoalCategory,
    GoalDecomposition,
    OptimizationRecommendation,
    OutcomeType,
    PlanRiskLevel,
    PlanStep,
    StrategyType,
)

__all__ = [
    "AdaptiveExecutionEngine",
    "AdaptivePlan",
    "AdaptivePlanner",
    "AdaptiveReplanner",
    "EvidenceDrivenOptimizer",
    "ExecutionOutcome",
    "ExecutionOutcomeRecordedEvent",
    "FailureType",
    "GoalAnalyzer",
    "GoalCategory",
    "GoalDecomposition",
    "OptimizationAppliedEvent",
    "OptimizationRecommendation",
    "OptimizationRecommendedEvent",
    "OptimizationRolledBackEvent",
    "OutcomeStore",
    "OutcomeType",
    "PlanFormulatedEvent",
    "PlanRiskLevel",
    "PlanStep",
    "PlanValidationFailedEvent",
    "PlanValidator",
    "PlanningEvent",
    "ReplanTriggeredEvent",
    "StrategySelector",
    "StrategyType",
]
