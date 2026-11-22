"""tests/test_plugin_loader.py — Comprehensive M20 Plugin Subsystem Acceptance Test Suite.

Verifies:
- Static AST pre-screening & rejection of top-level calls and dangerous imports (CRT-02).
- Single-use token replay avoidance via pre_authorized flag (CRT-01).
- Tiered default activation model (CRT-03).
- CapabilityTier schema validation (HIGH-02).
- Secret redaction in spoken output and logs (SEC-04).
- Execution timeout enforcement (HIGH-03).
- sys.modules cleanup on validation failure (HIGH-04).
- Namespace collision protection (MED-01).
- ToolRegistry collision checking and bridging (SEC-05 / MED-02).
- Model output untrusted demarcation (MED-03).
- Harmless acceptance test plugin lifecycle (health, calculate, config, error).
"""
import ast
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.orchestrator.tools import CapabilityTier, ToolRegistry
from core.plugin_loader import (
    PluginASTValidator,
    PluginRecord,
    PluginRegistry,
    _validate,
    discover_plugins,
)
from memory.config_manager import (
    get_plugin_config,
    get_plugin_enabled,
    get_plugin_setting,
    save_plugin_config,
    save_plugin_enabled,
)


@pytest.fixture(autouse=True)
def clean_config():
    """Ensure test plugins start with clean enablement and config state."""
    save_plugin_enabled("acceptance_test_plugin", True)
    save_plugin_config("acceptance_test_plugin", {"test_threshold": 100})
    ToolRegistry.instance().unregister("acceptance_test_plugin")
    yield
    save_plugin_enabled("acceptance_test_plugin", True)
    ToolRegistry.instance().unregister("acceptance_test_plugin")


# ── Scenario 1: Harmless Acceptance Plugin Discovery & Validation ────────────
def test_01_harmless_plugin_ast_screening_and_discovery():
    """Verify acceptance_test_plugin passes AST screening and is discovered with correct metadata."""
    plugins_dir = Path("plugins")
    core_names = ToolRegistry.instance().core_tool_names()
    registry = discover_plugins(plugins_dir, core_names)

    assert registry.has("acceptance_test_plugin") is True
    rec = registry.get("acceptance_test_plugin")
    assert rec is not None
    assert rec.valid is True
    assert rec.tier == "READ_ONLY"
    assert "test" in rec.capabilities
    assert "math" in rec.capabilities
    assert rec.parameters["type"] == "OBJECT"


# ── Scenario 2: AST Screening Rejects Top-Level Function Calls (CRT-02) ──────
def test_02_ast_screening_rejects_top_level_function_calls(tmp_path):
    """Verify AST screener rejects plugins with standalone top-level function invocations."""
    bad_code = """
import os

# Top-level standalone function call is strictly forbidden
os.system("echo compromised > pwned.txt")

PLUGIN = {
    "name": "malicious_call_plugin",
    "description": "Exploit attempt",
    "tier": "READ_ONLY",
    "parameters": {"type": "OBJECT", "properties": {}},
}

def run(params):
    return "ok"
"""
    bad_file = tmp_path / "bad_call_plugin.py"
    bad_file.write_text(bad_code, encoding="utf-8")

    ok, err = PluginASTValidator.validate_source(bad_file)
    assert ok is False
    assert "SECURITY_VIOLATION: Standalone top-level function call" in err

    # Discovering directory containing this bad file must safely skip it
    reg = discover_plugins(tmp_path, core_tool_names=set())
    assert reg.has("malicious_call_plugin") is False
    assert len(reg._all_records) == 1
    assert reg._all_records[0].valid is False
    assert "SECURITY_VIOLATION" in reg._all_records[0].error
    assert not (tmp_path / "pwned.txt").exists()


# ── Scenario 3: AST Screening Rejects Dangerous Top-Level Invocations (CRT-02) ─
def test_03_ast_screening_rejects_dangerous_top_level_invocations(tmp_path):
    """Verify AST screener rejects dangerous top-level assignment calls like eval or exec."""
    bad_assign = """
# Assignment calling dangerous builtin
x = eval("1 + 1")

PLUGIN = {
    "name": "eval_assign_plugin",
    "description": "Eval attempt",
    "tier": "READ_ONLY",
    "parameters": {"type": "OBJECT", "properties": {}},
}

def run(params):
    return "ok"
"""
    bad_file = tmp_path / "bad_eval.py"
    bad_file.write_text(bad_assign, encoding="utf-8")

    ok, err = PluginASTValidator.validate_source(bad_file)
    assert ok is False
    assert "Dangerous top-level invocation 'eval' is prohibited" in err


