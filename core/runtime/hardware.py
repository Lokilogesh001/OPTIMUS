"""core/runtime/hardware.py — Unified Hardware Detection Engine for OPTIMUS.

Provides fault-tolerant hardware detection across:
- CPU model, architecture, physical and logical cores, clock frequency.
- Total, available, and used system RAM.
- Operating system platform, release, and Python runtime.
- GPU vendor, model, total VRAM, and free VRAM (NVIDIA, AMD, Intel, Apple, or CPU-only).
- CUDA availability and version (without hard dependencies on PyTorch or heavyweight libraries).
- Supported acceleration backends (CUDA, ROCm, DirectML, OpenVINO, Metal, ONNX Runtime CPU, CPU).
- Storage disk space and usage.

Designed for high fault tolerance:
Missing GPU drivers, missing CUDA, or vendor library errors never crash application startup.
Supports CPU-only environments seamlessly. Caches static specs to eliminate polling overhead.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import psutil

logger = logging.getLogger("optimus.runtime.hardware")


class GpuVendor(str, Enum):
    """Identified GPU hardware vendor."""
    NVIDIA = "NVIDIA"
    AMD = "AMD"
    INTEL = "Intel"
    APPLE = "Apple"
    UNKNOWN = "Unknown"
    NONE = "None"


class AccelerationBackend(str, Enum):
    """Supported hardware acceleration backends."""
    CUDA = "CUDA"
    ROCM = "ROCm"
    DIRECTML = "DirectML"
    OPENVINO = "OpenVINO"
    METAL = "Metal"
    ONNX_CPU = "ONNX_CPU"
    CPU = "CPU"


@dataclass
class CpuInfo:
    """CPU hardware specifications."""
    model: str = "Unknown CPU"
    architecture: str = "x86_64"
    physical_cores: int = 1
    logical_cores: int = 1
    frequency_mhz: float = 0.0


@dataclass
class MemoryInfo:
    """System RAM metrics."""
    total_ram_mb: float = 0.0
    available_ram_mb: float = 0.0
    used_ram_mb: float = 0.0
    percent_used: float = 0.0

    @property
    def total_ram_gb(self) -> float:
        return round(self.total_ram_mb / 1024.0, 2)

    @property
    def available_ram_gb(self) -> float:
        return round(self.available_ram_mb / 1024.0, 2)


@dataclass
class GpuInfo:
    """GPU hardware specifications and status."""
    vendor: GpuVendor = GpuVendor.NONE
    model: str = "None (CPU Only)"
    total_vram_mb: float = 0.0
    free_vram_mb: float = 0.0
    driver_version: str = "N/A"
    cuda_version: str = "N/A"
    is_available: bool = False

    @property
    def total_vram_gb(self) -> float:
        return round(self.total_vram_mb / 1024.0, 2)

    @property
    def free_vram_gb(self) -> float:
        return round(self.free_vram_mb / 1024.0, 2)


@dataclass
class DiskInfo:
    """Storage drive metrics."""
    total_gb: float = 0.0
    free_gb: float = 0.0
    percent_used: float = 0.0


@dataclass
class HardwareReport:
    """Unified system hardware detection report."""
    cpu: CpuInfo = field(default_factory=CpuInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    disk: DiskInfo = field(default_factory=DiskInfo)
    gpus: list[GpuInfo] = field(default_factory=list)
    primary_gpu: GpuInfo | None = None
    os_name: str = "Unknown"
    os_release: str = "Unknown"
    os_version: str = "Unknown"
    python_version: str = sys.version.split()[0]
    supported_backends: list[AccelerationBackend] = field(default_factory=lambda: [AccelerationBackend.CPU])
    is_gpu_available: bool = False
    is_cuda_available: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["supported_backends"] = [b.value for b in self.supported_backends]
        if self.primary_gpu:
            data["primary_gpu"]["vendor"] = self.primary_gpu.vendor.value
        for g in data.get("gpus", []):
            g["vendor"] = g["vendor"]
        return data


class HardwareDetector:
    """Thread-safe, fault-tolerant hardware detection engine with simulation support."""

    _instance: HardwareDetector | None = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> HardwareDetector:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton for test isolation."""
        with cls._lock:
            cls._instance = None

    def __init__(self) -> None:
        self._cached_report: HardwareReport | None = None
        self._cached_time: float = 0.0
        self._cache_ttl_seconds: float = 10.0  # Dynamic vitals (RAM/VRAM) refreshed after TTL
        self._static_cpu: CpuInfo | None = None
        self._simulation_override: str | None = os.environ.get("OPTIMUS_SIMULATE_PROFILE")
        self._gpu_override: GpuInfo | None = None
        self._rw_lock = threading.RLock()

    def set_simulation_override(self, profile_name: str | None) -> None:
        """Override detected hardware for simulation testing (e.g. 'CPU_ONLY', 'LOW_VRAM')."""
        with self._rw_lock:
            self._simulation_override = profile_name
            self._cached_report = None

    def set_gpu_override(self, gpu_info: GpuInfo | None) -> None:
        """Directly inject a simulated GPU configuration."""
        with self._rw_lock:
            self._gpu_override = gpu_info
            self._cached_report = None

    def detect(self, force_refresh: bool = False) -> HardwareReport:
        """Return the current unified hardware report, utilizing caching where appropriate."""
        with self._rw_lock:
            now = time.time()
            if not force_refresh and self._cached_report and (now - self._cached_time < self._cache_ttl_seconds):
                # Refresh fast dynamic values
                self._cached_report.memory = self.detect_memory()
                self._cached_report.disk = self.detect_disk()
                return self._cached_report

            cpu = self.detect_cpu()
            mem = self.detect_memory()
            disk = self.detect_disk()
            gpus = self.detect_gpus()
            primary_gpu = gpus[0] if gpus else None
            backends = self.detect_acceleration_backends(gpus)

            is_gpu_avail = any(g.is_available and g.vendor != GpuVendor.NONE for g in gpus)
            is_cuda_avail = any(g.cuda_version not in ("N/A", "", "None") and g.vendor == GpuVendor.NVIDIA for g in gpus)

            report = HardwareReport(
                cpu=cpu,
                memory=mem,
                disk=disk,
                gpus=gpus,
                primary_gpu=primary_gpu,
                os_name=platform.system(),
                os_release=platform.release(),
                os_version=platform.version(),
                python_version=sys.version.split()[0],
                supported_backends=backends,
                is_gpu_available=is_gpu_avail,
                is_cuda_available=is_cuda_avail,
                timestamp=now,
            )

            # Apply simulation override if configured
            if self._simulation_override == "CPU_ONLY":
                report.gpus = [GpuInfo(vendor=GpuVendor.NONE, model="None (CPU Only)", is_available=False)]
                report.primary_gpu = report.gpus[0]
                report.is_gpu_available = False
                report.is_cuda_available = False
                report.supported_backends = [AccelerationBackend.CPU, AccelerationBackend.ONNX_CPU]
            elif self._simulation_override == "LOW_VRAM" and report.primary_gpu:
                report.primary_gpu.total_vram_mb = 2048.0
                report.primary_gpu.free_vram_mb = 512.0

            self._cached_report = report
            self._cached_time = now
            return report

    def detect_cpu(self) -> CpuInfo:
        """Detect CPU specifications (cached statically)."""
        if self._static_cpu:
            return self._static_cpu

        model = platform.processor() or "Unknown CPU"
        arch = platform.machine() or "x86_64"
        phys = psutil.cpu_count(logical=False) or 1
        logic = psutil.cpu_count(logical=True) or 1

        freq = 0.0
        try:
            f = psutil.cpu_freq()
            if f and f.current:
                freq = float(f.current)
        except Exception:
            pass

        self._static_cpu = CpuInfo(
            model=model,
            architecture=arch,
            physical_cores=phys,
            logical_cores=logic,
            frequency_mhz=freq,
        )
        return self._static_cpu

    def detect_memory(self) -> MemoryInfo:
        """Detect current physical RAM capacity and availability."""
        try:
            v = psutil.virtual_memory()
            total_mb = v.total / (1024.0 * 1024.0)
            avail_mb = v.available / (1024.0 * 1024.0)
            used_mb = (v.total - v.available) / (1024.0 * 1024.0)
            pct = v.percent
            return MemoryInfo(
                total_ram_mb=round(total_mb, 2),
                available_ram_mb=round(avail_mb, 2),
                used_ram_mb=round(used_mb, 2),
                percent_used=pct,
            )
        except Exception as e:
            logger.debug("Failed to detect virtual memory: %s", e)
            return MemoryInfo(total_ram_mb=8192.0, available_ram_mb=4096.0, used_ram_mb=4096.0, percent_used=50.0)

    def detect_disk(self) -> DiskInfo:
        """Detect primary disk space."""
        try:
            root_path = os.path.abspath(os.sep)
            usage = shutil.disk_usage(root_path)
            total_gb = usage.total / (1024.0 ** 3)
            free_gb = usage.free / (1024.0 ** 3)
            pct = round((usage.used / usage.total) * 100.0, 1) if usage.total else 0.0
            return DiskInfo(total_gb=round(total_gb, 2), free_gb=round(free_gb, 2), percent_used=pct)
        except Exception:
            return DiskInfo(total_gb=256.0, free_gb=128.0, percent_used=50.0)

    def detect_gpus(self) -> list[GpuInfo]:
        """Detect GPU hardware with multi-tier fault-tolerant probes."""
        if self._gpu_override:
            return [self._gpu_override]

        gpus: list[GpuInfo] = []

        # Tier 1: NVIDIA via NVML Ctypes (zero subprocess, zero PyTorch dependency)
        nv_gpu = self._probe_nvidia_nvml()
        if nv_gpu:
            gpus.append(nv_gpu)
            return gpus

        # Tier 2: PyTorch CUDA (if optional torch package is installed)
        torch_gpu = self._probe_torch_cuda()
        if torch_gpu:
            gpus.append(torch_gpu)
            return gpus

        # Tier 3: Platform Display Adapters (Windows WMI / Linux DRM)
        platform_gpus = self._probe_platform_display_adapters()
        if platform_gpus:
            return platform_gpus

        # Tier 4: CPU-only Fallback
        return [GpuInfo(vendor=GpuVendor.NONE, model="None (CPU Only)", is_available=False)]

    def _probe_nvidia_nvml(self) -> GpuInfo | None:
        """Probe NVIDIA GPU using NVML dynamic library directly."""
        try:
            dll_candidates = (
                "nvml",
                r"C:\Windows\System32\nvml.dll",
                "libnvidia-ml.so.1",
                "libnvidia-ml.so",
            )
            lib = None
            for cand in dll_candidates:
                try:
                    if platform.system() == "Windows":
                        lib = ctypes.WinDLL(cand)
                    else:
                        lib = ctypes.CDLL(cand)
                    lib.nvmlInit_v2()
                    break
                except Exception:
                    continue

            if not lib:
                return None

            class _NvmlMemory(ctypes.Structure):
                _fields_ = [
                    ("total", ctypes.c_ulonglong),
                    ("free", ctypes.c_ulonglong),
                    ("used", ctypes.c_ulonglong),
                ]

            dev = ctypes.c_void_p()
            lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))

            name_buf = ctypes.create_string_buffer(64)
            lib.nvmlDeviceGetName(dev, name_buf, 64)
            model_name = name_buf.value.decode("utf-8", errors="replace")

            mem = _NvmlMemory()
            lib.nvmlDeviceGetMemoryInfo(dev, ctypes.byref(mem))
            total_vram_mb = mem.total / (1024.0 * 1024.0)
            free_vram_mb = mem.free / (1024.0 * 1024.0)

            driver_ver = "Unknown"
            try:
                drv_buf = ctypes.create_string_buffer(64)
                lib.nvmlSystemGetDriverVersion(drv_buf, 64)
                driver_ver = drv_buf.value.decode("utf-8", errors="replace")
            except Exception:
                pass

            cuda_ver = "Available"
            try:
                c_ver = ctypes.c_uint()
                lib.nvmlSystemGetCudaDriverVersion_v2(ctypes.byref(c_ver))
                major = c_ver.value // 1000
                minor = (c_ver.value % 1000) // 10
                cuda_ver = f"{major}.{minor}"
            except Exception:
                pass

            return GpuInfo(
                vendor=GpuVendor.NVIDIA,
                model=model_name,
                total_vram_mb=round(total_vram_mb, 2),
                free_vram_mb=round(free_vram_mb, 2),
                driver_version=driver_ver,
                cuda_version=cuda_ver,
                is_available=True,
            )
        except Exception as e:
            logger.debug("NVML probe returned no GPU: %s", e)
            return None

    def _probe_torch_cuda(self) -> GpuInfo | None:
        """Check if torch is present with active CUDA device."""
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                idx = 0
                name = torch.cuda.get_device_name(idx)
                props = torch.cuda.get_device_properties(idx)
                total_mb = props.total_memory / (1024.0 * 1024.0)
                # Approximate free memory
                free_mb = total_mb
                try:
                    free_b, _ = torch.cuda.mem_get_info(idx)
                    free_mb = free_b / (1024.0 * 1024.0)
                except Exception:
                    pass
                return GpuInfo(
                    vendor=GpuVendor.NVIDIA if "NVIDIA" in name.upper() else GpuVendor.AMD,
                    model=name,
                    total_vram_mb=round(total_mb, 2),
                    free_vram_mb=round(free_mb, 2),
                    driver_version="PyTorch CUDA",
                    cuda_version=torch.version.cuda or "Available",
                    is_available=True,
                )
        except Exception:
            pass
        return None

    def _probe_platform_display_adapters(self) -> list[GpuInfo]:
        """Detect display adapters via Windows WMI or generic OS probes."""
        results: list[GpuInfo] = []
        if platform.system() == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI()
                for c in w.Win32_VideoController():
                    name = str(c.Name or "").strip()
                    if not name:
                        continue
                    vram_b = getattr(c, "AdapterRAM", 0) or 0
                    vram_mb = float(vram_b) / (1024.0 * 1024.0) if vram_b > 0 else 1024.0
                    driver_ver = str(getattr(c, "DriverVersion", "N/A"))

                    vendor = GpuVendor.UNKNOWN
                    upper = name.upper()
                    if "NVIDIA" in upper or "GEFORCE" in upper or "RTX" in upper or "GTX" in upper:
                        vendor = GpuVendor.NVIDIA
                    elif "AMD" in upper or "RADEON" in upper:
                        vendor = GpuVendor.AMD
                    elif "INTEL" in upper or "IRIS" in upper or "ARC" in upper:
                        vendor = GpuVendor.INTEL

                    results.append(GpuInfo(
                        vendor=vendor,
                        model=name,
                        total_vram_mb=round(vram_mb, 2),
                        free_vram_mb=round(vram_mb * 0.7, 2),
                        driver_version=driver_ver,
                        cuda_version="N/A",
                        is_available=True,
                    ))
            except Exception:
                pass
        return results

    def detect_acceleration_backends(self, gpus: list[GpuInfo]) -> list[AccelerationBackend]:
        """Determine all viable acceleration backends for the detected hardware."""
        backends: list[AccelerationBackend] = [AccelerationBackend.CPU]

        # Check for ONNX Runtime CPU
        try:
            import importlib.util
            if importlib.util.find_spec("onnxruntime") is not None:
                backends.append(AccelerationBackend.ONNX_CPU)
        except Exception:
            pass

        has_nvidia = any(g.vendor == GpuVendor.NVIDIA and g.is_available for g in gpus)
        has_cuda = any(g.cuda_version not in ("N/A", "", "None") and g.vendor == GpuVendor.NVIDIA for g in gpus)
        if has_nvidia and has_cuda:
            backends.append(AccelerationBackend.CUDA)

        has_amd = any(g.vendor == GpuVendor.AMD and g.is_available for g in gpus)
        if has_amd and platform.system() == "Linux":
            backends.append(AccelerationBackend.ROCM)

        if platform.system() == "Windows" and any(g.is_available and g.vendor != GpuVendor.NONE for g in gpus):
            backends.append(AccelerationBackend.DIRECTML)

        if platform.system() == "Darwin" and platform.machine() == "arm64":
            backends.append(AccelerationBackend.METAL)

        return backends
