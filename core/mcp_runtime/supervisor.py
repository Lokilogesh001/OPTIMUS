"""core/mcp_runtime/supervisor.py — Server supervisor, watchdog, and circuit breaker.

Enforces:
- Circuit breaker: 3 consecutive failures -> TRIPPED state for 30.0s cooldown
- Half-open probationary probe after cooldown expires
- Manual circuit breaker reset
- Health tracking and metrics
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.mcp_runtime.transports.base import MCPTransport
from core.mcp_runtime.types import MCPServerConfig, MCPServerState, MCPResponse, MCPErrorCode

logger = logging.getLogger("optimus.mcp_runtime.supervisor")


class CircuitState(str, Enum):
    CLOSED = "closed"        # Normal healthy operation
    TRIPPED = "tripped"      # Tripped open after repeated failures, rejecting requests
    HALF_OPEN = "half_open"  # Probationary testing state


class CircuitBreakerTripped(Exception):
    """Raised when an operation is attempted on a tripped circuit breaker."""


class CircuitBreaker:
    """Protects host orchestrator from cascading failures during server unresponsiveness."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.tripped_at: Optional[float] = None
        self.last_failure_reason: Optional[str] = None

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.tripped_at = None
        self.last_failure_reason = None

    def record_failure(self, reason: str = "") -> None:
        self.consecutive_failures += 1
        self.last_failure_reason = reason
        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.TRIPPED
            self.tripped_at = time.monotonic()
            logger.warning(
                "Circuit breaker TRIPPED after %d consecutive failures. Cooldown: %.1fs. Reason: %s",
                self.consecutive_failures, self.cooldown_seconds, reason
            )

    def can_attempt(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.TRIPPED:
            if self.tripped_at and (time.monotonic() - self.tripped_at) >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker entered HALF_OPEN state (probationary attempt)")
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True

        return False

    def reset(self) -> None:
        """Manual operator reset."""
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.tripped_at = None
        self.last_failure_reason = None
        logger.info("Circuit breaker manually reset to CLOSED")


@dataclass
class ServerHealthMetrics:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    restart_count: int = 0
    last_response_time_ms: float = 0.0
    last_active_timestamp: float = 0.0


class ServerSupervisor:
    """Supervises a single MCP server lifecycle, health checks, and circuit-breaker."""

    def __init__(self, config: MCPServerConfig, transport: MCPTransport):
        self.config = config
        self.transport = transport
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=config.max_restarts,
            cooldown_seconds=30.0,
        )
        self.state = MCPServerState.STOPPED
        self.metrics = ServerHealthMetrics()
        self.last_error: Optional[str] = None

    def start(self) -> bool:
        self.state = MCPServerState.STARTING
        success = self.transport.connect()
        if success:
            self.state = MCPServerState.RUNNING
            self.metrics.last_active_timestamp = time.monotonic()
            self.circuit_breaker.record_success()
            return True
        else:
            self.state = MCPServerState.FAILED
            self.last_error = "Transport connection failed"
            self.circuit_breaker.record_failure(self.last_error)
            return False

    def stop(self) -> None:
        self.state = MCPServerState.STOPPED
        self.transport.disconnect()

    def restart(self) -> bool:
        logger.info("Restarting MCP server '%s'", self.config.server_id)
        self.stop()
        self.metrics.restart_count += 1
        return self.start()

    def execute_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ) -> MCPResponse:
        """Executes a request guarded by circuit breaker and error accounting."""
        if not self.circuit_breaker.can_attempt():
            self.state = MCPServerState.TRIPPED
            return MCPResponse(
                id=None,
                error={
                    "code": MCPErrorCode.SERVER_TIMEOUT.value,
                    "message": (
                        f"Circuit breaker TRIPPED for server '{self.config.server_id}'. "
                        f"Cooldown remaining: {max(0, 30.0 - (time.monotonic() - (self.circuit_breaker.tripped_at or 0))):.1f}s"
                    )
                }
            )

        start_time = time.monotonic()
        self.metrics.total_requests += 1

        try:
            resp = self.transport.send_request(method, params, timeout=timeout)
            duration_ms = (time.monotonic() - start_time) * 1000.0
            self.metrics.last_response_time_ms = duration_ms
            self.metrics.last_active_timestamp = time.monotonic()

            if resp.is_error:
                self.metrics.failed_requests += 1
                self.circuit_breaker.record_failure(str(resp.error))
                if self.circuit_breaker.state == CircuitState.TRIPPED:
                    self.state = MCPServerState.TRIPPED
            else:
                self.metrics.successful_requests += 1
                self.circuit_breaker.record_success()
                self.state = MCPServerState.RUNNING

            return resp

        except Exception as e:
            self.metrics.failed_requests += 1
            self.last_error = str(e)
            self.circuit_breaker.record_failure(str(e))
            if self.circuit_breaker.state == CircuitState.TRIPPED:
                self.state = MCPServerState.TRIPPED
            return MCPResponse(
                id=None,
                error={"code": MCPErrorCode.INTERNAL_ERROR.value, "message": str(e)}
            )
