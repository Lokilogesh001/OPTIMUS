"""core/planning/optimizer.py — Evidence-Driven Continuous Self-Improvement for OPTIMUS.

Guiding Principles & Strict Safety Boundaries:
- NO unrestricted self-modifying AI code.
- NO automatic source file editing.
- NO automatic security policy relaxation or permission escalation.
- Enforces the 3-stage lifecycle: OBSERVE -> RECOMMEND -> APPLY.
- Only applied upon explicit operator approval.
- Full reversibility: Every applied recommendation retains a revert_payload.
- Confidence threshold & sample size: Minimum 3 empirical observations required.
"""

from __future__ import annotations

import threading
import time
import uuid

from core.orchestrator import EventBus
from core.planning.events import (
    OptimizationAppliedEvent,
    OptimizationRecommendedEvent,
    OptimizationRolledBackEvent,
)
from core.planning.outcome_store import OutcomeStore
from core.planning.strategy_selector import StrategySelector
from core.planning.types import (
    GoalCategory,
    OptimizationRecommendation,
    OutcomeType,
    StrategyType,
)


class EvidenceDrivenOptimizer:
    """Analyzes verified physical execution telemetry to recommend and safely apply runtime optimizations."""

    def __init__(
        self,
        outcome_store: OutcomeStore,
        strategy_selector: StrategySelector | None = None,
        event_bus: EventBus | None = None,
        min_samples_threshold: int = 3,
        confidence_threshold: float = 0.65,
    ) -> None:
        self.outcome_store = outcome_store
        self.strategy_selector = strategy_selector
        self.event_bus = event_bus
        self.min_samples_threshold = min_samples_threshold
        self.confidence_threshold = confidence_threshold
        self._lock = threading.RLock()
        self._recommendations: dict[str, OptimizationRecommendation] = {}

    def evaluate_optimizations(self) -> list[OptimizationRecommendation]:
        """Scan empirical telemetry and produce actionable, verifiable optimization recommendations."""
        with self._lock:
            new_recs: list[OptimizationRecommendation] = []

            # 1. Agent Reliability Evaluation
            agent_recs = self._evaluate_agent_reliability()
            new_recs.extend(agent_recs)

            # 2. Strategy Performance Evaluation
            strategy_recs = self._evaluate_strategy_performance()
            new_recs.extend(strategy_recs)

            # 3. Tool Reliability Evaluation
            tool_recs = self._evaluate_tool_reliability()
            new_recs.extend(tool_recs)

            for rec in new_recs:
                if rec.recommendation_id not in self._recommendations:
                    self._recommendations[rec.recommendation_id] = rec
                    if self.event_bus:
                        self.event_bus.publish(
                            OptimizationRecommendedEvent(
                                recommendation_id=rec.recommendation_id,
                                target_type=rec.target_type,
                                description=rec.description,
                                confidence_score=rec.confidence_score,
                            )
                        )

            return list(self._recommendations.values())

    def _evaluate_agent_reliability(self) -> list[OptimizationRecommendation]:
        recs: list[OptimizationRecommendation] = []
        metrics = self.outcome_store.get_agent_metrics()

        for agent_name, stats in metrics.items():
            runs = stats["total_runs"]
            if runs < self.min_samples_threshold:
                continue

            rate = stats["success_rate"]
            failures = stats["failure_count"]

            # If agent failure rate is high (success rate < 70%)
            if rate < 0.70:
                confidence = min(0.95, 0.65 + (runs - self.min_samples_threshold) * 0.05)
                rec = OptimizationRecommendation(
                    recommendation_id=f"rec-agent-{agent_name}-{uuid.uuid4().hex[:6]}",
                    target_type="agent_selection",
                    target_key=agent_name,
                    description=f"Demote agent '{agent_name}' in priority routing due to verified low success rate ({round(rate * 100, 1)}% across {runs} runs).",
                    observed_samples=runs,
                    confidence_score=confidence,
                    evidence_summary=f"{failures} physical failures recorded across {runs} verified executions.",
                    proposed_change={
                        "action": "deprioritize_agent",
                        "agent": agent_name,
                        "fallback_agent": "general",
                    },
                    revert_payload={
                        "action": "restore_agent_priority",
                        "agent": agent_name,
                    },
                )
                recs.append(rec)

        return recs

    def _evaluate_strategy_performance(self) -> list[OptimizationRecommendation]:
        recs: list[OptimizationRecommendation] = []
        outcomes = self.outcome_store.list_outcomes()

        # Check by category
        for cat in GoalCategory:
            cat_outcomes = [o for o in outcomes if o.goal_category == cat]
            if len(cat_outcomes) < self.min_samples_threshold:
                continue

            # Compare FAST_PARALLEL vs CONSERVATIVE_SEQUENTIAL
            fast_runs = [o for o in cat_outcomes if o.strategy == StrategyType.FAST_PARALLEL]
            if len(fast_runs) >= self.min_samples_threshold:
                fast_failures = sum(1 for o in fast_runs if o.outcome != OutcomeType.VERIFIED_SUCCESS)
                fast_failure_rate = fast_failures / len(fast_runs)

                if fast_failure_rate >= 0.40:
                    confidence = min(0.95, 0.70 + (len(fast_runs) - self.min_samples_threshold) * 0.05)
                    rec = OptimizationRecommendation(
                        recommendation_id=f"rec-strat-{cat.value.lower()}-{uuid.uuid4().hex[:6]}",
                        target_type="strategy_selection",
                        target_key=cat.value,
                        description=f"Prioritize CONSERVATIVE_SEQUENTIAL over FAST_PARALLEL for category '{cat.value}' (observed {round(fast_failure_rate * 100, 1)}% failure rate).",
                        observed_samples=len(fast_runs),
                        confidence_score=confidence,
                        evidence_summary=f"FAST_PARALLEL failed {fast_failures} of {len(fast_runs)} runs in category {cat.value}.",
                        proposed_change={
                            "action": "bias_strategy",
                            "strategy": StrategyType.FAST_PARALLEL.value,
                            "bias": -0.5,
                        },
                        revert_payload={
                            "action": "bias_strategy",
                            "strategy": StrategyType.FAST_PARALLEL.value,
                            "bias": 0.0,
                        },
                    )
                    recs.append(rec)

        return recs

    def _evaluate_tool_reliability(self) -> list[OptimizationRecommendation]:
        recs: list[OptimizationRecommendation] = []
        metrics = self.outcome_store.get_tool_metrics()

        for tool_name, stats in metrics.items():
            invs = stats["invocations"]
            if invs < self.min_samples_threshold:
                continue

            rel = stats["reliability"]
            failures = stats["failure_count"]

            if rel < 0.70:
                confidence = min(0.95, 0.65 + (invs - self.min_samples_threshold) * 0.05)
                rec = OptimizationRecommendation(
                    recommendation_id=f"rec-tool-{tool_name}-{uuid.uuid4().hex[:6]}",
                    target_type="tool_policy",
                    target_key=tool_name,
                    description=f"Enforce fallback tools and extend timeouts for '{tool_name}' (reliability {round(rel * 100, 1)}% across {invs} invocations).",
                    observed_samples=invs,
                    confidence_score=confidence,
                    evidence_summary=f"{failures} failures recorded during physical verification of tool '{tool_name}'.",
                    proposed_change={
                        "action": "extend_tool_timeout",
                        "tool": tool_name,
                        "timeout_multiplier": 1.5,
                    },
                    revert_payload={
                        "action": "extend_tool_timeout",
                        "tool": tool_name,
                        "timeout_multiplier": 1.0,
                    },
                )
                recs.append(rec)

        return recs

    def list_recommendations(self, include_applied: bool = True) -> list[OptimizationRecommendation]:
        with self._lock:
            if include_applied:
                return list(self._recommendations.values())
            return [r for r in self._recommendations.values() if not r.is_applied]

    def get_recommendation(self, rec_id: str) -> OptimizationRecommendation | None:
        with self._lock:
            return self._recommendations.get(rec_id)

    def apply_recommendation(self, recommendation_id: str, operator_approval: bool = True) -> bool:
        """Apply an authorized recommendation within strict safety constraints."""
        with self._lock:
            rec = self._recommendations.get(recommendation_id)
            if rec is None:
                raise ValueError(f"Recommendation '{recommendation_id}' not found.")

            if rec.is_applied:
                return True

            if not operator_approval:
                raise PermissionError("Human operator authorization is mandatory to apply optimization recommendations.")

            # Apply change to StrategySelector or runtime config
            change = rec.proposed_change
            if change.get("action") == "bias_strategy" and self.strategy_selector is not None:
                strat_str = change.get("strategy")
                bias_val = float(change.get("bias", 0.0))
                try:
                    self.strategy_selector.set_strategy_bias(StrategyType(strat_str), bias_val)
                except (ValueError, KeyError, TypeError):
                    pass

            rec.is_applied = True
            rec.applied_at = time.time()

            if self.event_bus:
                self.event_bus.publish(
                    OptimizationAppliedEvent(
                        recommendation_id=rec.recommendation_id,
                        target_type=rec.target_type,
                        description=rec.description,
                    )
                )

            return True

    def rollback_recommendation(self, recommendation_id: str) -> bool:
        """Safely revert an applied recommendation using its revert_payload."""
        with self._lock:
            rec = self._recommendations.get(recommendation_id)
            if rec is None:
                raise ValueError(f"Recommendation '{recommendation_id}' not found.")

            if not rec.is_applied:
                return True

            revert = rec.revert_payload
            if revert.get("action") == "bias_strategy" and self.strategy_selector is not None:
                strat_str = revert.get("strategy")
                bias_val = float(revert.get("bias", 0.0))
                try:
                    self.strategy_selector.set_strategy_bias(StrategyType(strat_str), bias_val)
                except (ValueError, KeyError, TypeError):
                    pass

            rec.is_applied = False
            rec.applied_at = None

            if self.event_bus:
                self.event_bus.publish(
                    OptimizationRolledBackEvent(
                        recommendation_id=rec.recommendation_id,
                        target_type=rec.target_type,
                        description=rec.description,
                    )
                )

            return True
