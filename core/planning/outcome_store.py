"""core/planning/outcome_store.py — Persistent Execution Outcome Ledger for OPTIMUS.

Features:
- Records authoritative execution outcomes backed exclusively by verified evidence.
- Differentiates VERIFIED_SUCCESS, VERIFIED_FAILURE, INCOMPLETE, AMBIGUOUS_EXECUTION, USER_CANCELLED.
- Computes empirical success rates, agent reliability, tool metrics, and replan recovery rates.
- Integrates with Component 2 MemoryManager to promote proven execution patterns to PROCEDURAL memory.
- Atomic JSON persistence in memory/planning_outcomes.json.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.memory import MemoryCategory, OptimusMemoryManager
from core.memory.types import MemoryEntry
from core.planning.types import (
    ExecutionOutcome,
    GoalCategory,
    OutcomeType,
)


class OutcomeStore:
    """Thread-safe persistent store for physical execution outcomes and empirical performance data."""

    def __init__(
        self,
        storage_path: Path | None = None,
        memory_manager: OptimusMemoryManager | None = None,
    ) -> None:
        self.storage_path = storage_path or (
            Path(__file__).resolve().parent.parent.parent / "memory" / "planning_outcomes.json"
        )
        self.memory_manager = memory_manager
        self._lock = threading.RLock()
        self._outcomes: list[ExecutionOutcome] = []
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
            self._outcomes = [ExecutionOutcome.from_dict(d) for d in raw_list]
        except Exception as e:  # noqa: BLE001
            print(f"[OutcomeStore] Failed to load outcomes from {self.storage_path}: {e}")

    def save(self) -> None:
        with self._lock:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.storage_path.with_suffix(".tmp")
            data = [o.to_dict() for o in self._outcomes]
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                temp_path.replace(self.storage_path)
            except Exception as e:  # noqa: BLE001
                print(f"[OutcomeStore] Failed to save outcomes: {e}")

    def record_outcome(self, outcome: ExecutionOutcome) -> None:
        """Append verified outcome and atomically save."""
        with self._lock:
            self._outcomes.append(outcome)
            self.save()

            # Promote proven complex/recovered plans to PROCEDURAL memory
            if (
                outcome.outcome == OutcomeType.VERIFIED_SUCCESS
                and self.memory_manager
                and (outcome.recovery_attempts > 0 or outcome.verified_evidence_count >= 2)
            ):
                self._promote_to_procedural_memory(outcome)

    def _promote_to_procedural_memory(self, outcome: ExecutionOutcome) -> None:
        try:
            key = f"procedural_pattern_{outcome.goal_category.value.lower()}_{outcome.plan_id[:8]}"
            content = {
                "goal": outcome.goal,
                "strategy": outcome.strategy.value,
                "agents_used": outcome.agents_used,
                "tools_used": outcome.tools_used,
                "duration_seconds": outcome.duration_seconds,
                "recovery_attempts": outcome.recovery_attempts,
                "proven_at": time.time(),
            }
            entry = MemoryEntry(
                entry_id=f"proc_{secrets.token_hex(6)}",
                category=MemoryCategory.PROCEDURAL,
                key=key,
                content=json.dumps(content),
                source="planning_outcome_store",
                metadata={"plan_id": outcome.plan_id, "verified": True},
                is_verified=True,
                confidence=1.0,
                tags=["procedural_pattern", outcome.goal_category.value.lower()],
            )
            self.memory_manager.store.put(entry)
        except Exception as e:  # noqa: BLE001
            print(f"[OutcomeStore] Failed to promote to procedural memory: {e}")

    def list_outcomes(self, limit: int = 100) -> list[ExecutionOutcome]:
        with self._lock:
            return list(self._outcomes[-limit:])

    def get_success_rate(self, category: GoalCategory | None = None) -> float:
        with self._lock:
            pool = [o for o in self._outcomes if category is None or o.goal_category == category]
            if not pool:
                return 1.0  # Default baseline when unobserved
            successes = sum(1 for o in pool if o.outcome == OutcomeType.VERIFIED_SUCCESS)
            return round(successes / len(pool), 3)

    def get_recovery_rate(self) -> float:
        """Calculate the proportion of replanned failures that successfully recovered."""
        with self._lock:
            replanned = [o for o in self._outcomes if o.recovery_attempts > 0]
            if not replanned:
                return 1.0
            recovered = sum(1 for o in replanned if o.outcome == OutcomeType.VERIFIED_SUCCESS)
            return round(recovered / len(replanned), 3)

    def get_average_duration(self, category: GoalCategory | None = None) -> float:
        with self._lock:
            pool = [o for o in self._outcomes if (category is None or o.goal_category == category) and o.duration_seconds > 0]
            if not pool:
                return 0.0
            return round(sum(o.duration_seconds for o in pool) / len(pool), 2)

    def get_agent_metrics(self) -> dict[str, dict[str, Any]]:
        """Calculate per-agent verified empirical reliability."""
        with self._lock:
            stats: dict[str, dict[str, Any]] = {}
            for o in self._outcomes:
                is_success = (o.outcome == OutcomeType.VERIFIED_SUCCESS)
                for ag in o.agents_used:
                    if ag not in stats:
                        stats[ag] = {"total_runs": 0, "successes": 0, "failures": 0, "total_duration": 0.0}
                    stats[ag]["total_runs"] += 1
                    stats[ag]["total_duration"] += o.duration_seconds
                    if is_success:
                        stats[ag]["successes"] += 1
                    else:
                        stats[ag]["failures"] += 1

            result = {}
            for ag, data in stats.items():
                runs = data["total_runs"]
                rate = round(data["successes"] / runs, 2) if runs > 0 else 1.0
                avg_dur = round(data["total_duration"] / runs, 2) if runs > 0 else 0.0
                result[ag] = {
                    "total_runs": runs,
                    "success_rate": rate,
                    "failure_count": data["failures"],
                    "avg_duration": avg_dur,
                }
            return result

    def get_tool_metrics(self) -> dict[str, dict[str, Any]]:
        """Calculate per-tool empirical reliability."""
        with self._lock:
            stats: dict[str, dict[str, Any]] = {}
            for o in self._outcomes:
                is_success = (o.outcome == OutcomeType.VERIFIED_SUCCESS)
                for t in o.tools_used:
                    if t not in stats:
                        stats[t] = {"invocations": 0, "successes": 0, "failures": 0}
                    stats[t]["invocations"] += 1
                    if is_success:
                        stats[t]["successes"] += 1
                    else:
                        stats[t]["failures"] += 1

            result = {}
            for t, data in stats.items():
                invs = data["invocations"]
                rel = round(data["successes"] / invs, 2) if invs > 0 else 1.0
                result[t] = {
                    "invocations": invs,
                    "reliability": rel,
                    "failure_count": data["failures"],
                }
            return result

    def get_summary_metrics(self) -> dict[str, Any]:
        """Aggregate high-level telemetry for Control Center dashboard."""
        with self._lock:
            total = len(self._outcomes)
            verified_successes = sum(1 for o in self._outcomes if o.outcome == OutcomeType.VERIFIED_SUCCESS)
            verified_failures = sum(1 for o in self._outcomes if o.outcome == OutcomeType.VERIFIED_FAILURE)
            ambiguous = sum(1 for o in self._outcomes if o.outcome == OutcomeType.AMBIGUOUS_EXECUTION)

            return {
                "total_executions": total,
                "verified_success_count": verified_successes,
                "verified_failure_count": verified_failures,
                "ambiguous_count": ambiguous,
                "overall_success_rate": round(verified_successes / total, 3) if total > 0 else 1.0,
                "recovery_rate": self.get_recovery_rate(),
                "average_duration_seconds": self.get_average_duration(),
                "agent_metrics": self.get_agent_metrics(),
                "tool_metrics": self.get_tool_metrics(),
            }

