"""core/intelligence/providers/ollama.py — Ollama Local Model Provider for OPTIMUS.

Provides local, private inference via Ollama HTTP REST endpoints (`/api/chat`, `/api/tags`).
Supports tool calling, JSON structured outputs, and streaming with zero cloud data leaks.
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
    StreamChunk,
    TimeoutError,
    TokenUsage,
    ToolCall,
)


class OllamaProvider(ModelProvider):
    """Local inference provider communicating with an Ollama daemon."""

    def __init__(
        self,
        name: str = "ollama_local",
        base_url: str | None = None,
        transport: Callable[[str, dict[str, Any] | None, dict[str, str], float], tuple[int, bytes]] | None = None,
    ) -> None:
        super().__init__(name=name, provider_type=ModelProviderType.OLLAMA, is_local=True)
        self.base_url = (base_url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self._transport = transport or self._default_transport

    @staticmethod
    def _default_transport(
        url: str,
        data: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        """Perform HTTP request using Python standard library."""
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST" if payload else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as he:
            err_body = he.read()
            return he.code, err_body
        except (urllib.error.URLError, TimeoutError) as ue:
            if "timed out" in str(ue).lower():
                raise TimeoutError(f"Ollama connection timed out: {ue}") from ue
            raise ProviderError(f"Ollama network error: {ue}", provider="ollama") from ue

    def _convert_messages(self, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Map normalized ChatMessages to Ollama chat format."""
        out = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in m.tool_calls
                ]
            out.append(msg)
        return out

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute chat completion with Ollama."""
        def _call(req: ModelRequest) -> ModelResponse:
            start_t = time.perf_counter()
            model_name = req.preferred_model or "llama3.2:latest"
            url = f"{self.base_url}/api/chat"

            from core.runtime.model_sizing import get_optimal_cpu_threads
            optimal_threads = get_optimal_cpu_threads()

            payload: dict[str, Any] = {
                "model": model_name,
                "messages": self._convert_messages(req.messages),
                "stream": False,
                "options": {
                    "temperature": req.temperature,
                    "num_thread": optimal_threads,
                },
            }
            if req.max_tokens:
                payload["options"]["num_predict"] = req.max_tokens
            if req.response_schema:
                payload["format"] = "json"
            if req.tools:
                payload["tools"] = req.tools

            status_code, body = self._transport(
                url,
                payload,
                {"Content-Type": "application/json"},
                req.timeout_seconds,
            )

            if status_code != 200:
                raise ProviderError(
                    f"Ollama returned HTTP {status_code}: {body.decode('utf-8', errors='replace')}",
                    provider=self.name,
                    status_code=status_code,
                )

            data = json.loads(body.decode("utf-8"))
            msg = data.get("message", {})
            text = msg.get("content", "")

            tool_calls = []
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                tool_calls.append(ToolCall(
                    id=f"call_{len(tool_calls)+1}",
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments", {}),
                ))

            usage = TokenUsage(
                prompt_tokens=data.get("prompt_eval_count", 0),
                completion_tokens=data.get("eval_count", 0),
                total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
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
                model_id=model_name,
                provider=self.name,
                finish_reason="tool_calls" if tool_calls else "stop",
                duration_ms=duration_ms,
                structured_data=structured,
            )

        return self.execute_with_resilience(request, _call)

    def stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        """Stream chunks from Ollama endpoint."""
        model_name = request.preferred_model or "llama3.2:latest"
        url = f"{self.base_url}/api/chat"
        from core.runtime.model_sizing import get_optimal_cpu_threads
        optimal_threads = get_optimal_cpu_threads()

        payload = {
            "model": model_name,
            "messages": self._convert_messages(request.messages),
            "stream": True,
            "options": {
                "temperature": request.temperature,
                "num_thread": optimal_threads,
            },
        }
        if request.response_schema:
            payload["format"] = "json"

        status_code, body = self._transport(url, payload, {"Content-Type": "application/json"}, request.timeout_seconds)
        if status_code != 200:
            raise ProviderError(f"Ollama stream error: HTTP {status_code}", provider=self.name, status_code=status_code)

        for line in body.splitlines():
            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue
            try:
                chunk_data = json.loads(line_str)
                content = chunk_data.get("message", {}).get("content", "")
                is_done = chunk_data.get("done", False)
                yield StreamChunk(
                    text_delta=content,
                    is_final=is_done,
                    finish_reason="stop" if is_done else None,
                )
            except Exception:
                continue

    def health_check(self) -> bool:
        """Check if Ollama service is reachable and responsive."""
        try:
            status, _ = self._transport(f"{self.base_url}/api/version", None, {}, 5.0)
            return status == 200
        except Exception:
            return False

    def list_models(self) -> list[ModelMetadata]:
        """Query installed local Ollama models from `/api/tags`."""
        try:
            status, body = self._transport(f"{self.base_url}/api/tags", None, {}, 5.0)
            if status != 200:
                return self._default_catalog()
            data = json.loads(body.decode("utf-8"))
            models = []
            for item in data.get("models", []):
                name = item.get("name", "unknown")
                models.append(ModelMetadata(
                    model_id=name,
                    provider_type=ModelProviderType.OLLAMA,
                    display_name=f"Ollama {name}",
                    context_window=8192,
                    is_local=True,
                    capabilities={
                        ModelCapability.CHAT,
                        ModelCapability.STREAMING,
                        ModelCapability.TOOL_CALLING,
                        ModelCapability.STRUCTURED_OUTPUT,
                    },
                ))
            return models or self._default_catalog()
        except Exception:
            return self._default_catalog()

    def _default_catalog(self) -> list[ModelMetadata]:
        """Fallback list of common Ollama local models."""
        return [
            ModelMetadata(
                model_id="llama3.2:latest",
                provider_type=ModelProviderType.OLLAMA,
                display_name="Llama 3.2 (Local)",
                context_window=128000,
                is_local=True,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.LONG_CONTEXT,
                },
            ),
            ModelMetadata(
                model_id="mistral:latest",
                provider_type=ModelProviderType.OLLAMA,
                display_name="Mistral 7B (Local)",
                context_window=32768,
                is_local=True,
                capabilities={
                    ModelCapability.CHAT,
                    ModelCapability.STREAMING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.STRUCTURED_OUTPUT,
                },
            ),
        ]
