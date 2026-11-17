"""core/intelligence/types.py — Data Contracts & Enums for Model & Intelligence Gateway.

Defines:
- ModelProviderType: Enumeration of supported backend providers.
- ModelCapability: Granular capability tags for intelligence routing.
- RoutingPolicy: Operational routing policies (privacy, cost, local-first, performance).
- CircuitState: Circuit breaker operational states.
- TokenUsage, ToolCall, ChatMessage: Normalized LLM transaction contracts.
- ModelMetadata: Descriptive and performance registry tracking record.
- ModelRequest, ModelResponse, StreamChunk: Unified input/output payloads.
- Normalized exception hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelProviderType(str, Enum):
    """Supported backend model provider categories."""
    OLLAMA = "OLLAMA"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    LOCAL_CUSTOM = "LOCAL_CUSTOM"
    GEMINI = "GEMINI"


class ModelCapability(str, Enum):
    """Functional model capabilities matched by the router."""
    CHAT = "CHAT"
    STREAMING = "STREAMING"
    TOOL_CALLING = "TOOL_CALLING"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    VISION = "VISION"
    LONG_CONTEXT = "LONG_CONTEXT"
    CODE_SPECIALIZED = "CODE_SPECIALIZED"


class RoutingPolicy(str, Enum):
    """High-level dispatch policies for model routing."""
    LOCAL_FIRST = "LOCAL_FIRST"
    CLOUD_PREFERRED = "CLOUD_PREFERRED"
    PRIVACY_STRICT = "PRIVACY_STRICT"
    COST_OPTIMIZED = "COST_OPTIMIZED"
    PERFORMANCE_OPTIMIZED = "PERFORMANCE_OPTIMIZED"
    MANUAL = "MANUAL"


class CircuitState(str, Enum):
    """Circuit breaker operational state."""
    CLOSED = "CLOSED"      # Normal healthy operation
    OPEN = "OPEN"          # Provider failing, calls fast-fail without network request
    HALF_OPEN = "HALF_OPEN"# Trial probe after recovery timeout


@dataclass
class TokenUsage:
    """Token consumption accounting."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class ToolCall:
    """Normalized tool/function invocation emitted by a model."""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ChatMessage:
    """Normalized multi-turn message exchanged with an LLM."""
    role: str  # "system", "user", "assistant", "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class ModelMetadata:
    """Metadata and telemetry tracking for registered model configurations."""
    model_id: str
    provider_type: ModelProviderType
    display_name: str = ""
    context_window: int = 8192
    is_local: bool = True
    capabilities: set[ModelCapability] = field(default_factory=set)
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    is_enabled: bool = True
    latency_p50_ms: float = 100.0

    # Operational statistics
    request_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0

    @property
    def average_latency_ms(self) -> float:
        if self.request_count == 0:
            return self.latency_p50_ms
        return self.total_latency_ms / self.request_count

    @property
    def error_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.error_count / self.request_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider_type": self.provider_type.value,
            "display_name": self.display_name or self.model_id,
            "context_window": self.context_window,
            "is_local": self.is_local,
            "capabilities": [c.value for c in self.capabilities],
            "input_cost_per_million": self.input_cost_per_million,
            "output_cost_per_million": self.output_cost_per_million,
            "is_enabled": self.is_enabled,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "average_latency_ms": round(self.average_latency_ms, 2),
        }


@dataclass
class ModelRequest:
    """Provider-neutral model execution request."""
    messages: list[ChatMessage] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int | None = None
    response_schema: dict[str, Any] | None = None
    required_capabilities: set[ModelCapability] = field(default_factory=set)
    is_private: bool = False
    preferred_model: str | None = None
    stream: bool = False
    timeout_seconds: float = 30.0

    @property
    def estimated_prompt_tokens(self) -> int:
        """Heuristic calculation: ~4 chars per token."""
        total_chars = sum(len(m.content) for m in self.messages)
        return max(1, total_chars // 4)


@dataclass
class ModelResponse:
    """Normalized model execution response."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    model_id: str = ""
    provider: str = ""
    finish_reason: str = "stop"  # "stop", "tool_calls", "length"
    duration_ms: float = 0.0
    structured_data: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "usage": self.usage.to_dict(),
            "model_id": self.model_id,
            "provider": self.provider,
            "finish_reason": self.finish_reason,
            "duration_ms": round(self.duration_ms, 2),
            "structured_data": self.structured_data,
        }


@dataclass
class StreamChunk:
    """Individual incremental response token emitted during streaming."""
    text_delta: str = ""
    tool_calls_delta: list[ToolCall] | None = None
    is_final: bool = False
    finish_reason: str | None = None


# ── Normalized Exception Hierarchy ───────────────────────────────────────────

class IntelligenceError(Exception):
    """Base exception for all OPTIMUS Intelligence Gateway failures."""


class ProviderError(IntelligenceError):
    """Failure reported by the underlying model provider or API transport."""
    def __init__(self, message: str, provider: str = "", status_code: int | None = None) -> None:
        super().__init__(f"[{provider}] {message}" if provider else message)
        self.provider = provider
        self.status_code = status_code


class TimeoutError(IntelligenceError):
    """Request exceeded configured timeout budget."""


class RateLimitError(ProviderError):
    """Provider API rejected request due to rate limit / 429 quota exhaustion."""


class PrivacyViolationError(IntelligenceError):
    """Request contains private context but no local model was permitted or available."""


class CircuitOpenError(IntelligenceError):
    """Provider circuit breaker is OPEN due to repeated consecutive errors."""


class NoAvailableModelError(IntelligenceError):
    """Router could not find any enabled model satisfying request constraints."""
