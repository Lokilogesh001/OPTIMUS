"""core/intelligence/circuit_breaker.py — Fault Tolerance Circuit Breakers for Model Providers.

Implements the standard Circuit Breaker design pattern:
- CLOSED: Normal operation, routing allowed.
- OPEN: Provider unavailable after repeated failures. Requests fail fast without network strain.
- HALF_OPEN: Test probe after cooldown interval. Single success restores health.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.intelligence.types import CircuitOpenError, CircuitState


class CircuitBreaker:
    """Thread-safe circuit breaker protecting providers from cascading failure loops."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        half_open_success_threshold: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_success_threshold = half_open_success_threshold

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_state_change = time.time()
        self._last_failure_time = 0.0
        self._last_error: str | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """Evaluate and return current operational state, automatically checking cooldown timers."""
        with self._lock:
            now = time.time()
            if self._state == CircuitState.OPEN:
                if (now - self._last_state_change) >= self.recovery_timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._consecutive_successes = 0
                    self._last_state_change = now
            return self._state

    def allow_request(self) -> bool:
        """Check whether a request is permitted to proceed to the provider."""
        current_state = self.state
        return current_state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def check_request(self) -> None:
        """Raise CircuitOpenError if circuit is currently open."""
        if not self.allow_request():
            remaining = max(0.0, self.recovery_timeout_seconds - (time.time() - self._last_state_change))
            raise CircuitOpenError(
                f"Provider '{self.name}' circuit breaker is OPEN due to repeated errors. "
                f"Cooldown active ({remaining:.1f}s remaining). Last error: {self._last_error}"
            )

    def record_success(self) -> None:
        """Register a successful provider invocation."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._last_state_change = time.time()
            elif self._state == CircuitState.CLOSED:
                self._consecutive_failures = 0

    def record_failure(self, error: Exception | str | None = None) -> None:
        """Register a failed provider invocation and trip circuit if threshold exceeded."""
        with self._lock:
            now = time.time()
            self._consecutive_failures += 1
            self._last_failure_time = now
            self._last_error = str(error) if error else "Unknown error"

            err_str = self._last_error.lower()
            is_unrecoverable = any(kw in err_str for kw in ("insufficient_quota", "credit_balance_exhausted", "quota", "auth failed", "invalid api key"))

            if self._state == CircuitState.HALF_OPEN or is_unrecoverable:
                # Immediate trip back to OPEN on probe failure or unrecoverable error
                self._state = CircuitState.OPEN
                self._last_state_change = now
            elif self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change = now

    def reset(self) -> None:
        """Manually force circuit breaker back to healthy CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._last_state_change = time.time()
            self._last_error = None

    def get_status(self) -> dict[str, Any]:
        """Telemetry diagnostics dictionary."""
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout_seconds,
                "last_error": self._last_error,
            }
