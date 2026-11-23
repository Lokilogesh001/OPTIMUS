"""tests/profile_optimus_runtime_baseline.py — Full Runtime Profiling Harness for OPTIMUS.

Measures:
- Application startup duration and per-section initialization times.
- Main thread and total process CPU utilization.
- RAM consumption (RSS, Private Bytes, Working Set) and idle growth.
- GPU utilization and VRAM (via pynvml / nvml if available).
- Qt event-loop latency and jitter over 200 ticks.
- Active background thread count and names.
- Active QTimer count and execution intervals.
- System monitor sampling latency.
- MRE agent orchestration latency.
- Resource consumption during active workloads (view switching, telemetry burst, MRE tasks).
"""

from __future__ import annotations

import gc
import json
import os
import platform
import sys
import threading
import time
from typing import Any

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["OPTIMUS_HEADLESS"] = "1"

import psutil

try:
    import pynvml
    pynvml.nvmlInit()
    _HAS_NVML = True
except Exception:
    _HAS_NVML = False


def _get_gpu_vitals() -> dict[str, Any]:
    if not _HAS_NVML:
        return {"has_gpu": False, "gpu_percent": 0.0, "vram_used_mb": 0.0, "vram_total_mb": 0.0}
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        return {
            "has_gpu": True,
            "gpu_percent": float(util.gpu),
            "vram_used_mb": round(mem.used / (1024**2), 1),
            "vram_total_mb": round(mem.total / (1024**2), 1),
        }
    except Exception as e:
        return {"has_gpu": False, "error": str(e), "gpu_percent": 0.0, "vram_used_mb": 0.0, "vram_total_mb": 0.0}


