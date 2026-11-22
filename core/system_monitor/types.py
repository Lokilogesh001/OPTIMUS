"""core/system_monitor/types.py — Telemetry Data Structures for Native System Monitoring.

Defines strongly-typed, frozen/dataclass telemetry models:
- CpuTelemetry: Total load, per-core load, clock speed, core counts.
- MemoryTelemetry: Total physical RAM, used, available, percentage, swap usage.
- GpuTelemetry: Dedicated GPU presence, vendor, model, VRAM metrics, utilization, temp, power, fan speed.
- DiskTelemetry: Storage partitions, mount points, usage percent, free space.
- NetworkTelemetry: Total bytes sent/received, packet rates, active interfaces.
- BatteryTelemetry: Battery percentage, plugged state, time remaining.
- ProcessTelemetry: Process PID, name, CPU %, memory %, status (strictly excluding cmdline).
- SystemSnapshot: Consolidated atomic system telemetry snapshot.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CpuTelemetry:
    """CPU load and core specifications."""
    total_percent: float = 0.0
    per_core_percent: tuple[float, ...] = field(default_factory=tuple)
    physical_cores: int = 1
    logical_cores: int = 1
    frequency_mhz: float = 0.0


@dataclass(frozen=True)
class MemoryTelemetry:
    """System RAM and virtual memory metrics."""
    total_mb: float = 0.0
    used_mb: float = 0.0
    available_mb: float = 0.0
    percent_used: float = 0.0
    swap_total_mb: float = 0.0
    swap_used_mb: float = 0.0
    swap_percent: float = 0.0

    @property
    def total_gb(self) -> float:
        return round(self.total_mb / 1024.0, 2)

    @property
    def used_gb(self) -> float:
        return round(self.used_mb / 1024.0, 2)

    @property
    def available_gb(self) -> float:
        return round(self.available_mb / 1024.0, 2)


@dataclass(frozen=True)
class GpuTelemetry:
    """GPU hardware utilization and thermal metrics."""
    has_gpu: bool = False
    vendor: str = "None"
    model: str = "CPU Only"
    driver_version: str = "N/A"
    cuda_version: str = "N/A"
    total_vram_mb: float = 0.0
    used_vram_mb: float = 0.0
    free_vram_mb: float = 0.0
    vram_percent: float = 0.0
    gpu_percent: float = 0.0
    memory_percent: float = 0.0
    temperature_c: float | None = None
    power_watts: float | None = None
    fan_speed_percent: float | None = None

    @property
    def total_vram_gb(self) -> float:
        return round(self.total_vram_mb / 1024.0, 2)

    @property
    def used_vram_gb(self) -> float:
        return round(self.used_vram_mb / 1024.0, 2)

    @property
    def free_vram_gb(self) -> float:
        return round(self.free_vram_mb / 1024.0, 2)


@dataclass(frozen=True)
class PartitionMetric:
    """Storage drive partition usage."""
    device: str
    mountpoint: str
    fstype: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent_used: float


# Type alias for backward compatibility
DiskTelemetry = PartitionMetric


@dataclass(frozen=True)
class NetworkTelemetry:
    """Network adapter throughput statistics."""
    bytes_sent_mb: float = 0.0
    bytes_recv_mb: float = 0.0
    packets_sent: int = 0
    packets_recv: int = 0


@dataclass(frozen=True)
class BatteryTelemetry:
    """Host battery status."""
    has_battery: bool = False
    percent: float = 100.0
    is_plugged: bool = True
    secs_left: int | None = None


@dataclass(frozen=True)
class ProcessTelemetry:
    """Individual process metrics (privacy-preserving, strictly zero cmdline)."""
    pid: int
    name: str
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    status: str


@dataclass(frozen=True)
class SystemSnapshot:
    """Consolidated point-in-time telemetry snapshot."""
    timestamp: float = field(default_factory=time.time)
    cpu: CpuTelemetry = field(default_factory=CpuTelemetry)
    memory: MemoryTelemetry = field(default_factory=MemoryTelemetry)
    gpu: GpuTelemetry = field(default_factory=GpuTelemetry)
    partitions: tuple[PartitionMetric, ...] = field(default_factory=tuple)
    network: NetworkTelemetry = field(default_factory=NetworkTelemetry)
    battery: BatteryTelemetry = field(default_factory=BatteryTelemetry)
    top_processes: tuple[ProcessTelemetry, ...] = field(default_factory=tuple)
    libre_hardware_available: bool = False
    hardware_sensors: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert snapshot to dictionary for tool evidence serialization."""
        return asdict(self)
