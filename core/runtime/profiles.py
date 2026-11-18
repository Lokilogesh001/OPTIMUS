"""core/runtime/profiles.py — Hardware Capability Profiles & Adaptive Rules for OPTIMUS.

Implements adaptive hardware execution profiles:
1. CPU_ONLY:
   - Zero GPU or CUDA dependency.
   - Cloud inference preferred; optional quantized CPU local inference.
   - Conservative agent concurrency (1-2 agents).
   - Relaxed background telemetry polling (4000-6000ms).
   - Bounded context window budgets.
2. GPU_ACCELERATED:
   - Leverages detected GPU acceleration backends (CUDA, ROCm, DirectML, Metal).
   - Higher concurrency (3-5 agents).
   - Standard background telemetry (3000ms).
   - Respects VRAM limits with automatic CPU fallback when VRAM is tight or acceleration fails.
3. HYBRID:
   - CPU-centric orchestrator and tool execution.
   - Offloads supported inference, vision, and embeddings to GPU.
   - Moderate concurrency (2-4 agents).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from core.runtime.hardware import AccelerationBackend, HardwareReport

logger = logging.getLogger("optimus.runtime.profiles")


class HardwareProfileType(str, Enum):
    """Active hardware execution profile."""
    CPU_ONLY = "CPU_ONLY"
    GPU_ACCELERATED = "GPU_ACCELERATED"
    HYBRID = "HYBRID"


@dataclass
class HardwareProfile:
    """Configuration envelope dictating runtime concurrency and resource limits."""
    profile_type: HardwareProfileType
    description: str
    max_concurrent_agents: int
    max_worker_threads: int
    max_context_tokens: int
    background_poll_interval_ms: int
    max_cached_skills: int
    allow_gpu_acceleration: bool
    preferred_inference_mode: str  # "cloud_preferred", "local_gpu", "local_cpu", "cloud_only"
    cpu_fallback_enabled: bool = True
    vram_headroom_mb: float = 512.0  # Buffer to prevent VRAM exhaustion


class CpuOnlyProfile(HardwareProfile):
    """Conservative, reliable execution profile for CPU-only systems or disabled GPU environments."""

    def __init__(self) -> None:
        super().__init__(
            profile_type=HardwareProfileType.CPU_ONLY,
            description="CPU-First execution: Zero GPU requirements, conservative concurrency, cloud preferred.",
            max_concurrent_agents=2,
            max_worker_threads=4,
            max_context_tokens=8192,
            background_poll_interval_ms=5000,
            max_cached_skills=8,
            allow_gpu_acceleration=False,
            preferred_inference_mode="cloud_preferred",
            cpu_fallback_enabled=True,
            vram_headroom_mb=0.0,
        )


class GpuAcceleratedProfile(HardwareProfile):
    """High-throughput execution profile for systems with dedicated GPU & substantial VRAM."""

    def __init__(self, vram_mb: float = 4096.0) -> None:
        super().__init__(
            profile_type=HardwareProfileType.GPU_ACCELERATED,
            description=f"GPU-Accelerated execution: Acceleration enabled ({vram_mb:.0f} MB VRAM), high concurrency.",
            max_concurrent_agents=4,
            max_worker_threads=12,
            max_context_tokens=16384,
            background_poll_interval_ms=3000,
            max_cached_skills=20,
            allow_gpu_acceleration=True,
            preferred_inference_mode="local_gpu",
            cpu_fallback_enabled=True,
            vram_headroom_mb=512.0,
        )


class HybridProfile(HardwareProfile):
    """Balanced execution profile: Lightweight CPU orchestration paired with GPU-accelerated inference."""

    def __init__(self, vram_mb: float = 4096.0) -> None:
        super().__init__(
            profile_type=HardwareProfileType.HYBRID,
            description=f"Hybrid execution: CPU orchestration with GPU offloading ({vram_mb:.0f} MB VRAM).",
            max_concurrent_agents=3,
            max_worker_threads=8,
            max_context_tokens=12288,
            background_poll_interval_ms=3000,
            max_cached_skills=15,
            allow_gpu_acceleration=True,
            preferred_inference_mode="cloud_preferred",
            cpu_fallback_enabled=True,
            vram_headroom_mb=512.0,
        )


def evaluate_hardware_profile(report: HardwareReport, user_override: str | None = None) -> HardwareProfile:
    """Dynamically determine the optimal hardware profile based on hardware specs and user preference."""
    if user_override:
        ov = user_override.strip().upper()
        if ov == HardwareProfileType.CPU_ONLY.value:
            return CpuOnlyProfile()
        if ov == HardwareProfileType.GPU_ACCELERATED.value:
            vram = report.primary_gpu.total_vram_mb if report.primary_gpu else 4096.0
            return GpuAcceleratedProfile(vram_mb=vram)
        if ov == HardwareProfileType.HYBRID.value:
            vram = report.primary_gpu.total_vram_mb if report.primary_gpu else 4096.0
            return HybridProfile(vram_mb=vram)

    # Automatic evaluation
    if not report.is_gpu_available or not report.primary_gpu:
        logger.info("No dedicated GPU detected; selecting CPU_ONLY profile.")
        return CpuOnlyProfile()

    vram = report.primary_gpu.total_vram_mb
    has_accel = any(b in (AccelerationBackend.CUDA, AccelerationBackend.DIRECTML, AccelerationBackend.ROCM, AccelerationBackend.METAL) for b in report.supported_backends)

    if not has_accel:
        logger.info("GPU detected but no compatible acceleration backend; selecting CPU_ONLY profile.")
        return CpuOnlyProfile()

    # If VRAM is constrained (< 3 GB), prefer CPU_ONLY or conservative Hybrid to prevent OOM
    if vram < 3072.0:
        logger.info("GPU VRAM is constrained (%.1f MB < 3GB); selecting CPU_ONLY profile for stability.", vram)
        return CpuOnlyProfile()

    # If VRAM >= 6 GB, GPU_ACCELERATED is safe; if 3-6 GB (like RTX 3050 4GB), HYBRID is ideal
    if vram >= 6144.0:
        logger.info("High-capacity GPU detected (%.1f MB); selecting GPU_ACCELERATED profile.", vram)
        return GpuAcceleratedProfile(vram_mb=vram)

    logger.info("Balanced GPU detected (%.1f MB); selecting HYBRID profile.", vram)
    return HybridProfile(vram_mb=vram)
