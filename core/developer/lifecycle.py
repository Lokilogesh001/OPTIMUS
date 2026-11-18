"""core/developer/lifecycle.py — Autonomous Developer Lifecycle & State Machine for OPTIMUS.

Implements the 8-Phase Lifecycle:
UNDERSTAND → INSPECT → PLAN → APPROVE → IMPLEMENT → EXECUTE → VERIFY → REPORT
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LifecyclePhase(str, Enum):
    """The 8 canonical stages of the Autonomous Developer Agent execution."""
    IDLE = "IDLE"
    UNDERSTAND = "UNDERSTAND"
    INSPECT = "INSPECT"
    PLAN = "PLAN"
    APPROVE = "APPROVE"
    IMPLEMENT = "IMPLEMENT"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    REPORT = "REPORT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


LIFECYCLE_SEQUENCE: list[LifecyclePhase] = [
    LifecyclePhase.UNDERSTAND,
    LifecyclePhase.INSPECT,
    LifecyclePhase.PLAN,
    LifecyclePhase.APPROVE,
    LifecyclePhase.IMPLEMENT,
    LifecyclePhase.EXECUTE,
    LifecyclePhase.VERIFY,
    LifecyclePhase.REPORT,
]


@dataclass
class PhaseResult:
    """Execution outcome of a single lifecycle phase."""
    phase: LifecyclePhase
    status: str = "success"  # "success", "failed", "skipped", "waiting_approval"
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


@dataclass
class DeveloperTaskPlan:
    """Structured plan and execution trace for a developer task."""
    task_id: str
    goal: str
    current_phase: LifecyclePhase = LifecyclePhase.IDLE
    phases_completed: list[LifecyclePhase] = field(default_factory=list)
    phase_results: dict[str, PhaseResult] = field(default_factory=dict)

    inspected_files: list[str] = field(default_factory=list)
    planned_modifications: list[dict[str, Any]] = field(default_factory=list)
    applied_modifications: list[dict[str, Any]] = field(default_factory=list)
    executed_commands: list[dict[str, Any]] = field(default_factory=list)
    verification_evidence: dict[str, Any] = field(default_factory=dict)

    requires_approval: bool = False
    is_approved: bool = False
    approval_reason: str = ""

    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    final_report: str = ""
    error: str | None = None

    @property
    def is_finished(self) -> bool:
        return self.current_phase in (LifecyclePhase.COMPLETED, LifecyclePhase.FAILED)

    def advance_phase(self, new_phase: LifecyclePhase) -> None:
        """Advance task to the next lifecycle phase."""
        if self.current_phase not in (LifecyclePhase.IDLE, LifecyclePhase.COMPLETED, LifecyclePhase.FAILED):
            if self.current_phase not in self.phases_completed:
                self.phases_completed.append(self.current_phase)
        self.current_phase = new_phase

    def record_phase_result(
        self,
        phase: LifecyclePhase,
        status: str,
        summary: str,
        evidence: dict[str, Any] | None = None,
        duration_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        """Record the outcome of a completed phase."""
        res = PhaseResult(
            phase=phase,
            status=status,
            summary=summary,
            evidence=evidence or {},
            duration_ms=duration_ms,
            error=error,
        )
        self.phase_results[phase.value] = res
        if status == "success" and phase not in self.phases_completed:
            self.phases_completed.append(phase)

    def to_dict(self) -> dict[str, Any]:
        """Serialize plan for UI and telemetry dispatch."""
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "current_phase": self.current_phase.value,
            "phases_completed": [p.value for p in self.phases_completed],
            "inspected_files": self.inspected_files,
            "planned_modifications": self.planned_modifications,
            "applied_modifications": self.applied_modifications,
            "executed_commands": self.executed_commands,
            "verification_evidence": self.verification_evidence,
            "requires_approval": self.requires_approval,
            "is_approved": self.is_approved,
            "approval_reason": self.approval_reason,
            "duration_s": round((self.end_time or time.time()) - self.start_time, 2),
            "final_report": self.final_report,
            "error": self.error,
        }
