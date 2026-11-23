# OPTIMUS Runtime Performance Baseline Report

**Date:** 2026-11-23  
**Auditor:** OPTIMUS Architecture & Performance Group  
**Host Environment:** Windows 11 on AMD64, Python 3.13.16 Virtual Environment (`D:\project\optimus\.venv`), 15.82 GB RAM, NVIDIA RTX GPU  
**Authoritative Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Git Baseline Commit:** `65a8a1d`  
**Structured Benchmark Data:** `reports/baseline_runtime_performance.json`  

---

## 1. Executive Summary

This report establishes the empirical, measured runtime performance baseline for the **OPTIMUS** desktop assistant application prior to architectural optimization.

Profiling was conducted directly on the host machine using automated non-invasive instrumentation measuring startup lifecycle, idle resource consumption, event-loop responsiveness, timer and thread overhead, telemetry sampling, and active interaction workload latencies.

### Critical Performance Baseline Findings
| Metric Category | Measured Baseline | Target | Status | Critical Bottleneck Indicator |
|---|---|---|---|---|
| **Idle Process CPU** | **46.91% avg** (Peak: **101.20%**) | < 5.0% | **SEVERE REGRESSION** | Continuous 60 FPS HudCanvas render loop + unthrottled background timers |
| **Idle RAM Drift** | **+22.58 MB / 10s** (+2.3 MB/s) | 0 KB/s steady | **SEVERE LEAK** | Continuous table/item reconstruction in hidden background views |
| **UI Event-Loop Spike** | **1,443.27 ms** (P95: 83.15 ms) | < 50.0 ms | **SEVERE FREEZE** | Synchronous WMI, disk I/O, and process scans during UI ticks |
| **Tab Switch Latency** | **172.69 ms avg** (Max: **2,041.07 ms**) | < 100.0 ms | **POOR RESPONSIVENESS** | Synchronous view refresh handlers rebuilding widgets on tab change |
| **Startup Duration** | **25,220.04 ms** (25.2 seconds) | < 3,000 ms | **UNACCEPTABLE DELAY** | Eager synchronous instantiation of all 20 views + app inventory scans |
| **Active QTimers** | **15 concurrent timers** | Minimum needed | **HIGH OVERHEAD** | Hidden views keep 16ms, 40ms, 1500ms, 2000ms timers running continuously |
| **Active Python Threads**| **6 threads** | Bounded & shared | **FRAGMENTED** | Duplicate telemetry loops (`gui/metrics.py` vs `core/system_monitor/`) |

---

## 2. Detailed Performance Measurement Inventory

### 2.1 Startup Duration & Per-Section Initialization
- **Total Application Startup Time:** `25,220.04 ms` (~25.2 seconds).
- **QApplication Initialization:** `48.21 ms`.
- **OptimusServiceBridge Initialization:** `12.14 ms`.
- **Eager Per-Section Instantiation Breakdown:**
  * `Section Applications`: **3,434.82 ms** (Synchronously loading full Windows application inventory and registry/UWP manifests).
  * `Section Integrations`: **3,910.11 ms** (Synchronously probing WMI namespaces, LibreHardwareMonitor, and MCP status).
  * `Section Dashboard`: **576.09 ms** (Constructing all child panels, SVG charts, and agent DAG nodes).
  * `Section System`: **498.39 ms** (Direct synchronous telemetry polling on construction).
  * `Section Developer`: **341.46 ms** (Scanning directory trees and git state).
  * `Section Vision`: **262.38 ms** (Synchronous visual state probe and initial table population).
  * `Section Skills`: **48.42 ms**.
  * `Section Personal`: **52.29 ms**.
  * `Section Proactive`: **30.93 ms**.
  * `Section SelfImprovement`: **28.41 ms**.
  * `Remaining 10 Sections`: ~150 ms total (~10–25 ms each).

### 2.2 Idle Resource Consumption & Memory Growth
- **Sampling Window:** 10 continuous seconds at zero user interaction.
- **Average CPU Utilization:** **46.91%** (Peak: **101.20%** — pinning an entire CPU logical core).
- **Initial RAM (RSS):** `446.05 MB`.
- **Final RAM (RSS):** `468.63 MB`.
- **RAM Drift in 10s:** **+22.58 MB** (**+2,312.00 KB/s**).
- **GPU Utilization:** `8.40%` (VRAM used: ~1.2 GB).

