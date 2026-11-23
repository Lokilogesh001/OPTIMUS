# OPTIMUS Reconciled Performance Architecture Specification

**Date:** 2026-11-23  
**Auditor:** OPTIMUS Architecture & Performance Group  
**Status:** **APPROVED & LOCKED FOR IMPLEMENTATION**  
**Preceding Artifacts:**
- Baseline Performance Report: `docs/audits/performance-optimization/baseline-report.md`
- Elephant Investigation: `docs/audits/performance-optimization/elephant-investigation.md`
- Independent Goldfish Review: `docs/audits/performance-optimization/goldfish-architecture-review.md`  

---

## 1. Executive Summary & Binding Directives

This specification reconciles the findings of the Elephant Performance Investigation with the 7 binding architectural mandates issued in the Independent Goldfish Review.

All performance remediations are locked to address the four critical baseline regressions:
1. **Application Startup Time:** Reduce from **25.2 seconds to < 1.0 second** via Lazy Tab Loading.
2. **Idle Process CPU:** Reduce from **46.91% to < 5.0%** via Animation & Timer Lifecycle Gating.
3. **Idle Memory Growth:** Eliminate the **+2.3 MB/s memory leak** via Persistent In-Place Table Controllers.
4. **Qt Event-Loop Stalls:** Reduce maximum jitter from **1,443 ms to < 50 ms** via Main-Thread Decoupling and Unified Non-Blocking Telemetry.

---

## 2. File-by-File Implementation Plan

### 2.1 `gui/hud.py` & `gui/window.py` — Animation Lifecycle & State Gating
- **File:** `gui/hud.py`
  - Implement `showEvent(event)`:
    * Re-anchor wall clock: `self._step_t = time.time()`.
    * Flush expired audio visemes (`self._visemes = None; self._vis_i = None`) to prevent mouth jumps.
    * Start timer: `self._tmr.start(16)`.
    * Request repaint: `self.update()`.
  - Implement `hideEvent(event)`:
    * Stop timer: `self._tmr.stop()`.
- **File:** `gui/window.py`
  - Implement `changeEvent(event)` on `MainWindow`:
    * Intercept `QEvent.Type.WindowStateChange`.
    * If `self.isMinimized()`: stop `self.hud._tmr`.
    * When restored from minimized: restart `self.hud._tmr` ONLY if `self._body_stack.currentIndex() == 0` (Classic HUD active).
  - In `_toggle_body_view()`:
    * When switching to Control Center (`index == 1`): stop `self.hud._tmr`.
    * When switching to Classic HUD (`index == 0`): restart `self.hud._tmr`.

---

### 2.2 `gui/optimus/control_center.py` — Lazy Tab Instantiation & Lifecycle Cascading
- **File:** `gui/optimus/control_center.py`
  - In `__init__`:
    * Pre-populate `self.stack = QStackedWidget(self.center_container)`.
    * Pre-insert 20 lightweight placeholder widgets (`QWidget()`) into `self.stack` at indices 0 through 19, matching `OptimusNavBar.SECTIONS` strictly.
    * Initialize `self._section_instances: dict[int, QWidget | None] = {i: None for i in range(20)}`.
    * Instantiate ONLY index 0 (`OptimusDashboardView`) immediately on startup.
  - Implement `_get_or_create_section(self, index: int) -> QWidget`:
    * If `self._section_instances[index]` is None:
      - Look up factory and class from static registry `_SECTION_REGISTRY`.
      - Instantiate view: `view = factory(self.bridge, parent=self.stack)`.
      - Swap placeholder in-place:
        ```python
        old_w = self.stack.widget(index)
        self.stack.removeWidget(old_w)
        self.stack.insertWidget(index, view)
        old_w.deleteLater()
        self._section_instances[index] = view
        ```
    * Return `self._section_instances[index]`.
  - Property Proxies for Backwards Compatibility:
    * Implement `@property` getters for all 20 views (`dashboard_view`, `command_view`, `workflows_view`, `agents_view`, `intelligence_view`, `memory_view`, `verification_view`, `approvals_view`, `system_view`, `settings_view`, `planning_view`, `developer_view`, `skills_view`, `missions_view`, `proactive_view`, `personal_view`, `self_improvement_view`, `vision_view`, `applications_view`, `integrations_view`).
    * Each getter invokes `self._get_or_create_section(INDEX)` transparently.
  - In `_on_tab_selected(self, index: int, title: str)`:
    * Call `view = self._get_or_create_section(index)` FIRST.
    * Set current index: `self.stack.setCurrentIndex(index)`.
    * Dispatch view refresh routines.
  - Implement `hideEvent` and `showEvent` on `OptimusControlCenterWidget`:
    * `hideEvent`: Forward hide notification to `self.stack.currentWidget()` so inactive background section timers pause immediately when user returns to Classic HUD.
    * `showEvent`: Forward show notification to `self.stack.currentWidget()`.

---

