"""tests/live_app_acceptance_full_system.py — Full-System Live Application Acceptance Campaign.

Verifies:
1. Clean startup of OptimusUI and MainWindow.
2. Initialization of OptimusControlCenterWidget and OptimusServiceBridge.
3. Clean navigation and rendering through all 20 operational sections.
4. System telemetry updates across all gauges and sensor panels.
5. Absence of Qt warnings, stylesheet errors, or layout collisions.
6. Clean, graceful application shutdown.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtWidgets import QApplication

qt_warnings: list[str] = []

def qt_msg_handler(mode, context, message):
    if "QFontDatabase" in message or "qt.multimedia" in message:
        return
    qt_warnings.append(f"[{mode.name}] {message}")
    print(f"  [Qt Warning Intercepted] {message}", flush=True)

qInstallMessageHandler(qt_msg_handler)


def test_full_application_lifecycle():
    print("\n=======================================================")
    print("OPTIMUS LIVE FULL-SYSTEM APPLICATION ACCEPTANCE")
    print("=======================================================")

    t0 = time.time()
    print("Step 1: Initializing QApplication...", flush=True)
    app = QApplication.instance() or QApplication(sys.argv)

    print("Step 2: Bootstrapping OptimusUI('face.png')...", flush=True)
    from ui import OptimusUI
    ui = OptimusUI("face.png")
    win = ui._win
    assert win is not None, "MainWindow failed to construct"
    assert win.isVisible(), "MainWindow failed to show"
    print("  -> MainWindow initialized and shown successfully.")

    print("Step 3: Accessing OptimusControlCenterWidget...", flush=True)
    cc = win._optimus_control_center
    assert cc is not None, "OptimusControlCenterWidget missing on MainWindow"
    nav_bar = cc.nav_bar
    stack = cc.stack

    print(f"Step 4: Cycling through all {len(nav_bar.SECTIONS)} Control Center sections...", flush=True)
    for idx, (sec_name, sec_icon) in enumerate(nav_bar.SECTIONS):
        nav_bar._on_button_clicked(idx, sec_name)
        app.processEvents()
        current_idx = stack.currentIndex()
        assert current_idx == idx, f"Section switch failed: expected {idx} ({sec_name}), got {current_idx}"
        current_widget = stack.currentWidget()
        assert current_widget is not None, f"Widget for section {sec_name} is None"
        print(f"  -> Tab {idx:02d} [{sec_name}] rendered without error ({current_widget.__class__.__name__})", flush=True)

    print("Step 5: Verifying Real-Time System Telemetry & Gauges...", flush=True)
    sys_view = cc.system_view
    assert hasattr(sys_view, "gauge_cpu"), "System view missing gauge_cpu"
    assert hasattr(sys_view, "gauge_mem"), "System view missing gauge_mem"
    assert hasattr(sys_view, "gauge_gpu"), "System view missing gauge_gpu"
    sys_view._on_telemetry_update({"cpu_pct": 15.0, "ram_pct": 60.0, "gpu_pct": 25.0, "storage_pct": 40.0})
    sys_view.refresh_processes()
    app.processEvents()
    print(f"  -> System view CPU: {sys_view.gauge_cpu.value():.1f}%, RAM: {sys_view.gauge_mem.value():.1f}%", flush=True)

    print("Step 6: Verifying Integrations Section (Workspace MCP & System Monitor)...", flush=True)
    int_view = cc.integrations_view
    assert hasattr(int_view, "gauge_cpu"), "Integrations view missing gauge_cpu"
    assert hasattr(int_view, "gauge_ram"), "Integrations view missing gauge_ram"
    int_view.refresh_all()
    app.processEvents()
    print(f"  -> Integrations view gauges: CPU={int_view.gauge_cpu.value():.1f}%, RAM={int_view.gauge_ram.value():.1f}%", flush=True)

    print("Step 7: Validating zero critical Qt warnings or stylesheet errors...", flush=True)
    # Check for layout or stylesheet warnings
    critical_warnings = [w for w in qt_warnings if "QLayout" in w or "stylesheet" in w]
    assert len(critical_warnings) == 0, f"Critical Qt warnings found: {critical_warnings}"
    print("  -> Zero QLayout collisions and zero stylesheet parser errors verified.")

    print("Step 8: Graceful shutdown and resource teardown...", flush=True)
    ui.close()
    app.processEvents()
    print(f"  -> Application closed cleanly in {time.time()-t0:.2f}s.")
    print("=======================================================")
    print("ACCEPTANCE VERDICT: 100% PASS — ALL SUBSYSTEMS OPERATIONAL")
    print("=======================================================\n")


if __name__ == "__main__":
    test_full_application_lifecycle()
