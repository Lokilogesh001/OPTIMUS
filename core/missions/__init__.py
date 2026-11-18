"""core/missions/__init__.py — OPTIMUS Autonomous Missions Subsystem.

Provides:
- MissionState, SubtaskStatus, MissionSubtask, MissionPlan, MissionRecord (types)
- MissionStore (atomic persistent storage)
- MissionMemory (shared context bus and strategy memory)
- MissionRecoveryEngine (failure diagnosis and adaptive recovery)
- MissionPlanner (DAG decomposition and resource estimation)
- MissionCoordinator (intelligent multi-agent coordination)
- MissionExecutionEngine, get_mission_engine (lifecycle state machine)
"""

from __future__ import annotations

from core.missions.coordinator import MissionCoordinator
from core.missions.engine import MissionExecutionEngine, get_mission_engine
from core.missions.memory import MissionMemory
from core.missions.planner import MissionPlanner
from core.missions.recovery import MissionRecoveryEngine
from core.missions.storage import MissionStore
from core.missions.types import (
    MissionPlan,
    MissionRecord,
    MissionState,
    MissionSubtask,
    SubtaskStatus,
)

__all__ = [
    "MissionCoordinator",
    "MissionExecutionEngine",
    "MissionMemory",
    "MissionPlan",
    "MissionPlanner",
    "MissionRecord",
    "MissionRecoveryEngine",
    "MissionState",
    "MissionStore",
    "MissionSubtask",
    "SubtaskStatus",
    "get_mission_engine",
]
