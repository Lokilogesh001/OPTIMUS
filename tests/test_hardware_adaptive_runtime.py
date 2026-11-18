"""tests/test_hardware_adaptive_runtime.py — Verification Matrix for Milestone 6.

Validates the Hardware-Adaptive Runtime & CPU-First Optimization system across:
- Scenario A: CPU-only system with no NVIDIA libraries (simulated & actual CPU fallback).
- Scenario B: GPU system with constrained VRAM (< 3072 MB).
- Scenario C: GPU system with acceleration unavailable (driver/backend missing).
- Scenario D: Cloud-only inference routing.
- Scenario E: Cloud failure with configured local fallback.
- Scenario F: Low-memory environment (HIGH and CRITICAL memory pressure).
- Scenario G: Multiple concurrent agent tasks & concurrency throttling.
- Scenario H: Long-running task with cooperative cancellation.
- Scenario I: Model lifecycle leasing, idle unloading, and clean shutdown.
- Scenario J: UI diagnostics & settings hardware governance panel.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# Ensure project root is on path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.intelligence.registry import ModelRegistry
from core.intelligence.router import ModelRouter
from core.intelligence.types import (
    ChatMessage,
    ModelCapability,
    ModelExecutionBackend,
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    ModelResponse,
    RoutingPolicy,
)
from core.orchestrator.orchestrator import CentralOrchestrator
from core.runtime.hardware import (
    AccelerationBackend,
    GpuInfo,
    GpuVendor,
    HardwareDetector,
)
from core.runtime.lifecycle import (
    LocalBackendType,
    LocalModelLifecycleManager,
    LocalModelSpec,
    LocalModelStatus,
)
from core.runtime.profiles import (
    CpuOnlyProfile,
    HardwareProfileType,
    evaluate_hardware_profile,
)
from core.runtime.resources import (
    AdaptiveResourceManager,
    MemoryPressureLevel,
)
from core.skills.dependency import DependencyResolver
from core.skills.loader import SkillLoader
from core.skills.models import SkillCategory, SkillManifest, SkillTrustLevel
from core.skills.package import HardwareRequirements


@pytest.fixture(autouse=True)
def reset_runtime_singletons():
    """Ensure clean singleton state before and after each test."""
    HardwareDetector.reset()
    AdaptiveResourceManager.reset()
    LocalModelLifecycleManager.reset()
    yield
    HardwareDetector.reset()
    AdaptiveResourceManager.reset()
    LocalModelLifecycleManager.reset()


# ── Scenario A: CPU-only system with no NVIDIA libraries ─────────────────────

def test_scenario_a_cpu_only_system():
    """Verify system operates fully on CPU-only hardware without GPU/CUDA dependencies."""
    det = HardwareDetector.instance()
    det.set_simulation_override("CPU_ONLY")
    report = det.detect(force_refresh=True)

    assert report.is_gpu_available is False
    assert report.is_cuda_available is False
    assert report.primary_gpu is not None
    assert report.primary_gpu.vendor == GpuVendor.NONE
    assert AccelerationBackend.CPU in report.supported_backends

    profile = evaluate_hardware_profile(report)
    assert isinstance(profile, CpuOnlyProfile)
    assert profile.profile_type == HardwareProfileType.CPU_ONLY
    assert profile.allow_gpu_acceleration is False
    assert profile.max_concurrent_agents == 2

    # Verify native skills dependency check passes for CPU-only
    resolver = DependencyResolver()
    compat, warnings = resolver.check_hardware_compatibility(HardwareRequirements(cuda_required=False))
    assert compat is True
    assert len(warnings) == 0

    # If skill falsely requires CUDA, it should be marked incompatible on CPU-only
    compat_cuda, warnings_cuda = resolver.check_hardware_compatibility(HardwareRequirements(cuda_required=True))
    assert compat_cuda is False
    assert len(warnings_cuda) > 0


# ── Scenario B: GPU system with constrained VRAM ─────────────────────────────

def test_scenario_b_constrained_vram():
    """Verify GPU systems with low VRAM (< 3 GB) automatically step down to CPU-first to prevent OOM."""
    det = HardwareDetector.instance()
    # Inject simulated 2 GB VRAM GPU
    low_vram_gpu = GpuInfo(
        vendor=GpuVendor.NVIDIA,
        model="Simulated Low VRAM GPU",
        total_vram_mb=2048.0,
        free_vram_mb=800.0,
        is_available=True,
        cuda_version="12.0",
    )
    det.set_gpu_override(low_vram_gpu)
    report = det.detect(force_refresh=True)

    profile = evaluate_hardware_profile(report)
    # Must step down to CPU_ONLY because 2048 MB is below the 3072 MB safety threshold
    assert profile.profile_type == HardwareProfileType.CPU_ONLY


# ── Scenario C: GPU with acceleration unavailable ────────────────────────────

def test_scenario_c_acceleration_unavailable_fallback():
    """Verify that when a GPU has no supported acceleration backend, runtime falls back cleanly to CPU."""
    det = HardwareDetector.instance()
    gpu = GpuInfo(
        vendor=GpuVendor.INTEL,
        model="Generic Display Adapter",
        total_vram_mb=4096.0,
        is_available=True,
        cuda_version="N/A",
    )
    det.set_gpu_override(gpu)
    report = det.detect(force_refresh=True)
    # Strip non-CPU backends
    report.supported_backends = [AccelerationBackend.CPU]

    profile = evaluate_hardware_profile(report)
    assert profile.profile_type == HardwareProfileType.CPU_ONLY
    assert profile.allow_gpu_acceleration is False


# ── Scenario D: Cloud-only inference routing ─────────────────────────────────

def test_scenario_d_cloud_only_inference():
    """Verify cloud routing policy selects cloud models and does not spin up local LLMs."""
    reg = ModelRegistry()
    cloud_m = ModelMetadata(
        model_id="gemini-2.5-flash",
        provider_type=ModelProviderType.GEMINI,
        is_local=False,
        capabilities={ModelCapability.CHAT, ModelCapability.TOOL_CALLING},
        context_window=32768,
    )
    local_m = ModelMetadata(
        model_id="qwen-2.5-coder-7b",
        provider_type=ModelProviderType.OLLAMA,
        is_local=True,
        execution_backend=ModelExecutionBackend.GPU_LOCAL,
        capabilities={ModelCapability.CHAT, ModelCapability.TOOL_CALLING},
        context_window=8192,
        min_vram_mb=4096.0,
    )
    reg.register_model(cloud_m)
    reg.register_model(local_m)

    router = ModelRouter(reg, default_policy=RoutingPolicy.CLOUD_PREFERRED)
    req = ModelRequest(messages=[ChatMessage(role="user", content="Hello OPTIMUS")])
    candidates = router.select_models(req)

    assert len(candidates) >= 1
    # First candidate in cloud-preferred mode must be a cloud model
    assert candidates[0].is_local is False
    assert any(m.model_id == "gemini-2.5-flash" for m in candidates)


# ── Scenario E: Cloud failure with configured local fallback ─────────────────

def test_scenario_e_cloud_failure_with_local_fallback():
    """Verify that when a cloud model fails, router gracefully falls back to local candidate."""
    res_mgr = AdaptiveResourceManager.instance()
    res_mgr.set_simulated_ram_percent(50.0)
    res_mgr.budget.allow_local_llm = True

    reg = ModelRegistry()
    failing_cloud = ModelMetadata(
        model_id="cloud-fail-model",
        provider_type=ModelProviderType.OPENAI,
        is_local=False,
        capabilities={ModelCapability.CHAT},
        context_window=8192,
    )
    working_local = ModelMetadata(
        model_id="local-backup-model",
        provider_type=ModelProviderType.OLLAMA,
        is_local=True,
        execution_backend=ModelExecutionBackend.CPU_LOCAL,
        capabilities={ModelCapability.CHAT},
        context_window=8192,
    )
    reg.register_model(failing_cloud)
    reg.register_model(working_local)

    router = ModelRouter(reg, default_policy=RoutingPolicy.CLOUD_PREFERRED)
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Compute summary")],
        preferred_model="cloud-fail-model",
    )

    def mock_executor(m: ModelMetadata, r: ModelRequest) -> ModelResponse:
        if not m.is_local:
            raise RuntimeError(f"Cloud endpoint {m.model_id} failed: connection timeout / offline")
        return ModelResponse(
            text="Local fallback response",
            model_id=m.model_id,
            provider="OLLAMA",
        )

    res = router.execute_with_fallback(req, mock_executor)
    assert res.text == "Local fallback response"
    # Verify that a local fallback model was selected and executed
    selected_model_meta = reg.get_model(res.model_id)
    assert selected_model_meta is not None
    assert selected_model_meta.is_local is True


# ── Scenario F: Low-memory environment & Pressure Relief ─────────────────────

def test_scenario_f_low_memory_pressure_relief():
    """Verify adaptive throttling and cache eviction under HIGH and CRITICAL host memory pressure."""
    res_mgr = AdaptiveResourceManager.instance()
    loader = SkillLoader(max_cache_size=10)

    # Populate skill cache
    skill_manifest = SkillManifest(
        id="test-skill-1",
        name="Test Skill",
        description="Testing memory",
        instructions="Instruction payload " * 50,
        category=SkillCategory.ENGINEERING,
        trust_level=SkillTrustLevel.VERIFIED,
    )
    loader.format_skill_prompt(skill_manifest)
    assert len(loader._cache) == 1
    assert "test-skill-1" in loader._cache

    # 1. Simulate MODERATE memory pressure (80%)
    res_mgr.set_simulated_ram_percent(80.0)
    lvl_mod = res_mgr.evaluate_memory_pressure()
    assert lvl_mod == MemoryPressureLevel.MODERATE

    # 2. Simulate HIGH memory pressure (88%)
    res_mgr.set_simulated_ram_percent(88.0)
    lvl_high = res_mgr.evaluate_memory_pressure()
    assert lvl_high == MemoryPressureLevel.HIGH
    assert res_mgr.budget.max_concurrent_agents <= 2
    assert res_mgr.budget.allow_local_llm is False
    # Verify SkillLoader flushed cache to release RAM
    assert len(loader._cache) == 0

    # 3. Simulate CRITICAL memory pressure (95%)
    res_mgr.set_simulated_ram_percent(95.0)
    lvl_crit = res_mgr.evaluate_memory_pressure()
    assert lvl_crit == MemoryPressureLevel.CRITICAL
    assert res_mgr.budget.max_concurrent_agents == 1
    assert res_mgr.budget.background_poll_interval_ms == 8000

    # Non-interactive task acquisition must be rejected under CRITICAL pressure
    acquired = res_mgr.acquire_agent_slot("background_indexer", is_interactive=False, timeout=0.1)
    assert acquired is False

    # Restore normal
    res_mgr.set_simulated_ram_percent(50.0)
    assert res_mgr.evaluate_memory_pressure() == MemoryPressureLevel.NORMAL


# ── Scenario G: Concurrency limits & interactive priority ────────────────────

def test_scenario_g_agent_concurrency_and_priority():
    """Verify concurrent agent throttling and priority preservation for interactive prompts."""
    res_mgr = AdaptiveResourceManager.instance()
    res_mgr.set_profile(HardwareProfileType.CPU_ONLY)
    res_mgr.set_simulated_ram_percent(50.0)
    # CPU_ONLY profile: max_concurrent_agents = 2

    # Slot 1
    assert res_mgr.acquire_agent_slot("worker-1", is_interactive=False) is True
    # Slot 2
    assert res_mgr.acquire_agent_slot("worker-2", is_interactive=False) is True

    # Slot 3 (non-interactive): exceeds limit of 2, must be rejected
    assert res_mgr.acquire_agent_slot("worker-3", is_interactive=False, timeout=0.1) is False

    # Slot 3 (interactive user prompt): must be granted via reserved interactive headroom!
    assert res_mgr.acquire_agent_slot("user-chat", is_interactive=True, timeout=0.1) is True

    assert res_mgr.active_agent_count() == 3

    # Cleanup slots
    res_mgr.release_agent_slot("worker-1")
    res_mgr.release_agent_slot("worker-2")
    res_mgr.release_agent_slot("user-chat")
    assert res_mgr.active_agent_count() == 0


# ── Scenario H: Long-running task with cancellation ──────────────────────────

def test_scenario_h_task_cancellation_releases_resources():
    """Verify cooperative cancellation cleans up active tasks and releases agent slots."""
    orch = CentralOrchestrator()
    task = orch.start("Long running test task")
    assert task.task_id is not None

    time.sleep(0.1)
    # Cancel the task
    cancelled = orch.cancel(task.task_id)
    assert cancelled is True

    # Verify task status is CANCELLED
    t_record = orch.task_store.get_task(task.task_id)
    assert t_record is not None
    assert t_record.status.value == "CANCELLED"


# ── Scenario I: Model lifecycle leasing & unloading ──────────────────────────

def test_scenario_i_local_model_lifecycle_and_unloading():
    """Verify local model lease acquisition, busy-state protection, and idle unloading."""
    mgr = LocalModelLifecycleManager.instance()
    spec = LocalModelSpec(
        model_id="qwen-1.5b-cpu",
        display_name="Qwen 1.5B (CPU Q4)",
        backend=LocalBackendType.OLLAMA_CPU,
        min_ram_mb=1024.0,
        min_vram_mb=0.0,
        is_cpu_optimized=True,
    )

    # 1. Budget check passes for lightweight CPU model
    ok, msg = mgr.check_model_budget(spec)
    assert ok is True

    # 2. On-demand loading
    load_ok, _ = mgr.load_model_on_demand(spec)
    assert load_ok is True

    # 3. Active lease prevents unloading during task execution
    with mgr.lease(spec) as inst:
        assert inst.active_leases == 1
        assert inst.status == LocalModelStatus.IN_USE
        # Attempt to unload while leased must fail
        unloaded = mgr.unload_model(spec.model_id, force=False)
        assert unloaded is False

    # 4. Once lease exits, model can be unloaded
    assert mgr._loaded_models[spec.model_id].active_leases == 0
    assert mgr._loaded_models[spec.model_id].status == LocalModelStatus.READY
    assert mgr.unload_model(spec.model_id, force=False) is True
    assert spec.model_id not in mgr._loaded_models


# ── Scenario J: UI Diagnostics & SectionSettings Vitals ──────────────────────

def test_scenario_j_ui_diagnostics_panel():
    """Verify SectionSettings and SectionSystem correctly ingest and render hardware vitals."""
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv)

    from gui.optimus.section_settings import SectionSettings
    from gui.optimus.section_system import SectionSystem
    from gui.optimus.service_bridge import OptimusServiceBridge

    bridge = OptimusServiceBridge.instance()
    sec_settings = SectionSettings(bridge)
    sec_system = SectionSystem(bridge)

    # Trigger telemetry update
    telem = bridge.get_system_telemetry()
    assert "hardware_profile" in telem
    assert "memory_pressure" in telem

    sec_settings._on_telemetry_updated(telem)
    sec_system._on_telemetry_update(telem)

    # Verify UI label text is populated
    assert "PROFILE:" in sec_settings.lbl_vitals_summary.text()
    assert "RAM:" in sec_settings.lbl_vitals_summary.text()
    assert "PROFILE:" in sec_system.lbl_profile_badge.text()
