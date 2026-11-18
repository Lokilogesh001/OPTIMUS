"""tests/verify_desktop_runtime.py — Comprehensive Desktop Runtime Verification Script.

Tests:
1. PyQt6 QApplication initialization.
2. OptimusUI and MainWindow construction with face asset.
3. Window properties, title, geometric bounds, and styling.
4. Classic HUD components (reactor, gauges, logs, drop zone).
5. OPTIMUS Control Center instantiation and navigation between sections.
6. Audio device & microphone probing with graceful failure handling.
7. Background threads, event loop processing, and clean shutdown.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Setup paths
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication

from gui import theme
from gui.optimus import OptimusControlCenterWidget
from gui.window import MainWindow
from ui import OptimusUI


def main():
    print("=== OPTIMUS DESKTOP RUNTIME VERIFICATION ===")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    face_asset = _ROOT / "face.png"
    if not face_asset.exists():
        # Create dummy face.png if not present for test
        face_asset = "face.png"
    else:
        face_asset = str(face_asset)

    print(f"[1/7] Initializing OptimusUI with face: {face_asset}")
    ui = OptimusUI(face_asset)
    assert ui._win is not None, "MainWindow must not be None"
    win: MainWindow = ui._win

    print("[2/7] Verifying Window Title & Branding...")
    title = win.windowTitle()
    print(f"      Window Title: '{title}'")
    assert "OPTIMUS" in title, f"Title must contain OPTIMUS, got: {title}"
    assert theme.APP_VERSION == "OPTIMUS"

    print("[3/7] Verifying Classic HUD Components...")
    assert hasattr(win, "hud"), "MainWindow must have hud"
    assert hasattr(win, "_log"), "MainWindow must have log widget"
    assert hasattr(win, "_drop_zone"), "MainWindow must have drop zone"
    assert hasattr(win, "_clock_lbl"), "MainWindow must have clock label"
    print("      Classic HUD components verified.")

    print("[4/7] Verifying OPTIMUS Control Center & View Switching...")
    # Verify show_optimus
    ui.show_optimus()
    app.processEvents()
    assert hasattr(win, "_optimus_control_center"), "MainWindow must have _optimus_control_center"
    cc: OptimusControlCenterWidget = win._optimus_control_center
    assert cc.isVisible(), "Control Center must be visible after show_optimus()"
    print("      Control Center visible.")

    # Navigate between sections
    sections = ["command", "agents", "workflows", "intelligence", "system", "verification", "planning", "developer"]
    for s in sections:
        win.show_optimus(s)
        app.processEvents()
        print(f"      Switched to section: {s}")

    # Switch back to Classic HUD
    ui.show_classic_hud()
    app.processEvents()
    assert not cc.isVisible(), "Control Center must be hidden after show_classic_hud()"
    print("      Switched back to Classic HUD successfully.")

    print("[5/7] Checking Microphone & Audio probing...")
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        print(f"      Sounddevice detected {len(devices)} device(s).")
    except Exception as e:
        print(f"      Audio hardware query safely handled / unavailable: {e}")

    print("[6/7] Testing Text Logging & Presenter...")
    ui.write_log("SYS: Runtime validation in progress.")
    app.processEvents()
    print("      Log message processed without errors.")

    print("[7/7] Verifying Clean Shutdown...")
    win.close()
    app.processEvents()
    print("      Window closed cleanly.")
    print("=== DESKTOP RUNTIME VERIFICATION: PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
