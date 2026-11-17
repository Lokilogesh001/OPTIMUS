"""core/intelligence/providers/base.py — Abstract Base Provider Contract for OPTIMUS.

Defines the common provider interface implemented by all concrete model adapters
(Ollama, OpenAI, Anthropic, Local Custom, etc.) with integrated circuit breaking,
retry with bounded exponential backoff, and sensitive credential protection.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import Any

from core.intelligence.circuit_breaker import CircuitBreaker
from core.intelligence.types import (
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    ModelResponse,
    ProviderError,
    RateLimitError,
    StreamChunk,
    TimeoutError,
)

logger = logging.getLogger("optimus.intelligence.provider")


class ModelProvider(ABC):
    """Abstract base class for all backend model service providers."""

    name: str
    provider_type: ModelProviderType
    is_local: bool
    circuit_breaker: CircuitBreaker

    def __init__(
        self,
        name: str,
        provider_type: ModelProviderType,
        is_local: bool = True,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.name = name
        self.provider_type = provider_type
        self.is_local = is_local
        self.circuit_breaker = circuit_breaker or CircuitBreaker(name=name)

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute a synchronous multi-turn completion request."""
        raise NotImplementedError

    @abstractmethod
    def stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        """Yield token chunks incrementally as they arrive from the model."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if provider endpoint is currently responsive."""
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[ModelMetadata]:
        """Return catalogue of models hosted or accessible via this provider."""
        raise NotImplementedError

    def execute_with_resilience(
        self,
        request: ModelRequest,
        operation: Callable[[ModelRequest], Any],
        max_retries: int = 2,
        initial_backoff: float = 0.5,
    ) -> Any:
        """Execute operation guarded by the circuit breaker and bounded exponential retry."""
        self.circuit_breaker.check_request()

        attempt = 0
        backoff = initial_backoff
        last_exception: Exception | None = None

        while attempt <= max_retries:
            try:
                result = operation(request)
                self.circuit_breaker.record_success()
                return result
            except (RateLimitError, TimeoutError) as recoverable_err:
                last_exception = recoverable_err
                attempt += 1
                if attempt > max_retries:
                    self.circuit_breaker.record_failure(recoverable_err)
                    raise
                time.sleep(backoff)
                backoff *= 2.0
            except Exception as non_rec_err:
                self.circuit_breaker.record_failure(non_rec_err)
                raise

        if last_exception:
            raise last_exception
        raise ProviderError(f"Operation failed after {max_retries} retries", provider=self.name)

    @staticmethod
    def mask_secret(secret: str | None) -> str:
        """Safe display of API credentials without leaking key material."""
        if not secret:
            return "<none>"
        if len(secret) <= 8:
            return "***"
        return f"{secret[:4]}...{secret[-4:]}"
