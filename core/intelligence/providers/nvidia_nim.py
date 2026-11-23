"""core/intelligence/providers/nvidia_nim.py — NVIDIA NIM Inference Provider for OPTIMUS.

Implements first-class NVIDIA NIM (NVIDIA Inference Microservices) integration
via the OpenAI-compatible REST API (https://integrate.api.nvidia.com/v1).

Inherits from OpenAIProvider to adhere to DRY compliance while specializing:
- Bearer-token authentication via secure OS Keyring with NVIDIA_API_KEY fallback.
- Dynamic parameter sanitization and HTTP 400 parameter stripping retry.
- Explicit AuthenticationError (401) and RateLimitError (429) normalization.
- Model discovery via /v1/models with curated high-performance fallback models.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from core.intelligence.providers.openai import OpenAIProvider
from core.intelligence.types import (
    AuthenticationError,
    ChatMessage,
    ModelCapability,
    ModelExecutionBackend,
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
from core.security.credentials import get_credential, redact_secret

logger = logging.getLogger("optimus.intelligence.nvidia_nim")

# Baseline curated NVIDIA NIM models with capabilities
NIM_DEFAULT_MODELS: list[dict[str, Any]] = [
    {
        "id": "meta/llama-3.1-70b-instruct",
        "name": "Llama 3.1 70B Instruct (NVIDIA NIM)",
        "context_window": 131072,
        "capabilities": [
            ModelCapability.CHAT,
            ModelCapability.STREAMING,
            ModelCapability.TOOL_CALLING,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.CODE_SPECIALIZED,
        ],
    },
    {
        "id": "meta/llama-3.1-8b-instruct",
        "name": "Llama 3.1 8B Instruct (NVIDIA NIM)",
        "context_window": 131072,
        "capabilities": [
            ModelCapability.CHAT,
            ModelCapability.STREAMING,
            ModelCapability.TOOL_CALLING,
            ModelCapability.STRUCTURED_OUTPUT,
        ],
    },
    {
        "id": "mistralai/mixtral-8x22b-instruct-v0.1",
        "name": "Mixtral 8x22B Instruct (NVIDIA NIM)",
        "context_window": 65536,
        "capabilities": [
            ModelCapability.CHAT,
            ModelCapability.STREAMING,
            ModelCapability.TOOL_CALLING,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.CODE_SPECIALIZED,
        ],
    },
    {
        "id": "nvidia/nemotron-4-340b-instruct",
        "name": "Nemotron-4 340B Instruct (NVIDIA NIM)",
        "context_window": 4096,
        "capabilities": [
            ModelCapability.CHAT,
            ModelCapability.STREAMING,
            ModelCapability.STRUCTURED_OUTPUT,
        ],
    },
]

# Disallowed parameters on heterogeneous NIM backends (e.g. TensorRT-LLM)
UNSUPPORTED_NIM_PARAMS = {"presence_penalty", "frequency_penalty", "top_k", "logprobs"}


class NvidiaNimProvider(OpenAIProvider):
    """NVIDIA NIM provider operating over https://integrate.api.nvidia.com/v1."""

    def __init__(
        self,
        name: str = "nvidia_nim",
        api_key: str | None = None,
        base_url: str | None = None,
        transport: Callable[[str, dict[str, Any] | None, dict[str, str], float], tuple[int, bytes]] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            api_key=api_key,
            base_url=base_url or "https://integrate.api.nvidia.com/v1",
            is_local=False,
            transport=transport,
        )
        self.provider_type = ModelProviderType.NVIDIA_NIM

        # Resolve credentials with Keyring -> Vault -> Environment variable precedence
        if not self.api_key:
            self.api_key = (
                get_credential("nvidia")
                or get_credential("nvidia_nim")
                or os.environ.get("NVIDIA_API_KEY", "")
                or os.environ.get("NVIDIA_NIM_API_KEY", "")
            ).strip()

    def _sanitize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Strip known unsupported parameters from NVIDIA NIM microservice payloads."""
        sanitized = dict(payload)
        for param in UNSUPPORTED_NIM_PARAMS:
            sanitized.pop(param, None)
        return sanitized

    def _handle_error_response(self, code: int, raw_body: bytes) -> None:
        """Normalize HTTP error statuses to OPTIMUS exception types."""
        body_str = raw_body.decode("utf-8", errors="replace")
        clean_msg = redact_secret(body_str)

        if code in (401, 403):
            raise AuthenticationError(
                f"NVIDIA NIM authentication failed ({code}): {clean_msg}",
                provider=self.name,
                status_code=code,
            )
        if code == 429:
            raise RateLimitError(
                f"NVIDIA NIM rate limit exceeded: {clean_msg}",
                provider=self.name,
                status_code=code,
            )
        if code in (502, 503, 504):
            raise TimeoutError(f"NVIDIA NIM gateway error ({code}): {clean_msg}")

        raise ProviderError(
            f"NVIDIA NIM error ({code}): {clean_msg}",
            provider=self.name,
            status_code=code,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Execute chat completion with automatic parameter-stripping retry on HTTP 400."""
        def _call(req: ModelRequest) -> ModelResponse:
            start_t = time.perf_counter()
            url = f"{self.base_url}/chat/completions"

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "OPTIMUS/2.0 (Windows NT 10.0; Win64; x64)",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            payload: dict[str, Any] = {
                "model": req.preferred_model or "meta/llama-3.1-70b-instruct",
                "messages": self._convert_messages(req.messages),
                "temperature": req.temperature,
                "stream": False,
            }
            if req.max_tokens:
                payload["max_tokens"] = req.max_tokens

            if req.tools:
                payload["tools"] = req.tools
                payload["tool_choice"] = "auto"

            if req.response_schema:
                payload["response_format"] = {"type": "json_object"}

            payload = self._sanitize_payload(payload)

            # Execution with automatic parameter stripping on HTTP 400
            code, resp_bytes = self._transport(url, payload, headers, req.timeout_seconds)

            if code == 400:
                err_text = resp_bytes.decode("utf-8", errors="replace")
                match = re.search(
                    r"(?:unrecognized|unknown|unsupported)\s+(?:request\s+)?argument[:\s=]+['\"]?([a-zA-Z0-9_]+)",
                    err_text,
                    re.IGNORECASE,
                )
                if match:
                    bad_param = match.group(1).lower()
                    if bad_param in payload:
                        logger.warning(
                            f"NVIDIA NIM rejected argument '{bad_param}'. Stripping and retrying."
                        )
                        payload.pop(bad_param, None)
                        code, resp_bytes = self._transport(url, payload, headers, req.timeout_seconds)

            if code != 200:
                self._handle_error_response(code, resp_bytes)

            duration_ms = (time.perf_counter() - start_t) * 1000.0
            data = json.loads(resp_bytes.decode("utf-8"))
            choice = data["choices"][0]
            msg = choice.get("message", {})

            tool_calls = []
            if "tool_calls" in msg and msg["tool_calls"]:
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = {}
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        args = {"raw": fn.get("arguments", "")}
                    tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))

            usage = TokenUsage()
            if "usage" in data and data["usage"]:
                u = data["usage"]
                usage = TokenUsage(
                    prompt_tokens=u.get("prompt_tokens", 0),
                    completion_tokens=u.get("completion_tokens", 0),
                    total_tokens=u.get("total_tokens", 0),
                )

            return ModelResponse(
                text=msg.get("content") or "",
                tool_calls=tool_calls,
                model_id=data.get("model", req.preferred_model or "meta/llama-3.1-70b-instruct"),
                provider=self.name,
                usage=usage,
                finish_reason=choice.get("finish_reason", "stop"),
                duration_ms=duration_ms,
            )

        return self.execute_with_resilience(request, _call)

    def stream(self, request: ModelRequest) -> Iterator[StreamChunk]:
        """Stream SSE chunks incrementally from NVIDIA NIM endpoint."""
        self.circuit_breaker.check_request()
        url = f"{self.base_url}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "OPTIMUS/2.0 (Windows NT 10.0; Win64; x64)",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        model_id = request.preferred_model or "meta/llama-3.1-70b-instruct"
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": self._convert_messages(request.messages),
            "temperature": request.temperature,
            "stream": True,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = "auto"

        payload = self._sanitize_payload(payload)
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=request.timeout_seconds) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if not line_str.startswith("data:"):
                        continue
                    content = line_str[5:].strip()
                    if content == "[DONE]":
                        yield StreamChunk(is_final=True, finish_reason="stop")
                        break
                    try:
                        chunk_json = json.loads(content)
                        choice = chunk_json["choices"][0]
                        delta = choice.get("delta", {})
                        text = delta.get("content") or ""
                        finish = choice.get("finish_reason")
                        yield StreamChunk(text_delta=text, finish_reason=finish, is_final=bool(finish))
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
            self.circuit_breaker.record_success()
        except urllib.error.HTTPError as he:
            raw_body = he.read()
            self._handle_error_response(he.code, raw_body)
        except Exception as ex:
            clean_ex = redact_secret(str(ex))
            self.circuit_breaker.record_failure(ex)
            raise ProviderError(f"NVIDIA NIM streaming connection failed: {clean_ex}", provider=self.name) from ex

    def health_check(self) -> bool:
        """Return True if NVIDIA NIM endpoint responds."""
        try:
            req = ModelRequest(
                messages=[ChatMessage(role="user", content="ping")],
                model_id="meta/llama-3.1-8b-instruct",
                max_tokens=2,
                timeout_seconds=5.0,
            )
            resp = self.complete(req)
            return bool(resp.text or resp.duration_ms > 0)
        except Exception:
            return False

    def list_models(self) -> list[ModelMetadata]:
        """Return catalogue of models accessible via NVIDIA NIM."""
        models: list[ModelMetadata] = []

        # 1. Start with curated baseline models
        for item in NIM_DEFAULT_MODELS:
            models.append(
                ModelMetadata(
                    model_id=item["id"],
                    display_name=item["name"],
                    provider_type=self.provider_type,
                    capabilities=set(item["capabilities"]),
                    context_window=item["context_window"],
                    is_local=False,
                    execution_backend=ModelExecutionBackend.CLOUD,
                )
            )

        # 2. Attempt dynamic discovery from /v1/models if endpoint reachable
        try:
            url = f"{self.base_url}/models"
            headers = {"Accept": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            code, resp_bytes = self._transport(url, None, headers, 3.0)
            if code == 200:
                data = json.loads(resp_bytes.decode("utf-8"))
                discovered = data.get("data", [])
                existing_ids = {m.model_id for m in models}
                for d in discovered:
                    mid = d.get("id")
                    if mid and mid not in existing_ids:
                        models.append(
                            ModelMetadata(
                                model_id=mid,
                                display_name=f"{mid} (NVIDIA NIM)",
                                provider_type=self.provider_type,
                                capabilities={ModelCapability.CHAT, ModelCapability.STREAMING, ModelCapability.TOOL_CALLING},
                                context_window=32768,
                                is_local=False,
                                execution_backend=ModelExecutionBackend.CLOUD,
                            )
                        )
        except Exception:
            pass

        return models
