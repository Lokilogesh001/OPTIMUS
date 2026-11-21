# OPTIMUS Milestone 15.1 — Elephant Investigation: Native Windows Explorer Launch Capability

## 1. Problem Statement
When a user requests to open a folder or file in Windows File Explorer (e.g., "Open D:\project in Explorer", "Show this directory in Explorer", "Open this file in Explorer"), OPTIMUS either:
1. Lists the directory contents instead of launching File Explorer, or
2. Responds that it cannot open File Explorer.

---

## 2. Comprehensive Codebase Investigation

### 2.1 Trace of Filesystem & Explorer Capabilities
Investigation across the 4 execution layers revealed five distinct disconnects:

1. **Intent Matching & Fast-Path Pipeline Gaps:**
   - In `core/orchestrator/mre.py`:
     ```python
     _EXPLORER_PATTERN = re.compile(
         r"^(?:open|explore|show|browse)\s+(?:folder|directory|path)?\s*([a-zA-Z]:[\\/][^<>:\"|?*\n]*|\.{1,2}[\\/][^<>:\"|?*\n]*)\s*$",
         re.IGNORECASE,
     )
     ```
     This pattern fails to match user queries containing "in Explorer", "in File Explorer", or file selection requests like "show this file in Explorer". When the user asks "Open D:\project in Explorer", `_EXPLORER_PATTERN` greedily includes "in Explorer" in the path capture group, resulting in an invalid path `D:\project in Explorer`, or fails to match entirely.
   - In `core/orchestrator/orchestrator.py` (`_try_fast_path`):
     ```python
     dir_match = re.match(
         r"^(?:open|explore)\s+(?:the\s+)?(?:folder\s+|directory\s+)?(downloads|desktop|documents|pictures|workspace|[\w\-\.\/\\]+)(?:\s+(?:folder|directory))?(?:\s+in\s+(?:the\s+)?file\s+manager)?$",
         stripped,
         re.IGNORECASE,
     )
     ```
     This regex only accepts `in (the) file manager` and fails on `in (Windows) Explorer`. Consequently, requests fall through to task planning.

2. **Agent Capability & Tool Authorization Mismatch:**
   - In `core/orchestrator/agents.py`, the `filesystem` agent profile declares:
     ```python
     allowed_tools=[
         "read_file", "write_file", "list_directory", "open_directory",
         "open_folder", "find_files", "largest_files", "create_directory", "create_folder"
     ]
     allowed_tiers=[CapabilityTier.READ_ONLY, CapabilityTier.WORKSPACE_WRITE]
     ```
     Notice that `open_in_explorer` is **absent** from `filesystem` agent's `allowed_tools`.
   - Furthermore, `open_in_explorer` in `core/windows_runtime/tools.py` was registered under `CapabilityTier.EXECUTION`. Because `filesystem` agent's allowed tiers are restricted to `READ_ONLY` and `WORKSPACE_WRITE`, any attempt by the filesystem agent to invoke `open_in_explorer` is denied by CBAC policy (`CapabilityTier.EXECUTION > WORKSPACE_WRITE`).
   - The `general` assistant profile also lacks `open_in_explorer` in `allowed_tools`.

3. **Absence of Unified Filesystem Service Method:**
   - `core/filesystem/service.py` (`UnifiedFilesystemService`) centralized all filesystem access in M15 (`read_file`, `write_file`, `list_directory`, `delete_file`, etc.), but does **not** provide an authoritative `open_in_explorer(path, select_file=False)` method.
   - Callers were forced to use either `os.startfile()` (which does not support `/select` for files and does not validate against `UnifiedFilesystemService` rules) or `core/windows_runtime/client.py` (which was isolated from the unified filesystem service).

4. **Missing Action in `actions/file_controller.py`:**
   - `file_controller` supports `list`, `read`, `write`, `create_file`, `create_folder`, `delete`, `move`, `copy`, `find`, `largest`, `disk_usage`, `organize_desktop`, and `info`.
   - It contains **zero** actions for `open`, `open_folder`, or `open_in_explorer`. Any call to `file_controller(parameters={"action": "open_in_explorer"})` returns `Unknown action: 'open_in_explorer'`.

5. **Session 0 vs Interactive Desktop Session:**
   - Windows Services run in Session 0, which has no interactive desktop. Attempting to launch `explorer.exe` directly from a Windows service running as LocalSystem in Session 0 produces an invisible, non-interactive Explorer process.
   - OPTIMUS requires a session-aware execution mechanism:
     - When running in an interactive session (Session ID > 0), launch Explorer directly using a secure process API (`subprocess.Popen([explorer_exe, ...])`, no `shell=True`).
     - When running in Session 0 (service mode), route the launch request to the interactive user session broker or use the authenticated IPC client to dispatch to the active desktop session.

---

## 3. Requirements & Invariants for M15.1

1. **Path Canonicalization & Validation:** Use `UnifiedFilesystemService.instance().resolve_path()` to canonicalize the path, expand shortcuts (`desktop`, `downloads`, etc.), and reject remote UNC paths (`\\server\share`) and device namespaces (`\\?\`, `\??\`).
2. **Existence & Accessibility:** Verify that the directory or file actually exists and is readable before dispatching.
3. **Directory vs File Selection:**
   - For directories: Launch Explorer focused on the directory (`explorer.exe <dir>`).
   - For files or `select_file=True`: Launch Explorer highlighting the file (`explorer.exe /select,"<file>"`).
4. **Process Safety:**
   - Full path resolution for `explorer.exe` (`C:\Windows\explorer.exe` or via `SystemRoot`).
   - Argument array invocation (`subprocess.Popen([explorer_exe, ...])`) with **`shell=False`**.
   - No command string concatenation.
5. **Interactive Session Integrity:**
   - Check current session ID via `kernel32.ProcessIdToSessionId`.
   - Direct launch in Session > 0; authenticated IPC dispatch if invoked from Session 0.
6. **Tool & Agent Registration:**
   - Register `open_in_explorer` in `ToolRegistry` as a routine, non-destructive operation accessible to `filesystem`, `general`, `developer`, and `windows_runtime` agents.
   - Support `open_directory` and `open_folder` as seamless aliases.
7. **Fast-Path & Intent Routing:**
   - Update `MREClassifier` and `CentralOrchestrator._try_fast_path` to reliably match "Open <path> in Explorer", "Show this directory in Explorer", "Show this file in Explorer", "Open <shortcut>".
