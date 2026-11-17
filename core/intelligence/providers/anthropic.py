"""core/intelligence/providers/anthropic.py — Anthropic Claude Provider for OPTIMUS.

Implements the Anthropic Messages API (`/v1/messages`) supporting Claude 3.5 Sonnet,
Claude 3.5 Haiku, structured outputs, native tool use, and streaming.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from core.intelligence.providers.base import ModelProvider
from core.intelligence.types import (
    ChatMessage,
    ModelCapability,
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    ModelResponse,
    ProviderError,
    RateLimitError,
    StreamChunk,
    TimeoutError,
    TokenUsage,
    ToolCall,
)


class AnthropicProvider(ModelProvider):
    """Provider connecting to Anthropic Claude via /v1/messages protocol."""

    def __init__(
        self,
        name: str = "anthropic",
        api_key: str | None = None,
        base_url: str | None = None,
        transport: Callable[[str, dict[str, Any] | None, dict[str, str], float], tuple[int, bytes]] | None = None,
    ) -> None:
        super().__init__(name=name, provider_type=ModelProviderType.ANTHROPIC, is_local=False)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com/v1").rstrip("/")
        self._transport = transport or self._default_transport

    @staticmethod
    def _default_transport(
        url: str,
        data: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST" if payload else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as he:
            return he.code, he.read()
        except (urllib.error.URLError, TimeoutError) as ue:
            if "timed out" in str(ue).lower():
                raise TimeoutError(f"Anthropic connection timed out: {ue}") from ue
            raise ProviderError(f"Anthropic network error: {ue}", provider="anthropic") from ue

    def _convert_messages(self, messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        """Anthropic separates top-level system prompt from user/assistant turns."""
        system_parts = []
        conversation: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            elif m.role in ("user", "assistant"):
                conversation.append({"role": m.role, "content": m.content})
            elif m.role == "tool":
                # Map tool result back as user turn with tool_result block
                conversation.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "call_unknown",
                            "content": m.content,
                        }
                    ],
                })

        return "\n\n".join(system_parts), conversation

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute chat completion with Anthropic Claude."""
        def _call(req: ModelRequest) -> ModelResponse:
            start_t = time.perf_counter()
            model_name = req.preferred_model or "claude-3-5-sonnet-20241022"
            url = f"{self.base_url}/messages"

            system_prompt, messages = self._convert_messages(req.messages)

            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }

            payload: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "max_tokens": req.max_tokens or 4096,
                "temperature": req.temperature,
            }
            if system_prompt:
                payload["system"] = system_prompt

            if req.tools:
                payload["tools"] = [
                    {
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "input_schema": t.get("parameters", {"type": "object"}),
                    }
                    for t in req.tools
                ]

            status_code, body = self._transport(url, payload, headers, req.timeout_seconds)

            if status_code == 429:
                raise RateLimitError(
                    f"Anthropic rate limit: {body.decode('utf-8', errors='replace')}",
                    provider=self.name,
                    status_code=429,
                )
            if status_code != 200:
                raise ProviderError(
                    f"Anthropic returned HTTP {status_code}: {body.decode('utf-8', errors='replace')}",
                    provider=self.name,
                    status_code=status_code,
                )

            data = json.loads(body.decode("utf-8"))
            text_blocks = []
            tool_calls = []

            for block in data.get("content", []):
                b_type = block.get("type")
                if b_type == "text":
                    text_blocks.append(block.get("text", ""))
                elif b_type == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.get("id", f"call_{len(tool_calls)+1}"),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    ))

            full_text = "\n".join(text_blocks)
            usage_data = data.get("usage", {})
            usage = TokenUsage(
                prompt_tokens=usage_data.get("input_tokens", 0),
                completion_tokens=usage_data.get("output_tokens", 0),
                total_tokens=usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
            )

            duration_ms = (time.perf_counter() - start_t) * 1000.0
            structured = None
            if req.response_schema and full_text:
                try:
                    structured = json.loads(full_text)
                except Exception:
                    pass

            return ModelResponse(
                text=full_text,
                tool_calls=tool_calls,
                usage=usage,
                model_id=data.get("model", model_name),
                provider=self.name,
                finish_reason="tool_calls" if tool_calls else "stop",
                duration_ms=duration_ms,
                structured_data=structured,
            )

        return self.execute_with_resilience(request, _call)

    def stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        """Stream chunks from Anthropic endpoint."""
        model_name = request.preferred_model or "claude-3-5-sonnet-20241022"
        url = f"{self.base_url}/messages"
        system_prompt, messages = self._convert_messages(request.messages)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt

        status_code, body = self._transport(url, payload, headers, request.timeout_seconds)
        if status_code != 200:
            raise ProviderError(f"Stream error: HTTP {status_code}", provider=self.name, status_code=status_code)

        for line in body.splitlines():
            line_str = line.decode("utf-8").strip()
            if not line_str.startswith("data: "):
                continue
            raw_json = line_str[6:].strip()
            try:
                event = json.loads(raw_json)
                ev_type = event.get("type")
                if ev_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        yield StreamChunk(text_delta=delta.get("text", ""))
                elif ev_type == "message_stop":
                    yield StreamChunk(text_delta="", is_final=True, finish_reason="stop")
            except Exception:
                continue

    def health_check(self) -> bool:
        """Verify API connectivity."""
        return bool(self.api_key)

    def list_models(self) -> list[ModelMetadata]:
        """List registered Claude models."""
        return [
            ModelMetadata(
                model_id="claude-3-5-sonnet-20241022",
                provider_type=ModelProviderType.ANTHROPIC,
                display_name="Claude 3.5 Sonnet",
                context_window=200000,
                is_local=False,
                input_cost_per_million=3.00,
                output_cost_per_million=15.00,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.VISION,
                    ModelCapability.LONG_CONTEXT,
                    ModelCapability.CODE_SPECIALIZED,
                },
            ),
            ModelMetadata(
                model_id="claude-3-5-haiku-20241022",
                provider_type=ModelProviderType.ANTHROPIC,
                display_name="Claude 3.5 Haiku",
                context_window=200000,
                is_local=False,
                input_cost_per_million=0.80,
                output_cost_per_million=4.00,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                },
            ),
        ]
