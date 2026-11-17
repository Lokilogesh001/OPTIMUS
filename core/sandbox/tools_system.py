"""core/sandbox/tools_system.py — Sandboxed System Diagnosis Tools for OPTIMUS.

Provides:
- Hardware, OS, and platform information.
- CPU, memory, and disk utilization monitoring.
- GPU utilization diagnostics.
- Process tree inspection and diagnostics.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from core.orchestrator.tools import CapabilityTier, ToolDefinition, ToolResult

# Optional psutil import with pure-python / ctypes fallback
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def system_info_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Gather comprehensive hardware, operating system, and runtime platform information."""
    boot_time_str = "Unknown"
    if _HAS_PSUTIL:
        try:
            boot_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(psutil.boot_time()))
        except Exception:
            pass

    info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor() or "Unknown",
        "python_version": sys.version.split()[0],
        "hostname": platform.node(),
        "boot_time": boot_time_str,
    }

    output = (
        f"### System Diagnosis Information\n"
        f"- **OS:** {info['os']} {info['os_release']} ({info['architecture']})\n"
        f"- **Kernel/Version:** {info['os_version']}\n"
        f"- **Processor:** {info['processor']}\n"
        f"- **Host:** {info['hostname']}\n"
        f"- **Python:** {info['python_version']}\n"
        f"- **Last Boot:** {info['boot_time']}"
    )
    return ToolResult(status="done", output=output, evidence=info)


def cpu_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Gather real-time CPU core counts and utilization metrics."""
    cpu_count = os.cpu_count() or 1
    cpu_percent = 0.0
    per_cpu = []

    if _HAS_PSUTIL:
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            per_cpu = psutil.cpu_percent(interval=0.1, percpu=True)
        except Exception:
            pass

    evidence = {
        "cores_logical": cpu_count,
        "overall_percent": cpu_percent,
        "per_core_percent": per_cpu[:8],
    }

    output = (
        f"### CPU Status\n"
        f"- **Logical Cores:** {cpu_count}\n"
        f"- **Total Utilization:** {cpu_percent:.1f}%\n"
        f"- **Core Sample:** {per_cpu[:4]}"
    )
    return ToolResult(status="done", output=output, evidence=evidence)


def memory_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Gather RAM and virtual memory metrics."""
    total_gb = 0.0
    avail_gb = 0.0
    used_percent = 0.0

    if _HAS_PSUTIL:
        try:
            mem = psutil.virtual_memory()
            total_gb = round(mem.total / (1024**3), 2)
            avail_gb = round(mem.available / (1024**3), 2)
            used_percent = mem.percent
        except Exception:
            pass
    elif os.name == "nt":
        # Windows pure ctypes fallback
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_gb = round(stat.ullTotalPhys / (1024**3), 2)
            avail_gb = round(stat.ullAvailPhys / (1024**3), 2)
            used_percent = float(stat.dwMemoryLoad)
        except Exception:
            pass

    evidence = {"total_gb": total_gb, "available_gb": avail_gb, "used_percent": used_percent}
    output = (
        f"### RAM Memory Status\n"
        f"- **Total Physical RAM:** {total_gb} GB\n"
        f"- **Available RAM:** {avail_gb} GB\n"
        f"- **Memory Load:** {used_percent:.1f}%"
    )
    return ToolResult(status="done", output=output, evidence=evidence)