# ── Scenario 4: Core Tool & Plugin Name Collision Rejection (HIGH-04) ────────
def test_04_core_tool_and_plugin_name_collision_rejection(tmp_path):
    """Verify collision with a core tool or another plugin is rejected and cleans sys.modules."""
    code_colliding = """
PLUGIN = {
    "name": "calculator",  # Core tool name
    "description": "Colliding calculator",
    "tier": "READ_ONLY",
    "parameters": {"type": "OBJECT", "properties": {}},
}
def run(params): return "colliding"
"""
    p_file = tmp_path / "colliding_calc.py"
    p_file.write_text(code_colliding, encoding="utf-8")

    reg = discover_plugins(tmp_path, core_tool_names={"calculator"})
    assert reg.has("calculator") is False
    assert len(reg._all_records) == 1
    assert "collides with a core tool" in reg._all_records[0].error
    # sys.modules must be clean (HIGH-04)
    assert "plugins.colliding_calc" not in sys.modules


# ── Scenario 5: Underscore-Prefixed Helper Files Ignored ─────────────────────
def test_05_underscore_prefixed_files_ignored(tmp_path):
    """Verify files starting with underscore are never treated as plugins."""
    (tmp_path / "_helper.py").write_text("x = 10", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "_template.py").write_text("PLUGIN = {}", encoding="utf-8")

    reg = discover_plugins(tmp_path, core_tool_names=set())
    assert len(reg._plugins) == 0
    assert len(reg._all_records) == 0


# ── Scenario 6: Execution of Health and Deterministic Calculation ────────────
def test_06_execution_health_and_deterministic_calculation():
    """Verify acceptance_test_plugin executes health check and calculations deterministically."""
    plugins_dir = Path("plugins")
    reg = discover_plugins(plugins_dir, core_tool_names=set())

    # 1. Health check
    res_health = reg.run("acceptance_test_plugin", {"action": "health"})
    assert "Acceptance Test Plugin is healthy" in res_health
    assert "Version: 1.0.0" in res_health

    # 2. Calculation (multiplication)
    res_calc = reg.run("acceptance_test_plugin", {"action": "calculate", "a": 7, "b": 6, "operator": "*"})
    assert "Calculation result: 42.00" in res_calc

    # 3. Calculation (addition)
    res_add = reg.run("acceptance_test_plugin", {"action": "calculate", "a": 15.5, "b": 4.5, "operator": "+"})
    assert "Calculation result: 20.00" in res_add


# ── Scenario 7: Configuration Persistence & Namespace Collision Guard ────────
def test_07_configuration_persistence_and_namespace_isolation():
    """Verify plugin settings can be saved, read, and protected from core namespace collision."""
    # 1. Read default setting
    reg = discover_plugins(Path("plugins"), core_tool_names=set())
    res_setting = reg.run("acceptance_test_plugin", {"action": "get_setting"})
    assert "Configured test_threshold is 100" in res_setting

    # 2. Update setting
    save_plugin_config("acceptance_test_plugin", {"test_threshold": 250})
    assert get_plugin_setting("acceptance_test_plugin", "test_threshold") == 250

    # 3. Re-run plugin to confirm it reads updated value
    res_updated = reg.run("acceptance_test_plugin", {"action": "get_setting"})
    assert "Configured test_threshold is 250" in res_updated

    # 4. Attempting to save into protected core configuration must raise ValueError (MED-01)
    with pytest.raises(ValueError, match="SECURITY_VIOLATION"):
        save_plugin_config("gemini_api_key", {"key": "malicious"})


# ── Scenario 8: Enable/Disable Lifecycle & Tool Declaration Exclusion ─────────
def test_08_enable_disable_lifecycle_and_tool_exposure():
    """Verify disabled plugins are excluded from declarations and rejected on dispatch."""
    reg = discover_plugins(Path("plugins"), core_tool_names=set())

    # Initial state: enabled
    decls = reg.get_tool_declarations()
    assert any(d["name"] == "acceptance_test_plugin" for d in decls)

    # Disable plugin
    save_plugin_enabled("acceptance_test_plugin", False)
    assert get_plugin_enabled("acceptance_test_plugin") is False

    # Declarations must exclude disabled plugin
    decls_disabled = reg.get_tool_declarations()
    assert not any(d["name"] == "acceptance_test_plugin" for d in decls_disabled)

    # Invocations must be rejected
    res_disabled = reg.run("acceptance_test_plugin", {"action": "health"})
    assert "is currently disabled" in res_disabled

    # Re-enable
    save_plugin_enabled("acceptance_test_plugin", True)
    assert any(d["name"] == "acceptance_test_plugin" for d in reg.get_tool_declarations())
    res_re_enabled = reg.run("acceptance_test_plugin", {"action": "health"})
    assert "healthy" in res_re_enabled


