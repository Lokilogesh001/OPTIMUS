"""tests/live_app_acceptance_m20.py — Real Running OPTIMUS Application Acceptance Test.

Executes all 14 mandatory steps required by Phase 4 of M20:
1. Launch OPTIMUS (OptimusUI + MainWindow).
2. Open Plugins interface (PluginManagerOverlay).
3. Confirm acceptance_test_plugin appears.
4. Open Plugin Settings (PluginSettingsOverlay).
5. Change and save a setting (test_threshold=250).
6. Invoke the plugin through the actual AI conversation/tool-dispatch path (_execute_tool).
7. Verify returned result.
8. Disable the plugin through the UI.
9. Attempt invocation again and verify rejection.
10. Re-enable it through UI and verify execution.
11. Restart OPTIMUS.
12. Confirm configuration and enabled state persist across restarts.
13. Trigger controlled failure and verify secret redaction.
14. Confirm OPTIMUS remains operational.
Captures screenshots and saves to docs/egm/m20-plugins/screenshots/.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Ensure repository root is on path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from google.genai import types
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton

from core import outcome
from main import OptimusLive
from memory.config_manager import (
    get_plugin_config,
    get_plugin_enabled,
    save_plugin_config,
    save_plugin_enabled,
)
from ui import OptimusUI

SHOTS_DIR = _ROOT / "docs" / "egm" / "m20-plugins" / "screenshots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)


def run_live_application_audit():
    print("=" * 70)
    print("🚀 OPTIMUS M20: GENUINE LIVE APPLICATION ACCEPTANCE TEST CAMPAIGN")
    print("=" * 70)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    # Clean baseline state
    save_plugin_enabled("acceptance_test_plugin", True)
    save_plugin_config("acceptance_test_plugin", {"test_threshold": 100, "test_api_key": ""})

    # ── STEP 1: Launch OPTIMUS ──────────────────────────────────────────────
    print("\n[Step 1/14] Launching actual OPTIMUS Desktop Application...")
    ui = OptimusUI("face.png")
    win = ui._win
    assert win is not None, "MainWindow must be initialized"
    win.show()
    app.processEvents()
    time.sleep(0.5)

    # Instantiate OptimusLive live assistant runner
    optimus = OptimusLive(ui)
    assert optimus._plugin_registry.has("acceptance_test_plugin"), "acceptance_test_plugin must be discovered"
    
    shot_01 = SHOTS_DIR / "01_optimus_live_main_window.png"
    win.grab().save(str(shot_01))
    print(f"  ✓ OPTIMUS MainWindow running. Screenshot saved: {shot_01.name}")

    # ── STEP 2: Open Plugins interface ──────────────────────────────────────
    print("\n[Step 2/14] Opening Plugin Manager via GUI...")
    win._open_plugin_manager()
    app.processEvents()
    time.sleep(0.3)
    pm_overlay = win._plugin_manager_overlay
    assert pm_overlay is not None, "PluginManagerOverlay must be created"
    assert pm_overlay.isVisible(), "PluginManagerOverlay must be visible"

    shot_02 = SHOTS_DIR / "02_plugin_manager_overlay.png"
    win.grab().save(str(shot_02))
    print(f"  ✓ PluginManagerOverlay opened. Screenshot saved: {shot_02.name}")

    # ── STEP 3: Confirm plugin appears ──────────────────────────────────────
    print("\n[Step 3/14] Confirming acceptance_test_plugin appears in roster...")
    plugins_roster = ui.get_plugins() if ui.get_plugins else []
    plugin_entry = next((p for p in plugins_roster if p["name"] == "acceptance_test_plugin"), None)
    assert plugin_entry is not None, "acceptance_test_plugin must appear in roster"
    assert plugin_entry["valid"] is True, "Plugin must be valid"
    assert plugin_entry["enabled"] is True, "Plugin must be enabled initially"
    print(f"  ✓ Plugin '{plugin_entry['name']}' found. Valid: {plugin_entry['valid']}, Tier: {plugin_entry['tier']}")

    # Close PM overlay
    pm_overlay.hide()
    app.processEvents()

    # ── STEP 4: Open Plugin Settings ────────────────────────────────────────
    print("\n[Step 4/14] Opening Plugin Settings overlay via GUI...")
    win._open_plugin_settings()
    app.processEvents()
    time.sleep(0.3)
    ps_overlay = win._plugin_settings_overlay
    assert ps_overlay is not None, "PluginSettingsOverlay must be created"
    assert ps_overlay.isVisible(), "PluginSettingsOverlay must be visible"

    shot_03 = SHOTS_DIR / "03_plugin_settings_overlay.png"
    win.grab().save(str(shot_03))
    print(f"  ✓ PluginSettingsOverlay opened. Screenshot saved: {shot_03.name}")

    # ── STEP 5: Change and save a setting ───────────────────────────────────
    print("\n[Step 5/14] Modifying and saving settings via GUI forms...")
    # Locate threshold input widget
    widget_key = ("acceptance_test_plugin", "test_threshold")
    threshold_input = ps_overlay._widgets.get(widget_key)
    assert isinstance(threshold_input, QLineEdit), "threshold widget must be QLineEdit"
    threshold_input.setText("250")
    app.processEvents()

    # Click SAVE
    ps_overlay._save_all()
    app.processEvents()
    time.sleep(0.2)

    persisted_cfg = get_plugin_config("acceptance_test_plugin")
    assert str(persisted_cfg.get("test_threshold")) == "250", "Saved setting must match input"
    print(f"  ✓ Settings saved via UI. Persisted in config: {persisted_cfg}")

    # Trigger test connection callback
    print("  Testing connection callback in settings...")
    ps_overlay._run_action("acceptance_test_plugin")
    start = time.time()
    while time.time() - start < 1.0:
        app.processEvents()
        time.sleep(0.05)
    
    shot_04 = SHOTS_DIR / "04_plugin_settings_saved_tested.png"
    win.grab().save(str(shot_04))
    print(f"  ✓ Action callback executed. Screenshot saved: {shot_04.name}")

    ps_overlay.hide()
    app.processEvents()

    # ── STEP 6 & 7: Invoke plugin via AI tool-dispatch path ─────────────────
    print("\n[Step 6 & 7/14] Invoking plugin through live OptimusLive._execute_tool dispatch...")
    fc = types.FunctionCall(
        id="call_test_01",
        name="acceptance_test_plugin",
        args={"action": "calculate", "a": 24, "b": 18, "operator": "+"}
    )
    # Execute through actual async dispatch pipeline
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    response = loop.run_until_complete(optimus._execute_tool(fc))
    
    assert response is not None, "Response must not be None"
    assert response.name == "acceptance_test_plugin"
    model_res = response.response["result"]
    print(f"  Raw model response: {model_res[:60]}...")
    assert "<<<UNTRUSTED" in model_res, "Plugin return value must be untrusted-demarcated for model"
    unwrapped = outcome.unwrap(model_res)
    assert "Calculation result: 42.00" in unwrapped
    print(f"  ✓ Tool executed deterministically: {unwrapped}")

    # ── STEP 8: Disable plugin through UI ───────────────────────────────────
    print("\n[Step 8/14] Disabling plugin through UI toggle button...")
    win._open_plugin_manager()
    app.processEvents()
    time.sleep(0.2)
    pm_overlay = win._plugin_manager_overlay
    
    # Click toggle button
    toggle_btn = None
    for child in pm_overlay.findChildren(QPushButton):
        if child.text() == "ON":
            toggle_btn = child
            break
    assert toggle_btn is not None, "Toggle button in ON state must exist"
    toggle_btn.click()
    app.processEvents()
    time.sleep(0.2)

    assert toggle_btn.text() == "OFF", "Toggle button state must transition to OFF"
    assert get_plugin_enabled("acceptance_test_plugin") is False, "Config state must be disabled"
    
    shot_05 = SHOTS_DIR / "05_plugin_disabled_ui.png"
    win.grab().save(str(shot_05))
    print(f"  ✓ Plugin disabled via UI. Screenshot saved: {shot_05.name}")

    # ── STEP 9: Attempt invocation while disabled ───────────────────────────
    print("\n[Step 9/14] Invoking disabled plugin through AI dispatch pipeline...")
    fc_disabled = types.FunctionCall(
        id="call_test_02",
        name="acceptance_test_plugin",
        args={"action": "health"}
    )
    response_disabled = loop.run_until_complete(optimus._execute_tool(fc_disabled))
    model_res_dis = outcome.unwrap(response_disabled.response["result"])
    print(f"  Disabled invocation result: {model_res_dis}")
    assert "currently disabled" in model_res_dis
    print("  ✓ Disabled plugin invocation correctly rejected!")

    # ── STEP 10: Re-enable through UI and verify execution ──────────────────
    print("\n[Step 10/14] Re-enabling plugin through UI toggle button...")
    toggle_btn.click()
    app.processEvents()
    time.sleep(0.2)
    assert toggle_btn.text() == "ON"
    assert get_plugin_enabled("acceptance_test_plugin") is True

    shot_06 = SHOTS_DIR / "06_plugin_re_enabled_ui.png"
    win.grab().save(str(shot_06))
    print(f"  ✓ Plugin re-enabled. Screenshot saved: {shot_06.name}")

    response_reenabled = loop.run_until_complete(optimus._execute_tool(fc_disabled))
    model_res_re = outcome.unwrap(response_reenabled.response["result"])
    assert "Acceptance Test Plugin is healthy" in model_res_re
    print(f"  ✓ Execution succeeded after re-enabling: {model_res_re}")

    pm_overlay.hide()
    app.processEvents()

    # ── STEP 11 & 12: Restart OPTIMUS & verify persistence ──────────────────
    print("\n[Step 11 & 12/14] Restarting OPTIMUS and verifying state persistence...")
    ui.close()
    app.processEvents()
    time.sleep(0.5)

    # Launch new instance
    ui_new = OptimusUI("face.png")
    win_new = ui_new._win
    win_new.show()
    app.processEvents()
    time.sleep(0.3)

    optimus_new = OptimusLive(ui_new)
    
    # Verify settings persisted
    reloaded_cfg = get_plugin_config("acceptance_test_plugin")
    assert str(reloaded_cfg.get("test_threshold")) == "250", "Setting must persist across restart"
    assert get_plugin_enabled("acceptance_test_plugin") is True, "Enablement must persist across restart"
    print(f"  ✓ State successfully persisted across restart: threshold={reloaded_cfg.get('test_threshold')}")

    shot_07 = SHOTS_DIR / "07_optimus_restarted_persistent_state.png"
    win_new.grab().save(str(shot_07))
    print(f"  ✓ Restart screenshot saved: {shot_07.name}")

    # ── STEP 13: Controlled failure & secret redaction ──────────────────────
    print("\n[Step 13/14] Triggering controlled failure with credential leak...")
    fc_err = types.FunctionCall(
        id="call_test_03",
        name="acceptance_test_plugin",
        args={"action": "trigger_error"}
    )
    response_err = loop.run_until_complete(optimus_new._execute_tool(fc_err))
    err_res = outcome.unwrap(response_err.response["result"])
    print(f"  Sanitized error output: {err_res}")
    assert "[REDACTED_API_KEY]" in err_res, "API key must be redacted in error string"
    assert "sk-live-1234567890abcdef1234567890" not in err_res, "Raw secret must never appear in response"
    print("  ✓ Secret properly scrubbed from failure output.")

    # ── STEP 14: Confirm OPTIMUS remains operational ────────────────────────
    print("\n[Step 14/14] Confirming OPTIMUS remains fully operational after crash...")
    fc_after_crash = types.FunctionCall(
        id="call_test_04",
        name="acceptance_test_plugin",
        args={"action": "calculate", "a": 10, "b": 32, "operator": "+"}
    )
    response_after = loop.run_until_complete(optimus_new._execute_tool(fc_after_crash))
    res_after = outcome.unwrap(response_after.response["result"])
    assert "Calculation result: 42.00" in res_after
    
    # Confirm UI window is active and responsive
    assert win_new.isVisible()
    shot_08 = SHOTS_DIR / "08_optimus_healthy_after_failure.png"
    win_new.grab().save(str(shot_08))
    print(f"  ✓ Assistant is healthy and responsive. Screenshot saved: {shot_08.name}")

    # Clean shutdown
    ui_new.close()
    app.processEvents()
    loop.close()

    print("\n" + "=" * 70)
    print("🎉 ALL 14 GENUINE LIVE APPLICATION TESTS COMPLETED WITH 100% SUCCESS!")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_live_application_audit())
