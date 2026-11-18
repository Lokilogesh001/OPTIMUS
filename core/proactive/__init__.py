"""core/proactive/__init__.py — Public API for OPTIMUS Proactive Personal Intelligence."""

from core.proactive.context import (
    ContextSummary,
    ProactiveContextManager,
    sanitize_dict,
    sanitize_sensitive_data,
)
from core.proactive.engine import (
    ProactiveIntelligenceEngine,
    get_proactive_engine,
)
from core.proactive.governance import (
    ActionGovernor,
    get_action_governor,
)
from core.proactive.monitors import (
    ConditionMonitorEngine,
    get_condition_monitor_engine,
)
from core.proactive.notifications import (
    NotificationManager,
    get_notification_manager,
)
from core.proactive.scheduler import (
    IntelligentScheduler,
    get_intelligent_scheduler,
)
from core.proactive.types import (
    ActionGovernanceLevel,
    ConditionMonitorRecord,
    MonitorStatus,
    NotificationRecord,
    Recommendation,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationStatus,
    ScheduleFrequency,
    ScheduleRecord,
    ScheduleStatus,
    ScheduleType,
)

__all__ = [
    # Enums & Models
    "ActionGovernanceLevel",
    "RecommendationCategory",
    "RecommendationPriority",
    "RecommendationStatus",
    "ScheduleType",
    "ScheduleFrequency",
    "ScheduleStatus",
    "MonitorStatus",
    "Recommendation",
    "ScheduleRecord",
    "ConditionMonitorRecord",
    "NotificationRecord",
    "ContextSummary",
    # Subsystems & Singletons
    "ActionGovernor",
    "get_action_governor",
    "NotificationManager",
    "get_notification_manager",
    "IntelligentScheduler",
    "get_intelligent_scheduler",
    "ConditionMonitorEngine",
    "get_condition_monitor_engine",
    "ProactiveContextManager",
    "ProactiveIntelligenceEngine",
    "get_proactive_engine",
    # Sanitization
    "sanitize_sensitive_data",
    "sanitize_dict",
]
