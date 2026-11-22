"""core/system_monitor — First-Party Native Windows System Telemetry & Monitor Subsystem."""

from core.system_monitor.adapter import WindowsSystemMonitorAdapter
from core.system_monitor.tools import register_system_monitor_tools
from core.system_monitor.types import (
    BatteryTelemetry,
    CpuTelemetry,
    GpuTelemetry,
    MemoryTelemetry,
    NetworkTelemetry,
    PartitionMetric,
    ProcessTelemetry,
    SystemSnapshot,
)

__all__ = [
    "BatteryTelemetry",
    "CpuTelemetry",
    "GpuTelemetry",
    "MemoryTelemetry",
    "NetworkTelemetry",
    "PartitionMetric",
    "ProcessTelemetry",
    "SystemSnapshot",
    "WindowsSystemMonitorAdapter",
    "register_system_monitor_tools",
]
