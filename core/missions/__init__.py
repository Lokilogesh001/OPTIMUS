"""core/missions/__init__.py — OPTIMUS Autonomous Missions Subsystem.

Provides:
- MissionState, SubtaskStatus, MissionSubtask, MissionPlan, MissionRecord (types)
- MissionStore (atomic persistent storage)
- MissionMemory (shared context bus and strategy memory)
- MissionRecoveryEngine (failure diagnosis and adaptive recovery)
- MissionPlanner, SafeASTEvaluator (DAG decomposition and resource estimation)
- MissionCoordinator (intelligent multi-agent coordination)
- MissionExecutionEngine, get_mission_engine (lifecycle state machine)
- MissionIntentParser, ParsedMissionIntent (intent parsing)
- MissionClassifier, MissionClassification (classification and risk scoring)
- UniversalCapabilityRegistry, CapabilityDescriptor (dynamic capabilities)
- SagaCoordinator, CompensationRecord (transactional rollback)
- TokenAccountingGovernor, SemanticLoopDetector, GovernanceBudget (governance)
"""

from __future__ import annotations

from core.missions.capabilities import (
    CapabilityDescriptor,
    IdempotencyMode,
    UniversalCapabilityRegistry,
)
from core.missions.classification import (
    AuthorizationLevel,
    ComplexityTier,
    MissionClassification,
    MissionClassifier,
    MissionRiskTier,
    MissionType,
)
from core.missions.coordinator import MissionCoordinator, wrap_untrusted_data
from core.missions.engine import MissionExecutionEngine, get_mission_engine
from core.missions.governance import (
    GovernanceBudget,
    SemanticLoopDetector,
    TokenAccountingGovernor,
)
from core.missions.intent import (
    CompletionCriterion,
    CriterionType,
    IntentEntity,
    MissionIntentParser,
    ParsedMissionIntent,
    SingleIntent,
    TemporalConstraint,
)
from core.missions.memory import MissionMemory
from core.missions.planner import MissionPlanner, SafeASTEvaluator
from core.missions.recovery import MissionRecoveryEngine
from core.missions.saga import CompensationRecord, SagaCoordinator
from core.missions.storage import MissionStore
from core.missions.types import (
    MissionPlan,
    MissionRecord,
    MissionState,
    MissionSubtask,
    SubtaskStatus,
)

__all__ = [
    "AuthorizationLevel",
    "CapabilityDescriptor",
    "CompensationRecord",
    "CompletionCriterion",
    "ComplexityTier",
    "CriterionType",
    "GovernanceBudget",
    "IdempotencyMode",
    "IntentEntity",
    "MissionClassification",
    "MissionClassifier",
    "MissionCoordinator",
    "MissionExecutionEngine",
    "MissionIntentParser",
    "MissionMemory",
    "MissionPlan",
    "MissionPlanner",
    "MissionRecord",
    "MissionRecoveryEngine",
    "MissionRiskTier",
    "MissionState",
    "MissionStore",
    "MissionSubtask",
    "MissionType",
    "ParsedMissionIntent",
    "SafeASTEvaluator",
    "SagaCoordinator",
    "SemanticLoopDetector",
    "SingleIntent",
    "SubtaskStatus",
    "TemporalConstraint",
    "TokenAccountingGovernor",
    "UniversalCapabilityRegistry",
    "get_mission_engine",
    "wrap_untrusted_data",
]
