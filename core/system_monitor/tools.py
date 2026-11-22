"""core/system_monitor/tools.py — Central ToolRegistry Tools for Native Windows System Monitoring.

Registers:
- system_telemetry: Complete host vitals overview.
- memory_status: Dedicated RAM and virtual memory metrics.
- gpu_status: Dedicated GPU utilization and VRAM analytics.
- process_diagnostics: Top active processes ranked by memory and CPU.
"""

from __future__ import annotations

import json
from typing import Any

from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    sanitize_tool_output,
)
from core.system_monitor.adapter import WindowsSystemMonitorAdapter


def _system_telemetry_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Collect full host vitals snapshot."""
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()

    cpu = snap.cpu
    mem = snap.memory
    gpu = snap.gpu
    bat = snap.battery

    lines = [
        "### Windows Host System Telemetry",
        f"- **CPU:** {cpu.total_percent}% total load ({cpu.physical_cores} physical / {cpu.logical_cores} logical cores @ {cpu.frequency_mhz} MHz)",
        f"- **RAM:** {mem.used_gb} GB used / {mem.total_gb} GB total ({mem.percent_used}% load, {mem.available_gb} GB free)",
    ]

    if gpu.has_gpu:
        lines.append(f"- **GPU:** {gpu.model} ({gpu.gpu_percent}% compute, {gpu.used_vram_gb} GB / {gpu.total_vram_gb} GB VRAM, {gpu.temperature_c or 'N/A'}°C)")
    else:
        lines.append(f"- **GPU:** {gpu.model} (Discrete GPU telemetry offline)")

    if snap.partitions:
        p_strs = [f"{p.mountpoint} ({p.percent_used}% used, {p.free_gb} GB free)" for p in snap.partitions[:3]]
        lines.append(f"- **Storage:** {', '.join(p_strs)}")

    if bat.has_battery:
        plugged_str = "Plugged in" if bat.is_plugged else "On battery"
        lines.append(f"- **Battery:** {bat.percent}% ({plugged_str})")

    lines.append(f"- **Network:** Sent {snap.network.bytes_sent_mb} MB, Recv {snap.network.bytes_recv_mb} MB")

    out_text = sanitize_tool_output("\n".join(lines))
    return ToolResult(status="done", output=out_text, evidence=snap.to_dict())


def _memory_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Retrieve detailed physical and virtual memory usage."""
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()
    mem = snap.memory

    lines = [
        "### System Memory Status (RAM)",
        f"- **Physical RAM Load:** {mem.percent_used}%",
        f"- **Used Memory:** {mem.used_gb} GB ({mem.used_mb} MB)",
        f"- **Total Memory:** {mem.total_gb} GB ({mem.total_mb} MB)",
        f"- **Available Memory:** {mem.available_gb} GB ({mem.available_mb} MB)",
        f"- **Swap / Pagefile:** {mem.swap_used_mb} MB used / {mem.swap_total_mb} MB total ({mem.swap_percent}%)",
    ]

    out_text = sanitize_tool_output("\n".join(lines))
    return ToolResult(status="done", output=out_text, evidence=snap.memory.__dict__)


def _gpu_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Retrieve dedicated GPU utilization and VRAM statistics."""
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()
    gpu = snap.gpu

    if not gpu.has_gpu:
        out = (
            "### GPU Telemetry\n"
            f"- **Status:** {gpu.model}\n"
            "- **Details:** No active discrete NVIDIA GPU detected or NVML unavailable. Running in CPU-only mode."
        )
        return ToolResult(status="done", output=out, evidence=gpu.__dict__)

    lines = [
        "### NVIDIA GPU Telemetry",
        f"- **Device Model:** {gpu.model}",
        f"- **Driver Version:** {gpu.driver_version} (CUDA Driver: {gpu.cuda_version})",
        f"- **GPU Compute Load:** {gpu.gpu_percent:.1f}%",
        f"- **Memory Controller Load:** {gpu.memory_percent:.1f}%",
        f"- **VRAM Utilization:** {gpu.vram_percent:.1f}% ({gpu.used_vram_mb} MB used / {gpu.total_vram_mb} MB total, {gpu.free_vram_mb} MB free)",
    ]

    if gpu.temperature_c is not None:
        lines.append(f"- **Temperature:** {gpu.temperature_c:.1f}°C")
    if gpu.power_watts is not None:
        lines.append(f"- **Power Draw:** {gpu.power_watts:.1f} W")
    if gpu.fan_speed_percent is not None:
        lines.append(f"- **Fan Speed:** {gpu.fan_speed_percent:.1f}%")

    out_text = sanitize_tool_output("\n".join(lines))
    return ToolResult(status="done", output=out_text, evidence=gpu.__dict__)


def _process_diagnostics_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """List top resource-consuming processes (strictly privacy-preserving)."""
    top_n = max(1, min(int(params.get("top_n", 8)), 25))
    adapter = WindowsSystemMonitorAdapter.instance()
    snap = adapter.get_snapshot()

    sort_by = str(params.get("sort_by", "memory")).lower()
    procs = list(snap.top_processes)
    if sort_by == "cpu":
        procs.sort(key=lambda x: (x.cpu_percent, x.memory_percent), reverse=True)
    else:
        procs.sort(key=lambda x: (x.memory_percent, x.cpu_percent), reverse=True)

    selected = procs[:top_n]
    lines = [f"### Top {len(selected)} Active Processes (Sorted by {sort_by.upper()}):"]
    lines.append(f"{'PID':>7} | {'PROCESS NAME':<26} | {'RAM %':>7} | {'RAM MB':>8} | {'CPU %':>7} | {'STATUS'}")
    lines.append("-" * 75)

    for p in selected:
        lines.append(f"{p.pid:7d} | {p.name:<26} | {p.memory_percent:6.1f}% | {p.memory_mb:7.1f}M | {p.cpu_percent:6.1f}% | {p.status}")

    out_text = sanitize_tool_output("\n".join(lines))
    evidence = {"top_processes": [p.__dict__ for p in selected], "sort_by": sort_by}
    return ToolResult(status="done", output=out_text, evidence=evidence)


def register_system_monitor_tools(registry: ToolRegistry) -> None:
    """Register all native Windows System Monitor tools with ToolRegistry."""
    registry.register(ToolDefinition(
        name="system_telemetry",
        description="Comprehensive real-time host hardware and OS telemetry (CPU, RAM, GPU, Disk, Network, Battery).",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_system_telemetry_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="memory_status",
        description="Measure physical RAM and swap memory usage, allocated bytes, and load percentage.",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_memory_status_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="gpu_status",
        description="Measure NVIDIA GPU utilization, VRAM usage, temperature, power, and clock analytics.",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_gpu_status_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="process_diagnostics",
        description="Inspect top resource-consuming processes ranked by memory or CPU usage.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "top_n": {"type": "INTEGER", "description": "Number of top processes to return (default 8, max 25)"},
                "sort_by": {"type": "STRING", "description": "Sort criterion: 'memory' or 'cpu' (default: memory)"},
            },
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_process_diagnostics_handler,
        is_mutating=False,
    ))
