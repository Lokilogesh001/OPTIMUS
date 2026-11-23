"""core/system_monitor/adapter.py — Native Windows System Telemetry & Hardware Monitor Adapter.

Implements:
- Continuous, non-blocking background telemetry sampling with a 2.0s refresh cycle.
- Instant, O(1) in-memory snapshot access for tool callers (zero caller thread blocking).
- Complete psutil telemetry: CPU (overall and per-core), RAM, Swap, Disk, Network, Battery.
- Robust pynvml telemetry: NVIDIA GPU model, VRAM load, compute/memory utilization, thermals, power, fan speed.
- Transient NVML error recovery (TDR backoff) rather than permanent disqualification.
- Safe, optional LibreHardwareMonitor WMI sensor inspection (resilient to 0x8004100e).
- Strict privacy and security invariants: NO process termination, NO command-line collection, NO setting mutations.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
import time
from typing import Any

from core.system_monitor.types import (
    BatteryTelemetry,
    CpuTelemetry,
    DiskTelemetry,
    GpuTelemetry,
    MemoryTelemetry,
    NetworkTelemetry,
    PartitionMetric,
    ProcessTelemetry,
    SystemSnapshot,
)

logger = logging.getLogger("optimus.system_monitor")

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

try:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import nvidia_ml_py as pynvml  # Modern official NVIDIA NVML package
        except ImportError:
            import pynvml
    _HAS_PYNVML = True
except ImportError:
    _HAS_PYNVML = False


class WindowsSystemMonitorAdapter:
    """Thread-safe, non-blocking telemetry sampling engine for Windows host vitals."""

    _instance: WindowsSystemMonitorAdapter | None = None
    _lock = threading.RLock()

    @classmethod
    def instance(cls) -> WindowsSystemMonitorAdapter:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, refresh_interval_s: float = 2.0, auto_start: bool = True) -> None:
        self.refresh_interval_s = max(0.5, refresh_interval_s)
        self._snapshot_lock = threading.RLock()
        self._current_snapshot: SystemSnapshot = SystemSnapshot()
        self._running = False
        self._thread: threading.Thread | None = None

        # NVML state
        self._nvml_initialized = False
        self._nvml_last_error_time: float = 0.0
        self._nvml_backoff_s: float = 30.0

        # WMI state
        self._wmi_checked = False
        self._wmi_available = False
        self._wmi_last_check_time: float = 0.0
        self._wmi_backoff_s: float = 60.0

        # Process sampling cache state (10s TTL to prevent CPU/memory churn)
        self._last_process_sample_time: float = 0.0
        self._process_cache_ttl_s: float = 10.0
        self._cached_processes: tuple[ProcessTelemetry, ...] = ()

        # Pre-prime CPU percent sampling so subsequent non-blocking calls return valid data
        if _HAS_PSUTIL:
            try:
                psutil.cpu_percent(interval=None)
                psutil.cpu_percent(interval=None, percpu=True)
            except Exception:
                pass

        # Perform initial synchronous sample to ensure immediate availability
        self._current_snapshot = self._sample_all()

        if auto_start:
            self.start()

    def start(self) -> None:
        """Start background sampling thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._sampling_worker,
                name="OptimusSystemMonitorWorker",
                daemon=True,
            )
            self._thread.start()
            logger.info("WindowsSystemMonitorAdapter background sampler started (interval: %.1fs)", self.refresh_interval_s)

    def stop(self) -> None:
        """Stop background sampling thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._shutdown_nvml()
        logger.info("WindowsSystemMonitorAdapter background sampler stopped.")

    def get_snapshot(self) -> SystemSnapshot:
        """Retrieve the latest telemetry snapshot in O(1) time without blocking caller thread."""
        with self._snapshot_lock:
            return self._current_snapshot

    # ── Background Worker Loop ────────────────────────────────────────────────

    def _sampling_worker(self) -> None:
        """Periodic background sampling loop."""
        while self._running:
            try:
                snap = self._sample_all()
                with self._snapshot_lock:
                    self._current_snapshot = snap
            except Exception as e:
                logger.warning("Error during system monitor sampling iteration: %s", e)
            time.sleep(self.refresh_interval_s)

    def _sample_all(self) -> SystemSnapshot:
        """Collect all telemetry metrics atomically."""
        cpu = self._sample_cpu()
        memory = self._sample_memory()
        gpu = self._sample_gpu()
        partitions = self._sample_partitions()
        network = self._sample_network()
        battery = self._sample_battery()
        processes = self._sample_processes(top_n=10)
        lhm_available, lhm_sensors = self._sample_libre_hardware()

        return SystemSnapshot(
            timestamp=time.time(),
            cpu=cpu,
            memory=memory,
            gpu=gpu,
            partitions=partitions,
            network=network,
            battery=battery,
            top_processes=processes,
            libre_hardware_available=lhm_available,
            hardware_sensors=lhm_sensors,
        )

    # ── Metric Collectors ─────────────────────────────────────────────────────

    def _sample_cpu(self) -> CpuTelemetry:
        if not _HAS_PSUTIL:
            return CpuTelemetry(
                total_percent=0.0,
                physical_cores=os.cpu_count() or 1,
                logical_cores=os.cpu_count() or 1,
            )

        try:
            # Non-blocking query because interval is None
            total_pct = float(psutil.cpu_percent(interval=None))
            per_core = tuple(float(x) for x in psutil.cpu_percent(interval=None, percpu=True))
            phys = psutil.cpu_count(logical=False) or 1
            log_c = psutil.cpu_count(logical=True) or 1
            freq = 0.0
            try:
                f_info = psutil.cpu_freq()
                if f_info:
                    freq = float(f_info.current)
            except Exception:
                pass

            return CpuTelemetry(
                total_percent=round(total_pct, 1),
                per_core_percent=per_core,
                physical_cores=phys,
                logical_cores=log_c,
                frequency_mhz=round(freq, 1),
            )
        except Exception as e:
            logger.debug("Failed sampling CPU: %s", e)
            return CpuTelemetry()

    def _sample_memory(self) -> MemoryTelemetry:
        if not _HAS_PSUTIL:
            return MemoryTelemetry()

        try:
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()
            return MemoryTelemetry(
                total_mb=round(vm.total / (1024**2), 1),
                used_mb=round(vm.used / (1024**2), 1),
                available_mb=round(vm.available / (1024**2), 1),
                percent_used=round(vm.percent, 1),
                swap_total_mb=round(swap.total / (1024**2), 1),
                swap_used_mb=round(swap.used / (1024**2), 1),
                swap_percent=round(swap.percent, 1),
            )
        except Exception as e:
            logger.debug("Failed sampling memory: %s", e)
            return MemoryTelemetry()

    def _ensure_nvml(self) -> bool:
        """Initialize NVML with transient error backoff."""
        if not _HAS_PYNVML:
            return False

        if self._nvml_initialized:
            return True

        now = time.time()
        if now - self._nvml_last_error_time < self._nvml_backoff_s:
            return False

        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                pynvml.nvmlInit()
            self._nvml_initialized = True
            return True
        except Exception as e:
            self._nvml_last_error_time = now
            self._nvml_initialized = False
            logger.debug("NVML init failed (backing off for %.0fs): %s", self._nvml_backoff_s, e)
            return False

    def _shutdown_nvml(self) -> None:
        if self._nvml_initialized and _HAS_PYNVML:
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=FutureWarning)
                    pynvml.nvmlShutdown()
            except Exception:
                pass
            finally:
                self._nvml_initialized = False

    def _sample_gpu(self) -> GpuTelemetry:
        if not self._ensure_nvml():
            return GpuTelemetry(has_gpu=False, model="CPU Only")

        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=FutureWarning)
                count = pynvml.nvmlDeviceGetCount()
                if count == 0:
                    return GpuTelemetry(has_gpu=False, model="CPU Only")

                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)

                driver_ver = "N/A"
                try:
                    raw_drv = pynvml.nvmlSystemGetDriverVersion()
                    driver_ver = raw_drv.decode("utf-8") if isinstance(raw_drv, bytes) else str(raw_drv)
                except Exception:
                    pass

                cuda_ver = "N/A"
                try:
                    c_code = pynvml.nvmlSystemGetCudaDriverVersion_v2()
                    cuda_ver = f"{c_code // 1000}.{(c_code % 1000) // 10}"
                except Exception:
                    pass

                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_mb = round(mem.total / (1024**2), 1)
                used_mb = round(mem.used / (1024**2), 1)
                free_mb = round(mem.free / (1024**2), 1)
                vram_pct = round((mem.used / max(1, mem.total)) * 100.0, 1)

                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                gpu_pct = float(util.gpu)
                mem_pct = float(util.memory)

                temp_c = None
                try:
                    temp_c = float(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
                except Exception:
                    pass

                power_w = None
                try:
                    # Power is reported in milliwatts
                    power_w = round(pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0, 1)
                except Exception:
                    pass

                fan_pct = None
                try:
                    fan_pct = float(pynvml.nvmlDeviceGetFanSpeed(handle))
                except Exception:
                    pass

                return GpuTelemetry(
                    has_gpu=True,
                    vendor="NVIDIA",
                    model=name,
                    driver_version=driver_ver,
                    cuda_version=cuda_ver,
                    total_vram_mb=total_mb,
                    used_vram_mb=used_mb,
                    free_vram_mb=free_mb,
                    vram_percent=vram_pct,
                    gpu_percent=gpu_pct,
                    memory_percent=mem_pct,
                    temperature_c=temp_c,
                    power_watts=power_w,
                    fan_speed_percent=fan_pct,
                )
        except Exception as e:
            logger.debug("NVML query encountered error; resetting handle: %s", e)
            self._shutdown_nvml()
            self._nvml_last_error_time = time.time()
            return GpuTelemetry(has_gpu=False, model="CPU Only (GPU Offline)")

    def _sample_partitions(self) -> tuple[PartitionMetric, ...]:
        if not _HAS_PSUTIL:
            return ()

        parts: list[PartitionMetric] = []
        try:
            for p in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(p.mountpoint)
                    parts.append(PartitionMetric(
                        device=p.device,
                        mountpoint=p.mountpoint,
                        fstype=p.fstype,
                        total_gb=round(usage.total / (1024**3), 2),
                        used_gb=round(usage.used / (1024**3), 2),
                        free_gb=round(usage.free / (1024**3), 2),
                        percent_used=round(usage.percent, 1),
                    ))
                except (PermissionError, OSError):
                    continue
        except Exception as e:
            logger.debug("Failed sampling disk partitions: %s", e)

        return tuple(parts)

    def _sample_network(self) -> NetworkTelemetry:
        if not _HAS_PSUTIL:
            return NetworkTelemetry()

        try:
            net = psutil.net_io_counters()
            return NetworkTelemetry(
                bytes_sent_mb=round(net.bytes_sent / (1024**2), 1),
                bytes_recv_mb=round(net.bytes_recv / (1024**2), 1),
                packets_sent=net.packets_sent,
                packets_recv=net.packets_recv,
            )
        except Exception as e:
            logger.debug("Failed sampling network counters: %s", e)
            return NetworkTelemetry()

    def _sample_battery(self) -> BatteryTelemetry:
        if not _HAS_PSUTIL or not hasattr(psutil, "sensors_battery"):
            return BatteryTelemetry(has_battery=False, is_plugged=True)

        try:
            bat = psutil.sensors_battery()
            if bat is None:
                return BatteryTelemetry(has_battery=False, is_plugged=True)

            return BatteryTelemetry(
                has_battery=True,
                percent=round(bat.percent, 1),
                is_plugged=bool(bat.power_plugged),
                secs_left=int(bat.secsleft) if bat.secsleft > 0 else None,
            )
        except Exception as e:
            logger.debug("Failed sampling battery: %s", e)
            return BatteryTelemetry(has_battery=False, is_plugged=True)

    def _sample_processes(self, top_n: int = 10, force: bool = False) -> tuple[ProcessTelemetry, ...]:
        """Collect top processes sorted by resource usage (STRICTLY NO cmdline collection). Cached with 10s TTL."""
        if not _HAS_PSUTIL:
            return ()

        now = time.monotonic()
        if not force and self._cached_processes and (now - self._last_process_sample_time) < self._process_cache_ttl_s:
            return self._cached_processes[:top_n]

        collected: list[ProcessTelemetry] = []
        try:
            # Query memory metrics first across all processes without computing full CPU diffs
            candidate_procs = []
            for p in psutil.process_iter(["pid", "name", "memory_percent", "status", "memory_info"]):
                try:
                    info = p.info
                    mem_pct = float(info.get("memory_percent") or 0.0)
                    candidate_procs.append((mem_pct, p))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Sort primarily by memory usage descending and take the top candidate pool
            candidate_procs.sort(key=lambda x: x[0], reverse=True)
            top_pool = candidate_procs[:max(top_n, 20)]

            for mem_pct, p in top_pool:
                try:
                    info = p.info
                    pid = int(info.get("pid") or 0)
                    name = str(info.get("name") or "unknown")
                    status = str(info.get("status") or "running")
                    mem_info = info.get("memory_info")
                    mem_mb = round(mem_info.rss / (1024**2), 1) if mem_info else 0.0
                    cpu_pct = 0.0
                    try:
                        cpu_pct = float(p.cpu_percent())
                    except Exception:
                        pass

                    collected.append(ProcessTelemetry(
                        pid=pid,
                        name=name,
                        cpu_percent=round(cpu_pct, 1),
                        memory_percent=round(mem_pct, 1),
                        memory_mb=mem_mb,
                        status=status,
                    ))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.debug("Failed enumerating processes: %s", e)

        # Sort primarily by memory %, secondarily by CPU %
        collected.sort(key=lambda x: (x.memory_percent, x.cpu_percent), reverse=True)
        self._cached_processes = tuple(collected)
        self._last_process_sample_time = now
        return self._cached_processes[:top_n]

    def _sample_libre_hardware(self) -> tuple[bool, dict[str, Any]]:
        """Safely probe optional LibreHardwareMonitor WMI namespace without crashing or polling continuously."""
        if platform.system() != "Windows":
            return False, {}

        now = time.monotonic()
        if not self._wmi_available and (now - self._wmi_last_check_time) < self._wmi_backoff_s:
            return False, {}

        self._wmi_last_check_time = now

        try:
            import pythoncom  # type: ignore
            import wmi  # type: ignore
        except ImportError:
            return False, {}

        co_init = False
        try:
            try:
                pythoncom.CoInitialize()
                co_init = True
            except Exception:
                pass

            w = wmi.WMI(namespace="root/LibreHardwareMonitor", find_classes=False)
            sensors = w.Sensor()
            parsed: dict[str, list[dict[str, Any]]] = {}
            for s in sensors[:20]:
                stype = getattr(s, "SensorType", "Other")
                name = getattr(s, "Name", "Unknown")
                val = getattr(s, "Value", 0.0)
                parsed.setdefault(stype, []).append({"name": name, "value": val})
            self._wmi_available = True
            self._wmi_backoff_s = 60.0
            return True, parsed
        except Exception:
            # Expected when LibreHardwareMonitor is not running (e.g. 0x8004100e)
            self._wmi_available = False
            self._wmi_backoff_s = min(600.0, self._wmi_backoff_s * 2.0)
            return False, {}
        finally:
            import gc
            w = None
            sensors = None
            gc.collect()
            if co_init:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
