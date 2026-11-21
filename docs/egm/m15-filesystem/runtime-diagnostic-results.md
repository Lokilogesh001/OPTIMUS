# M15 — Runtime Diagnostic Results: Controlled Filesystem Operations

**Author:** Principal Windows Systems Engineer & Application Security Architect  
**Subsystem:** OPTIMUS Filesystem Authorization & Diagnostic Suite  
**Date:** 2026-11-21  
**Environment:** Windows 11 Build 26300, Python 3.13.16 x64, User `loges` (SID: `S-1-5-21-4017490004-1364888395-2216946005-1001`)  

---

## 1. CONTROLLED TEST MATRIX

Ten test locations were evaluated using non-destructive, read-only operations across three parallel execution layers:
1. **Layer 1: Native Windows OS Execution (`os.listdir` / `os.stat`):** Direct Python call to underlying Win32 filesystem API.
2. **Layer 2: `actions.file_controller.file_controller`:** Tool invoked by desktop and voice pipelines.
3. **Layer 3: `core.orchestrator.tools.ToolRegistry` (`list_directory`):** Tool invoked by `CentralOrchestrator` subagents under default context (`allowed_roots = [Path.cwd()]`).
4. **Layer 4: `core.windows_runtime.policy.WindowsRuntimePolicy`:** Policy evaluation by Windows Privileged Runtime.
5. **Reference Layer: Windows File Explorer:** Operating system user session GUI reference.

---

## 2. DETAILED TEST OBSERVATION LEDGER

### Test 1: `C:\Windows`
- **Operation:** `list_dir`
- **Canonical Path:** `C:\Windows`
- **Native OS Result:** **SUCCESS** (`125` entries enumerated, e.g. `appcompat`, `apppatch`, `AppReadiness`). Win32 Error: `None`.
- **`actions.file_controller` Result:** **FAILED (Application Denied)**
  - *Output:* `"Access denied: C:\Windows"`
  - *Mechanism:* Intercepted by `_is_safe_path()` (`C:\Windows` is outside `Path.home()`).
- **`core.orchestrator.tools` Result:** **FAILED (Approval Gated)**
  - *Status:* `unverified`
  - *Output:* `APPROVAL_REQUIRED: PROTECTED_PATH_RESTRICTION: Operation on protected Windows system path 'C:\Windows' requires explicit approval.`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized (`is_allowed=True`, `requires_approval=False`).
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** `APPLICATION_ALLOWLIST_DENIED` (in `file_controller`) / `PROTECTED_PATH_GATED` (in `orchestrator`).

---

### Test 2: `C:\Users`
- **Operation:** `list_dir`
- **Canonical Path:** `C:\Users`
- **Native OS Result:** **SUCCESS** (`8` entries enumerated, e.g. `All Users`, `Default`, `loges`). Win32 Error: `None`.
- **`actions.file_controller` Result:** **FAILED (Application Denied)**
  - *Output:* `"Access denied: C:\Users"`
  - *Mechanism:* Intercepted by `_is_safe_path()` (`C:\Users` is outside `Path.home()`).
- **`core.orchestrator.tools` Result:** **FAILED (Workspace Denied)**
  - *Status:* `failed`
  - *Output:* `"List error: PATH_VIOLATION: Access denied: path 'C:\Users' is outside permitted directories (allowed roots: ['D:\project\srrr\Mark-LVII-main'])"`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized (`is_allowed=True`, `requires_approval=False`).
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** `APPLICATION_ALLOWLIST_DENIED`.

---

### Test 3: `C:\Program Files`
- **Operation:** `list_dir`
- **Canonical Path:** `C:\Program Files`
- **Native OS Result:** **SUCCESS** (`48` entries enumerated, e.g. `Adobe`, `Application Verifier`, `ASUS`). Win32 Error: `None`.
- **`actions.file_controller` Result:** **FAILED (Application Denied)**
  - *Output:* `"Access denied: C:\Program Files"`
  - *Mechanism:* Intercepted by `_is_safe_path()`.
- **`core.orchestrator.tools` Result:** **FAILED (Approval Gated)**
  - *Status:* `unverified`
  - *Output:* `APPROVAL_REQUIRED: PROTECTED_PATH_RESTRICTION: Operation on protected Windows system path 'C:\Program Files' requires explicit approval.`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized (`is_allowed=True`, `requires_approval=False`).
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** `APPLICATION_ALLOWLIST_DENIED` / `PROTECTED_PATH_GATED`.

---