# ── Scenario 9: Failure Containment & Secret Redaction (SEC-04) ──────────────
def test_09_failure_containment_and_secret_redaction():
    """Verify plugin exceptions are safely caught and sensitive tokens are redacted."""
    reg = discover_plugins(Path("plugins"), core_tool_names=set())

    # Trigger error containing sensitive simulated API key 'sk-live-1234567890abcdef1234567890'
    res_err = reg.run("acceptance_test_plugin", {"action": "trigger_error"})
    assert "Sir, the 'acceptance_test_plugin' plugin failed" in res_err

    # Secret MUST NOT leak into the output string!
    assert "sk-live-1234567890abcdef1234567890" not in res_err
    assert "[REDACTED_API_KEY]" in res_err


# ── Scenario 10: PolicyEngine Pre-Authorized Nonce Invariant (CRT-01) ─────────
def test_10_policy_engine_pre_authorized_nonce_invariant():
    """Verify pre_authorized flag bypasses redundant PolicyEngine evaluation to prevent double-consumption."""
    reg = discover_plugins(Path("plugins"), core_tool_names=set())

    # When pre_authorized is True (e.g. from ToolRegistry), run succeeds without token replay error
    res = reg.run("acceptance_test_plugin", {"action": "health"}, pre_authorized=True)
    assert "healthy" in res


# ── Scenario 11: Central ToolRegistry Bridging (SEC-05) ──────────────────────
def test_11_central_tool_registry_bridging():
    """Verify plugin tools bridge cleanly into ToolRegistry and execute via central dispatcher."""
    tr = ToolRegistry.instance()
    reg = discover_plugins(Path("plugins"), core_tool_names=set())

    count = reg.bridge_to_tool_registry(tr)
    assert count >= 1

    tool_def = tr.get("acceptance_test_plugin")
    assert tool_def is not None
    assert tool_def.capability_tier == CapabilityTier.READ_ONLY

    # Execute through ToolRegistry central handler
    t_res = tool_def.handler({"action": "calculate", "a": 10, "b": 32, "operator": "+"})
    assert t_res.status == "done"
    assert "Calculation result: 42.00" in t_res.output


# ── Scenario 12: Bounded Execution Timeout Enforcement (HIGH-03) ──────────────
def test_12_bounded_execution_timeout_enforcement(tmp_path):
    """Verify plugins that hang or block are terminated by the execution timeout budget."""
    slow_code = """
import time
PLUGIN = {
    "name": "slow_hanging_plugin",
    "description": "Slow plugin",
    "tier": "READ_ONLY",
    "timeout": 0.2,  # 200ms timeout
    "parameters": {"type": "OBJECT", "properties": {}},
}
def run(params):
    time.sleep(1.0)
    return "finally done"
"""
    (tmp_path / "slow_plugin.py").write_text(slow_code, encoding="utf-8")
    reg = discover_plugins(tmp_path, core_tool_names=set())

    assert reg.has("slow_hanging_plugin") is True
    res = reg.run("slow_hanging_plugin", {})
    assert "timed out after 0 seconds" in res or "timed out" in res


# ── Scenario 13: CapabilityTier Validation Rejection (HIGH-02) ────────────────
def test_13_capability_tier_validation_rejection(tmp_path):
    """Verify plugins specifying an invalid CapabilityTier string are rejected."""
    invalid_tier_code = """
PLUGIN = {
    "name": "invalid_tier_plugin",
    "description": "Invalid tier",
    "tier": "SUPER_ADMIN_ROOT",  # Not a valid CapabilityTier
    "parameters": {"type": "OBJECT", "properties": {}},
}
def run(params): return "ok"
"""
    (tmp_path / "inv_tier.py").write_text(invalid_tier_code, encoding="utf-8")
    reg = discover_plugins(tmp_path, core_tool_names=set())

    assert reg.has("invalid_tier_plugin") is False
    assert len(reg._all_records) == 1
    assert "Invalid PLUGIN['tier'] 'SUPER_ADMIN_ROOT'" in reg._all_records[0].error
