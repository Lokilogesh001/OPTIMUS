"""core/application_intelligence/__init__.py — Universal Application Intelligence Subsystem."""

from __future__ import annotations

from core.application_intelligence.adapters import (
    BaseApplicationAdapter,
    BrowserAdapter,
    DevToolsAdapter,
    DocumentAdapter,
    WhatsAppAdapter,
    get_adapter,
)
from core.application_intelligence.capabilities import (
    STANDARD_CAPABILITIES,
    is_control_destructive,
    is_control_safe_for_exploration,
)
from core.application_intelligence.desktop_lock import (
    DesktopInteractionLock,
    DesktopInteractionLockTimeout,
)
from core.application_intelligence.discovery import (
    discover_all_installed_applications,
    discover_running_processes_and_windows,
    resolve_file_association,
)
from core.application_intelligence.interaction_router import InteractionRouter
from core.application_intelligence.learning_engine import ApplicationLearningEngine
from core.application_intelligence.registry import ApplicationRegistry
from core.application_intelligence.types import (
    ActionContract,
    AppCategory,
    AppDefinition,
    AppInteractionTier,
    ExecutionEvidence,
    LearnedWorkflow,
    ResolutionResult,
    ResolutionStatus,
)
from core.application_intelligence.workflow_store import WorkflowStore

__all__ = [
    "AppInteractionTier",
    "AppCategory",
    "AppDefinition",
    "ResolutionStatus",
    "ResolutionResult",
    "ActionContract",
    "ExecutionEvidence",
    "LearnedWorkflow",
    "DesktopInteractionLock",
    "DesktopInteractionLockTimeout",
    "discover_all_installed_applications",
    "discover_running_processes_and_windows",
    "resolve_file_association",
    "ApplicationRegistry",
    "STANDARD_CAPABILITIES",
    "is_control_destructive",
    "is_control_safe_for_exploration",
    "BaseApplicationAdapter",
    "WhatsAppAdapter",
    "BrowserAdapter",
    "DocumentAdapter",
    "DevToolsAdapter",
    "get_adapter",
    "InteractionRouter",
    "ApplicationLearningEngine",
    "WorkflowStore",
]
