"""core/intelligence/__init__.py — OPTIMUS Model & Intelligence Gateway Package.

Provides a unified, provider-neutral model interface supporting Ollama, OpenAI,
Anthropic, local inference servers, adaptive capability routing, privacy boundaries,
and fault-tolerant circuit breaking.
"""

from __future__ import annotations

from core.intelligence.circuit_breaker import CircuitBreaker
from core.intelligence.gateway import IntelligenceGateway, get_intelligence_gateway
from core.intelligence.providers import (
    AnthropicProvider,
    LocalCustomProvider,
    ModelProvider,
    OllamaProvider,
    OpenAIProvider,
)
from core.intelligence.registry import ModelRegistry
from core.intelligence.router import ModelRouter
from core.intelligence.types import (
    CircuitOpenError,
    CircuitState,
    IntelligenceError,
    ModelCapability,
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    ModelResponse,
    NoAvailableModelError,
    PrivacyViolationError,
    ProviderError,
    RateLimitError,
    RoutingPolicy,
    StreamChunk,
    TimeoutError,
    TokenUsage,
    ToolCall,
)

__all__ = [
    "AnthropicProvider",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "IntelligenceError",
    "IntelligenceGateway",
    "LocalCustomProvider",
    "ModelCapability",
    "ModelMetadata",
    "ModelProvider",
    "ModelProviderType",
    "ModelRegistry",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "NoAvailableModelError",
    "OllamaProvider",
    "OpenAIProvider",
    "PrivacyViolationError",
    "ProviderError",
    "RateLimitError",
    "RoutingPolicy",
    "StreamChunk",
    "TimeoutError",
    "TokenUsage",
    "ToolCall",
    "get_intelligence_gateway",
]
