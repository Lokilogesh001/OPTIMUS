"""core/missions/governance.py — Execution Governance & Safety Guardrails for OPTIMUS.

Implements:
1. GovernanceBudget: Hard operational limits (duration, steps, worker counts, replans).
2. TokenAccountingGovernor: Tracks LLM token usage across agents and enforces budgets (resolving MED-05).
3. SemanticLoopDetector: Detects repetitive tool loops, argument drift, and oscillatory cycles (resolving HIGH-04).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("optimus.missions.governance")


@dataclass
class GovernanceBudget:
    """Hard operational resource boundaries for an autonomous mission."""
    max_mission_duration_seconds: float = 600.0  # 10 minutes default
    max_subtask_duration_seconds: float = 120.0  # 2 minutes default
    max_total_steps: int = 50
    max_replan_attempts: int = 3
    max_concurrent_workers: int = 4
    max_token_budget: int = 100_000


class TokenAccountingGovernor:
    """Tracks cumulative token consumption across specialist agents and enforces budgets.
    
    Resolves MED-05: Prevents runaway agent loops and quota exhaustion.
    """

    def __init__(self, token_budget: int = 100_000) -> None:
        self.token_budget = token_budget
        self.consumed_tokens: int = 0
        self._history: list[dict[str, Any]] = []

    def record_usage(self, agent_name: str, subtask_id: str, tokens: int, model: str = "") -> None:
        """Record token expenditure from an agent execution."""
        self.consumed_tokens += max(0, tokens)
        self._history.append({
            "agent_name": agent_name,
            "subtask_id": subtask_id,
            "tokens": tokens,
            "cumulative": self.consumed_tokens,
            "model": model,
            "timestamp": time.time(),
        })
        if self.is_exceeded():
            logger.warning(
                "TOKEN BUDGET EXCEEDED: Consumed %d tokens (Budget: %d)",
                self.consumed_tokens,
                self.token_budget,
            )

    def is_exceeded(self) -> bool:
        """Check if cumulative tokens exceed authorized budget."""
        return self.consumed_tokens > self.token_budget


class SemanticLoopDetector:
    """Detects repetitive tool invocations and oscillatory cycles across execution.
    
    Resolves HIGH-04:
    - Normalizes dynamic arguments (stripping timestamps, session IDs, random paths).
    - Uses sliding n-gram hashing (n=2, 3, 4) to catch periodic multi-step cycles.
    - Monitors repeated failure signatures.
    """

    def __init__(self, window_size: int = 12, max_cycle_repetitions: int = 2) -> None:
        self.window_size = window_size
        self.max_cycle_repetitions = max_cycle_repetitions
        self._action_history: deque[str] = deque(maxlen=window_size)
        self._failure_signatures: dict[str, int] = {}

    def _normalize_args(self, args: dict[str, Any]) -> str:
        """Normalize arguments to capture semantic intent while stripping dynamic noise."""
        cleaned = {}
        for k, v in sorted(args.items()):
            # Skip dynamic noise keys
            if k.lower() in ("timestamp", "now", "nonce", "request_id", "session_id", "retry_count", "_ts"):
                continue
            # Redact floating numbers that look like timestamps
            v_str = str(v)
            v_str = re.sub(r"\b17\d{8,10}(?:\.\d+)?\b", "[TIMESTAMP]", v_str)
            cleaned[k] = v_str
        try:
            return json.dumps(cleaned, sort_keys=True)
        except (TypeError, ValueError):
            return str(cleaned)

    def record_action(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Record an action and return True if a harmful repetitive loop is detected."""
        norm_args = self._normalize_args(arguments)
        action_sig = f"{tool_name}:{hashlib.sha256(norm_args.encode('utf-8')).hexdigest()[:12]}"
        self._action_history.append(action_sig)

        # Check for immediate repeat (A -> A -> A)
        if len(self._action_history) >= 3:
            if (
                self._action_history[-1] == self._action_history[-2]
                and self._action_history[-2] == self._action_history[-3]
            ):
                logger.warning("LoopDetector: Immediate 3x tool repeat detected for signature '%s'", action_sig)
                return True

        # Check for periodic oscillations (e.g. A -> B -> A -> B)
        history_list = list(self._action_history)
        for n in (2, 3, 4):
            if len(history_list) >= n * 2:
                pattern1 = history_list[-n:]
                pattern2 = history_list[-(n * 2):-n]
                if pattern1 == pattern2:
                    logger.warning("LoopDetector: Periodic cycle (period=%d) detected: %s", n, pattern1)
                    return True

        return False

    def record_failure(self, tool_name: str, error_msg: str) -> bool:
        """Record a failure signature. Returns True if identical failure repeats consecutively."""
        sig = f"{tool_name}:{error_msg[:100]}"
        count = self._failure_signatures.get(sig, 0) + 1
        self._failure_signatures[sig] = count

        if count >= 3:
            logger.warning("LoopDetector: Tool '%s' failed 3 times with identical signature: %s", tool_name, error_msg[:60])
            return True
        return False
