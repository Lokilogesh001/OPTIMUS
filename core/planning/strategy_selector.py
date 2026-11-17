"""core/planning/strategy_selector.py — Adaptive Execution Strategy Selector for OPTIMUS.

Selects the optimal strategy based on:
- Risk tier (LOW, MEDIUM, HIGH, CRITICAL).
- Plan complexity score and dependency topology.
- Presence of destructive actions or mandatory approval gates.
- Empirical performance statistics from OutcomeStore.
- Explicit operator overrides.
"""

from __future__ import annotations

from core.planning.outcome_store import OutcomeStore
from core.planning.types import (
    GoalCategory,
    GoalDecomposition,
    PlanRiskLevel,
    PlanStep,
    StrategyType,
)


class StrategySelector:
    """Evaluates goal attributes, step structures, and empirical outcomes to pick an optimal strategy."""

    def __init__(self, outcome_store: OutcomeStore | None = None) -> None:
        self.outcome_store = outcome_store
        self._override: StrategyType | None = None
        self._strategy_biases: dict[str, float] = {}  # Runtime strategy adjustments from optimizer

    def set_override(self, strategy: StrategyType | None) -> None:
        """Set a temporary manual override for strategy selection."""
        self._override = strategy

    def get_override(self) -> StrategyType | None:
        return self._override

    def set_strategy_bias(self, strategy: StrategyType, bias: float) -> None:
        """Apply a preference bias weight (e.g., from optimizer recommendations)."""
        self._strategy_biases[strategy.value] = bias

    def clear_biases(self) -> None:
        self._strategy_biases.clear()

    def select_strategy(
        self,
        decomposition: GoalDecomposition,
        steps: list[PlanStep],
    ) -> StrategyType:
        """Deterministically select an execution strategy adhering to safety boundaries."""
        # 1. Manual override takes highest precedence
        if self._override is not None:
            return self._override

        # 2. Strict Safety Boundaries: High or Critical risk MUST use CONSERVATIVE_SEQUENTIAL
        if decomposition.risk_level in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL) or decomposition.is_destructive:
            return StrategyType.CONSERVATIVE_SEQUENTIAL

        if any(step.is_destructive or step.requires_approval for step in steps):
            return StrategyType.CONSERVATIVE_SEQUENTIAL

        # 3. Trivial / Single-step or focused tasks use SPECIALIZED_MINIMAL
        if len(steps) == 1 and decomposition.complexity_score <= 3.0:
            return StrategyType.SPECIALIZED_MINIMAL

        # 4. Check Empirical History from OutcomeStore if available
        if self.outcome_store is not None:
            empirical_choice = self._evaluate_empirical_history(decomposition.category)
            if empirical_choice is not None:
                return empirical_choice

        # 5. Check Recovery or System Diagnostics volatility -> ROBUST_FAULT_TOLERANT
        if decomposition.category in (GoalCategory.RECOVERY, GoalCategory.SYSTEM_OPS) and decomposition.complexity_score > 4.0:
            return StrategyType.ROBUST_FAULT_TOLERANT

        # 6. Parallelizable multi-step tasks with LOW/MEDIUM risk -> FAST_PARALLEL
        parallelizable_count = sum(1 for s in steps if s.is_parallelizable or not s.depends_on)
        if len(steps) >= 2 and parallelizable_count >= 2 and decomposition.risk_level == PlanRiskLevel.LOW:
            # Check optimizer bias if FAST_PARALLEL has negative bias
            if self._strategy_biases.get(StrategyType.FAST_PARALLEL.value, 0.0) < -0.2:
                return StrategyType.CONSERVATIVE_SEQUENTIAL
            return StrategyType.FAST_PARALLEL

        # Default fallback: Conservative Sequential
        return StrategyType.CONSERVATIVE_SEQUENTIAL

    def _evaluate_empirical_history(self, category: GoalCategory) -> StrategyType | None:
        """Check historical success rates across strategies for this goal category."""
        if self.outcome_store is None:
            return None

        outcomes = [o for o in self.outcome_store.list_outcomes() if o.goal_category == category]
        if len(outcomes) < 3:
            return None  # Insufficient sample size

        # Tally success rates by strategy
        strategy_stats: dict[StrategyType, dict[str, int]] = {}
        for o in outcomes:
            st = o.strategy
            if st not in strategy_stats:
                strategy_stats[st] = {"total": 0, "success": 0}
            strategy_stats[st]["total"] += 1
            if o.outcome.value == "VERIFIED_SUCCESS":
                strategy_stats[st]["success"] += 1

        best_strategy = None
        best_rate = -1.0
        for st, counts in strategy_stats.items():
            if counts["total"] >= 2:
                rate = counts["success"] / counts["total"]
                # Apply optimizer bias if present
                rate += self._strategy_biases.get(st.value, 0.0)
                if rate > best_rate and rate >= 0.70:
                    best_rate = rate
                    best_strategy = st

        return best_strategy

    def get_selection_explanation(
        self,
        decomposition: GoalDecomposition,
        steps: list[PlanStep],
        chosen: StrategyType,
    ) -> str:
        """Provide human-interpretable rationale for the chosen strategy."""
        if self._override is not None:
            return f"Manual operator override explicitly enforced {chosen.value}."
        if decomposition.is_destructive or decomposition.risk_level in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL):
            return "Safety policy enforced CONSERVATIVE_SEQUENTIAL due to destructive actions or high risk tier."
        if chosen == StrategyType.SPECIALIZED_MINIMAL:
            return "Goal complexity is minimal (single focused step); selected SPECIALIZED_MINIMAL for lowest latency."
        if chosen == StrategyType.FAST_PARALLEL:
            return "Goal consists of independent parallelizable steps with low risk tier; selected FAST_PARALLEL."
        if chosen == StrategyType.ROBUST_FAULT_TOLERANT:
            return "Goal involves sensitive recovery/system operations or historical volatility; selected ROBUST_FAULT_TOLERANT."
        return "Standard sequential execution selected for predictability and verifiable postcondition isolation."