### 2.3 Qt Event-Loop Latency & Responsiveness
- **Target Timer Interval:** 10.0 ms (200 sample ticks).
- **Mean Event Loop Jitter:** **33.79 ms**.
- **P95 Event Loop Jitter:** **83.15 ms**.
- **Maximum Event Loop Latency Spike:** **1,443.27 ms** (UI frozen for ~1.44 seconds).

### 2.4 Active Concurrency & Timer Footprint
- **Active Threads (6):**
  1. `MainThread`: Main Qt GUI event loop.
  2. `Thread-1 (_loop)`: `gui/metrics.py` `_SysMetrics` polling psutil/NVML every 3.0s.
  3. `OptimusSystemMonitorWorker`: `core/system_monitor/adapter.py` polling psutil/NVML/WMI every 2.0s.
  4. `WorkspaceMCP_StdoutReader`: MCP process stdio reader.
  5. `WorkspaceMCP_StderrDrainer`: MCP process stderr drainer.
  6. `wake-state-warm`: Wake word detector background initialization thread.
- **Active QTimers (15):**
  1. `HudCanvas`: `16 ms` (60 FPS) — **Runs continuously even when Control Center is active and HUD is hidden!**
  2. `FileDropZone`: `40 ms` (25 FPS) — Drag-and-drop animation timer running continuously.
  3. `SectionVision`: `1500 ms` — Rebuilding all windows and tabs with `QPushButton` cell widgets every 1.5s in the background!
  4. `SectionIntegrations`: `2000 ms` (Timer 1) & `2000 ms` (Timer 2) — Re-querying telemetry and rebuilding gauges.
  5. `SectionSelfImprovement`: `5000 ms` — Polling optimizer outcomes.
  6. `SectionProactive`: `10000 ms` (Timer 1) & `10000 ms` (Timer 2).
  7. `MainWindow`: 4 separate timers (`2000 ms`, `1000 ms`, `1000 ms`, `2000 ms`).
  8. `LogWidget` / `ClipboardPanel` / `_CameraPreview`: 0ms single-shots / inactive.

### 2.5 Workload Interaction Latencies
- **Representative Workload:** Navigating across all 20 Control Center views across 3 full cycles.
- **Total Tab Switches:** 60.
- **Mean Tab Switch Latency:** **172.69 ms**.
- **P95 Tab Switch Latency:** **1,586.64 ms**.
- **Maximum Tab Switch Freeze:** **2,041.07 ms** (over 2 seconds!).
- **Active Workload CPU:** **78.70%**.
- **Active Workload RAM:** **454.77 MB**.

---

## 3. Root Cause Attribution Matrix

1. **Root Cause A: Continuous 60 FPS Hidden Rendering**
   - `HudCanvas` runs a 16ms QTimer driving face/reactor animations. When the user views the Control Center, `HudCanvas` is not visible, yet its QTimer continues calculating math and triggering `self.update()`, consuming ~25–35% CPU constantly.
2. **Root Cause B: Hidden Views Actively Polling and Rebuilding Tables**
   - `SectionVision`, `SectionIntegrations`, `SectionSelfImprovement`, and `SectionProactive` maintain active QTimers even while hidden in `QStackedWidget`.
   - `SectionVision._populate_windows_table()` allocates dozens of `QTableWidgetItem` instances and new `QPushButton("Focus")` widgets every 1,500ms, causing the observed **+2.3 MB/s memory leak**.
3. **Root Cause C: Eager Startup Instantiation of All 20 Views**
   - `OptimusControlCenterWidget.__init__` instantiates all 20 views synchronously. `SectionApplications` and `SectionIntegrations` block the main thread for over 7.3 seconds combined during startup.
4. **Root Cause D: Duplicate Hardware Telemetry Pollers**
   - Two completely independent threads (`gui/metrics.py` and `core/system_monitor/adapter.py`) poll psutil, NVML, and WMI every 2–3 seconds, causing lock contention and redundant CPU spikes.
5. **Root Cause E: Synchronous WMI and IPC on UI Ticks**
   - `service_bridge.py` queries `get_windows_runtime_client().discover_drives()`, `psutil.virtual_memory()`, and `psutil.Process().memory_info()` on every UI telemetry tick, causing 1.4-second event loop stalls.
