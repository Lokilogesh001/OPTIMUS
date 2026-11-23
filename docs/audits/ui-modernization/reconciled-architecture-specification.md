# OPTIMUS UI 2.0 — Reconciled Architecture & Engineering Specification

**Date:** 2026-11-23  
**Status:** **APPROVED & RECONCILED**  
**Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Execution Environment:** Python 3.13.16 · PyQt6 6.12.1 · Qt 6.12.0 LTS (Direct3D 11 RHI)  
**Reference Reviews:**  
- Elephant Architecture Investigation: `docs/audits/ui-modernization/elephant-investigation.md`
- Goldfish Architecture Review: `docs/audits/ui-modernization/goldfish-architecture-review.md`

---

## 1. Reconciliation Matrix for Goldfish Mandates

| Mandate | Core Requirement | Reconciled Implementation Plan |
|---|---|---|
| **M-1** | Pure QtWidgets & Option A | Retain PyQt6 6.12.1 exclusively. Prohibit PySide6 and QQuickWidget inside the 20 views. Keep all 20 views as pure QtWidgets. |
| **M-2** | Native D3D11 RHI, No OpenGL Flags | Remove `AA_UseDesktopOpenGL` and `QT_OPENGL=desktop`. Retain default Direct3D 11 RHI on Windows (`QSG_RHI_BACKEND=d3d11`). Use `QOpenGLWidget` viewport selectively only for `HudCanvas`. |
| **M-3** | Paint Attribute Safety | Restrict `WA_OpaquePaintEvent` strictly to 100% opaque, non-rounded rectangular root containers. Prohibit it on widgets with rounded borders, alpha translucency, or chamfered cuts. |
| **M-4** | High-Performance Stream Console | Implement `StreamConsoleWidget` using `QPlainTextEdit` (or optimized direct `QTextCursor` text block manipulation), double-buffered FIFO queues, 30 Hz (33.3 ms) adaptive flush throttling, and edit-block wrapping (`beginEditBlock()`/`endEditBlock()`). |
| **M-5** | Universal Stream Console Deployment | Deploy `StreamConsoleWidget` across `SectionCommandCenter.chat_history`, `SectionCommandCenter.out_display`, `OptimusDashboardView.chat_transcript`, and `MainWindow._log` (`LogWidget`). |
| **M-6** | Backward-Compatible Token System | Establish `OptimusTheme` as a superset of `gui.theme.C`, preserving 100% attribute backward compatibility. Refactor `HoloPanel` to inherit/wrap `OptimusCard` so all 20 views inherit new styling without regressions. |
| **M-7** | Accessible Reduced Motion | Detect Windows 11 animation setting via `SPI_GETCLIENTAREAANIMATION` on startup. Expose `reduced_motion_changed` signal on `OptimusServiceBridge`. Halt HUD rotation, typewriter timers, and dash animations when enabled. |
| **M-8** | Main-Thread Blocking Elimination | Offload `psutil.process_iter` in `SectionSystem` to a background worker in `OptimusServiceBridge`. Eliminate `setStyleSheet` calls inside recurring update loops (such as `TelemetryMetricBar.set_value`). |

---

## 2. Detailed Technical Design & Implementation Units

### 2.1 Direct3D 11 Hardware Graphics Pipeline (`ui.py`, `gui/window.py`)
In `ui.py::_apply_ui_scale()` and `OptimusUI.__init__()`:
- Ensure native Direct3D 11 RHI is respected on Windows. Do **not** inject `AA_UseDesktopOpenGL`.
- Enable high-DPI pixmap scaling: `QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)`.
- In `gui/window.py`, apply `QOpenGLWidget` as the viewport for `_center_split` or `HudCanvas` if hardware OpenGL/Direct3D rasterization is requested, while preserving standard raster backing for QtWidgets.