### Test 4: User Downloads (`C:\Users\loges\Downloads`)
- **Operation:** `list_dir`
- **Canonical Path:** `C:\Users\loges\Downloads`
- **Native OS Result:** **SUCCESS** (`114` items enumerated). Win32 Error: `None`.
- **`actions.file_controller` Result:** **SUCCESS**
  - *Output:* `Contents of Downloads/ (114 items): 📄 1.jpeg (44.5 KB)...`
  - *Mechanism:* Inside `_SAFE_ROOTS` (`C:\Users\loges`). `_is_safe_path()` evaluates to `True`.
- **`core.orchestrator.tools` Result:** **FAILED (Workspace Denied)**
  - *Status:* `failed`
  - *Output:* `"List error: PATH_VIOLATION: Access denied: path 'C:\Users\loges\Downloads' is outside permitted directories (allowed roots: ['D:\project\srrr\Mark-LVII-main'])"`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized (`is_allowed=True`, `requires_approval=False`).
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** `WORKSPACE_RESTRICTION` in orchestrator subtasks; Permitted in `file_controller`.

---

### Test 5: User Desktop (`C:\Users\loges\Desktop`)
- **Operation:** `list_dir`
- **Canonical Path:** `C:\Users\loges\Desktop`
- **Native OS Result:** **SUCCESS** (`6` items enumerated, e.g. `BrahmaProjects`, `ProcessAudit`). Win32 Error: `None`.
- **`actions.file_controller` Result:** **SUCCESS**
  - *Output:* `Contents of Desktop/ (6 items): 📁 BrahmaProjects/...`
  - *Mechanism:* Inside `_SAFE_ROOTS` (`C:\Users\loges`). `_is_safe_path()` evaluates to `True`.
- **`core.orchestrator.tools` Result:** **FAILED (Workspace Denied)**
  - *Status:* `failed`
  - *Output:* `"List error: PATH_VIOLATION: Access denied: path 'C:\Users\loges\Desktop' is outside permitted directories (allowed roots: ['D:\project\srrr\Mark-LVII-main'])"`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized (`is_allowed=True`, `requires_approval=False`).
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** `WORKSPACE_RESTRICTION` in orchestrator subtasks; Permitted in `file_controller`.

---

### Test 6: User Documents (`C:\Users\loges\Documents`)
- **Operation:** `list_dir`
- **Canonical Path:** `C:\Users\loges\Documents`
- **Native OS Result:** **SUCCESS** (`7` items enumerated, e.g. `.connect_pic`, `ByTune-Analysis`, `Codex`). Win32 Error: `None`.
- **`actions.file_controller` Result:** **FAILED (Unhandled Junction Stat Exception)**
  - *Output:* `Error listing files: [WinError 2] The system cannot find the file specified: 'C:\Users\loges\Documents\My Pictures'`
  - *Mechanism:* `_is_safe_path()` passed, but `item.stat()` failed on broken Windows junction `My Pictures`.
- **`core.orchestrator.tools` Result:** **FAILED (Workspace Denied)**
  - *Status:* `failed`
  - *Output:* `"List error: PATH_VIOLATION: Access denied: path 'C:\Users\loges\Documents' is outside permitted directories"`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized (`is_allowed=True`, `requires_approval=False`).
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** `JUNCTION_TRAVERSAL_EXCEPTION` in `file_controller` / `WORKSPACE_RESTRICTION` in orchestrator.

---

### Test 7: User Music (`C:\Users\loges\Music`)
- **Operation:** `list_dir`
- **Canonical Path:** `C:\Users\loges\Music`
- **Native OS Result:** **SUCCESS** (`1` item: `desktop.ini`). Win32 Error: `None`.
- **`actions.file_controller` Result:** **SUCCESS**
  - *Output:* `Contents of Music/ (1 items): 📄 desktop.ini (504.0 B)`
  - *Mechanism:* Inside `_SAFE_ROOTS` (`C:\Users\loges`). `_is_safe_path()` evaluates to `True`.
- **`core.orchestrator.tools` Result:** **FAILED (Workspace Denied)**
  - *Status:* `failed`
  - *Output:* `"List error: PATH_VIOLATION: Access denied: path 'C:\Users\loges\Music' is outside permitted directories"`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized.
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** Permitted in `file_controller` / `WORKSPACE_RESTRICTION` in orchestrator.

---

### Test 8: Known Accessible D: Directory (`D:\project`)
- **Operation:** `list_dir`
- **Canonical Path:** `D:\project`
- **Native OS Result:** **SUCCESS** (`4` items: `AGRIMIND`, `optimus`, `srrr`, etc.). Win32 Error: `None`.
- **`actions.file_controller` Result:** **FAILED (Application Denied)**
  - *Output:* `"Access denied: D:\project"`
  - *Mechanism:* Intercepted by `_is_safe_path()`. Drive `D:` is NOT relative to `Path.home()` (`C:\Users\loges`).
