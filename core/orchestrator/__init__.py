"""OPTIMUS Core Orchestrator Runtime for Mark-LVII.

Provides hybrid hierarchical orchestration:
- Central Orchestrator owns planning, permissions, workflow state, and verification.
- Specialized agents perform domain-specific work without peer-to-peer delegation.
- Capability-based access control (CBAC) ensures zero unverified execution.
- Task runtime provides crash-resilient persistence and cascading cancellation.
- In-process event bus provides non-blocking desktop UI synchronization.
"""

from __future__ import annotations

from core.orchestrator import desktop
from core.orchestrator.agents import (
    AgentCapability,
    AgentProfile,
    AgentRegistry,
)
from core.orchestrator.events import (
    EventBus,
    OrchestratorEvent,
    PlanCreatedEvent,
    SubtaskCompletedEvent,
    SubtaskDelegatedEvent,
    SubtaskFailedEvent,
    SubtaskProgressEvent,
    SubtaskVerifiedEvent,
    TaskCancelledEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskFailedEvent,
)
from core.orchestrator.orchestrator import (
    CentralOrchestrator,
)
from core.orchestrator.runtime import (
    ExecutionPlan,
    Subtask,
    SubtaskStatus,
    TaskRecord,
    TaskStatus,
    TaskStore,
    new_subtask_id,
    new_task_id,
)
from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    safe_eval_expression,
)

__all__ = [
    # Orchestrator
    "CentralOrchestrator",
    # Agents
    "AgentRegistry",
    "AgentProfile",
    "AgentCapability",
    # Tools & CBAC
    "ToolRegistry",
    "ToolDefinition",
    "ToolResult",
    "CapabilityTier",
    "safe_eval_expression",
    # Task Runtime
    "TaskStatus",
    "SubtaskStatus",
    "Subtask",
    "ExecutionPlan",
    "TaskRecord",
    "TaskStore",
    "new_task_id",
    "new_subtask_id",
    # Events
    "EventBus",
    "OrchestratorEvent",
    "TaskCreatedEvent",
    "PlanCreatedEvent",
    "SubtaskDelegatedEvent",
    "SubtaskProgressEvent",
    "SubtaskVerifiedEvent",
    "SubtaskCompletedEvent",
    "SubtaskFailedEvent",
    "TaskCompletedEvent",
    "TaskFailedEvent",
    "TaskCancelledEvent",
    # Desktop integration
    "desktop",
]