def run_baseline_profile() -> dict[str, Any]:
    print("=" * 70)
    print("OPTIMUS RUNTIME PERFORMANCE BASELINE PROFILER")
    print("=" * 70)

    cur_proc = psutil.Process()
    # Baseline CPU warm-up
    cur_proc.cpu_percent(interval=None)

    results: dict[str, Any] = {
        "timestamp": time.time(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version,
            "logical_cpus": psutil.cpu_count(logical=True),
            "physical_cpus": psutil.cpu_count(logical=False),
            "total_ram_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        },
        "startup": {},
        "per_section_init": {},
        "idle_metrics": {},
        "event_loop_latency": {},
        "concurrency_and_timers": {},
        "telemetry_sampling": {},
        "mre_orchestration": {},
        "workload_metrics": {},
    }

    print(f"Platform: {results['platform']['system']} | Python {sys.version.split()[0]} | RAM: {results['platform']['total_ram_gb']} GB")

    # ── Phase 1: Startup & Section Initialization Duration ───────────────────
    print("\n[Phase 1] Measuring Application & Section Startup Duration...")
    t0_start = time.perf_counter()

    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    t_qapp = time.perf_counter()
    results["startup"]["qapplication_init_ms"] = round((t_qapp - t0_start) * 1000, 2)

    # Individual section imports and instantiations
    from gui.optimus.service_bridge import OptimusServiceBridge
    t_b0 = time.perf_counter()
    bridge = OptimusServiceBridge.instance()
    t_b1 = time.perf_counter()
    results["startup"]["service_bridge_init_ms"] = round((t_b1 - t_b0) * 1000, 2)

    from gui.optimus.dashboard import OptimusDashboardView
    from gui.optimus.section_command import SectionCommandCenter
    from gui.optimus.section_workflows import SectionWorkflows
    from gui.optimus.section_agents import SectionAgents
    from gui.optimus.section_intelligence import SectionIntelligence
    from gui.optimus.section_memory import SectionMemory
    from gui.optimus.section_verification import SectionVerification
    from gui.optimus.section_approvals import SectionApprovals
    from gui.optimus.section_system import SectionSystem
    from gui.optimus.section_settings import SectionSettings
    from gui.optimus.section_planning import SectionPlanning
    from gui.optimus.section_developer import SectionDeveloper
    from gui.optimus.section_skills import SectionSkills
    from gui.optimus.section_missions import SectionMissions
    from gui.optimus.section_proactive import SectionProactive
    from gui.optimus.section_personal import SectionPersonal
    from gui.optimus.section_self_improvement import SectionSelfImprovement
    from gui.optimus.section_vision import SectionVision
    from gui.optimus.section_applications import SectionApplications
    from gui.optimus.section_integrations import SectionIntegrations

    section_classes = [
        ("Dashboard", OptimusDashboardView),
        ("Command", SectionCommandCenter),
        ("Workflows", SectionWorkflows),
        ("Agents", SectionAgents),
        ("Intelligence", SectionIntelligence),
        ("Memory", SectionMemory),
        ("Verification", SectionVerification),
        ("Approvals", SectionApprovals),
        ("System", SectionSystem),
        ("Settings", SectionSettings),
        ("Planning", SectionPlanning),
        ("Developer", SectionDeveloper),
        ("Skills", SectionSkills),
        ("Missions", SectionMissions),
        ("Proactive", SectionProactive),
        ("Personal", SectionPersonal),
        ("SelfImprovement", SectionSelfImprovement),
        ("Vision", SectionVision),
        ("Applications", SectionApplications),
        ("Integrations", SectionIntegrations),
    ]

    total_section_init_ms = 0.0
    for name, cls in section_classes:
        s_t0 = time.perf_counter()
        inst = cls(bridge)
        s_t1 = time.perf_counter()
        dur = round((s_t1 - s_t0) * 1000, 2)
        results["per_section_init"][name] = dur
        total_section_init_ms += dur
        print(f"  -> Section {name:16}: {dur:6.2f} ms")

    results["startup"]["total_section_init_ms"] = round(total_section_init_ms, 2)

    # Full OptimusUI initialization
    from ui import OptimusUI
    t_ui0 = time.perf_counter()
    face_path = os.path.join(_ROOT, "face.png")
    ui = OptimusUI(face_path if os.path.exists(face_path) else "face.png")
    t_ui1 = time.perf_counter()
    results["startup"]["optimus_ui_full_init_ms"] = round((t_ui1 - t_ui0) * 1000, 2)
    results["startup"]["total_startup_duration_ms"] = round((t_ui1 - t0_start) * 1000, 2)

    print(f"Total Startup Time: {results['startup']['total_startup_duration_ms']:.2f} ms")

    # ── Phase 2: Concurrency & Timer Inventory ───────────────────────────────
    print("\n[Phase 2] Auditing Threads & Timers...")
    threads = threading.enumerate()
    thread_info = [{"name": t.name, "daemon": t.daemon, "alive": t.is_alive()} for t in threads]
    results["concurrency_and_timers"]["thread_count"] = len(threads)
    results["concurrency_and_timers"]["threads"] = thread_info
    print(f"  -> Active Python Threads: {len(threads)}")
    for t in threads:
        print(f"     * {t.name} (daemon={t.daemon})")

    # Find all QTimers in QApplication
    all_timers = []
    for obj in app.allWidgets():
        for child in obj.children():
            if isinstance(child, QTimer):
                all_timers.append({
                    "parent_widget": obj.__class__.__name__,
                    "interval_ms": child.interval(),
                    "is_active": child.isActive(),
                    "single_shot": child.isSingleShot(),
                })
    results["concurrency_and_timers"]["qtimer_count"] = len(all_timers)
    results["concurrency_and_timers"]["qtimers"] = all_timers
    print(f"  -> Active QTimers in GUI tree: {len(all_timers)}")
    for q in all_timers:
        print(f"     * {q['parent_widget']:20} interval={q['interval_ms']:5}ms active={q['is_active']}")

    # ── Phase 3: Qt Event Loop Latency Measurement ───────────────────────────
    print("\n[Phase 3] Measuring Qt Event Loop Latency & Jitter...")
    target_interval_ms = 10
    total_samples = 200
    delays: list[float] = []
    last_tick = [time.perf_counter()]

    loop_done = False
    loop_samples = [0]

    lat_timer = QTimer()
    lat_timer.setInterval(target_interval_ms)

    def on_lat_tick():
        now = time.perf_counter()
        actual_delta_ms = (now - last_tick[0]) * 1000.0
        jitter_ms = abs(actual_delta_ms - target_interval_ms)
        delays.append(jitter_ms)
        last_tick[0] = now
        loop_samples[0] += 1
        if loop_samples[0] >= total_samples:
            lat_timer.stop()

    lat_timer.timeout.connect(on_lat_tick)
    lat_timer.start()

    # Spin event loop for 2.5 seconds to collect samples
    t_spin_start = time.time()
    while loop_samples[0] < total_samples and (time.time() - t_spin_start) < 4.0:
        app.processEvents()
        time.sleep(0.005)

    if delays:
        mean_jitter = sum(delays) / len(delays)
        max_jitter = max(delays)
        delays_sorted = sorted(delays)
        p95_jitter = delays_sorted[int(len(delays_sorted) * 0.95)]
        results["event_loop_latency"] = {
            "target_interval_ms": target_interval_ms,
            "samples_collected": len(delays),
            "mean_jitter_ms": round(mean_jitter, 2),
            "p95_jitter_ms": round(p95_jitter, 2),
            "max_jitter_ms": round(max_jitter, 2),
        }
        print(f"  -> Event Loop Mean Jitter : {mean_jitter:6.2f} ms")
        print(f"  -> Event Loop P95 Jitter  : {p95_jitter:6.2f} ms")
        print(f"  -> Event Loop Max Spike   : {max_jitter:6.2f} ms")

    # ── Phase 4: Idle Resource Consumption & Memory Growth ───────────────────
    print("\n[Phase 4] Measuring Idle CPU/RAM/GPU & Memory Drift (10 seconds)...")
    gc.collect()
    start_rss = cur_proc.memory_info().rss / (1024**2)
    samples_cpu: list[float] = []
    samples_ram: list[float] = []
    samples_gpu: list[float] = []

    # Reset CPU counter
    cur_proc.cpu_percent(interval=None)

    for i in range(10):
        time.sleep(1.0)
        app.processEvents()
        c_pct = cur_proc.cpu_percent(interval=None)
        mem_info = cur_proc.memory_info()
        r_mb = mem_info.rss / (1024**2)
        samples_cpu.append(c_pct)
        samples_ram.append(r_mb)
        gpu_stat = _get_gpu_vitals()
        samples_gpu.append(gpu_stat["gpu_percent"])

    end_rss = cur_proc.memory_info().rss / (1024**2)
    rss_drift_mb = end_rss - start_rss
    drift_rate_kb_s = (rss_drift_mb * 1024) / 10.0

    avg_idle_cpu = sum(samples_cpu) / len(samples_cpu)
    max_idle_cpu = max(samples_cpu)
    avg_idle_ram = sum(samples_ram) / len(samples_ram)
    avg_idle_gpu = sum(samples_gpu) / len(samples_gpu) if samples_gpu else 0.0

    results["idle_metrics"] = {
        "duration_seconds": 10.0,
        "avg_process_cpu_pct": round(avg_idle_cpu, 2),
        "max_process_cpu_pct": round(max_idle_cpu, 2),
        "start_ram_rss_mb": round(start_rss, 2),
        "end_ram_rss_mb": round(end_rss, 2),
        "avg_ram_rss_mb": round(avg_idle_ram, 2),
        "rss_drift_10s_mb": round(rss_drift_mb, 2),
        "rss_drift_kb_per_sec": round(drift_rate_kb_s, 2),
        "gpu_vitals": _get_gpu_vitals(),
        "avg_gpu_percent": round(avg_idle_gpu, 2),
    }

    print(f"  -> Idle Process CPU Avg   : {avg_idle_cpu:5.2f}% (Peak: {max_idle_cpu:5.2f}%)")
    print(f"  -> Idle RAM Baseline      : {avg_idle_ram:5.2f} MB")
    print(f"  -> Idle RAM Drift (10s)   : {rss_drift_mb:+5.2f} MB ({drift_rate_kb_s:+5.2f} KB/s)")
    print(f"  -> Idle GPU Utilization   : {avg_idle_gpu:5.2f}%")

    # ── Phase 5: Telemetry Sampling Latency ──────────────────────────────────
    print("\n[Phase 5] Measuring Telemetry Sampling Latencies...")
    from core.system_monitor import WindowsSystemMonitorAdapter
    mon = WindowsSystemMonitorAdapter.instance()

    latencies_mon: list[float] = []
    for _ in range(50):
        t0 = time.perf_counter()
        _ = mon.get_snapshot()
        latencies_mon.append((time.perf_counter() - t0) * 1000.0)

    from gui.metrics import _metrics
    latencies_gui_metrics: list[float] = []
    for _ in range(50):
        t0 = time.perf_counter()
        _ = _metrics.snapshot()
        latencies_gui_metrics.append((time.perf_counter() - t0) * 1000.0)

    latencies_bridge: list[float] = []
    for _ in range(20):
        t0 = time.perf_counter()
        _ = bridge.get_system_telemetry()
        latencies_bridge.append((time.perf_counter() - t0) * 1000.0)

    results["telemetry_sampling"] = {
        "system_monitor_adapter_mean_ms": round(sum(latencies_mon) / len(latencies_mon), 3),
        "system_monitor_adapter_max_ms": round(max(latencies_mon), 3),
        "gui_metrics_mean_ms": round(sum(latencies_gui_metrics) / len(latencies_gui_metrics), 3),
        "gui_metrics_max_ms": round(max(latencies_gui_metrics), 3),
        "bridge_get_telemetry_mean_ms": round(sum(latencies_bridge) / len(latencies_bridge), 3),
        "bridge_get_telemetry_max_ms": round(max(latencies_bridge), 3),
    }
    print(f"  -> SystemMonitorAdapter Snapshot Latency : {results['telemetry_sampling']['system_monitor_adapter_mean_ms']} ms")
    print(f"  -> GuiMetrics Snapshot Latency           : {results['telemetry_sampling']['gui_metrics_mean_ms']} ms")
    print(f"  -> Bridge get_system_telemetry Latency   : {results['telemetry_sampling']['bridge_get_telemetry_mean_ms']} ms (Max: {results['telemetry_sampling']['bridge_get_telemetry_max_ms']} ms)")

    # ── Phase 6: Orchestration & MRE Task Latency ────────────────────────────
    print("\n[Phase 6] Measuring MRE & Tool Execution Latencies...")
    from core.orchestrator.mre import MREPipeline
    from core.orchestrator.tools import ToolRegistry

    reg = ToolRegistry.instance()
    mre = MREPipeline(tool_registry=reg)

    test_queries = [
        "check system memory usage",
        "show me host hardware telemetry",
        "calculate 500 * 20",
        "what processes are running",
    ]

    mre_latencies: list[float] = []
    for q in test_queries:
        t0 = time.perf_counter()
        decision = mre.classifier.classify(q)
        mre_latencies.append((time.perf_counter() - t0) * 1000.0)

    tool_latencies: list[float] = []
    for t_name, args in [("memory_status", {}), ("system_telemetry", {}), ("process_diagnostics", {"top_n": 5})]:
        t0 = time.perf_counter()
        _ = reg.execute(t_name, args, caller_agent="system_diagnosis")
        tool_latencies.append((time.perf_counter() - t0) * 1000.0)

    results["mre_orchestration"] = {
        "mre_plan_mean_ms": round(sum(mre_latencies) / len(mre_latencies), 2),
        "mre_plan_max_ms": round(max(mre_latencies), 2),
        "tool_exec_mean_ms": round(sum(tool_latencies) / len(tool_latencies), 2),
        "tool_exec_max_ms": round(max(tool_latencies), 2),
    }
    print(f"  -> MRE Classification & Planning Latency : {results['mre_orchestration']['mre_plan_mean_ms']} ms")
    print(f"  -> Core Tool Execution Latency           : {results['mre_orchestration']['tool_exec_mean_ms']} ms")

    # ── Phase 7: Active Workload Profiling (Section Switching & Telemetry Storm)
    print("\n[Phase 7] Measuring Performance Under Representative Workloads...")
    cc = ui._win._optimus_control_center

    cur_proc.cpu_percent(interval=None)
    t_workload_0 = time.perf_counter()

    switch_latencies: list[float] = []
    for iteration in range(3):
        for idx in range(len(section_classes)):
            s0 = time.perf_counter()
            cc._on_tab_selected(idx, section_classes[idx][0])
            app.processEvents()
            s1 = time.perf_counter()
            switch_latencies.append((s1 - s0) * 1000.0)

    workload_cpu = cur_proc.cpu_percent(interval=None)
    workload_ram = cur_proc.memory_info().rss / (1024**2)

    results["workload_metrics"] = {
        "total_tab_switches": len(switch_latencies),
        "mean_tab_switch_latency_ms": round(sum(switch_latencies) / len(switch_latencies), 2),
        "p95_tab_switch_latency_ms": round(sorted(switch_latencies)[int(len(switch_latencies) * 0.95)], 2),
        "max_tab_switch_latency_ms": round(max(switch_latencies), 2),
        "active_workload_cpu_pct": round(workload_cpu, 2),
        "active_workload_ram_mb": round(workload_ram, 2),
    }
    print(f"  -> Tab Switch Mean Latency : {results['workload_metrics']['mean_tab_switch_latency_ms']} ms")
    print(f"  -> Tab Switch P95 Latency  : {results['workload_metrics']['p95_tab_switch_latency_ms']} ms")
    print(f"  -> Tab Switch Max Spike    : {results['workload_metrics']['max_tab_switch_latency_ms']} ms")
    print(f"  -> Active Workload CPU     : {workload_cpu:5.2f}%")
    print(f"  -> Active Workload RAM     : {workload_ram:5.2f} MB")

    # Cleanup
    ui.close()
    app.quit()

    os.makedirs(os.path.join(_ROOT, "reports"), exist_ok=True)
    report_json_path = os.path.join(_ROOT, "reports", "baseline_runtime_performance.json")
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved structured JSON report to: {report_json_path}")
    print("=" * 70)
    print("BASELINE PROFILING COMPLETE")
    print("=" * 70)

    return results


if __name__ == "__main__":
    run_baseline_profile()
