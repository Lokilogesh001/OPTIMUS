"""core/sandbox/tools_system.py — Sandboxed System Diagnosis Tools for OPTIMUS.

Provides:
- Hardware, OS, and platform information.
- CPU, memory, and disk utilization monitoring.
- GPU utilization diagnostics.
- Process tree inspection and diagnostics.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
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
    """Query dedicated GPU utilization, driver, and VRAM metrics using NVML or hardware bridge."""
    gpu_pct = -1.0
    gpu_name = "Unknown GPU"
    driver_version = "Unknown"
    vram_total_mb = 0
    vram_used_mb = 0

    try:
        from actions.system_monitor import _get_gpu_usage, _nvml_lib
        gpu_pct = _get_gpu_usage()
        if _nvml_lib is not None:
            try:
                import ctypes
                dev = ctypes.c_void_p()
                _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                name_buf = ctypes.create_string_buffer(64)
                _nvml_lib.nvmlDeviceGetName(dev, name_buf, 64)
                gpu_name = name_buf.value.decode("utf-8", errors="replace")

                drv_buf = ctypes.create_string_buffer(64)
                _nvml_lib.nvmlSystemGetDriverVersion(drv_buf, 64)
                driver_version = drv_buf.value.decode("utf-8", errors="replace")

                class Memory(ctypes.Structure):
                    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]
                mem = Memory()
                _nvml_lib.nvmlDeviceGetMemoryInfo(dev, ctypes.byref(mem))
                vram_total_mb = int(mem.total // (1024 * 1024))
                vram_used_mb = int(mem.used // (1024 * 1024))
            except Exception:
                pass
    except Exception:
        pass

    has_gpu = (gpu_pct >= 0.0)
    evidence = {
        "has_gpu": has_gpu,
        "gpu_name": gpu_name if has_gpu else "N/A",
        "driver_version": driver_version if has_gpu else "N/A",
        "gpu_utilization_percent": gpu_pct if has_gpu else 0.0,
        "vram_total_mb": vram_total_mb,
        "vram_used_mb": vram_used_mb,
    }

    if has_gpu:
        output = (
            f"### GPU Status & Telemetry\n"
            f"- **GPU Model:** {gpu_name}\n"
            f"- **Driver Version:** {driver_version}\n"
            f"- **Utilization:** {gpu_pct:.1f}%\n"
            f"- **VRAM:** {vram_used_mb} MB / {vram_total_mb} MB ({round((vram_used_mb/max(1, vram_total_mb))*100, 1)}% used)"
        )
    else:
        output = (
            f"### GPU Status & Telemetry\n"
            f"- **Dedicated GPU:** Unavailable or not detected (NVML interface not accessible).\n"
            f"- **Operating Mode:** CPU-first execution profile active."
        )

    return ToolResult(status="done", output=output, evidence=evidence)


def ollama_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Inspect the running Ollama local LLM service and enumerate loaded/available models."""
    host = str(params.get("host") or "http://localhost:11434").rstrip("/")
    timeout = float(params.get("timeout") or 2.5)

    is_running = False
    version = "Unknown"
    models: list[dict[str, Any]] = []

    # 1. Probe version endpoint
    try:
        req_v = urllib.request.Request(f"{host}/api/version", headers={"User-Agent": "OPTIMUS/1.0"})
        with urllib.request.urlopen(req_v, timeout=timeout) as resp:
            if resp.status == 200:
                is_running = True
                data_v = json.loads(resp.read().decode("utf-8", errors="replace"))
                version = data_v.get("version", "Unknown")
    except Exception:
        pass

    # 2. Probe tags (models) endpoint
    if is_running:
        try:
            req_t = urllib.request.Request(f"{host}/api/tags", headers={"User-Agent": "OPTIMUS/1.0"})
            with urllib.request.urlopen(req_t, timeout=timeout) as resp:
                if resp.status == 200:
                    data_t = json.loads(resp.read().decode("utf-8", errors="replace"))
                    for m in data_t.get("models", []):
                        size_gb = round(m.get("size", 0) / (1024**3), 2)
                        models.append({
                            "name": m.get("name", "unknown"),
                            "size_gb": size_gb,
                            "modified_at": m.get("modified_at", ""),
                            "digest": m.get("digest", "")[:12],
                        })
        except Exception:
            pass

    evidence = {
        "is_running": is_running,
        "host": host,
        "version": version,
        "model_count": len(models),
        "models": [m["name"] for m in models],
        "model_details": models,
    }

    if is_running:
        model_lines = [f"  - `{m['name']}` ({m['size_gb']} GB)" for m in models]
        models_text = "\n".join(model_lines) if model_lines else "  - (No models pulled yet)"
        output = (
            f"### Ollama Service Status: RUNNING\n"
            f"- **Endpoint:** `{host}`\n"
            f"- **Ollama Version:** {version}\n"
            f"- **Installed Models ({len(models)}):**\n"
            f"{models_text}"
        )
        return ToolResult(status="done", output=output, evidence=evidence)
    else:
        output = (
            f"### Ollama Service Status: OFFLINE\n"
            f"- **Endpoint:** `{host}` is unreachable.\n"
            f"- **Detail:** Ollama daemon is not running on this port or connection refused."
        )
        return ToolResult(status="done", output=output, evidence=evidence)


