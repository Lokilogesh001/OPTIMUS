"""core/orchestrator/mre_types.py — Type Definitions for Minimum Reliable Execution (MRE).

MRE Principle:
For every user request, OPTIMUS identifies the simplest, safest, and most reliable
way to achieve the requested outcome, executes it with minimal unnecessary actions,
and verifies that the outcome actually occurred.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskCategory(str, Enum):
    """The 14 canonical task categories supported by OPTIMUS MRE."""
    FILESYSTEM = "FILESYSTEM"
    APPLICATION_MANAGEMENT = "APPLICATION_MANAGEMENT"
    BROWSER_WEB = "BROWSER_WEB"
    COMMUNICATION = "COMMUNICATION"
    EMAIL = "EMAIL"
    CALENDAR_SCHEDULING = "CALENDAR_SCHEDULING"
    SYSTEM_ADMINISTRATION = "SYSTEM_ADMINISTRATION"
    DEVICE_HARDWARE_INFO = "DEVICE_HARDWARE_INFO"
    DATA_RETRIEVAL_ANALYSIS = "DATA_RETRIEVAL_ANALYSIS"
    CALCULATIONS = "CALCULATIONS"
    SOFTWARE_DEVELOPMENT = "SOFTWARE_DEVELOPMENT"
    COMPUTER_USE = "COMPUTER_USE"
    MULTI_STEP_WORKFLOW = "MULTI_STEP_WORKFLOW"
    MULTI_AGENT_MISSION = "MULTI_AGENT_MISSION"


class MethodPriority(int, Enum):
    """Deterministic Method Priority Ladder."""
    P1_NATIVE_DETERMINISTIC = 1    # Zero-LLM, instant computation/AST/local query
    P2_STRUCTURED_API = 2          # Official REST API adapter (Gmail, Calendar, etc.)
    P3_REGISTERED_TOOL = 3         # Registered ToolRegistry tool via PolicyEngine
    P4_MULTI_AGENT_PLAN = 4        # Yield to CentralOrchestrator DAG planning


@dataclass
class OutcomeContract:
    """Specification of the required postcondition to verify success."""
    contract_type: str            # "file_exists", "process_running", "email_verified", "math_result", "system_status"
    target: str                   # Target entity name, path, expression, etc.
    expected_state: Any = None
    custom_verifier: Callable[[Any], bool] | None = None


@dataclass
class MREDecision:
    """The selected execution decision produced by the MRE pipeline."""
    category: TaskCategory
    priority: MethodPriority
    tool_name: str | None
    arguments: dict[str, Any] = field(default_factory=dict)
    caller_agent: str = "general"
    outcome_contract: OutcomeContract | None = None
    should_yield_to_planner: bool = False
    explanation: str = ""


@dataclass
class MREResult:
    """Structured result returned by MRE execution."""
    success: bool
    status: str
    output: str
    evidence: dict[str, Any] = field(default_factory=dict)
    postcondition_verified: bool = False
    verification_details: str = ""
    decision: MREDecision | None = None
    latency_ms: float = 0.0
    error: str | None = None
