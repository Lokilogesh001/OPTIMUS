# Milestone 15.1: Native Windows Explorer Launch Capability — Test Evidence

**Test Run Date:** 2026-11-22  
**Test Suite:** `tests/test_explorer_capability.py`  
**Execution Environment:** Python 3.13.16 on Windows 11  
**Results:** **29 passed in 4.16s (100% pass rate)**  
**Broader Regression Suite:** **158 passed, 1 skipped in 23.22s**

---

## 1. Test Suite Coverage & Results Matrix

| Test ID | Test Function | Purpose / Assertion | Result |
| :--- | :--- | :--- | :---: |
| **TEST-01** | `test_open_d_project` | Open `D:\project` directory in File Explorer | **PASSED** |
| **TEST-02** | `test_open_d_project_optimus` | Open `D:\project\optimus` in File Explorer | **PASSED** |
| **TEST-03** | `test_open_user_downloads` | Open `C:\Users\<user>\Downloads` in File Explorer | **PASSED** |
| **TEST-04** | `test_open_valid_file_selected` | Open file with `/select,<path>` highlighting | **PASSED** |
| **TEST-05** | `test_open_nonexistent_path` | Handle nonexistent path (`PATH_NOT_FOUND`) cleanly | **PASSED** |
| **TEST-06** | `test_open_inaccessible_credential_path` | Block unauthorized access to `.env` / credentials | **PASSED** |
| **TEST-07** | `test_open_unc_path_rejected` | Block remote UNC path (`\\server\share`) | **PASSED** |
| **TEST-08** | `test_open_explorer_across_agent_profiles[filesystem]` | Agent `filesystem` authorized to call `open_in_explorer` | **PASSED** |
| **TEST-09** | `test_open_explorer_across_agent_profiles[general]` | Agent `general` authorized to call `open_in_explorer` | **PASSED** |
| **TEST-10** | `test_open_explorer_across_agent_profiles[researcher]` | Agent `researcher` authorized to call `open_in_explorer` | **PASSED** |
| **TEST-11** | `test_open_explorer_across_agent_profiles[execution_agent]` | Agent `execution_agent` authorized to call `open_in_explorer` | **PASSED** |
| **TEST-12** | `test_tool_aliases_open_directory_and_open_folder` | Verify aliases `open_directory` and `open_folder` work | **PASSED** |
| **TEST-13** | `test_actual_explorer_process_launch_evidence` | Validate PID, process verification, and session ID | **PASSED** |
| **TEST-14** | `test_session_0_isolation_safeguard` | Enforce Session 0 diagnostic guard without active desktop | **PASSED** |
| **TEST-15** | `test_mre_intent_classification_explorer[Open D:\project in Explorer]` | Parse intent with trailing "in Explorer" | **PASSED** |
| **TEST-16** | `test_mre_intent_classification_explorer[Open D:\project in Explorer.]` | Parse intent with trailing period | **PASSED** |
| **TEST-17** | `test_mre_intent_classification_explorer[Open D:\project in File Explorer]` | Parse intent with "in File Explorer" | **PASSED** |
| **TEST-18** | `test_mre_intent_classification_explorer[Open D:\project in Windows Explorer]` | Parse intent with "in Windows Explorer" | **PASSED** |
| **TEST-19** | `test_mre_intent_classification_explorer[Show this directory in Explorer]` | Parse contextual token "this directory" | **PASSED** |
| **TEST-20** | `test_mre_intent_classification_explorer[Open this folder]` | Parse contextual token "this folder" | **PASSED** |
| **TEST-21** | `test_mre_intent_classification_explorer[Open D:\project]` | Parse direct folder target | **PASSED** |
| **TEST-22** | `test_mre_intent_classification_explorer[Open this file's containing folder]` | Parse parent folder intent | **PASSED** |
| **TEST-23** | `test_mre_intent_classification_explorer[Show this file in Explorer]` | Parse file target with selection | **PASSED** |
| **TEST-24** | `test_mre_intent_classification_explorer[Open downloads in explorer]` | Parse known folder shortcut | **PASSED** |
| **TEST-25** | `test_mre_intent_classification_explorer[Open downloads]` | Parse plain downloads shortcut | **PASSED** |
| **TEST-26** | `test_mre_does_not_hijack_application_launches` | Preserve application launches ("open notepad", "open calc") | **PASSED** |
| **TEST-27** | `test_orchestrator_fast_path_open_in_explorer` | Verify CentralOrchestrator fast-path execution | **PASSED** |
| **TEST-28** | `test_file_controller_action_open` | Verify `file_controller` actions dispatch correctly | **PASSED** |
| **TEST-29** | `test_privileged_broker_and_runtime_client_open_explorer` | Verify runtime client and broker IPC integration | **PASSED** |

---

## 2. Regression Test Summary

```
======================= 158 passed, 1 skipped in 23.22s =======================
tests/test_explorer_capability.py               29 passed
tests/test_unified_filesystem.py                18 passed
tests/test_core_orchestrator.py                 20 passed
tests/test_verification_engine.py               16 passed
tests/test_gmail_intelligence_integration.py    12 passed, 1 skipped
tests/test_gmail_oauth_repair.py                15 passed
tests/test_personal_intelligence.py             21 passed
tests/test_policy_engine.py                     11 passed
tests/test_windows_runtime.py                   16 passed
```
