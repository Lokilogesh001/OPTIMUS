"""core/workflows/__init__.py — OPTIMUS Autonomous Workflow Engine Package.

Provides declarative DAG workflow orchestration, safe conditional branching,
human-in-the-loop authorization gates, atomic disk persistence, and crash recovery.
"""

from __future__ import annotations

from core.workflows.approval import ApprovalManager
from core.workflows.checkpointing import (
    CheckpointError,
    CheckpointStore,
    CorruptCheckpointError,
    PreResumptionDeliverableReVerifier,
    RepetitiveFailureLoopError,
    ReverificationReport,
    SemanticLoopDetector,
    WorkflowCheckpoint,
)
from core.workflows.conditions import SafeConditionEvaluator
from core.workflows.engine import WorkflowEngine
from core.workflows.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    WorkflowCancelledEvent,
    WorkflowCompletedEvent,
    WorkflowCreatedEvent,
    WorkflowFailedEvent,
    WorkflowPausedEvent,
    WorkflowRecoveryStartedEvent,
    WorkflowResumedEvent,
    WorkflowStartedEvent,
    WorkflowStepCompletedEvent,
    WorkflowStepFailedEvent,
    WorkflowStepSkippedEvent,
    WorkflowStepStartedEvent,
)
from core.workflows.resolver import DependencyResolver
from core.workflows.storage import WorkflowStore
from core.workflows.types import (
    AmbiguousExecutionError,
    ApprovalDecision,
    ApprovalRequiredError,
    ApprovalStatus,
    CircularDependencyError,
    ConditionType,
    MissingDependencyError,
    RetryPolicy,
    StepCondition,
    StepExecutionRecord,
    StepStatus,
    WorkflowDefinition,
    WorkflowError,
    WorkflowExecutionRecord,
    WorkflowStatus,
    WorkflowStepDefinition,
    WorkflowValidationError,
    new_execution_id,
)

__all__ = [
    "AmbiguousExecutionError",
    "ApprovalDecision",
    "ApprovalManager",
    "ApprovalRequestedEvent",
    "ApprovalRequiredError",
    "ApprovalResolvedEvent",
    "ApprovalStatus",
    "CheckpointError",
    "CheckpointStore",
    "CircularDependencyError",
    "ConditionType",
    "CorruptCheckpointError",
    "DependencyResolver",
    "MissingDependencyError",
    "PreResumptionDeliverableReVerifier",
    "RepetitiveFailureLoopError",
    "RetryPolicy",
    "ReverificationReport",
    "SafeConditionEvaluator",
    "SemanticLoopDetector",
    "StepCondition",
    "StepExecutionRecord",
    "StepStatus",
    "WorkflowCancelledEvent",
    "WorkflowCheckpoint",
    "WorkflowCompletedEvent",
    "WorkflowCreatedEvent",
    "WorkflowDefinition",
    "WorkflowEngine",
    "WorkflowError",
    "WorkflowExecutionRecord",
    "WorkflowFailedEvent",
    "WorkflowPausedEvent",
    "WorkflowRecoveryStartedEvent",
    "WorkflowResumedEvent",
    "WorkflowStartedEvent",
    "WorkflowStatus",
    "WorkflowStepCompletedEvent",
    "WorkflowStepDefinition",
    "WorkflowStepFailedEvent",
    "WorkflowStepSkippedEvent",
    "WorkflowStepStartedEvent",
    "WorkflowStore",
    "WorkflowValidationError",
    "new_execution_id",
]