def python_version_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Retrieve installed Python version, virtual environment details, and developer tooling versions."""
    py_ver = sys.version.split()[0]
    py_exe = sys.executable
    is_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix))

    tools_to_check = params.get("tools") or ["python", "git", "node", "npm", "docker", "cargo", "rustc", "ollama"]
    detected_tools: dict[str, str] = {}

    for t in tools_to_check:
        loc = shutil.which(str(t))
        if not loc:
            detected_tools[t] = "Not installed"
            continue
        try:
            res = subprocess.run([loc, "--version"], capture_output=True, text=True, timeout=2.0)
            raw = (res.stdout or res.stderr).strip()
            ver_line = raw.splitlines()[0] if raw else "Installed"
            detected_tools[t] = ver_line
        except Exception as ex:
            detected_tools[t] = f"Installed (version check failed: {ex})"

    lines = [
        f"### Python & Developer Environment\n",
        f"- **Python Version:** {py_ver} ({platform.python_implementation()})",
        f"- **Executable:** `{py_exe}`",
        f"- **Virtual Environment:** {'Active' if is_venv else 'System Python'}",
        f"\n**Installed Developer Tools:**",
    ]
    for t_name, t_ver in detected_tools.items():
        lines.append(f"- **{t_name:10}:** {t_ver}")

    evidence = {
        "python_version": py_ver,
        "executable": py_exe,
        "is_venv": is_venv,
        "developer_tools": detected_tools,
    }
    return ToolResult(status="done", output="\n".join(lines), evidence=evidence)


def computer_audit_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    """Execute a comprehensive, read-only physical computer audit covering hardware, OS, storage, memory, processes, dev tools, and Ollama."""
    start_time = time.time()

    # 1. Gather all sub-components directly using internal handlers
    sys_res = system_info_handler({}, context)
    cpu_res = cpu_status_handler({}, context)
    mem_res = memory_status_handler({}, context)
    disk_res = disk_status_handler({}, context)
    gpu_res = gpu_status_handler({}, context)
    proc_res = process_diagnostics_handler({"top_n": 5}, context)
    env_res = python_version_handler({}, context)
    ollama_res = ollama_status_handler({}, context)

    # 2. Gather Startup Applications (Windows Registry read-only)
    startup_apps: list[dict[str, str]] = []
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    key = winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Run")
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        name, val, _ = winreg.EnumValue(key, i)
                        startup_apps.append({"name": name, "command": str(val)[:80]})
                    winreg.CloseKey(key)
                except Exception:
                    pass
        except Exception:
            pass

    # 3. Assess Actionable Bottlenecks & Recommendations
    recommendations: list[str] = []

    # Check RAM pressure
    used_ram_pct = mem_res.evidence.get("used_percent", 0.0)
    if used_ram_pct > 80.0:
        recommendations.append(f"**[P1 - High RAM Pressure]** RAM usage is {used_ram_pct:.1f}%. Close heavy background browser or IDE processes.")
    elif used_ram_pct > 60.0:
        recommendations.append(f"**[P2 - Moderate RAM Usage]** RAM usage is {used_ram_pct:.1f}%. Operating normally within capacity.")
    else:
        recommendations.append(f"**[P3 - RAM Healthy]** RAM usage is {used_ram_pct:.1f}%. Ample memory available.")

    # Check Disk Space
    partitions = disk_res.evidence.get("partitions", [])
    for p in partitions:
        free_gb = p.get("free_gb")
        pct_used = p.get("percent")
        if isinstance(pct_used, (int, float)) and pct_used > 85.0:
            recommendations.append(f"**[P1 - Storage Alert]** Drive {p.get('mountpoint')} is {pct_used}% full ({free_gb} GB remaining). Clean temporary builds and caches.")
        elif isinstance(pct_used, (int, float)) and pct_used > 70.0:
            recommendations.append(f"**[P2 - Storage Notice]** Drive {p.get('mountpoint')} is {pct_used}% full ({free_gb} GB remaining).")

    # Check Ollama
    if ollama_res.evidence.get("is_running"):
        models_cnt = ollama_res.evidence.get("model_count", 0)
        recommendations.append(f"**[P3 - Local AI Ready]** Ollama daemon active with {models_cnt} local models loaded.")
    else:
        recommendations.append(f"**[P2 - Local AI Inactive]** Ollama service is offline. Start Ollama to enable zero-token local inference.")

    # Check Startup Apps
    if len(startup_apps) > 10:
        recommendations.append(f"**[P2 - Startup Optimization]** {len(startup_apps)} startup entries registered in registry. Consider trimming unused auto-launch programs.")

    # Construct comprehensive audit report
    report_lines = [
        f"# OPTIMUS System Audit Report",
        f"*Conducted on {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))} via direct physical telemetry (zero speculation, zero file modification)*\n",
        f"## 1. Operating System & Platform",
        f"- **OS:** {sys_res.evidence.get('os')} {sys_res.evidence.get('os_release')} ({sys_res.evidence.get('architecture')})",
        f"- **Host:** {sys_res.evidence.get('hostname')} | Kernel: {sys_res.evidence.get('os_version')}",
        f"- **Last Boot:** {sys_res.evidence.get('boot_time')}",
        f"\n## 2. Processor & Compute Metrics",
        f"- **CPU Model:** {sys_res.evidence.get('processor')}",
        f"- **Logical Cores:** {cpu_res.evidence.get('cores_logical')} | Overall Utilization: {cpu_res.evidence.get('overall_percent'):.1f}%",
        f"\n## 3. Dedicated Graphics & VRAM",
    ]

    if gpu_res.evidence.get("has_gpu"):
        report_lines.extend([
            f"- **GPU Model:** {gpu_res.evidence.get('gpu_name')}",
            f"- **Driver Version:** {gpu_res.evidence.get('driver_version')}",
            f"- **VRAM:** {gpu_res.evidence.get('vram_used_mb')} MB used / {gpu_res.evidence.get('vram_total_mb')} MB total",
            f"- **GPU Load:** {gpu_res.evidence.get('gpu_utilization_percent'):.1f}%",
        ])
    else:
        report_lines.append(f"- Dedicated GPU interface not detected or inaccessible via NVML; system running on integrated graphics / CPU-first mode.")

    report_lines.extend([
        f"\n## 4. Memory & Storage Telemetry",
        f"- **Physical RAM:** {mem_res.evidence.get('total_gb')} GB total | {mem_res.evidence.get('available_gb')} GB available ({mem_res.evidence.get('used_percent'):.1f}% load)",
    ])
    for p in partitions:
        report_lines.append(f"- **Partition {p.get('mountpoint')}**: {p.get('free_gb')} GB free of {p.get('total_gb')} GB ({p.get('percent')}% used)")

    report_lines.extend([
        f"\n## 5. Active Processes (Top Consumers)",
    ])
    top_procs = proc_res.evidence.get("top_processes", [])
    for p in top_procs[:5]:
        report_lines.append(f"- PID {p['pid']:6} | `{p['name']:25}` | RAM: {p['mem_pct']}% | CPU: {p['cpu_pct']}%")

    report_lines.extend([
        f"\n## 6. Developer Environment & Local AI",
        f"- **Python Version:** {env_res.evidence.get('python_version')} (`{env_res.evidence.get('executable')}`)",
        f"- **Ollama Daemon:** {'ONLINE' if ollama_res.evidence.get('is_running') else 'OFFLINE'} | Models: {', '.join(ollama_res.evidence.get('models', [])) or 'None'}",
    ])
    for t_name, t_ver in env_res.evidence.get("developer_tools", {}).items():
        if t_name not in ("python", "ollama"):
            report_lines.append(f"- **{t_name:10}:** {t_ver}")

    if startup_apps:
        report_lines.append(f"\n## 7. Relevant Startup Applications ({len(startup_apps)} detected)")
        for sa in startup_apps[:6]:
            report_lines.append(f"- `{sa['name']}`: {sa['command']}")

    report_lines.append(f"\n## 8. Prioritized Recommendations & Action Plan")
    for rec in recommendations:
        report_lines.append(f"- {rec}")

    full_report = "\n".join(report_lines)

    complete_evidence = {
        "timestamp": start_time,
        "duration_seconds": round(time.time() - start_time, 3),
        "system": sys_res.evidence,
        "cpu": cpu_res.evidence,
        "memory": mem_res.evidence,
        "disks": disk_res.evidence,
        "gpu": gpu_res.evidence,
        "processes": proc_res.evidence,
        "environment": env_res.evidence,
        "ollama": ollama_res.evidence,
        "startup_count": len(startup_apps),
        "recommendations_count": len(recommendations),
    }

    return ToolResult(status="done", output=full_report, evidence=complete_evidence)


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
            description="Check dedicated GPU presence, driver, VRAM, and utilization.",
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
        ToolDefinition(
            name="ollama_status",
            description="Check if local Ollama daemon is active, retrieve version, and list installed models.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "host": {"type": "STRING", "description": "Ollama host endpoint (default: http://localhost:11434)"},
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=ollama_status_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="check_ollama",
            description="Alias for ollama_status: verify running Ollama service and models.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=ollama_status_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="python_version",
            description="Retrieve active Python interpreter version, virtualenv status, and installed developer CLI tools.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=python_version_handler,
            is_mutating=False,
        ),
        ToolDefinition(
            name="computer_audit",
            description="Execute complete read-only audit of computer hardware, OS, storage, memory, processes, dev tools, and Ollama.",
            parameters={"type": "OBJECT", "properties": {}},
            capability_tier=CapabilityTier.READ_ONLY,
            handler=computer_audit_handler,
            is_mutating=False,
        ),
    ]
