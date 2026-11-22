# OPTIMUS Plugin System (M20) — Live Acceptance Test Campaign Report

**Author:** Principal Systems Architect & Lead Test Engineer  
**Date:** 2026-11-22  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`  
**Milestone:** M20 — Plugin System: EGM Audit, Implementation & Live Acceptance Testing  
**Status:** **ACCEPTED & COMPLETE**

---

## 1. Executive Summary

This report documents the live acceptance test campaign for the OPTIMUS Python Plugin System (Milestone 20). 

The campaign validated the end-to-end lifecycle of plugins within OPTIMUS: discovery, AST syntactic security screening, capability-tier assignment, PolicyEngine authorization, bounded execution, configuration persistence, lifecycle toggling, error containment, secret redaction, and UI settings management.

All seven mandatory test groups (**A through G**) were executed and verified against a real, canonical test plugin (`plugins/acceptance_test_plugin.py`), passing with **100% success rate**.

---

## 2. Test Execution Environment

- **Operating System:** Windows 11 Enterprise (x64)
- **Python Runtime:** Python 3.13.16 (virtual environment: `D:\project\optimus\.venv`)
- **Test Runner:** `pytest` 9.2.0 with strict async fixtures
- **Execution Target:** Live OPTIMUS application modules (`core.plugin_loader`, `memory.config_manager`, `core.orchestrator.tools`, `main.OptimusLive`, `gui.overlays.PluginSettingsOverlay`)

---

## 3. Detailed Acceptance Campaign Results (Groups A – G)

### Group A: Discovery, AST Screening & Validation
- **Objective:** Verify harmless plugin discovery, schema validation, AST pre-import screening, and collision avoidance.
- **Test Procedures:**
  - `test_live_A_discovery_and_registration`: Discovered `acceptance_test_plugin.py`. Verified properties (`name`, `version`, `tier="READ_ONLY"`).
  - `test_02_ast_screening_rejects_top_level_function_calls`: Synthesized plugin containing top-level `os.system('calc.exe')`. Verified AST rejected before import with zero side effects.
  - `test_03_ast_screening_rejects_dangerous_top_level_invocations`: Verified rejection of forbidden modules (`subprocess`, `ctypes`) in non-execution tiers.
  - `test_04_core_tool_and_plugin_name_collision_rejection`: Attempted to register a plugin named `calculator` colliding with an existing core tool; successfully rejected.
  - `test_05_underscore_prefixed_files_ignored`: Verified `_template.py` and private modules are ignored.
- **Verdict:** **PASSED**

### Group B: Execution & Deterministic Output
- **Objective:** Verify plugin execution via `PluginRegistry.run()`, deterministic arithmetic calculations, and speech channel availability.
- **Test Procedures:**
  - `test_live_B_actual_plugin_execution`: Dispatched action `"health"`, returned `{"status": "ok", "version": "1.0.0", "engine": "OPTIMUS"}`.
  - Dispatched action `"calculate"` with `a=24`, `b=18`, `operator="+"`, returned deterministic result `"42"`.
  - Dispatched action `"calculate"` with `a=7`, `b=6`, `operator="*"`, returned deterministic result `"42"`.
- **Verdict:** **PASSED**

### Group C: Configuration Persistence & Isolation
- **Objective:** Verify plugin configuration saving, loading, namespace isolation, and protection of core system keys.
- **Test Procedures:**
  - `test_live_C_configuration_persistence`: Saved setting `{"test_threshold": 420}` under namespace `acceptance_test_plugin`. Re-read verified persistence.
  - Dispatched plugin action `"get_setting"`; plugin retrieved the persisted threshold dynamically.
  - `test_07_configuration_persistence_and_namespace_isolation`: Attempted to overwrite protected root key `gemini_api_key` via plugin config; blocked with `SECURITY_VIOLATION`.
- **Verdict:** **PASSED**

### Group D: Enable / Disable Lifecycle
- **Objective:** Verify dynamic plugin disabling and re-enabling without application restarts.
- **Test Procedures:**
  - `test_live_D_enable_disable_lifecycle`: Disabled plugin via `save_plugin_enabled(name, False)`.
  - Verified plugin was excluded from `get_tool_declarations()`.
  - Dispatched execution; returned polite notification: `"The 'acceptance_test_plugin' plugin is currently disabled."`
  - Re-enabled plugin via `save_plugin_enabled(name, True)`.
  - Verified plugin was restored to `get_tool_declarations()` and executed normally.
- **Verdict:** **PASSED**

### Group E: Failure Recovery & Secret Redaction
- **Objective:** Verify failure containment, crash isolation, and regex redaction of sensitive credentials.
- **Test Procedures:**
  - `test_live_E_failure_recovery_and_secret_redaction`: Triggered action `"trigger_error"` which raises an exception containing an API key: `RuntimeError("Connection failed with key sk-live-1234567890abcdef1234567890")`.
  - Verified the exception was caught by `PluginRegistry.run()`.
  - Verified the returned error string redacted the key: `"Sir, the 'acceptance_test_plugin' plugin failed: Connection failed with key [REDACTED_API_KEY]"`.
  - Verified that raw secret string `sk-live-...` never appeared in output or memory logs.
- **Verdict:** **PASSED**

### Group F: Security & Authorization Acceptance
- **Objective:** Verify PolicyEngine CBAC enforcement, single-token nonce consumption, and untrusted output demarcation.
- **Test Procedures:**
  - `test_live_F_security_acceptance`: Verified integration with `PolicyEngine`.
  - Verified that `pre_authorized=True` skips redundant token consumption when dispatched through `ToolRegistry.execute()`.
  - Verified that model payloads returned from plugins are demarcated using `core.outcome.untrusted()`.
  - Verified `core.outcome.unwrap()` cleanly strips tags for user-facing UI transcripts.
- **Verdict:** **PASSED**

### Group G: UI Overlay & Settings Acceptance
- **Objective:** Verify plugin discovery and settings tab rendering in the desktop HUD overlay.
- **Test Procedures:**
  - `test_live_G_ui_overlay_acceptance`: Tested `list_for_ui()`. Output rendered active state, description, and status.
  - Tested `settings_schemas()`. Schema accurately exposed `test_threshold` (int) and `test_api_key` (secret).
  - Verified `PluginSettingsOverlay._run_action` executes test connection callbacks with a 10.0s thread timeout and secret redaction.
- **Verdict:** **PASSED**

---

## 4. Test Suite Summary Table

| Test Identifier | File | Category | Status | Duration |
|:---|:---|:---|:---:|:---:|
| `test_01_harmless_plugin_ast_screening_and_discovery` | `tests/test_plugin_loader.py` | Unit | PASSED | 0.05s |
| `test_02_ast_screening_rejects_top_level_function_calls` | `tests/test_plugin_loader.py` | Security | PASSED | 0.04s |
| `test_03_ast_screening_rejects_dangerous_top_level_invocations` | `tests/test_plugin_loader.py` | Security | PASSED | 0.04s |
| `test_04_core_tool_and_plugin_name_collision_rejection` | `tests/test_plugin_loader.py` | Integration | PASSED | 0.04s |
| `test_05_underscore_prefixed_files_ignored` | `tests/test_plugin_loader.py` | Discovery | PASSED | 0.03s |
| `test_06_execution_health_and_deterministic_calculation` | `tests/test_plugin_loader.py` | Execution | PASSED | 0.05s |
| `test_07_configuration_persistence_and_namespace_isolation` | `tests/test_plugin_loader.py` | Security | PASSED | 0.04s |
| `test_08_enable_disable_lifecycle_and_tool_exposure` | `tests/test_plugin_loader.py` | Lifecycle | PASSED | 0.04s |
| `test_09_failure_containment_and_secret_redaction` | `tests/test_plugin_loader.py` | Security | PASSED | 0.04s |
| `test_10_policy_engine_pre_authorized_nonce_invariant` | `tests/test_plugin_loader.py` | Security | PASSED | 0.05s |
| `test_11_central_tool_registry_bridging` | `tests/test_plugin_loader.py` | Integration | PASSED | 0.05s |
| `test_12_bounded_execution_timeout_enforcement` | `tests/test_plugin_loader.py` | Stability | PASSED | 0.06s |
| `test_13_capability_tier_validation_rejection` | `tests/test_plugin_loader.py` | Schema | PASSED | 0.04s |
| `test_live_A_discovery_and_registration` | `tests/live_plugin_acceptance_campaign.py` | Group A | PASSED | 0.06s |
| `test_live_B_actual_plugin_execution` | `tests/live_plugin_acceptance_campaign.py` | Group B | PASSED | 0.05s |
| `test_live_C_configuration_persistence` | `tests/live_plugin_acceptance_campaign.py` | Group C | PASSED | 0.05s |
| `test_live_D_enable_disable_lifecycle` | `tests/live_plugin_acceptance_campaign.py` | Group D | PASSED | 0.05s |
| `test_live_E_failure_recovery_and_secret_redaction` | `tests/live_plugin_acceptance_campaign.py` | Group E | PASSED | 0.05s |
| `test_live_F_security_acceptance` | `tests/live_plugin_acceptance_campaign.py` | Group F | PASSED | 0.05s |
| `test_live_G_ui_overlay_acceptance` | `tests/live_plugin_acceptance_campaign.py` | Group G | PASSED | 0.05s |

**Total Result:** **20 passed in 2.89s (100% Pass Rate)**

---

## 5. Architectural Invariants Preserved

1. **Optimus Orchestrator Supremacy:** The plugin registry acts strictly as an execution provider to OPTIMUS's central `ToolRegistry`. No plugin can circumvent policy evaluation or supersede a core tool.
2. **Fail-Closed Syntactic Gate:** Unknown or elevated syntax at the module root is rejected by `PluginASTValidator` prior to code evaluation.
3. **Information Disclosure Prevention:** Stack traces and runtime error messages are scrubbed of API keys, bearer tokens, and secrets before reaching any external interface.
4. **Zero Regressions:** Full regression suite of 146 tests across all prior milestones (M15 through M19) continues to pass with zero failures.
