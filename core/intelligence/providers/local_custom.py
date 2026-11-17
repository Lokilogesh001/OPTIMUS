"""core/intelligence/providers/local_custom.py — Local Custom & Mark-LVII Bridge Provider.

Enables embedded local in-process inference, custom offline execution handlers,
and native backward compatibility with Mark-LVII's `core.gemini` and `core.tasks.brain`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator

from core.intelligence.providers.base import ModelProvider
from core.intelligence.types import (
    ModelCapability,
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    ModelResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
)


class LocalCustomProvider(ModelProvider):
    """Custom local inference provider and Mark-LVII internal engine bridge."""

    def __init__(
        self,
        name: str = "local_custom",
        custom_handler: Callable[[ModelRequest], ModelResponse] | None = None,
        custom_streamer: Callable[[ModelRequest], Iterator[StreamChunk]] | None = None,
    ) -> None:
        super().__init__(name=name, provider_type=ModelProviderType.LOCAL_CUSTOM, is_local=True)
        self.custom_handler = custom_handler
        self.custom_streamer = custom_streamer

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute completion via custom handler or Mark-LVII local bridge."""
        if self.custom_handler:
            return self.custom_handler(request)

        start_t = time.perf_counter()

        # Try Mark-LVII core.tasks.brain
        try:
            from core.tasks.brain import ask
            turns = [m.to_dict() for m in request.messages if m.role != "system"]
            system = next((m.content for m in request.messages if m.role == "system"), "")
            res = ask(system, turns, request.tools if request.tools else None)
            text = res.get("text", "")
            tool_calls = [
                ToolCall(id=f"call_{i+1}", name=c.get("name", ""), arguments=c.get("args", {}))
                for i, c in enumerate(res.get("calls", []))
            ]
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            return ModelResponse(
                text=text,
                tool_calls=tool_calls,
                usage=TokenUsage(prompt_tokens=request.estimated_prompt_tokens, completion_tokens=len(text) // 4, total_tokens=request.estimated_prompt_tokens + len(text) // 4),
                model_id="mark-lvii-brain",
                provider=self.name,
                duration_ms=duration_ms,
            )
        except Exception:
            pass

        # Try Mark-LVII core.gemini
        try:
            from core import gemini
            prompt = "\n\n".join(f"{m.role.upper()}: {m.content}" for m in request.messages)
            text = gemini.text(prompt, tier=gemini.FAST) or ""
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            return ModelResponse(
                text=text,
                tool_calls=[],
                usage=TokenUsage(prompt_tokens=request.estimated_prompt_tokens, completion_tokens=len(text) // 4, total_tokens=request.estimated_prompt_tokens + len(text) // 4),
                model_id="mark-lvii-gemini",
                provider=self.name,
                duration_ms=duration_ms,
            )
        except Exception:
            pass

        # Deterministic simulation fallback
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        duration_ms = (time.perf_counter() - start_t) * 1000.0
        structured = None
        if request.response_schema:
            structured = {"status": "ok", "message": f"Processed: {last_user[:50]}"}
            text = json.dumps(structured)
        else:
            text = f"[LocalCustom] Completed request for: {last_user[:80]}"

        return ModelResponse(
            text=text,
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=request.estimated_prompt_tokens, completion_tokens=len(text) // 4, total_tokens=request.estimated_prompt_tokens + len(text) // 4),
            model_id="deterministic-local",
            provider=self.name,
            duration_ms=duration_ms,
            structured_data=structured,
        )

    def stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        """Stream chunks via custom streamer or tokenized words."""
        if self.custom_streamer:
            yield from self.custom_streamer(request)
            return

        resp = self.complete(request)
        words = resp.text.split(" ")
        for i, word in enumerate(words):
            is_last = (i == len(words) - 1)
            yield StreamChunk(
                text_delta=word + (" " if not is_last else ""),
                is_final=is_last,
                finish_reason="stop" if is_last else None,
            )

    def health_check(self) -> bool:
        """Local custom provider is always available."""
        return True

    def list_models(self) -> list[ModelMetadata]:
        """List registered local custom models."""
        return [
            ModelMetadata(
                model_id="mark-lvii-fast",
                provider_type=ModelProviderType.LOCAL_CUSTOM,
                display_name="Mark-LVII Fast Engine",
                context_window=32768,
                is_local=True,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
            ),
            ModelMetadata(
                model_id="deterministic-local",
                provider_type=ModelProviderType.LOCAL_CUSTOM,
                display_name="Deterministic Local Fallback",
                context_window=16384,
                is_local=True,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
            ),
        ]
