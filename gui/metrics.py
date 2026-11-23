"""gui/metrics.py — System telemetry for the HUD: CPU, memory, network, GPU and temperature.

Unified with WindowsSystemMonitorAdapter: zero duplicate background polling threads,
providing O(1) non-blocking snapshots for HUD and presentation layers.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("optimus.gui.metrics")

from core.system_monitor.adapter import WindowsSystemMonitorAdapter
from gui.theme import _OS

# ── Windows GPU via NVML DLL (for backward compatibility) ──────────────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    """Unified telemetry translation facade over authoritative WindowsSystemMonitorAdapter.

    Eliminates redundant background thread and lock contention while preserving
    the exact dict structure expected by callers of _metrics.snapshot().
    """

    def __init__(self):
        self._adapter = WindowsSystemMonitorAdapter.instance()
        self._lock = threading.Lock()
        self._last_net_mb: float = 0.0
        self._last_net_t: float = time.time()
        self._cached_net_rate: float = 0.0

    def snapshot(self) -> dict[str, float]:
        """Return instantaneous telemetry dictionary in O(1) time from cached snapshot."""
        snap = self._adapter.get_snapshot()

        now = time.time()
        with self._lock:
            dt = now - self._last_net_t
            tot_mb = snap.network.bytes_sent_mb + snap.network.bytes_recv_mb
            if dt >= 1.0 and self._last_net_t > 0:
                if self._last_net_mb > 0 and tot_mb >= self._last_net_mb:
                    self._cached_net_rate = (tot_mb - self._last_net_mb) / dt
                self._last_net_mb = tot_mb
                self._last_net_t = now

            net_rate = self._cached_net_rate

        gpu_val = snap.gpu.gpu_percent if snap.gpu.has_gpu else -1.0
        tmp_val = snap.gpu.temperature_c if (snap.gpu.has_gpu and snap.gpu.temperature_c is not None) else -1.0

        return {
            "cpu": snap.cpu.total_percent,
            "mem": snap.memory.percent_used,
            "net": round(net_rate, 2),
            "gpu": round(gpu_val, 1),
            "tmp": round(tmp_val, 1),
        }

    @property
    def cpu(self) -> float:
        return self.snapshot()["cpu"]

    @property
    def mem(self) -> float:
        return self.snapshot()["mem"]

    @property
    def net(self) -> float:
        return self.snapshot()["net"]

    @property
    def gpu(self) -> float:
        return self.snapshot()["gpu"]

    @property
    def tmp(self) -> float:
        return self.snapshot()["tmp"]


_metrics = _SysMetrics()