### 2.2 High-Performance Stream Console Engine (`gui/widgets.py`)
Implement `StreamConsoleWidget`:
```python
class StreamConsoleWidget(QPlainTextEdit):
    """Hardware-accelerated, stutter-free streaming console widget.
    
    Uses double-buffered FIFO token queuing with adaptive 30 Hz throttling,
    programmatic QTextCursor formatting (zero HTML parsing overhead),
    and beginEditBlock/endEditBlock batching.
    """
```
Key architectural features:
1. **Double-Buffered FIFO Queue:** New chunks appended via `append_chunk(speaker, text)` or `append_line(speaker, text)` are pushed to an in-memory `collections.deque`.
2. **Adaptive 30 Hz Throttle:** A dedicated `QTimer` configured with a 33 ms interval (30 Hz) handles buffer flushing during active token streaming.
3. **Edit-Block Batching:**
   ```python
   cursor = self.textCursor()
   cursor.movePosition(QTextCursor.MoveOperation.End)
   cursor.beginEditBlock()
   try:
       for speaker, text, fmt in batch:
           cursor.insertText(f"[{speaker}] ", header_fmt)
           cursor.insertText(text, body_fmt)
   finally:
       cursor.endEditBlock()
   self.setTextCursor(cursor)
   self.ensureCursorVisible()
   ```
4. **Instant Flush on Stream Completion:** A `flush_now()` method flushes all remaining tokens immediately when a generation finishes.

### 2.3 Universal Deployment Across Streaming Surfaces
1. **`gui/optimus/section_command.py`:**
   Replace `self.chat_history = QTextEdit()` and `self.out_display = QTextEdit()` with `StreamConsoleWidget`.
2. **`gui/optimus/dashboard.py`:**
   Replace `self.chat_transcript = QTextEdit()` with `StreamConsoleWidget`.
3. **`gui/widgets.py::LogWidget`:**
   Refactor `LogWidget` to inherit from `StreamConsoleWidget`, maintaining complete API compatibility for `append_log(text)` while eliminating cursor thrashing.

### 2.4 Design Token Modernization & Backward Compatibility (`gui/theme.py`, `gui/style.py`)
1. Extend `gui/theme.py::C` with `OptimusTheme`:
   - `BG_VOID`: `#04070d`
   - `BG_BASE`: `#070b14`
   - `BG_PANEL`: `#0c1322`
   - `BG_ELEVATED`: `#111a2e`
   - `ACCENT_PRI`: `#0084ff`
   - `ACCENT_CYAN`: `#38bdf8`
   - `TEXT_PRIMARY`: `#f8fafc`
   - `TEXT_SECONDARY`: `#94a3b8`
   - `BORDER_HAIRLINE`: `#1e293b`
   - `BORDER_ACTIVE`: `#38bdf8`
2. Maintain all legacy attributes on `C` (`C.BG`, `C.PANEL`, `C.PANEL2`, `C.BORDER`, `C.PRI`, `C.WHITE`, `C.GREEN`, `C.RED`, `C.TEXT`, `C.TEXT_DIM`, `C.TEXT_MED`, etc.).
3. Update `HoloPanel` (`gui/widgets.py`):
   Wrap modern styling (`OptimusCard`), clean borders, and crisp headers so all 20 Control Center views instantly inherit the modernized aesthetic without requiring rewrites.

### 2.5 Accessible Reduced Motion System (`gui/theme.py`, `gui/optimus/service_bridge.py`, `gui/hud.py`)
1. Windows API query for OS animation preference:
   ```python
   def get_system_reduced_motion() -> bool:
       if sys.platform == "win32":
           try:
               import ctypes
               anim = ctypes.c_bool()
               # SPI_GETCLIENTAREAANIMATION = 0x1042
               if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(anim), 0):
                   return not anim.value
           except Exception:
               pass
       return False
   ```
2. Bridge Signal: `reduced_motion_changed = pyqtSignal(bool)` on `OptimusServiceBridge`.
3. State Reaction:
   - When `reduced_motion` is enabled:
     - `HudCanvas._tmr.stop()`: Halts reactor rotation, keeps canvas rendered at current angle.
     - `StreamConsoleWidget`: Disables simulated typing delay, flushes instantly.
     - `FileDropZone._anim_tmr.stop()`: Halts dashed border marching animation.

### 2.6 Background Thread Offloading for System Telemetry (`gui/optimus/service_bridge.py`, `section_system.py`, `dashboard.py`)
1. In `OptimusServiceBridge`:
   - Introduce background thread executor for process list enumeration: `_poll_process_list_background()`.
   - Emit `processes_snapshot_ready = pyqtSignal(list)` to deliver the top processes asynchronously to `SectionSystem` without touching the GUI thread.
2. In `OptimusDashboardView::TelemetryMetricBar`:
   - Eliminate `setStyleSheet` calls inside `set_value()`.
   - Update `self.bar.setValue()` and text labels only, avoiding layout and style cascade invalidation.
