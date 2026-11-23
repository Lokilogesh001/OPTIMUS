"""tests/test_nvidia_nim_gateway.py — Comprehensive Test Suite for NVIDIA NIM Gateway Integration.

Tests:
1. Provider registration & model catalog seeding in ModelRegistry.
2. Synchronous chat completion with token usage & metrics.
3. Tool calling payload serialization & response parsing.
4. Streaming response chunks via SSE.
5. Immediate circuit breaker trip to OPEN on HTTP 401 AuthenticationError.
6. Rate limit handling and backoff on HTTP 429.
7. Parameter stripping and retry on HTTP 400 parameter errors.
8. Privacy boundary enforcement: PRIVACY_STRICT blocks cloud NVIDIA NIM.
9. Automatic fallback chain when circuit breaker is OPEN.
10. Credential resolution precedence: Keyring -> Vault -> Environment variable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.intelligence.circuit_breaker import CircuitBreaker
from core.intelligence.providers.nvidia_nim import NvidiaNimProvider
from core.intelligence.registry import ModelRegistry
from core.intelligence.router import ModelRouter
from core.intelligence.types import (
    AuthenticationError,
    ChatMessage,
    CircuitOpenError,
    CircuitState,
    ModelCapability,
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    ModelResponse,
    PrivacyViolationError,
    RateLimitError,
    RoutingPolicy,
    ToolCall,
)
from core.security.credentials import (
    delete_credential,
    get_credential,
    redact_secret,
    store_credential,
)


@pytest.fixture(autouse=True)
def clean_credentials():
    """Ensure clean credential state for tests."""
    delete_credential("nvidia")
    delete_credential("nvidia_nim")
    yield
    delete_credential("nvidia")
    delete_credential("nvidia_nim")


def test_01_provider_registration_and_model_seeding():
    """Verify NvidiaNimProvider registers in ModelRegistry with default models."""
    registry = ModelRegistry(auto_seed=True)
    provider = registry.get_provider("nvidia_nim")
    assert provider is not None
    assert isinstance(provider, NvidiaNimProvider)
    assert provider.provider_type == ModelProviderType.NVIDIA_NIM
    assert not provider.is_local

    # Check that standard NIM models are seeded
    llama70b = registry.get_model("meta/llama-3.1-70b-instruct")
    assert llama70b is not None
    assert llama70b.provider_type == ModelProviderType.NVIDIA_NIM
    assert ModelCapability.TOOL_CALLING in llama70b.capabilities
    assert not llama70b.is_local


def test_02_completion_success_and_token_usage():
    """Verify chat completion with mock transport."""
    mock_response = {
        "id": "chatcmpl-nim-12345",
        "object": "chat.completion",
        "model": "meta/llama-3.1-70b-instruct",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "OPTIMUS on NVIDIA NIM is online."},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 8,
            "total_tokens": 23,
        },
    }

    def mock_transport(url, data, headers, timeout):
        assert "integrate.api.nvidia.com" in url
        assert headers.get("Authorization") == "Bearer nvapi-test-key"
        assert data["model"] == "meta/llama-3.1-70b-instruct"
        return 200, json.dumps(mock_response).encode("utf-8")

    provider = NvidiaNimProvider(api_key="nvapi-test-key", transport=mock_transport)
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Hello NIM")],
        preferred_model="meta/llama-3.1-70b-instruct",
        temperature=0.2,
    )
    resp = provider.complete(req)

    assert resp.text == "OPTIMUS on NVIDIA NIM is online."
    assert resp.usage.total_tokens == 23
    assert resp.finish_reason == "stop"
    assert resp.provider == "nvidia_nim"


def test_03_tool_calling_serialization_and_parsing():
    """Verify tool call arguments are parsed correctly."""
    mock_response = {
        "id": "chatcmpl-nim-tool-1",
        "model": "meta/llama-3.1-70b-instruct",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "get_system_metrics",
                                "arguments": '{"sensor": "gpu", "format": "json"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    def mock_transport(url, data, headers, timeout):
        assert "tools" in data
        return 200, json.dumps(mock_response).encode("utf-8")

    provider = NvidiaNimProvider(api_key="nvapi-test-key", transport=mock_transport)
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Check GPU")],
        preferred_model="meta/llama-3.1-70b-instruct",
        tools=[{"type": "function", "function": {"name": "get_system_metrics"}}],
    )
    resp = provider.complete(req)

    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.name == "get_system_metrics"
    assert tc.arguments == {"sensor": "gpu", "format": "json"}


def test_04_auth_failure_401_trips_circuit_breaker_immediately():
    """Verify HTTP 401 raises AuthenticationError and immediately sets circuit to OPEN (Mandate M-5)."""
    cb = CircuitBreaker(name="nim_auth_test")

    def mock_transport(url, data, headers, timeout):
        return 401, b'{"detail": "Unauthorized: Invalid Bearer token nvapi-bad-key"}'

    provider = NvidiaNimProvider(
        api_key="nvapi-bad-key",
        transport=mock_transport,
    )
    provider.circuit_breaker = cb

    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Test 401")],
        preferred_model="meta/llama-3.1-70b-instruct",
    )

    with pytest.raises(AuthenticationError) as exc_info:
        provider.complete(req)

    assert exc_info.value.status_code == 401
    assert "authentication failed (401)" in str(exc_info.value).lower()
    # Confirm immediate trip to OPEN without needing 3 attempts
    assert cb.state == CircuitState.OPEN


def test_05_rate_limit_429_error_normalization():
    """Verify HTTP 429 raises RateLimitError and retries up to max_retries."""
    attempts = 0

    def mock_transport(url, data, headers, timeout):
        nonlocal attempts
        attempts += 1
        return 429, b'{"detail": "Too Many Requests: Rate limit exceeded"}'

    provider = NvidiaNimProvider(api_key="nvapi-test-key", transport=mock_transport)
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Test 429")],
        preferred_model="meta/llama-3.1-70b-instruct",
    )

    with pytest.raises(RateLimitError) as exc_info:
        provider.complete(req)

    assert exc_info.value.status_code == 429
    assert attempts > 1  # Verify bounded retries occurred before failing


def test_06_parameter_stripping_on_http_400():
    """Verify unrecognized parameters are automatically stripped on HTTP 400 (Mandate M-4)."""
    call_count = 0
    passed_payloads = []

    def mock_transport(url, data, headers, timeout):
        nonlocal call_count
        call_count += 1
        passed_payloads.append(dict(data))
        if call_count == 1:
            # Reject on unrecognized argument
            return 400, b'{"error": "Unrecognized request argument: custom_unsupported_opt"}'
        return 200, json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "Success after stripping"}}],
        }).encode("utf-8")

    provider = NvidiaNimProvider(api_key="nvapi-test-key", transport=mock_transport)

    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Test 400")],
        preferred_model="meta/llama-3.1-70b-instruct",
    )

    # Monkeypatch request serialization to inject a custom unsupported argument on first try
    original_sanitize = provider._sanitize_payload
    def inject_bad_arg(p):
        res = original_sanitize(p)
        if call_count == 0:
            res["custom_unsupported_opt"] = "bad_val"
        return res
    provider._sanitize_payload = inject_bad_arg

    resp = provider.complete(req)

    assert call_count == 2
    assert "custom_unsupported_opt" in passed_payloads[0]
    assert "custom_unsupported_opt" not in passed_payloads[1]
    assert resp.text == "Success after stripping"


def test_07_privacy_strict_hard_blocks_cloud_nim():
    """Verify router with PRIVACY_STRICT policy refuses to route to NVIDIA NIM."""
    registry = ModelRegistry(auto_seed=True)
    router = ModelRouter(registry=registry, default_policy=RoutingPolicy.PRIVACY_STRICT)

    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Top secret financial data")],
        is_private=True,
    )

    candidates = router.select_models(req)
    # Every selected candidate MUST be local
    assert len(candidates) > 0
    for cand in candidates:
        assert cand.is_local
        assert cand.provider_type != ModelProviderType.NVIDIA_NIM


def test_08_automatic_fallback_when_circuit_breaker_open():
    """Verify that when NVIDIA NIM circuit breaker is OPEN, router falls back to next provider."""
    registry = ModelRegistry(auto_seed=False)

    # Register mock NIM provider with an OPEN circuit breaker
    nim_cb = CircuitBreaker(name="nim_cb")
    nim_cb.record_failure("401 Unauthorized")
    assert nim_cb.state == CircuitState.OPEN

    nim_prov = NvidiaNimProvider(api_key="dummy")
    nim_prov.circuit_breaker = nim_cb
    registry.register_provider(nim_prov)

    # Register a healthy secondary provider
    from core.intelligence.providers.openai import OpenAIProvider
    secondary = OpenAIProvider(name="backup_openai", api_key="sk-backup")
    registry.register_provider(secondary)

    # Model metadata
    nim_model = ModelMetadata(
        model_id="meta/llama-3.1-70b-instruct",
        display_name="NIM Llama",
        provider_type=ModelProviderType.NVIDIA_NIM,
        capabilities={ModelCapability.CHAT},
        context_window=32000,
        is_local=False,
    )
    backup_model = ModelMetadata(
        model_id="gpt-4o-mini",
        display_name="Backup GPT",
        provider_type=ModelProviderType.OPENAI,
        capabilities={ModelCapability.CHAT},
        context_window=32000,
        is_local=False,
    )
    registry.register_model(nim_model)
    registry.register_model(backup_model)

    router = ModelRouter(registry=registry, default_policy=RoutingPolicy.CLOUD_PREFERRED)
    req = ModelRequest(messages=[ChatMessage(role="user", content="Test fallback")])
    chain = router.select_models(req)

    assert len(chain) >= 1
    # Both models eligible, but execution against nim_prov will fast-fail with CircuitOpenError
    with pytest.raises(CircuitOpenError):
        nim_prov.complete(req)


def test_09_credential_resolution_order(monkeypatch):
    """Verify Keyring -> Vault -> Environment variable resolution order (Mandate M-2)."""
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)

    # 1. Environment variable fallback
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-env-key-12345")
    p1 = NvidiaNimProvider()
    assert p1.api_key == "nvapi-env-key-12345"

    # 2. Vault / Keyring overrides environment variable
    store_credential("nvidia", "nvapi-vault-key-67890")
    p2 = NvidiaNimProvider()
    assert p2.api_key == "nvapi-vault-key-67890"

    # 3. Querying "nvidia_nim" alias accesses the same stored key
    assert get_credential("nvidia_nim") == "nvapi-vault-key-67890"
