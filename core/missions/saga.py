"""core/missions/saga.py — Saga Transactional Rollback Coordinator for OPTIMUS.

Provides transactional compensations for mutating multi-step missions:
- Resolves CRIT-04: Prevents fragmented state, orphaned files, and dirty workspaces
  when multi-step mutating missions abort, fail, or are cancelled.
- Records executed mutating subtasks into an append-only compensation stack.
- Executes backward rollback in reverse topological order upon failure.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from core.orchestrator.tools import ToolRegistry, ToolResult

logger = logging.getLogger("optimus.missions.saga")


@dataclass
class CompensationRecord:
    """An executable compensation action designed to undo a specific mutation."""
    subtask_id: str
    action_name: str
    compensation_capability_id: str
    compensation_args: dict[str, Any]
    executed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "action_name": self.action_name,
            "compensation_capability_id": self.compensation_capability_id,
            "compensation_args": self.compensation_args,
            "executed_at": self.executed_at,
        }


class SagaCoordinator:
    """Coordinates backward transactional unwinding for aborted mutating missions."""

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self.tool_registry = tool_registry or ToolRegistry()
        self.compensation_stack: list[CompensationRecord] = []
        self._lock = threading.RLock()

    def record_mutation(self, rec: CompensationRecord) -> None:
        """Register a successful mutation and its corresponding compensation."""
        with self._lock:
            logger.info(
                "Saga recorded mutation for subtask '%s': %s (Compensation: %s)",
                rec.subtask_id,
                rec.action_name,
                rec.compensation_capability_id,
            )
            self.compensation_stack.append(rec)

    def rollback(self) -> list[ToolResult]:
        """Execute all recorded compensation actions in reverse chronological order."""
        results: list[ToolResult] = []
        with self._lock:
            if not self.compensation_stack:
                logger.info("Saga rollback invoked, but compensation stack is empty.")
                return results

            logger.warning(
                "Initiating Saga Transactional Rollback (%d actions to unwind)...",
                len(self.compensation_stack),
            )

            while self.compensation_stack:
                rec = self.compensation_stack.pop()
                logger.info(
                    "Unwinding mutation '%s' via compensation '%s'...",
                    rec.action_name,
                    rec.compensation_capability_id,
                )
                try:
                    res: ToolResult = self.tool_registry.execute(
                        tool_name=rec.compensation_capability_id,
                        arguments=rec.compensation_args,
                        caller_agent="saga_coordinator",
                    )
                    results.append(res)
                    if not res.is_success:
                        logger.error(
                            "Saga compensation '%s' for subtask '%s' failed: %s",
                            rec.compensation_capability_id,
                            rec.subtask_id,
                            res.error,
                        )
                except Exception as e:
                    logger.exception("Saga compensation encountered unexpected error: %s", e)
                    results.append(ToolResult(status="failed", error=str(e)))

            logger.info("Saga Transactional Rollback completed.")
            return results

    def clear(self) -> None:
        """Clear the compensation stack upon successful mission completion."""
        with self._lock:
            self.compensation_stack.clear()