def disk_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Inspect storage partition usage and available free space."""
    disks = []
    if _HAS_PSUTIL:
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disks.append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total_gb": round(usage.total / (1024**3), 2),
                        "free_gb": round(usage.free / (1024**3), 2),
                        "percent": usage.percent,
                    })
                except (PermissionError, OSError):
                    continue
        except Exception:
            pass

    if not disks:
        # Fallback to current drive
        cwd = Path.cwd()
        root = cwd.anchor or "."
        disks.append({
            "device": root,
            "mountpoint": root,
            "total_gb": "N/A",
            "free_gb": "N/A",
            "percent": "N/A",
        })

    lines = ["### Storage Disk Status & Partitions:"]
    for d in disks:
        lines.append(f"- **{d['mountpoint']}** ({d.get('device', '')}): {d['free_gb']} GB free of {d['total_gb']} GB ({d['percent']}% used)")

    return ToolResult(status="done", output="\n".join(lines), evidence={"partitions": disks})


def gpu_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Query dedicated GPU utilization using Mark-LVII monitor bridge."""
    gpu_pct = -1.0
    try:
        from actions.system_monitor import _get_gpu_usage
        gpu_pct = _get_gpu_usage()
    except Exception:
        pass

    has_gpu = (gpu_pct >= 0.0)
    output = (
        f"### GPU Utilization\n"
        f"- **Dedicated GPU Detected:** {'Yes' if has_gpu else 'No or Unavailable'}\n"
        f"- **Load:** {f'{gpu_pct:.1f}%' if has_gpu else 'N/A'}"
    )
    return ToolResult(status="done", output=output, evidence={"has_gpu": has_gpu, "gpu_utilization_percent": gpu_pct})


def process_diagnostics_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Inspect running processes, top CPU/RAM consumers, or diagnose a specific PID."""
    target_pid = params.get("pid")
    top_n = int(params.get("top_n") or 8)

    if not _HAS_PSUTIL:
        return ToolResult(
            status="done",
            output=f"Process diagnostics: Current process PID is {os.getpid()}.",
            evidence={"pid": os.getpid()},
        )

    try:
        if target_pid is not None:
            p = psutil.Process(int(target_pid))
            info = {
                "pid": p.pid,
                "name": p.name(),
                "status": p.status(),
                "memory_mb": round(p.memory_info().rss / (1024**2), 2),
                "cpu_percent": p.cpu_percent(interval=0.05),
            }
            output = f"PID {info['pid']} ({info['name']}): status={info['status']}, RAM={info['memory_mb']}MB, CPU={info['cpu_percent']}%"
            return ToolResult(status="done", output=output, evidence=info)

        # List top processes by memory
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
            try:
                procs.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"] or "unknown",
                    "mem_pct": round(p.info["memory_percent"] or 0.0, 1),
                    "cpu_pct": round(p.info["cpu_percent"] or 0.0, 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda x: x["mem_pct"], reverse=True)
        top = procs[:top_n]

        lines = [f"### Top {top_n} Active Processes by Memory:"]
        for p in top:
            lines.append(f"- PID {p['pid']:6} | {p['name']:25} | RAM: {p['mem_pct']}% | CPU: {p['cpu_pct']}%")

        return ToolResult(status="done", output="\n".join(lines), evidence={"top_processes": top})
    except Exception as ex:
        return ToolResult(status="failed", error=f"Process diagnostic error: {ex}")


def get_system_tools() -> list[ToolDefinition]:
    """Return tool definitions for the System Diagnosis Agent."""
    return [
        ToolDefinition(
            name="system_info",
            description="Inspect hardware, OS, platform architecture, and Python runtime info.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=system_info_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="cpu_status",
            description="Measure real-time CPU core counts, load averages, and utilization.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=cpu_status_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="memory_status",
            description="Measure physical RAM and swap memory usage.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=memory_status_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="disk_status",
            description="Inspect storage partitions, filesystem mounts, and free disk space.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=disk_status_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="gpu_status",
            description="Check dedicated GPU presence and utilization.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=gpu_status_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="process_diagnostics",
            description="Inspect top resource-consuming processes or query specific process PID.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "pid": {"type": "INTEGER", "description": "Optional PID to inspect"},
                    "top_n": {"type": "INTEGER", "description": "Number of top processes to return (default 8)"},
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=process_diagnostics_handler,
            is_mutating=False,
        ),
    ]