- **`core.orchestrator.tools` Result:** **FAILED (Workspace Denied)**
  - *Status:* `failed`
  - *Output:* `"List error: PATH_VIOLATION: Access denied: path 'D:\project' is outside permitted directories (allowed roots: ['D:\project\srrr\Mark-LVII-main'])"`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized.
- **File Explorer Reference:** Displays full directory contents.
- **Classification:** `APPLICATION_ALLOWLIST_DENIED` across both tools.

---

### Test 9: Known Inaccessible / Protected D: Directory (`D:\System Volume Information`)
- **Operation:** `list_dir`
- **Canonical Path:** `D:\System Volume Information`
- **Native OS Result:** **FAILED (Legitimate OS NTFS Denied)**
  - *Exception:* `PermissionError: [WinError 5] Access is denied: 'D:\System Volume Information'`
  - *Win32 Error Code:* `5` (`ERROR_ACCESS_DENIED`)
- **`actions.file_controller` Result:** **FAILED (Application Denied)**
  - *Output:* `"Access denied: D:\System Volume Information"` (Intercepted before OS check).
- **`core.orchestrator.tools` Result:** **FAILED (Protected Path Gated)**
  - *Status:* `unverified`
  - *Output:* `APPROVAL_REQUIRED: PROTECTED_PATH_RESTRICTION: Operation on protected Windows system path 'D:\System Volume Information' requires explicit approval.`
- **File Explorer Reference:** Prompts with UAC / Windows Security: *"You don't currently have permission to access this folder."*
- **Classification:** **`LEGITIMATE_OS_SECURITY_DENIAL`** (Win32 Error 5).

---

### Test 10: D: Drive Root (`D:\`)
- **Operation:** `metadata` / `stat`
- **Canonical Path:** `D:\`
- **Native OS Result:** **SUCCESS** (Directory mode `0o40777`, size `4096` bytes). Win32 Error: `None`.
- **`actions.file_controller` Result:** **FAILED (Application Denied)**
  - *Output:* `"Access denied: D:\"` (Intercepted by `_is_safe_path()`).
- **`core.orchestrator.tools` Result:** **FAILED (Workspace Denied)**
  - *Status:* `failed`
  - *Output:* `"List error: PATH_VIOLATION: Access denied: path 'D:\' is outside permitted directories"`
- **`WindowsRuntimePolicy` Decision:** Routine read authorized.
- **File Explorer Reference:** Opens drive root immediately.
- **Classification:** `APPLICATION_ALLOWLIST_DENIED`.

---

## 3. SUMMARY COMPARISON MATRIX

| Location | Native OS | File Explorer | `file_controller` | `orchestrator.tools` | `WindowsRuntimePolicy` | Root Cause Stage |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| `C:\Windows` | ✅ OK | ✅ OK | ❌ Access denied | ⚠️ Approval Gated | ✅ Authorized | Application Allowlist (`_SAFE_ROOTS`) |
| `C:\Users` | ✅ OK | ✅ OK | ❌ Access denied | ❌ Path violation | ✅ Authorized | Application Allowlist (`_SAFE_ROOTS`) |
| `C:\Program Files` | ✅ OK | ✅ OK | ❌ Access denied | ⚠️ Approval Gated | ✅ Authorized | Application Allowlist (`_SAFE_ROOTS`) |
| `User Downloads` | ✅ OK | ✅ OK | ✅ OK | ❌ Path violation | ✅ Authorized | Orchestrator Workspace Root (`Path.cwd()`) |
| `User Desktop` | ✅ OK | ✅ OK | ✅ OK | ❌ Path violation | ✅ Authorized | Orchestrator Workspace Root (`Path.cwd()`) |
| `User Documents` | ✅ OK | ✅ OK | ❌ Junction error | ❌ Path violation | ✅ Authorized | Junction Broken Stat / Workspace Root |
| `User Music` | ✅ OK | ✅ OK | ✅ OK | ❌ Path violation | ✅ Authorized | Orchestrator Workspace Root (`Path.cwd()`) |
| `D:\project` | ✅ OK | ✅ OK | ❌ Access denied | ❌ Path violation | ✅ Authorized | Application Allowlist (`_SAFE_ROOTS`) |
| `D:\System Vol` | ❌ OS Error 5 | ❌ Denied | ❌ Access denied | ⚠️ Approval Gated | ✅ Authorized | **True Win32 Error 5 (NTFS ACL)** |
| `D:\` Root | ✅ OK | ✅ OK | ❌ Access denied | ❌ Path violation | ✅ Authorized | Application Allowlist (`_SAFE_ROOTS`) |
