"""core/intelligence/gateway.py — Central Model & Intelligence Gateway for OPTIMUS.

The primary entrypoint unifying LLM access across all specialized agents,
the Central Orchestrator, and Mark-LVII desktop/voice interfaces.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from typing import Any

from core.intelligence.providers.base import ModelProvider
from core.intelligence.registry import ModelRegistry
from core.intelligence.router import ModelRouter
from core.intelligence.types import (
    ChatMessage,
    ModelCapability,
    ModelMetadata,
    ModelRequest,
    ModelResponse,
    NoAvailableModelError,
    RoutingPolicy,
    StreamChunk,
)

logger = logging.getLogger("optimus.intelligence.gateway")


class ProviderHealthCache:
    """Thread-safe TTL cache for provider health checks to prevent redundant network I/O."""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[bool, float]] = {}
        self._lock = threading.Lock()

    def get_health(self, provider: ModelProvider, force_refresh: bool = False) -> bool:
        """Check provider health using cached result if fresh, or perform health check."""
        # Fast path: If circuit breaker is OPEN, provider is unhealthy without network check
        if hasattr(provider, "circuit_breaker") and provider.circuit_breaker.state.value == "OPEN":
            return False

        now = time.time()
        with self._lock:
            if not force_refresh and provider.name in self._cache:
                status, timestamp = self._cache[provider.name]
                if now - timestamp < self.ttl_seconds:
                    return status

        try:
            status = provider.health_check()
        except Exception:
            status = False

        with self._lock:
            self._cache[provider.name] = (status, now)
        return status

    def invalidate(self, provider_name: str | None = None) -> None:
        """Clear cache for a specific provider or all providers."""
        with self._lock:
            if provider_name:
                self._cache.pop(provider_name, None)
            else:
                self._cache.clear()


class IntelligenceGateway:
    """The central unified model and intelligence gateway for OPTIMUS."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        router: ModelRouter | None = None,
        policy: RoutingPolicy = RoutingPolicy.CLOUD_PREFERRED,
    ) -> None:
        self.registry = registry or ModelRegistry()
        self.router = router or ModelRouter(self.registry, default_policy=policy)
        self.health_cache = ProviderHealthCache(ttl_seconds=60.0)

    @classmethod
    def instance(cls) -> IntelligenceGateway:
        return get_intelligence_gateway()

    def set_policy(self, policy: RoutingPolicy) -> None:
        """Update active routing policy."""
        self.router.set_policy(policy)

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Route and execute a completion request through the optimal provider with failover."""
        # Auto-infer required capabilities from request
        if request.tools and ModelCapability.TOOL_CALLING not in request.required_capabilities:
            request.required_capabilities.add(ModelCapability.TOOL_CALLING)
        if request.response_schema and ModelCapability.STRUCTURED_OUTPUT not in request.required_capabilities:
            request.required_capabilities.add(ModelCapability.STRUCTURED_OUTPUT)

        def _execute(candidate: ModelMetadata, req: ModelRequest) -> ModelResponse:
            provider = self._get_provider_for_model(candidate)
            if not provider:
                raise NoAvailableModelError(f"No provider found for model '{candidate.model_id}'")
            return provider.complete(req)

        return self.router.execute_with_fallback(request, _execute)

    def stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        """Stream response tokens from the selected model candidate with safe stream cleanup."""
        candidates = self.router.select_models(request)
        if not candidates:
            raise NoAvailableModelError("No eligible model found for streaming.")

        candidate = candidates[0]
        provider = self._get_provider_for_model(candidate)
        if not provider:
            raise NoAvailableModelError(f"No provider found for model '{candidate.model_id}'")

        req_copy = ModelRequest(
            messages=request.messages,
            tools=request.tools,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            response_schema=request.response_schema,
            required_capabilities=request.required_capabilities,
            is_private=request.is_private,
            preferred_model=candidate.model_id,
            stream=True,
            timeout_seconds=request.timeout_seconds,
        )
        stream_iter = provider.stream(req_copy)
        try:
            for chunk in stream_iter:
                yield chunk
        finally:
            if hasattr(stream_iter, "close"):
                try:
                    stream_iter.close()
                except Exception:
                    pass

    def ask(
        self,
        prompt: str,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        is_private: bool = False,
        response_schema: dict[str, Any] | None = None,
        preferred_model: str | None = None,
    ) -> ModelResponse:
        """Convenience method for simple single-turn prompts."""
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))

        request = ModelRequest(
            messages=messages,
            tools=tools or [],
            is_private=is_private,
            response_schema=response_schema,
            preferred_model=preferred_model,
        )
        return self.complete(request)

    def ask_turns(
        self,
        system: str,
        turns: list[dict[str, Any]],
        schemas: list[dict[str, Any]] | None = None,
        is_private: bool = False,
    ) -> dict[str, Any]:
        """Bridge format compatible with Central Orchestrator and Mark-LVII turns."""
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))

        for turn in turns:
            role = str(turn.get("role") or "user")
            content = str(turn.get("text") or turn.get("content") or "")
            if role == "tool":
                results = turn.get("results") or []
                for res in results:
                    messages.append(ChatMessage(
                        role="tool",
                        content=str(res.get("result", "")),
                        tool_call_id=str(res.get("id", "")),
                        name=str(res.get("name", "")),
                    ))
            else:
                messages.append(ChatMessage(role=role, content=content))

        # Convert schemas to tools
        tools = []
        if schemas:
            for s in schemas:
                tools.append(s)

        req = ModelRequest(
            messages=messages,
            tools=tools,
            is_private=is_private,
        )
        resp = self.complete(req)

        return {
            "text": resp.text,
            "calls": [
                {"name": tc.name, "args": tc.arguments, "id": tc.id}
                for tc in resp.tool_calls
            ],
            "model": resp.model_id,
            "provider": resp.provider,
        }

    def get_brain_fn(self, is_private: bool = False) -> Any:
        """Return a brain_fn callable suitable for CentralOrchestrator injection."""
        def _brain(system: str, turns: list[dict[str, Any]], schemas: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            return self.ask_turns(system, turns, schemas, is_private=is_private)
        return _brain

    def _get_provider_for_model(self, model: ModelMetadata) -> ModelProvider | None:
        """Match model metadata to its registered provider instance."""
        for provider in self.registry.list_providers():
            if provider.provider_type == model.provider_type:
                return provider
        return None

    def health_summary(self, fast: bool = True, force_refresh: bool = False) -> dict[str, Any]:
        """Return end-to-end health check of all providers and routing policies."""
        provider_health = {}
        for p in self.registry.list_providers():
            if fast:
                is_healthy = (p.circuit_breaker.state.value != "OPEN")
            else:
                is_healthy = self.health_cache.get_health(p, force_refresh=force_refresh)

            provider_health[p.name] = {
                "type": p.provider_type.value,
                "is_local": p.is_local,
                "is_healthy": is_healthy,
                "circuit_breaker": p.circuit_breaker.get_status(),
            }
        return {
            "active_policy": self.router.policy.value,
            "providers": provider_health,
            "models_count": len(self.registry.list_models(enabled_only=False)),
            "enabled_models": len(self.router.select_models(ModelRequest())),
        }


# Singleton accessor
_GLOBAL_GATEWAY: IntelligenceGateway | None = None


def get_intelligence_gateway(policy: RoutingPolicy = RoutingPolicy.CLOUD_PREFERRED) -> IntelligenceGateway:
    """Retrieve or initialize the global Intelligence Gateway singleton."""
    global _GLOBAL_GATEWAY
    if _GLOBAL_GATEWAY is None:
        _GLOBAL_GATEWAY = IntelligenceGateway(policy=policy)
    return _GLOBAL_GATEWAY
