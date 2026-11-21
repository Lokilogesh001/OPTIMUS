# OPTIMUS Milestone 15.1 — EGM Reconciliation: Native Windows Explorer Launch Capability

## 1. Overview
This document records the formal reconciliation between the Elephant Technical Specification and the Independent Goldfish Audit (`APPROVED_WITH_CONCERNS`). All four findings have been incorporated into the final implementation plan.

---

## 2. Reconciliation Matrix

| Finding ID | Severity | Audit Finding | Concrete Resolution & Implementation Contract |
|---|---|---|---|
| **Finding 1** | `Major` | **Session 0 Delegation:** Circular dispatch if Session 0 service calls `WindowsRuntimeClient`. | 1. Direct interactive execution in Session > 0 via `subprocess.Popen([explorer_exe, ...], shell=False)`.<br>2. In Session 0 (`session_id == 0`), attempt user-token launch via `WTSGetActiveConsoleSessionId()`. If no interactive console session is active or token acquisition is unavailable, return clean diagnostic `ToolResult(status="failed", error="SESSION_0_ISOLATION: No active interactive user desktop session available to display File Explorer.")`.<br>3. Guard Windows-specific session calls with `if os.name == "nt":`. |
| **Finding 2** | `Major` | **Policy Denial Contract:** Explicit `ToolResult` status for unauthorized or approval-gated targets. | When `check_access(target, "open_in_explorer")` fails, return:<br>`ToolResult(status="unverified" if req_appr else "failed", error=reason, evidence={"requires_approval": req_appr, "approval_reason": reason, "target": str(target)})`. |
| **Finding 3** | `Robustness` | **Contextual Semantic Tokens:** Phrasings like "open this folder", "show this directory in Explorer". | Preprocessor in `MREClassifier` and `_try_fast_path` maps contextual tokens (`"this"`, `"this folder"`, `"this directory"`, `"here"`, `"workspace"`, `"."`) to `str(Path.cwd())` before path resolution. |
| **Finding 4** | `Nit` | **Automatic File Highlighting Invariant:** Target is file. | Invariant enforced in all callers:<br>`if select_file or target.is_file(): cmd = [explorer_exe, f"/select,{target}"] else: cmd = [explorer_exe, str(target)]`. |

---

## 3. Concrete Implementation Steps

1. **Step 1: `UnifiedFilesystemService.open_in_explorer` (`core/filesystem/service.py`)**
   - Implement `open_in_explorer(self, path: str | Path, select_file: bool = False, context: dict[str, Any] | None = None) -> ToolResult`.
   - Apply path canonicalization, existence check, policy check, Session 0 detection, and safe process launch.
2. **Step 2: `PrivilegedExecutionBroker._op_open_in_explorer` (`core/windows_runtime/broker.py`)**
   - Update to use `UnifiedFilesystemService.instance().open_in_explorer()`.
3. **Step 3: `ToolRegistry` & Agent Profiles (`core/orchestrator/tools.py` & `core/orchestrator/agents.py`)**
   - Register `open_in_explorer` with tier `CapabilityTier.READ_ONLY`.
   - Register aliases `open_directory` and `open_folder` routing to `open_in_explorer`.
   - Add `"open_in_explorer"` to `filesystem`, `general`, `developer`, `researcher`, and `windows_runtime` agents.
4. **Step 4: `actions/file_controller.py`**
   - Add actions `"open"`, `"open_folder"`, `"open_in_explorer"` delegating to `UnifiedFilesystemService.instance().open_in_explorer()`.
5. **Step 5: Intent Recognition & Fast-Path Pipeline (`core/orchestrator/mre.py` & `orchestrator.py`)**
   - Update `_EXPLORER_PATTERN` and `_try_fast_path` regexes to capture "in Explorer", "show this file in Explorer", "open D:\project", etc., with contextual token normalization.
6. **Step 6: Comprehensive Verification Suite (`tests/test_explorer_capability.py`)**
   - Test all required cases across interactive sessions, directory paths, file selection, nonexistent paths, sensitive credential gating, and agent profiles.
