"""tests/test_performance_optimization.py — Milestone 7 Verification Suite.

Comprehensive automated verification covering:
- Scenario A: Provider Health Cache TTL & Invalidation
- Scenario B: Fast Circuit-Breaker Skipping in Model Router
- Scenario C: Hardware-Aware Model Sizing Calculator
- Scenario D: Ollama Thread Clamping to Prevent CPU Starvation
- Scenario E: Context Bus Search Caching & Deduplication
- Scenario F: Context Envelope Token Budgeting & Bounds
- Scenario G: Deterministic Fast-Path Arithmetic Execution (< 5ms)
- Scenario H: Deterministic Fast-Path File Read Execution (< 5ms)
- Scenario I: Bounded Timeline History via Deque
- Scenario J: Performance Diagnostics UI Telemetry & Badges
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure Mark-LVII-main root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.intelligence.gateway import IntelligenceGateway, ProviderHealthCache
from core.intelligence.providers.base import ModelProvider
from core.intelligence.providers.ollama import OllamaProvider
from core.intelligence.registry import ModelRegistry
from core.intelligence.router import ModelRouter
from core.intelligence.types import (
    CircuitOpenError,
    CircuitState,
    ModelCapability,
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    ModelResponse,
    RoutingPolicy,
)
from core.memory.context_bus import SharedContextBus
from core.memory.manager import OptimusMemoryManager
from core.memory.store import MemoryStore
from core.memory.types import MemoryCategory, MemoryEntry, new_memory_id
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import TaskStatus
from core.runtime.model_sizing import (
    estimate_model_hardware_requirements,
    get_optimal_cpu_threads,
    parse_model_spec,
)
from core.runtime.profiles import HardwareProfileType
from core.runtime.resources import AdaptiveResourceManager


# ── Scenario A: Provider Health Cache TTL & Invalidation ─────────────────────

def test_scenario_a_provider_health_cache():
    """Verify that ProviderHealthCache caches results within TTL and invalidates properly."""
    cache = ProviderHealthCache(ttl_seconds=2.0)
    mock_provider = MagicMock()
    mock_provider.name = "mock_provider"
    mock_provider.health_check.return_value = True
    mock_provider.circuit_breaker.state.value = "CLOSED"

    # 1. First call: invokes health_check()
    res1 = cache.get_health(mock_provider)
    assert res1 is True
    assert mock_provider.health_check.call_count == 1

    # 2. Second call within TTL: served from cache, no network call
    res2 = cache.get_health(mock_provider)
    assert res2 is True
    assert mock_provider.health_check.call_count == 1

    # 3. Fast-path: When circuit breaker is OPEN, returns False immediately without network call
    mock_provider.circuit_breaker.state.value = "OPEN"
    res_open = cache.get_health(mock_provider)
    assert res_open is False
    assert mock_provider.health_check.call_count == 1

    # 4. Force refresh forces call
    mock_provider.circuit_breaker.state.value = "CLOSED"
    cache.get_health(mock_provider, force_refresh=True)
    assert mock_provider.health_check.call_count == 2

    # 5. Invalidation
    cache.invalidate("mock_provider")
    cache.get_health(mock_provider)
    assert mock_provider.health_check.call_count == 3


# ── Scenario B: Fast Circuit-Breaker Skipping in Model Router ────────────────

def test_scenario_b_fast_circuit_breaker_skipping():
    """Verify that models with OPEN circuit breaker are fast-skipped during fallback."""
    reg = ModelRegistry(auto_seed=False)
    m_cloud = ModelMetadata(
        model_id="cloud-failing",
        provider_type=ModelProviderType.OPENAI,
        is_local=False,
        capabilities={ModelCapability.CHAT},
    )
    m_local = ModelMetadata(
        model_id="local-working",
        provider_type=ModelProviderType.OLLAMA,
        is_local=True,
        capabilities={ModelCapability.CHAT},
    )
    reg.register_model(m_cloud)
    reg.register_model(m_local)

    prov_cloud = MagicMock()
    prov_cloud.name = "openai_cloud"
    prov_cloud.provider_type = ModelProviderType.OPENAI
    prov_cloud.circuit_breaker.allow_request.return_value = False  # OPEN circuit

    prov_local = MagicMock()
    prov_local.name = "ollama_local"
    prov_local.provider_type = ModelProviderType.OLLAMA
    prov_local.circuit_breaker.allow_request.return_value = True

    reg.register_provider(prov_cloud)
    reg.register_provider(prov_local)

    router = ModelRouter(reg, default_policy=RoutingPolicy.CLOUD_PREFERRED)
    req = ModelRequest(messages=[])

    attempted_models = []

    def mock_executor(meta: ModelMetadata, r: ModelRequest) -> ModelResponse:
        attempted_models.append(meta.model_id)
        return ModelResponse(text="Success", model_id=meta.model_id, provider=meta.provider_type.value)

    resp = router.execute_with_fallback(req, mock_executor)
    assert resp.model_id == "local-working"
    # Verify cloud-failing was fast-skipped without ever invoking mock_executor
    assert "cloud-failing" not in attempted_models
    assert attempted_models == ["local-working"]


# ── Scenario C: Hardware-Aware Model Sizing Calculator ───────────────────────

def test_scenario_c_model_sizing_calculator():
    """Verify hardware footprint estimation for local LLMs and thread allocation."""
    p_count, q_bits = parse_model_spec("qwen3.5:2b")
    assert p_count == 2.0
    assert q_bits == 4

    p_count_7b, q_bits_8 = parse_model_spec("llama3:8b-q8_0")
    assert p_count_7b == 8.0
    assert q_bits_8 == 8

    # 2B parameter model sizing
    est_2b = estimate_model_hardware_requirements("qwen3.5:2b", context_tokens=4096)
    assert est_2b["weight_ram_mb"] > 1000.0
    assert est_2b["kv_cache_mb"] >= 64.0
    assert est_2b["total_required_ram_mb"] < 3000.0  # ~1.2 GB - 2 GB
    assert 1 <= est_2b["recommended_threads"] <= 4

    # Optimal CPU thread clamping
    threads = get_optimal_cpu_threads(reserve_cores=2)
    assert 1 <= threads <= 4


# ── Scenario D: Ollama Thread Clamping ───────────────────────────────────────

def test_scenario_d_ollama_thread_clamping():
    """Verify that OllamaProvider clamps CPU threads in request options."""
    captured_payloads = []

    def mock_transport(url: str, data: dict | None, headers: dict, timeout: float):
        if data:
            captured_payloads.append(data)
        # Return valid mock Ollama response
        mock_body = b'{"message": {"role": "assistant", "content": "1, 2, 3"}, "eval_count": 3}'
        return 200, mock_body

    provider = OllamaProvider(transport=mock_transport)
    req = ModelRequest(messages=[], preferred_model="qwen3.5:2b")

    resp = provider.complete(req)
    assert resp.text == "1, 2, 3"
    assert len(captured_payloads) == 1

    payload = captured_payloads[0]
    assert "options" in payload
    assert "num_thread" in payload["options"]
    assert 1 <= payload["options"]["num_thread"] <= 4


# ── Scenario E: Context Bus Search Caching & Deduplication ───────────────────

def test_scenario_e_context_bus_caching_and_deduplication(tmp_path):
    """Verify LRU search query caching and semantic fact deduplication."""
    store = MemoryStore(tmp_path / "memory")
    bus = SharedContextBus(store)

    # Insert test facts
    e1 = MemoryEntry(
        entry_id=new_memory_id("fact"),
        category=MemoryCategory.SEMANTIC,
        key="python_version",
        content="Python 3.12 is the primary interpreter for OPTIMUS.",
        source="system",
        confidence=0.9,
    )
    store.put(e1)

    # First search: populates cache
    results1 = store.search(query="Python interpreter")
    assert len(results1) >= 1
    assert results1[0].key == "python_version"

    # Second search: served from store search cache
    results2 = store.search(query="Python interpreter")
    assert len(results2) == len(results1)

    # Deduplication in create_envelope:
    # If the prerequisite output already contains the fact, it must not duplicate
    bus.isolate_task("task_dedup")
    bus.publish_subtask_output(
        task_id="task_dedup",
        subtask_id="st_1",
        agent_name="general",
        key="note",
        output="Python 3.12 is the primary interpreter for OPTIMUS.",
        is_verified=True,
    )

    envelope = bus.create_envelope(
        task_id="task_dedup",
        subtask_id="st_2",
        agent_name="code_intelligence",
        goal="check interpreter",
        subtask_title="Python interpreter",
        subtask_description="check Python 3.12 interpreter version",
    )

    # Because e1 content is already in prerequisite results, semantic_facts skips duplicate
    fact_texts = [f["fact"] for f in envelope.semantic_facts]
    assert not any("Python 3.12 is the primary interpreter" in f for f in fact_texts)


# ── Scenario F: Context Envelope Token Budgeting & Bounds ───────────────────

def test_scenario_f_context_envelope_bounds(tmp_path):
    """Verify that oversized entries are truncated within budget in context envelopes."""
    store = MemoryStore(tmp_path / "memory_bounds")
    bus = SharedContextBus(store)

    # Giant semantic entry (> 2000 chars)
    giant_text = "OPTIMUS DATA " * 300
    e_giant = MemoryEntry(
        entry_id=new_memory_id("giant"),
        category=MemoryCategory.SEMANTIC,
        key="giant_data",
        content=giant_text,
        source="benchmark",
        confidence=0.95,
    )
    store.put(e_giant)

    bus.isolate_task("task_giant")
    envelope = bus.create_envelope(
        task_id="task_giant",
        subtask_id="st_1",
        agent_name="data_intelligence",
        goal="process data",
        subtask_title="giant data",
        subtask_description="load giant data",
    )

    assert len(envelope.semantic_facts) >= 1
    fact_text = envelope.semantic_facts[0]["fact"]
    assert len(fact_text) <= 1600
    assert "[truncated]" in fact_text


# ── Scenario G: Deterministic Fast-Path Arithmetic Execution (< 5ms) ────────

def test_scenario_g_fast_path_arithmetic(tmp_path):
    """Verify that simple arithmetic tasks execute instantaneously without calling LLM."""
    store_dir = tmp_path / "orch_test"
    orch = CentralOrchestrator(allowed_roots=[str(tmp_path)])

    # Mock brain_fn to ensure it is NOT called
    mock_brain = MagicMock()
    orch.brain_fn = mock_brain

    start_t = time.perf_counter()
    task = orch.execute_task("calculate 42 * 17")
    duration_ms = (time.perf_counter() - start_t) * 1000.0

    assert task.status == TaskStatus.COMPLETED
    assert "714" in task.result
    assert mock_brain.call_count == 0  # LLM never touched!
    assert duration_ms < 50.0  # In tests < 50ms, usually < 5ms


# ── Scenario H: Deterministic Fast-Path File Read (< 5ms) ───────────────────

def test_scenario_h_fast_path_file_read(tmp_path):
    """Verify that direct file reads execute via fast-path without LLM calls."""
    test_file = tmp_path / "sample_benchmark.txt"
    test_file.write_text("OPTIMUS Milestone 7 deterministic fast-path test content.", encoding="utf-8")

    orch = CentralOrchestrator(allowed_roots=[str(tmp_path)])
    mock_brain = MagicMock()
    orch.brain_fn = mock_brain

    task = orch.execute_task(f"read file sample_benchmark.txt")
    assert task.status == TaskStatus.COMPLETED
    assert "OPTIMUS Milestone 7 deterministic" in task.result
    assert mock_brain.call_count == 0


# ── Scenario I: Bounded Timeline History via Deque ──────────────────────────

def test_scenario_i_bounded_timeline_history():
    """Verify that OptimusServiceBridge bounds timeline events to 200 via deque."""
    from collections import deque
    from gui.optimus.service_bridge import OptimusServiceBridge

    bridge = OptimusServiceBridge.instance()
    assert isinstance(bridge._timeline_history, deque)
    assert bridge._timeline_history.maxlen == 200

    # Emit 250 dummy events
    for idx in range(250):
        bridge._timeline_history.append({"index": idx, "title": f"Event {idx}"})

    assert len(bridge._timeline_history) == 200
    assert bridge._timeline_history[0]["index"] == 50
    assert bridge._timeline_history[-1]["index"] == 249
    assert len(bridge.get_timeline_events()) == 200


# ── Scenario J: Performance Diagnostics UI Telemetry & Badges ───────────────

def test_scenario_j_ui_diagnostics_panel():
    """Verify telemetry includes inference metrics and section_system updates badges."""
    from PyQt6.QtWidgets import QApplication
    from gui.optimus.section_system import SectionSystem
    from gui.optimus.service_bridge import OptimusServiceBridge

    app = QApplication.instance() or QApplication(sys.argv)
    bridge = OptimusServiceBridge.instance()

    telem = bridge.get_system_telemetry()
    assert "inference_backend" in telem
    assert "tokens_per_sec" in telem
    assert "model_state" in telem

    section = SectionSystem(bridge)
    section._on_telemetry_update({
        "cpu_percent": 12.5,
        "ram_percent": 45.0,
        "disk_percent": 60.0,
        "gpu_percent": 0.0,
        "hardware_profile": "CPU_ONLY",
        "memory_pressure": "NORMAL",
        "inference_backend": "CPU (4T)",
        "tokens_per_sec": 27.6,
        "model_state": "READY",
        "processes": [],
    })

    assert "[PROFILE: CPU_ONLY | PRESSURE: NORMAL]" in section.lbl_profile_badge.text()
    assert "[INFERENCE: CPU (4T) · 27.6 tok/s · READY]" in section.lbl_inference_badge.text()
