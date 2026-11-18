"""core/intelligence/providers/openai.py — OpenAI & Compatible API Provider for OPTIMUS.

Supports OpenAI official endpoints and OpenAI-compatible local/cloud inference servers
(vLLM, LM Studio, LocalAI, Jan, Groq, OpenRouter) with tool calling, JSON schemas,
and streaming token decoding.
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


class OpenAIProvider(ModelProvider):
    """Provider communicating via OpenAI /v1/chat/completions standard protocol."""

    def __init__(
        self,
        name: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        is_local: bool = False,
        transport: Callable[[str, dict[str, Any] | None, dict[str, str], float], tuple[int, bytes]] | None = None,
    ) -> None:
        p_type = ModelProviderType.OPENAI_COMPATIBLE if is_local else ModelProviderType.OPENAI
        super().__init__(name=name, provider_type=p_type, is_local=is_local)
        if not api_key:
            try:
                from core.security.credentials import get_credential
                api_key = get_credential(name) or get_credential("openai")
            except Exception:
                pass
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
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
                raise TimeoutError(f"OpenAI connection timed out: {ue}") from ue
            raise ProviderError(f"OpenAI network error: {ue}", provider="openai") from ue

    def _convert_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.name:
                msg["name"] = m.name
            out.append(msg)
        return out

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute chat completion with OpenAI-compatible API."""
        def _call(req: ModelRequest) -> ModelResponse:
            start_t = time.perf_counter()
            model_name = req.preferred_model or "gpt-4o-mini"
            url = f"{self.base_url}/chat/completions"

            headers = {
                "Content-Type": "application/json",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            payload: dict[str, Any] = {
                "model": model_name,
                "messages": self._convert_messages(req.messages),
                "temperature": req.temperature,
                "stream": False,
            }
            if req.max_tokens:
                payload["max_tokens"] = req.max_tokens

            if req.response_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_response",
                        "schema": req.response_schema,
                    },
                }

            if req.tools:
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.get("name"),
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {}),
                        },
                    }
                    for t in req.tools
                ]

            status_code, body = self._transport(url, payload, headers, req.timeout_seconds)

            if status_code == 429:
                raise RateLimitError(
                    f"Rate limit exceeded (HTTP 429): {body.decode('utf-8', errors='replace')}",
                    provider=self.name,
                    status_code=429,
                )
            if status_code != 200:
                raise ProviderError(
                    f"HTTP {status_code}: {body.decode('utf-8', errors='replace')}",
                    provider=self.name,
                    status_code=status_code,
                )

            data = json.loads(body.decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message", {})
            text = msg.get("content") or ""
            finish_reason = choice.get("finish_reason", "stop")

            tool_calls = []
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                raw_args = fn.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.get("id", f"call_{len(tool_calls)+1}"),
                    name=fn.get("name", ""),
                    arguments=args,
                ))

            usage_data = data.get("usage", {})
            usage = TokenUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )

            duration_ms = (time.perf_counter() - start_t) * 1000.0
            structured = None
            if req.response_schema and text:
                try:
                    structured = json.loads(text)
                except Exception:
                    pass

            return ModelResponse(
                text=text,
                tool_calls=tool_calls,
                usage=usage,
                model_id=data.get("model", model_name),
                provider=self.name,
                finish_reason=finish_reason,
                duration_ms=duration_ms,
                structured_data=structured,
            )

        return self.execute_with_resilience(request, _call)

    def stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        """Stream response chunks from OpenAI endpoint."""
        model_name = request.preferred_model or "gpt-4o-mini"
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": model_name,
            "messages": self._convert_messages(request.messages),
            "stream": True,
            "temperature": request.temperature,
        }

        status_code, body = self._transport(url, payload, headers, request.timeout_seconds)
        if status_code != 200:
            raise ProviderError(f"Stream error: HTTP {status_code}", provider=self.name, status_code=status_code)

        for line in body.splitlines():
            line_str = line.decode("utf-8").strip()
            if not line_str.startswith("data: "):
                continue
            raw_json = line_str[6:].strip()
            if raw_json == "[DONE]":
                yield StreamChunk(text_delta="", is_final=True, finish_reason="stop")
                break
            try:
                chunk = json.loads(raw_json)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                finish = chunk.get("choices", [{}])[0].get("finish_reason")
                yield StreamChunk(
                    text_delta=content or "",
                    is_final=bool(finish),
                    finish_reason=finish,
                )
            except Exception:
                continue

    def health_check(self) -> bool:
        """Verify API key and endpoint readiness."""
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            status, _ = self._transport(f"{self.base_url}/models", None, headers, 5.0)
            return status in (200, 401)  # 401 means server is up even if key invalid
        except Exception:
            return False

    def list_models(self) -> list[ModelMetadata]:
        """List registered OpenAI models."""
        return [
            ModelMetadata(
                model_id="gpt-4o",
                provider_type=self.provider_type,
                display_name="GPT-4o (Omni)",
                context_window=128000,
                is_local=self.is_local,
                input_cost_per_million=2.50,
                output_cost_per_million=10.00,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.VISION,
                    ModelCapability.LONG_CONTEXT,
                },
            ),
            ModelMetadata(
                model_id="gpt-4o-mini",
                provider_type=self.provider_type,
                display_name="GPT-4o Mini",
                context_window=128000,
                is_local=self.is_local,
                input_cost_per_million=0.15,
                output_cost_per_million=0.60,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                },
            ),
        ]
