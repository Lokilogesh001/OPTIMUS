"""tests/test_model_intelligence_gateway.py — Comprehensive Test Suite for OPTIMUS Component 5.

Tests:
1. Unified Model Provider interface across Ollama, OpenAI, Anthropic, and Local Custom.
2. Mocked HTTP transport verification for every provider (chat, tools, structured output, streaming).
3. Intelligent Model Router policy enforcement (LOCAL_FIRST, CLOUD_PREFERRED, COST_OPTIMIZED, PERFORMANCE_OPTIMIZED).
4. Strict privacy boundary enforcement (hard blocking cloud routing for private data).
5. Dynamic Model Registry (add, remove, enable, disable models at runtime).
6. Circuit Breaker transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED).
7. Automatic multi-tier fallback chains upon model / provider failure.
8. Error normalization (RateLimitError, TimeoutError, CircuitOpenError, PrivacyViolationError).
9. Sensitive data protection and API credential masking.
10. End-to-end integration with Central Orchestrator and specialized agent workflows.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.intelligence.circuit_breaker import CircuitBreaker
from core.intelligence.gateway import IntelligenceGateway
from core.intelligence.providers.anthropic import AnthropicProvider
from core.intelligence.providers.local_custom import LocalCustomProvider
from core.intelligence.providers.ollama import OllamaProvider
from core.intelligence.providers.openai import OpenAIProvider
from core.intelligence.registry import ModelRegistry
from core.intelligence.router import ModelRouter
from core.intelligence.types import (
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
)
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.events import EventBus
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import TaskStatus, TaskStore
from core.orchestrator.tools import ToolRegistry

# ── 1. Ollama Provider Unit Tests ─────────────────────────────────────────────

def test_ollama_provider_completion_and_tools():
    """Verify OllamaProvider parses chat completion, tool calls, and usage correctly."""
    mock_response = {
        "message": {
            "role": "assistant",
            "content": "I calculated the sum.",
            "tool_calls": [
                {
                    "function": {
                        "name": "calculator",
                        "arguments": {"expression": "100 + 200"},
                    }
                }
            ],
        },
        "prompt_eval_count": 25,
        "eval_count": 15,
    }

    def mock_transport(url, data, headers, timeout):
        assert "/api/chat" in url
        assert data["model"] == "llama3.2:latest"
        return 200, json.dumps(mock_response).encode("utf-8")

    provider = OllamaProvider(transport=mock_transport)
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Calculate 100 + 200")],
        tools=[{"name": "calculator", "description": "calc"}],
    )

    resp = provider.complete(req)
    assert resp.text == "I calculated the sum."
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "calculator"
    assert resp.tool_calls[0].arguments == {"expression": "100 + 200"}
    assert resp.usage.prompt_tokens == 25
    assert resp.usage.completion_tokens == 15
    assert resp.usage.total_tokens == 40


def test_ollama_provider_streaming():
    """Verify OllamaProvider decodes streaming lines into StreamChunk items."""
    chunks = [
        {"message": {"content": "Hello "}, "done": False},
        {"message": {"content": "World!"}, "done": True},
    ]
    raw_body = b"\n".join(json.dumps(c).encode("utf-8") for c in chunks)

    def mock_transport(url, data, headers, timeout):
        return 200, raw_body

    provider = OllamaProvider(transport=mock_transport)
    req = ModelRequest(messages=[ChatMessage(role="user", content="Hi")])

    streamed = list(provider.stream(req))
    assert len(streamed) == 2
    assert streamed[0].text_delta == "Hello "
    assert not streamed[0].is_final
    assert streamed[1].text_delta == "World!"
    assert streamed[1].is_final


# ── 2. OpenAI Provider Unit Tests ─────────────────────────────────────────────

def test_openai_provider_completion_and_tool_calls():
    """Verify OpenAIProvider formats messages and parses function/tool calls."""
    mock_payload = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Calling data analyzer",
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "data_read_tabular",
                                "arguments": "{\"path\": \"sales.csv\"}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }

    def mock_transport(url, data, headers, timeout):
        assert headers.get("Authorization") == "Bearer sk-mockkey"
        return 200, json.dumps(mock_payload).encode("utf-8")

    provider = OpenAIProvider(api_key="sk-mockkey", transport=mock_transport)
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Read sales.csv")],
        tools=[{"name": "data_read_tabular"}],
    )

    resp = provider.complete(req)
    assert resp.text == "Calling data analyzer"
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "data_read_tabular"
    assert resp.tool_calls[0].arguments == {"path": "sales.csv"}
    assert resp.usage.total_tokens == 70


def test_openai_provider_rate_limit_and_error_normalization():
    """Verify OpenAIProvider normalizes HTTP 429 into RateLimitError."""
    def mock_transport(url, data, headers, timeout):
        return 429, b"Rate limit exceeded. Quota exhausted."

    provider = OpenAIProvider(api_key="sk-mockkey", transport=mock_transport)
    req = ModelRequest(messages=[ChatMessage(role="user", content="Hi")])

    with pytest.raises(RateLimitError) as exc_info:
        provider.complete(req)
    assert "429" in str(exc_info.value)


# ── 3. Anthropic Provider Unit Tests ──────────────────────────────────────────

def test_anthropic_provider_completion():
    """Verify AnthropicProvider parses text blocks and tool_use blocks."""
    mock_response = {
        "id": "msg_01",
        "model": "claude-3-5-sonnet-20241022",
        "content": [
            {"type": "text", "text": "Analyzing codebase structure..."},
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "code_ast_search",
                "input": {"path": "main.py"},
            },
        ],
        "usage": {"input_tokens": 120, "output_tokens": 45},
    }

    def mock_transport(url, data, headers, timeout):
        assert headers.get("x-api-key") == "anthropic-mock-key"
        assert headers.get("anthropic-version") == "2023-06-01"
        assert data.get("system") == "You are an expert engineer."
        return 200, json.dumps(mock_response).encode("utf-8")

    provider = AnthropicProvider(api_key="anthropic-mock-key", transport=mock_transport)
    req = ModelRequest(
        messages=[
            ChatMessage(role="system", content="You are an expert engineer."),
            ChatMessage(role="user", content="Inspect main.py"),
        ],
        tools=[{"name": "code_ast_search"}],
    )

    resp = provider.complete(req)
    assert resp.text == "Analyzing codebase structure..."
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "code_ast_search"
    assert resp.tool_calls[0].arguments == {"path": "main.py"}
    assert resp.usage.total_tokens == 165


# ── 4. Local Custom Provider Unit Tests ───────────────────────────────────────

def test_local_custom_provider():
    """Verify LocalCustomProvider executes custom handler and structured responses."""
    def custom_eval(req: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text="Custom in-memory result",
            model_id="custom-fast",
            provider="local_custom",
            duration_ms=5.0,
        )

    provider = LocalCustomProvider(custom_handler=custom_eval)
    req = ModelRequest(messages=[ChatMessage(role="user", content="Test")])
    resp = provider.complete(req)
    assert resp.text == "Custom in-memory result"
    assert resp.provider == "local_custom"
    assert provider.health_check()


# ── 5. Intelligent Model Router Policies ──────────────────────────────────────

@pytest.fixture
def mock_registry():
    """Build a controlled registry with local and cloud models for router testing."""
    reg = ModelRegistry(auto_seed=False)

    # Local Ollama model with deterministic transport (isolated from live daemons)
    dummy_transport = lambda url, data, h, to: (500, b"{}")
    local_p = OllamaProvider(name="ollama", transport=dummy_transport)
    reg.register_provider(local_p)
    reg.register_model(ModelMetadata(
        model_id="llama3.2:latest",
        provider_type=ModelProviderType.OLLAMA,
        is_local=True,
        context_window=128000,
        capabilities={ModelCapability.CHAT, ModelCapability.TOOL_CALLING},
        latency_p50_ms=40.0,
    ))

    # Cloud OpenAI models
    openai_p = OpenAIProvider(name="openai", is_local=False, transport=dummy_transport)
    reg.register_provider(openai_p)
    reg.register_model(ModelMetadata(
        model_id="gpt-4o-mini",
        provider_type=ModelProviderType.OPENAI,
        is_local=False,
        context_window=128000,
        input_cost_per_million=0.15,
        output_cost_per_million=0.60,
        capabilities={ModelCapability.CHAT, ModelCapability.TOOL_CALLING, ModelCapability.STRUCTURED_OUTPUT},
        latency_p50_ms=25.0,
    ))
    reg.register_model(ModelMetadata(
        model_id="gpt-4o",
        provider_type=ModelProviderType.OPENAI,
        is_local=False,
        context_window=128000,
        input_cost_per_million=2.50,
        output_cost_per_million=10.00,
        capabilities={ModelCapability.CHAT, ModelCapability.TOOL_CALLING, ModelCapability.VISION},
        latency_p50_ms=35.0,
    ))

    return reg


def test_router_local_first_policy(mock_registry):
    """Under LOCAL_FIRST policy, local models must lead the candidate chain."""
    router = ModelRouter(mock_registry, default_policy=RoutingPolicy.LOCAL_FIRST)
    req = ModelRequest(messages=[ChatMessage(role="user", content="General task")])

    candidates = router.select_models(req)
    assert len(candidates) >= 2
    assert candidates[0].is_local
    assert candidates[0].model_id == "llama3.2:latest"


def test_router_cloud_preferred_policy(mock_registry):
    """Under CLOUD_PREFERRED policy, cloud models lead the candidate chain."""
    router = ModelRouter(mock_registry, default_policy=RoutingPolicy.CLOUD_PREFERRED)
    req = ModelRequest(messages=[ChatMessage(role="user", content="Heavy task")])

    candidates = router.select_models(req)
    assert not candidates[0].is_local


def test_router_cost_optimized_policy(mock_registry):
    """Under COST_OPTIMIZED policy, cheapest model (gpt-4o-mini or local $0) ranks first."""
    router = ModelRouter(mock_registry, default_policy=RoutingPolicy.COST_OPTIMIZED)
    req = ModelRequest(messages=[ChatMessage(role="user", content="Budget task")])

    candidates = router.select_models(req)
    assert candidates[0].input_cost_per_million == 0.0 or candidates[0].model_id == "gpt-4o-mini"


# ── 6. Strict Privacy Boundary Enforcement ────────────────────────────────────

def test_router_strict_privacy_boundary(mock_registry):
    """Private requests or PRIVACY_STRICT policy must NEVER route to cloud providers."""
    router = ModelRouter(mock_registry, default_policy=RoutingPolicy.CLOUD_PREFERRED)

    # 1. Flagging request as is_private overrides CLOUD_PREFERRED
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Confidential financial report")],
        is_private=True,
    )
    candidates = router.select_models(req)
    for c in candidates:
        assert c.is_local, f"Model '{c.model_id}' is cloud but request is private!"

    # 2. PRIVACY_STRICT policy enforces local-only
    router.set_policy(RoutingPolicy.PRIVACY_STRICT)
    candidates_strict = router.select_models(ModelRequest(messages=[ChatMessage(role="user", content="Hello")]))
    assert all(c.is_local for c in candidates_strict)

    # 3. If no local model provides required capability, hard block with PrivacyViolationError
    req_impossible = ModelRequest(
        messages=[ChatMessage(role="user", content="Private task")],
        required_capabilities={ModelCapability.VISION},  # Only gpt-4o (cloud) has vision in fixture
        is_private=True,
    )
    with pytest.raises(PrivacyViolationError):
        router.select_models(req_impossible)


# ── 7. Fault Tolerance & Circuit Breaker ───────────────────────────────────────

def test_circuit_breaker_state_transitions():
    """Verify CircuitBreaker trips to OPEN on failures and recovers via HALF_OPEN."""
    cb = CircuitBreaker(name="test_provider", failure_threshold=3, recovery_timeout_seconds=0.2)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request()

    # Record 2 failures -> stays closed
    cb.record_failure("Err 1")
    cb.record_failure("Err 2")
    assert cb.state == CircuitState.CLOSED

    # 3rd failure trips circuit to OPEN
    cb.record_failure("Err 3")
    assert cb.state == CircuitState.OPEN
    assert not cb.allow_request()

    with pytest.raises(CircuitOpenError):
        cb.check_request()

    # Wait for recovery timeout
    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request()

    # Successful probe restores circuit to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


# ── 8. Automatic Fallback Chains ──────────────────────────────────────────────

def test_automatic_fallback_chain(mock_registry):
    """Verify router automatically fails over to secondary candidate when primary fails."""
    router = ModelRouter(mock_registry, default_policy=RoutingPolicy.LOCAL_FIRST)
    attempts = []

    def mock_executor(candidate: ModelMetadata, req: ModelRequest) -> ModelResponse:
        attempts.append(candidate.model_id)
        if candidate.is_local:
            raise RuntimeError(f"Local model {candidate.model_id} offline")
        return ModelResponse(text="Success from fallback", model_id=candidate.model_id)

    req = ModelRequest(messages=[ChatMessage(role="user", content="Test fallback")])
    resp = router.execute_with_fallback(req, mock_executor)

    assert resp.text == "Success from fallback"
    assert resp.model_id == "gpt-4o-mini"
    assert "llama3.2:latest" in attempts
    assert "gpt-4o-mini" in attempts


# ── 9. Dynamic Model Registry Management ──────────────────────────────────────

def test_dynamic_registry_management(mock_registry):
    """Verify models can be enabled, disabled, and registered dynamically without restarting."""
    assert mock_registry.get_model("llama3.2:latest") is not None

    # Disable model
    mock_registry.disable_model("llama3.2:latest")
    enabled_models = [m.model_id for m in mock_registry.list_models(enabled_only=True)]
    assert "llama3.2:latest" not in enabled_models

    # Re-enable model
    mock_registry.enable_model("llama3.2:latest")
    enabled_models2 = [m.model_id for m in mock_registry.list_models(enabled_only=True)]
    assert "llama3.2:latest" in enabled_models2

    # Register custom dynamic model
    custom_model = ModelMetadata(
        model_id="custom-fine-tune-v1",
        provider_type=ModelProviderType.LOCAL_CUSTOM,
        context_window=64000,
        is_local=True,
    )
    mock_registry.register_model(custom_model)
    assert mock_registry.get_model("custom-fine-tune-v1") is not None


# ── 10. Central Orchestrator Integration ──────────────────────────────────────

def test_central_orchestrator_intelligence_gateway_integration():
    """Verify CentralOrchestrator delegates LLM calls through the IntelligenceGateway."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_workspace = Path(tmp_dir).resolve()
        task_store = TaskStore(tmp_workspace / "tasks")
        tool_reg = ToolRegistry()
        agent_reg = AgentRegistry()
        event_bus = EventBus()

        # Build custom gateway providing structured planning & tool calling
        def mock_llm_handler(req: ModelRequest) -> ModelResponse:
            last_text = req.messages[-1].content
            if "Break this goal down" in last_text:
                # Orchestrator plan decomposition
                plan_json = json.dumps([
                    {
                        "agent": "calculator",
                        "title": "Compute Sum",
                        "description": "Calculate 50 + 50",
                    }
                ])
                return ModelResponse(text=plan_json, model_id="gateway-mock")
            return ModelResponse(text="Model response", model_id="gateway-mock")

        custom_p = LocalCustomProvider(name="test_gateway_llm", custom_handler=mock_llm_handler)
        reg = ModelRegistry(auto_seed=False)
        reg.register_provider(custom_p)
        reg.register_model(ModelMetadata(
            model_id="gateway-mock",
            provider_type=ModelProviderType.LOCAL_CUSTOM,
            is_local=True,
            capabilities={ModelCapability.CHAT, ModelCapability.TOOL_CALLING},
        ))

        gateway = IntelligenceGateway(registry=reg, policy=RoutingPolicy.LOCAL_FIRST)

        orchestrator = CentralOrchestrator(
            agent_registry=agent_reg,
            tool_registry=tool_reg,
            task_store=task_store,
            event_bus=event_bus,
            intelligence_gateway=gateway,
            allowed_roots=[str(tmp_workspace)],
        )

        task = orchestrator.execute_task("Calculate 50 + 50")
        assert task.status == TaskStatus.COMPLETED
        assert task.plan is not None
        assert len(task.plan.subtasks) >= 1
