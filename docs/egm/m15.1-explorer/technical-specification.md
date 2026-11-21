# OPTIMUS Milestone 15.1 — Technical Specification: Native Windows Explorer Launch Capability

## 1. Architectural Scope

This specification defines the unified design for native Windows File Explorer launching across OPTIMUS, bridging:
1. `core/filesystem/service.py` (`UnifiedFilesystemService.open_in_explorer`)
2. `core/windows_runtime/broker.py` & `core/windows_runtime/client.py` (Session-aware execution)
3. `core/orchestrator/tools.py` & `core/sandbox/tools_execution.py` (Tool definition and alias routing)
4. `core/orchestrator/agents.py` (Agent capability profiles: `filesystem`, `general`, `developer`)
5. `actions/file_controller.py` (Action dispatch for `open`, `open_folder`, `open_in_explorer`)
6. `core/orchestrator/mre.py` & `core/orchestrator/orchestrator.py` (Intent parsing and fast-path execution)

---

## 2. Component Design & Contracts

### 2.1 `UnifiedFilesystemService.open_in_explorer(path: str | Path, select_file: bool = False, context: dict[str, Any] | None = None) -> ToolResult`
- **Method Signature:**
  ```python
  def open_in_explorer(
      self,
      path: str | Path,
      select_file: bool = False,
      context: dict[str, Any] | None = None,
  ) -> ToolResult
  ```
- **Execution Flow:**
  1. `target = self.resolve_path(path)`:
     - Normalizes shortcuts (`downloads`, `desktop`, `documents`, `workspace`, etc.).
     - Blocks remote UNC paths (`\\server\share`) and device namespaces (`\\?\`, `\??\`).
     - Resolves canonical `Path`.
  2. Existence Check:
     - If not `target.exists()`: returns `ToolResult(status="failed", error=f"PATH_NOT_FOUND: Target '{target}' does not exist.")`.
  3. Security / Policy Check:
     - `auth, req_appr, reason = self.check_access(target, "open_in_explorer", context=context)`
     - Opening in Explorer is classified as a routine read-only inspection. Routine access to non-credential paths is authorized without friction. Protected credential stores (`.ssh`, `.aws`, `.env`) require approval tokens.
  4. Interactive Session Detection:
     - Query current session ID via `kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id))`.
     - If `session_id.value == 0` (Windows Service in Session 0):
       - Route request to `core.windows_runtime.client.get_windows_runtime_client()` or user-session broker to ensure Explorer opens on the interactive user's desktop.
     - If `session_id.value != 0` (Interactive Session):
       - Resolve full path to `explorer.exe` via `os.environ.get("SystemRoot", r"C:\Windows") + r"\explorer.exe"`.
       - If `select_file` or `target.is_file()`:
         `cmd = [explorer_exe, f"/select,{target}"]`
       - Else:
         `cmd = [explorer_exe, str(target)]`
       - Launch via `subprocess.Popen(cmd, shell=False)` without blocking.
  5. Return `ToolResult(status="done", output=f"Opened '{target}' in Windows File Explorer.", evidence={"path": str(target), "is_file": target.is_file(), "select_file": select_file, "session_id": session_id.value, "verified": True})`.

### 2.2 ToolRegistry Registration & Capability Tiers
- Tool Name: `open_in_explorer`
- Aliases: `open_directory`, `open_folder` (both routed to `UnifiedFilesystemService.instance().open_in_explorer`)
- Parameters Schema:
  ```json
  {
    "type": "OBJECT",
    "properties": {
      "path": {"type": "STRING", "description": "Folder or file path to open or highlight in Windows File Explorer"},
      "select_file": {"type": "BOOLEAN", "description": "If true, highlights the specified file in Explorer"}
    },
    "required": ["path"]
  }
  ```
- **Capability Tier:** `CapabilityTier.READ_ONLY` (opening an existing directory/file in Explorer is non-mutating and exploratory).
- **Agent Allowances (`core/orchestrator/agents.py`):**
  - Add `"open_in_explorer"` to `filesystem`, `general`, `developer`, `researcher`, and `windows_runtime` agents.

### 2.3 `actions/file_controller.py` Integration
- Add handler `open_in_explorer(path: str, name: str = "", select_file: bool = False) -> str`:
  - Dispatches to `UnifiedFilesystemService.instance().open_in_explorer()`.
  - Supports `parameters={"action": "open" | "open_folder" | "open_in_explorer"}`.

### 2.4 Intent Matching & Fast-Path (`core/orchestrator/mre.py` & `orchestrator.py`)
- Regex in `MREClassifier` to handle:
  - `"open D:\project in Explorer"`
  - `"show this directory in Explorer"`
  - `"open this folder"`
  - `"show this file in Explorer"`
  - `"open D:\project"`
  - `"open downloads in explorer"`
- Extraction correctly strips trailing `"in (Windows )?(File )?Explorer"`, handles drive letters, quotes, shortcuts, and `/select` intent.
