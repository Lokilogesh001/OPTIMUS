"""tests/live_plugin_acceptance_campaign.py — Real Live Acceptance Test Campaign for M20.

Executes all 7 mandatory test groups (A through G) specified in the M20 prompt against the
actual OPTIMUS application subsystems, UI overlays, registries, and configuration stores.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

# Ensure QApplication exists for GUI overlay testing
from PyQt6.QtWidgets import QApplication

_qapp = QApplication.instance()
if _qapp is None:
    _qapp = QApplication([])

from core.orchestrator.tools import ToolRegistry
from core.plugin_loader import PluginASTValidator, discover_plugins
from gui.overlays import PluginManagerOverlay, PluginSettingsOverlay
from memory.config_manager import (
    CONFIG_FILE,
    get_plugin_enabled,
    get_plugin_setting,
    save_plugin_config,
    save_plugin_enabled,
)


class TestLivePluginAcceptanceCampaign:
    """Live acceptance campaign executing groups A through G."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Prepare clean initial state for acceptance plugin."""
        save_plugin_enabled("acceptance_test_plugin", True)
        save_plugin_config("acceptance_test_plugin", {"test_threshold": 100, "test_api_key": ""})
        ToolRegistry.instance().unregister("acceptance_test_plugin")
        yield
        save_plugin_enabled("acceptance_test_plugin", True)
        ToolRegistry.instance().unregister("acceptance_test_plugin")

    # ── GROUP A: Discovery & Registration ──────────────────────────────────────
    def test_live_A_discovery_and_registration(self):
        """A. Verify live plugin discovery, metadata, tool declarations, collisions, and helper skipping."""
        plugins_dir = Path("plugins")
        tr = ToolRegistry.instance()
        core_names = tr.core_tool_names()

        # 1. Start / discover
        reg = discover_plugins(plugins_dir, core_names)
        assert reg.has("acceptance_test_plugin") is True

        # 2. Verify metadata appears in UI list
        ui_list = reg.list_for_ui()
        p_entry = next((p for p in ui_list if p["name"] == "acceptance_test_plugin"), None)
        assert p_entry is not None
        assert p_entry["valid"] is True
        assert p_entry["tier"] == "READ_ONLY"
        assert "math" in p_entry["capabilities"]

        # 3. Tool declaration reaches tool declarations
        decls = reg.get_tool_declarations()
        decl = next((d for d in decls if d["name"] == "acceptance_test_plugin"), None)
        assert decl is not None
        assert decl["parameters"]["type"] == "OBJECT"

        # 4. Bridge to central ToolRegistry and verify registration
        bridged = reg.bridge_to_tool_registry(tr)
        assert bridged >= 1
        assert tr.is_registered("acceptance_test_plugin") is True

        # 5. Verify underscore-prefixed helper files are not independently discovered
        assert not reg.has("_template")
        assert not any(p["name"] == "_template" for p in ui_list)

        # 6. Verify duplicate / colliding plugin names are rejected
        dummy_dir = Path("plugins") / "_test_dummy"
        dummy_dir.mkdir(exist_ok=True)
        try:
            collision_file = dummy_dir / "dup_plugin.py"
            collision_file.write_text("""
PLUGIN = {
    "name": "acceptance_test_plugin",  # DUPLICATE
    "description": "Duplicate attempt",
    "tier": "READ_ONLY",
    "parameters": {"type": "OBJECT", "properties": {}},
}
def run(p): return "dup"
""", encoding="utf-8")
            reg_dup = discover_plugins(dummy_dir, core_tool_names={"acceptance_test_plugin"})
            assert reg_dup.has("acceptance_test_plugin") is False
            assert len(reg_dup._all_records) == 1
            assert "collides with a core tool" in reg_dup._all_records[0].error or "already used" in reg_dup._all_records[0].error
        finally:
            if dummy_dir.exists():
                import shutil
                shutil.rmtree(dummy_dir, ignore_errors=True)

    # ── GROUP B: Actual Plugin Execution ───────────────────────────────────────
    def test_live_B_actual_plugin_execution(self):
        """B. Invoke through real tool execution path and verify deterministic output & logging."""
        plugins_dir = Path("plugins")
        reg = discover_plugins(plugins_dir, core_tool_names=set())
        tr = ToolRegistry.instance()
        reg.bridge_to_tool_registry(tr)

        # 1. Health check via ToolRegistry handler
        tool_def = tr.get("acceptance_test_plugin")
        assert tool_def is not None

        res_health = tool_def.handler({"action": "health"})
        assert res_health.status == "done"
        assert "Acceptance Test Plugin is healthy. Version: 1.0.0." in res_health.output

        # 2. Deterministic calculation: 14 * 3 = 42
        res_calc = tool_def.handler({"action": "calculate", "a": 14, "b": 3, "operator": "*"})
        assert res_calc.status == "done"
        assert "Calculation result: 42.00" in res_calc.output

        # 3. Direct PluginRegistry.run() with mock player logging
        class MockPlayer:
            def __init__(self):
                self.logs = []
            def write_log(self, text):
                self.logs.append(text)

        player = MockPlayer()
        res_direct = reg.run("acceptance_test_plugin", {"action": "health"}, player=player)
        assert "Acceptance Test Plugin is healthy" in res_direct
        assert any("Acceptance Test Plugin is healthy" in l for l in player.logs)

    # ── GROUP C: Configuration & Settings Persistence ──────────────────────────
    def test_live_C_configuration_persistence(self):
        """C. Verify settings rendering, saving, and persistence across process restart simulation."""
        reg = discover_plugins(Path("plugins"), core_tool_names=set())

        # 1. Verify settings schema delivered to UI
        schemas = reg.settings_schemas()
        acc_sec = next((s for s in schemas if s["namespace"] == "acceptance_test_plugin"), None)
        assert acc_sec is not None
        assert acc_sec["title"] == "Acceptance Test Plugin Settings"
        assert any(f["name"] == "test_threshold" for f in acc_sec["fields"])

        # 2. Modify and save setting via config_manager
        save_plugin_config("acceptance_test_plugin", {"test_threshold": 450, "test_api_key": "simulated_val"})
        assert get_plugin_setting("acceptance_test_plugin", "test_threshold") == 450

        # 3. Simulate application restart (re-read from physical disk)
        raw_disk = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        assert raw_disk["plugin_config"]["acceptance_test_plugin"]["test_threshold"] == 450

        # 4. Plugin run reads updated persistent value
        res_setting = reg.run("acceptance_test_plugin", {"action": "get_setting"})
        assert "Configured test_threshold is 450" in res_setting

    # ── GROUP D: Enable and Disable Lifecycle ───────────────────────────────────
    def test_live_D_enable_disable_lifecycle(self):
        """D. Verify live disabling, tool declaration exclusion, execution rejection, and re-enablement."""
        reg = discover_plugins(Path("plugins"), core_tool_names=set())

        # 1. Initially enabled
        assert reg.has("acceptance_test_plugin") is True
        assert any(d["name"] == "acceptance_test_plugin" for d in reg.get_tool_declarations())

        # 2. Disable via UI/config action
        save_plugin_enabled("acceptance_test_plugin", False)
        assert get_plugin_enabled("acceptance_test_plugin") is False

        # 3. Excluded from tool declarations
        decls_after_disable = reg.get_tool_declarations()
        assert not any(d["name"] == "acceptance_test_plugin" for d in decls_after_disable)

        # 4. Tool execution rejected
        res_dis = reg.run("acceptance_test_plugin", {"action": "health"})
        assert "The 'acceptance_test_plugin' plugin is currently disabled." in res_dis

        # 5. Re-enable
        save_plugin_enabled("acceptance_test_plugin", True)
        assert any(d["name"] == "acceptance_test_plugin" for d in reg.get_tool_declarations())
        res_re = reg.run("acceptance_test_plugin", {"action": "health"})
        assert "Acceptance Test Plugin is healthy" in res_re

    # ── GROUP E: Failure Recovery & Error Containment ──────────────────────────
    def test_live_E_failure_recovery_and_secret_redaction(self):
        """E. Trigger controlled error; verify containment, secret redaction, and recovery."""
        reg = discover_plugins(Path("plugins"), core_tool_names=set())

        # 1. Trigger error
        res_err = reg.run("acceptance_test_plugin", {"action": "trigger_error"})
        assert "Sir, the 'acceptance_test_plugin' plugin failed" in res_err

        # 2. Secret key redacted
        assert "sk-live-1234567890abcdef1234567890" not in res_err
        assert "[REDACTED_API_KEY]" in res_err

        # 3. System remains healthy; subsequent calls execute cleanly
        res_subsequent = reg.run("acceptance_test_plugin", {"action": "health"})
        assert "Acceptance Test Plugin is healthy" in res_subsequent

    # ── GROUP F: Security Acceptance ───────────────────────────────────────────
    def test_live_F_security_acceptance(self):
        """F. Verify AST pre-screening of dangerous code, protected namespaces, and model demarcation."""
        # 1. AST rejection of dangerous imports
        src_bad_import = """
import subprocess
PLUGIN = {"name": "pwn", "description": "d", "tier": "READ_ONLY", "parameters": {"type": "OBJECT"}}
def run(p): subprocess.run(["whoami"])
"""
        tmp_bad = Path("plugins") / "_temp_bad.py"
        try:
            tmp_bad.write_text(src_bad_import, encoding="utf-8")
            # Dangerous module import must be rejected by AST screener
            ok, err = PluginASTValidator.validate_source(tmp_bad)
            assert ok is False
            assert "dangerous module 'subprocess'" in err
        finally:
            if tmp_bad.exists():
                tmp_bad.unlink()

        # 2. Protected namespace rejection
        with pytest.raises(ValueError, match="SECURITY_VIOLATION"):
            save_plugin_config("providers", {"ollama": "malicious"})

        # 3. Untrusted output demarcation test
        from core import outcome
        raw_res = "Result with possible instructions"
        demarcated = outcome.untrusted(source="plugin:acceptance_test_plugin", content=raw_res)
        assert "<<<UNTRUSTED" in demarcated
        assert "Result with possible instructions" in demarcated
        assert "<<<END UNTRUSTED>>>" in demarcated
        assert outcome.unwrap(demarcated) == raw_res

    # ── GROUP G: UI Overlay Acceptance ─────────────────────────────────────────
    def test_live_G_ui_overlay_acceptance(self):
        """G. Verify PluginManagerOverlay and PluginSettingsOverlay widgets initialize, render, and toggle."""
        reg = discover_plugins(Path("plugins"), core_tool_names=set())

        # 1. PluginManagerOverlay creation & render
        manager_overlay = PluginManagerOverlay(plugins=reg.list_for_ui())
        assert manager_overlay is not None
        assert manager_overlay.isVisible() is False
        manager_overlay.show()
        assert manager_overlay.isVisible() is True
        manager_overlay.hide()

        # 2. PluginSettingsOverlay creation & render
        settings_schemas = reg.settings_schemas()
        settings_overlay = PluginSettingsOverlay(sections=settings_schemas)
        assert settings_overlay is not None
        settings_overlay.show()
        assert settings_overlay.isVisible() is True

        # 3. Execute settings action callback (TEST button)
        action_ran = False
        def mock_done(ns, ok, msg):
            nonlocal action_ran
            action_ran = True
            assert ns == "acceptance_test_plugin"
            assert ok is True
            assert "Connection verified" in msg

        settings_overlay._test_done.connect(mock_done)
        settings_overlay._run_action("acceptance_test_plugin")

        # Allow worker thread up to 1 second to complete
        start = time.time()
        while not action_ran and (time.time() - start < 1.0):
            QApplication.processEvents()
            time.sleep(0.05)

        assert action_ran is True
        settings_overlay.hide()
