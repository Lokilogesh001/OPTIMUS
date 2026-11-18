"""core/runtime/resources.py — Central Adaptive Resource Manager for OPTIMUS.

Dynamically manages:
- Agent concurrency and worker thread allocation.
- Dynamic memory pressure detection and graceful degradation.
- Task context size limits and skill cache bounds.
- Background polling throttling under high system load.
- Priority scheduling (ensuring interactive requests never starve behind background tasks).
- Model loading permissions based on RAM and VRAM availability.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.runtime.hardware import HardwareDetector, HardwareReport
from core.runtime.profiles import (
    HardwareProfile,
    HardwareProfileType,
    evaluate_hardware_profile,
)

logger = logging.getLogger("optimus.runtime.resources")


class MemoryPressureLevel(str, Enum):
    """Current host memory pressure level."""
    NORMAL = "NORMAL"          # < 75% RAM used: full capacity
    MODERATE = "MODERATE"      # 75% - 85% RAM used: gentle throttling
    HIGH = "HIGH"              # 85% - 93% RAM used: aggressive throttling, unload idle models
    CRITICAL = "CRITICAL"      # >= 93% RAM used: pause optional tasks, compact contexts


@dataclass
class ResourceBudget:
    """Configurable and dynamically adjusted resource limits."""
    max_concurrent_agents: int = 3
    max_worker_threads: int = 8
    max_context_tokens: int = 8192
    background_poll_interval_ms: int = 3000
    max_cached_skills: int = 15
    task_timeout_seconds: float = 120.0
    allow_local_llm: bool = True
    max_vram_mb: float = 4096.0

    # User configured overrides
    user_concurrency_override: int | None = None
    user_poll_interval_override: int | None = None
    user_local_llm_override: bool | None = None


class AdaptiveResourceManager:
    """Central resource governor coordinating concurrency, budgets, and memory pressure."""

    _instance: AdaptiveResourceManager | None = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls, detector: HardwareDetector | None = None) -> AdaptiveResourceManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(detector=detector)
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton for test isolation."""
        with cls._lock:
            cls._instance = None

    def __init__(self, detector: HardwareDetector | None = None) -> None:
        self.detector = detector or HardwareDetector.instance()
        self._report: HardwareReport = self.detector.detect()
        self.active_profile: HardwareProfile = evaluate_hardware_profile(self._report)
        self.budget: ResourceBudget = self._build_budget_from_profile(self.active_profile)

        # Dynamic Tracking State
        self._rw_lock = threading.RLock()
        self._active_agents: dict[str, float] = {}  # agent_id/name -> start_time
        self._interactive_slots: int = 1  # Reserved buffer for immediate user prompts
        self._relief_handlers: list[Callable[[MemoryPressureLevel], None]] = []
        self._last_pressure: MemoryPressureLevel = MemoryPressureLevel.NORMAL
        self._simulated_ram_pct: float | None = None

    def _build_budget_from_profile(self, profile: HardwareProfile) -> ResourceBudget:
        """Initialize resource budget from a hardware profile."""
        vram = self._report.primary_gpu.total_vram_mb if self._report.primary_gpu else 0.0
        return ResourceBudget(
            max_concurrent_agents=profile.max_concurrent_agents,
            max_worker_threads=profile.max_worker_threads,
            max_context_tokens=profile.max_context_tokens,
            background_poll_interval_ms=profile.background_poll_interval_ms,
            max_cached_skills=profile.max_cached_skills,
            task_timeout_seconds=120.0,
            allow_local_llm=(profile.profile_type != HardwareProfileType.CPU_ONLY or self._report.memory.available_ram_gb >= 6.0),
            max_vram_mb=vram,
        )

    # ── Hardware Profile Management ──────────────────────────────────────────

    def get_profile(self) -> HardwareProfile:
        with self._rw_lock:
            return self.active_profile

    def set_profile(self, profile_type: HardwareProfileType | str) -> None:
        """Explicitly switch the active hardware execution profile."""
        with self._rw_lock:
            p_val = profile_type.value if isinstance(profile_type, HardwareProfileType) else str(profile_type)
            self.active_profile = evaluate_hardware_profile(self._report, user_override=p_val)
            self.budget = self._build_budget_from_profile(self.active_profile)
            self._apply_user_overrides()
            logger.info("Hardware profile switched to: %s", self.active_profile.profile_type.value)

    def set_simulated_ram_percent(self, pct: float | None) -> None:
        """Inject simulated RAM percent for testing memory pressure behavior."""
        with self._rw_lock:
            self._simulated_ram_pct = pct

    # ── Memory Pressure & Adaptive Throttling ────────────────────────────────

    def evaluate_memory_pressure(self) -> MemoryPressureLevel:
        """Assess host RAM utilization and classify memory pressure level."""
        with self._rw_lock:
            if self._simulated_ram_pct is not None:
                ram_pct = self._simulated_ram_pct
            else:
                mem = self.detector.detect_memory()
                ram_pct = mem.percent_used

            if ram_pct >= 93.0:
                pressure = MemoryPressureLevel.CRITICAL
            elif ram_pct >= 85.0:
                pressure = MemoryPressureLevel.HIGH
            elif ram_pct >= 75.0:
                pressure = MemoryPressureLevel.MODERATE
            else:
                pressure = MemoryPressureLevel.NORMAL

            if pressure != self._last_pressure:
                logger.warning("Memory pressure shifted from %s to %s (RAM: %.1f%%)", self._last_pressure.value, pressure.value, ram_pct)
                self._last_pressure = pressure
                self._on_pressure_change(pressure)

            return pressure

    def _on_pressure_change(self, pressure: MemoryPressureLevel) -> None:
        """Adjust budgets dynamically and invoke registered relief callbacks."""
        # 1. Adapt budgets
        if pressure == MemoryPressureLevel.CRITICAL:
            self.budget.max_concurrent_agents = 1
            self.budget.max_context_tokens = 4096
            self.budget.background_poll_interval_ms = 8000
            self.budget.max_cached_skills = 3
            self.budget.allow_local_llm = False
            self.budget.task_timeout_seconds = 60.0
        elif pressure == MemoryPressureLevel.HIGH:
            self.budget.max_concurrent_agents = max(1, self.active_profile.max_concurrent_agents - 2)
            self.budget.max_context_tokens = min(8192, self.active_profile.max_context_tokens)
            self.budget.background_poll_interval_ms = 6000
            self.budget.max_cached_skills = 5
            self.budget.allow_local_llm = False
            self.budget.task_timeout_seconds = 90.0
        elif pressure == MemoryPressureLevel.MODERATE:
            self.budget.max_concurrent_agents = max(2, self.active_profile.max_concurrent_agents - 1)
            self.budget.background_poll_interval_ms = 4500
            self.budget.max_cached_skills = 10
            self.budget.allow_local_llm = (self._report.memory.available_ram_gb >= 4.0)
        else:
            # Normal: restore profile defaults
            self.budget = self._build_budget_from_profile(self.active_profile)

        self._apply_user_overrides()

        # 2. Trigger registered relief handlers
        self.trigger_pressure_relief(pressure)

    def register_pressure_relief_handler(self, handler: Callable[[MemoryPressureLevel], None]) -> None:
        """Register a callback invoked when host memory pressure rises."""
        with self._rw_lock:
            if handler not in self._relief_handlers:
                self._relief_handlers.append(handler)

    def trigger_pressure_relief(self, pressure: MemoryPressureLevel | None = None) -> None:
        """Dispatch pressure notification to all registered handlers."""
        lvl = pressure or self._last_pressure
        with self._rw_lock:
            handlers = list(self._relief_handlers)
        for h in handlers:
            try:
                h(lvl)
            except Exception as e:
                logger.error("Error in pressure relief handler %s: %s", h, e)

    # ── Agent Concurrency & Priority Scheduling ──────────────────────────────

    def acquire_agent_slot(
        self,
        agent_name: str,
        is_interactive: bool = True,
        timeout: float = 3.0,
    ) -> bool:
        """Request permission to spawn or execute an agent, with priority for interactive tasks."""
        deadline = time.time() + timeout
        with self._rw_lock:
            pressure = self.evaluate_memory_pressure()

            # Rule: Under CRITICAL pressure, block non-interactive agents immediately
            if pressure == MemoryPressureLevel.CRITICAL and not is_interactive:
                logger.warning("Rejected non-interactive agent '%s' due to CRITICAL memory pressure.", agent_name)
                return False

            max_agents = self.budget.max_concurrent_agents

            # Interactive prompts can utilize reserved headroom unless CRITICAL
            if is_interactive and pressure != MemoryPressureLevel.CRITICAL:
                effective_limit = max_agents + self._interactive_slots
            else:
                effective_limit = max_agents

            if len(self._active_agents) < effective_limit:
                self._active_agents[agent_name] = time.time()
                return True

        # Wait up to timeout if requested
        while time.time() < deadline:
            time.sleep(0.1)
            with self._rw_lock:
                if len(self._active_agents) < effective_limit:
                    self._active_agents[agent_name] = time.time()
                    return True

        logger.warning(
            "Agent concurrency limit reached (%d/%d). Slot denied for '%s'.",
            len(self._active_agents),
            effective_limit,
            agent_name,
        )
        return False

    def release_agent_slot(self, agent_name: str) -> None:
        """Release a previously acquired agent execution slot."""
        with self._rw_lock:
            if agent_name in self._active_agents:
                del self._active_agents[agent_name]

    def active_agent_count(self) -> int:
        with self._rw_lock:
            return len(self._active_agents)

    def list_active_agents(self) -> list[str]:
        with self._rw_lock:
            return list(self._active_agents.keys())

    # ── User Configuration Overrides ─────────────────────────────────────────

    def set_user_concurrency(self, max_agents: int | None) -> None:
        """Allow user to manually pin maximum agent concurrency."""
        with self._rw_lock:
            self.budget.user_concurrency_override = max_agents
            self._apply_user_overrides()

    def set_user_poll_interval(self, interval_ms: int | None) -> None:
        """Allow user to tune telemetry poll rate."""
        with self._rw_lock:
            self.budget.user_poll_interval_override = interval_ms
            self._apply_user_overrides()

    def set_user_local_llm_allowed(self, allowed: bool | None) -> None:
        """Allow user to permit or forbid local LLM execution."""
        with self._rw_lock:
            self.budget.user_local_llm_override = allowed
            self._apply_user_overrides()

    def _apply_user_overrides(self) -> None:
        if self.budget.user_concurrency_override is not None:
            self.budget.max_concurrent_agents = max(1, self.budget.user_concurrency_override)
        if self.budget.user_poll_interval_override is not None:
            self.budget.background_poll_interval_ms = max(1000, self.budget.user_poll_interval_override)
        if self.budget.user_local_llm_override is not None:
            self.budget.allow_local_llm = self.budget.user_local_llm_override

    # ── Diagnostics & Telemetry Snapshot ─────────────────────────────────────

    def get_vitals(self) -> dict[str, Any]:
        """Produce a comprehensive runtime vitals report for UI and diagnostics."""
        with self._rw_lock:
            pressure = self.evaluate_memory_pressure()
            mem = self.detector.detect_memory()
            gpu = self._report.primary_gpu
            gpu_vram_pct = (1.0 - (gpu.free_vram_mb / gpu.total_vram_mb)) * 100.0 if (gpu and gpu.total_vram_mb > 0) else 0.0

            return {
                "profile": self.active_profile.profile_type.value,
                "profile_description": self.active_profile.description,
                "memory_pressure": pressure.value,
                "ram_percent": mem.percent_used,
                "ram_total_gb": mem.total_ram_gb,
                "ram_avail_gb": mem.available_ram_gb,
                "gpu_name": gpu.model if gpu else "None",
                "gpu_vendor": gpu.vendor.value if gpu else "None",
                "gpu_vram_total_mb": gpu.total_vram_mb if gpu else 0.0,
                "gpu_vram_free_mb": gpu.free_vram_mb if gpu else 0.0,
                "gpu_vram_pct": round(gpu_vram_pct, 1),
                "is_gpu_available": self._report.is_gpu_available,
                "is_cuda_available": self._report.is_cuda_available,
                "active_agents": len(self._active_agents),
                "max_concurrent_agents": self.budget.max_concurrent_agents,
                "max_worker_threads": self.budget.max_worker_threads,
                "max_context_tokens": self.budget.max_context_tokens,
                "poll_interval_ms": self.budget.background_poll_interval_ms,
                "allow_local_llm": self.budget.allow_local_llm,
                "supported_backends": [b.value for b in self._report.supported_backends],
            }