### 2.3 `gui/metrics.py` — Unified Telemetry Facade
- **File:** `gui/metrics.py`
  - Terminate and delete the background polling thread `_loop` from `_SysMetrics`.
  - Wire `_SysMetrics` to `WindowsSystemMonitorAdapter.instance()`:
    ```python
    class _SysMetrics:
        def __init__(self):
            self._adapter = WindowsSystemMonitorAdapter.instance()
            self._lock = threading.Lock()

        def snapshot(self) -> dict[str, float]:
            snap = self._adapter.get_snapshot()
            net_mb_s = (snap.network.bytes_sent_per_sec + snap.network.bytes_recv_per_sec) / (1024 * 1024)
            gpu_val = snap.gpu.gpu_percent if snap.gpu.has_gpu else -1.0
            tmp_val = snap.gpu.temperature_c if snap.gpu.has_gpu else -1.0
            return {
                "cpu": snap.cpu.total_percent,
                "mem": snap.memory.percent_used,
                "net": round(net_mb_s, 2),
                "gpu": round(gpu_val, 1),
                "tmp": round(tmp_val, 1),
            }

        @property
        def cpu(self) -> float: return self.snapshot()["cpu"]
        @property
        def mem(self) -> float: return self.snapshot()["mem"]
        @property
        def net(self) -> float: return self.snapshot()["net"]
        @property
        def gpu(self) -> float: return self.snapshot()["gpu"]
        @property
        def tmp(self) -> float: return self.snapshot()["tmp"]
    ```
  - Preserves exact contract for `_metrics.snapshot()` and attributes `_metrics.cpu`, `_metrics.mem`, etc.
  - Zero duplicate OS threads, zero duplicate NVML/psutil polling.

---

### 2.4 `gui/optimus/service_bridge.py` — Main-Thread Decoupling
- **File:** `gui/optimus/service_bridge.py`
  - In `get_system_telemetry()`:
    * Remove non-existent `discover_drives()` call.
    * Cache `wr_client.is_service_available()` asynchronously with a 30s TTL, eliminating the 500ms `WaitNamedPipe` stall on the main Qt thread.
    * In `get_top_processes()`: Read `WindowsSystemMonitorAdapter.instance().get_snapshot().top_processes` instead of running synchronous `psutil.process_iter()`.
    * Remove redundant `psutil.virtual_memory()` and `psutil.Process().memory_info()` syscalls on every UI tick; read directly from cached `SystemSnapshot`.

---

### 2.5 `gui/optimus/section_vision.py` — Memory Leak Elimination
- **File:** `gui/optimus/section_vision.py`
  - Implement Lifecycle Methods:
    * `pause_polling()`: `self.timer.stop()`.
    * `resume_polling()`: `self.timer.start(1500); self.refresh_vision()`.
    * `showEvent(event)`: `super().showEvent(event); self.resume_polling()`.
    * `hideEvent(event)`: `super().hideEvent(event); self.pause_polling()`.
  - In `_populate_windows_table(self, windows)`:
    * Add diff signature check: `sig = (len(windows), tuple((w.hwnd, w.is_active, w.title) for w in windows[:15]))`.
    * If `sig == self._last_windows_sig`: skip update entirely!
    * In-place row management:
      - If `len(windows) < rowCount()`: call `removeRow()` down to target.
      - If `len(windows) > rowCount()`: call `insertRow()` up to target.
      - Update existing `QTableWidgetItem` instances via `item(r, c).setText()`.
      - Allocate `QPushButton("Focus")` once per row; store target HWND via `btn.setProperty("target_hwnd", w.hwnd)`.
  - In `_populate_tabs_table(self, tabs)`:
    * Apply identical in-place item reuse and diff guard.

---

### 2.6 `gui/optimus/section_integrations.py` — Async Probing & Lifecycle Gating
- **File:** `gui/optimus/section_integrations.py`
  - In `__init__`:
    * Do NOT invoke `self.workspace_adapter.get_gmail_status()` synchronously before layout.
  - Implement Lifecycle Methods:
    * `pause_polling()`: `self.ui_timer.stop()`.
    * `resume_polling()`: `self.ui_timer.start(2000); self.update_telemetry_ui()`.
    * `showEvent(event)`: `super().showEvent(event); self.resume_polling()`.
    * `hideEvent(event)`: `super().hideEvent(event); self.pause_polling()`.

---

### 2.7 `gui/optimus/section_self_improvement.py` & `section_proactive.py`
- Add `showEvent` and `hideEvent` lifecycle hooks to pause their internal QTimers (`5000ms`, `10000ms`) when hidden, resuming only when visible.

---

### 2.8 `core/vision/service.py` — Browser Tab Walk Caching
- **File:** `core/vision/service.py`
  - In `enumerate_browser_tabs()`:
    * Add a 10.0-second TTL cache for UI Automation browser tab enumerations.
    * When `get_desktop_state()` is invoked, reuse cached tab metadata unless `force_scan=True` is explicitly specified.

---

## 3. Verification Criteria & Acceptance Targets

| Subsystem | Metric | Baseline | Target |
|---|---|---|---|
| **Startup** | Total Startup Duration | 25,220 ms | **< 1,200 ms** |
| **Idle Host** | Process CPU Average | 46.91% | **< 5.0%** |
| **Idle Memory** | Process RAM Drift / 10s | +22.58 MB (+2,312 KB/s) | **< 0.5 MB (~0 KB/s steady)** |
| **Event Loop** | Maximum Latency Spike | 1,443 ms | **< 50 ms** |
| **Navigation** | P95 Tab Switch Latency | 1,586 ms | **< 100 ms** |
| **Concurrency** | Redundant Polling Threads | 2 threads | **1 unified thread** |
| **Timers** | Active Timers at Idle | 15 continuous | **Gated to active view** |
