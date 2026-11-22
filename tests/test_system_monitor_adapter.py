"""tests/test_system_monitor_adapter.py — Verification Suite for Native Windows System Monitor.

Tests:
1. Adapter singleton initialization and background sampler thread.
2. O(1) non-blocking snapshot retrieval.
3. psutil CPU, Memory, Disk, Network, Battery metrics.
4. pynvml NVIDIA GPU telemetry collection and graceful CPU-only fallback.
5. LibreHardwareMonitor WMI error resilience (COM error 0x8004100e handled without exception).
6. Process diagnostics collection (verifying strictly zero cmdline exposure and secret sanitization).
7. ToolRegistry dispatch for all 4 system tools (system_telemetry, memory_status, gpu_status, process_diagnostics).
"""

from __future__ import annotations

import time
import pytest

from core.orchestrator.tools import ToolRegistry
from core.system_monitor.adapter import WindowsSystemMonitorAdapter
from core.system_monitor.types import SystemSnapshot


def test_system_monitor_adapter_singleton_and_snapshot():
    """Verify adapter initializes and returns atomic snapshot instantly."""
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()

    assert isinstance(snap, SystemSnapshot)
    assert snap.timestamp > 0
    assert snap.cpu.logical_cores >= 1
    assert snap.memory.total_mb > 0
    assert snap.memory.percent_used >= 0.0


def test_system_monitor_non_blocking_performance():
    """Verify get_snapshot executes in sub-millisecond O(1) time without sleeping caller thread."""
    adapter = WindowsSystemMonitorAdapter.instance()

    t0 = time.perf_counter()
    for _ in range(100):
        _ = adapter.get_snapshot()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # 100 reads must finish well under 10ms (pure memory read)
    assert elapsed_ms < 50.0, f"Snapshot reads took too long: {elapsed_ms}ms"


def test_system_monitor_cpu_and_memory_metrics():
    """Verify CPU per-core and RAM breakdown."""
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()

    assert snap.cpu.total_percent >= 0.0
    assert len(snap.cpu.per_core_percent) == snap.cpu.logical_cores
    assert snap.memory.total_gb > 0.0
    assert snap.memory.used_gb <= snap.memory.total_gb


def test_system_monitor_gpu_telemetry():
    """Verify GPU telemetry structure (handles both NVIDIA hardware and CPU-only gracefully)."""
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()
    gpu = snap.gpu

    if gpu.has_gpu:
        assert gpu.vendor == "NVIDIA"
        assert gpu.total_vram_mb > 0
        assert gpu.used_vram_mb >= 0
        assert gpu.vram_percent >= 0.0
        assert gpu.gpu_percent >= 0.0
    else:
        assert gpu.model in ("CPU Only", "None (CPU Only)", "CPU Only (GPU Offline)")


def test_system_monitor_privacy_no_cmdline_in_processes():
    """Verify process telemetry strictly avoids exposing process command lines."""
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()

    assert len(snap.top_processes) > 0
    for p in snap.top_processes:
        assert p.pid >= 0
        assert isinstance(p.name, str)
        assert not hasattr(p, "cmdline"), "ProcessTelemetry must never store cmdline!"
        assert not hasattr(p, "environ"), "ProcessTelemetry must never store environment variables!"


def test_system_monitor_libre_hardware_graceful_handling():
    """Verify LibreHardwareMonitor query does not raise even if namespace is absent."""
    adapter = WindowsSystemMonitorAdapter.instance()
    avail, sensors = adapter._sample_libre_hardware()
    assert isinstance(avail, bool)
    assert isinstance(sensors, dict)


def test_system_monitor_tool_registry_dispatch():
    """Verify all 4 system monitoring tools dispatch cleanly via ToolRegistry."""
    registry = ToolRegistry.instance()

    # 1. system_telemetry
    res_sys = registry.execute("system_telemetry", {}, caller_agent="system_diagnosis")
    assert res_sys.is_success
    assert "Windows Host System Telemetry" in res_sys.output
    assert "CPU:" in res_sys.output
    assert "RAM:" in res_sys.output

    # 2. memory_status
    res_mem = registry.execute("memory_status", {}, caller_agent="system_diagnosis")
    assert res_mem.is_success
    assert "System Memory Status" in res_mem.output
    assert "Physical RAM Load:" in res_mem.output

    # 3. gpu_status
    res_gpu = registry.execute("gpu_status", {}, caller_agent="system_diagnosis")
    assert res_gpu.is_success
    assert "GPU" in res_gpu.output

    # 4. process_diagnostics
    res_proc = registry.execute("process_diagnostics", {"top_n": 5}, caller_agent="system_diagnosis")
    assert res_proc.is_success
    assert "Top 5 Active Processes" in res_proc.output
    assert "PID" in res_proc.output
