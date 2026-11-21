# M15 — Stage 3: Architecture & Security Reconciliation Report

**Author:** Principal Windows Systems Engineer & Application Security Architect  
**Subsystem:** OPTIMUS Filesystem Authorization & Execution Subsystem (M15)  
**Date:** 2026-11-21  
**Status:** **RECONCILED — READY FOR IMPLEMENTATION**  

---

## 1. RECONCILIATION OVERVIEW

The Independent Goldfish Reviewer issued an **APPROVE_WITH_CONCERNS** verdict, validating the root-cause diagnosis and raising six specific security and architectural findings.

This reconciliation document records the concrete technical resolution and architectural contract for each finding. All resolutions are codified in the updated Technical Specification (`docs/egm/m15-filesystem/technical-specification.md`).

---

## 2. RECONCILIATION OF INDIVIDUAL FINDINGS

### Finding 1: Sensitive User Credential & Token Stores Left Unprotected [BLOCKER]
- **Goldfish Concern:** Classifying all reads/searches in user directories as "Routine (Auto)" leaves high-value credential stores (`.ssh/id_rsa`, `.aws/credentials`, Chrome/Firefox profile databases, `.env`, `.git-credentials`) vulnerable to prompt injection or rogue agent exfiltration.
- **Elephant Resolution:**
  - `UnifiedFilesystemService` establishes an explicit, immutable security pattern blacklist:
    ```python
    _PROTECTED_CREDENTIAL_PATTERNS = [
        re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\\.(ssh|aws|azure|kube)(\\.*)?$", re.IGNORECASE),
        re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\AppData\\Local\\(Google\\Chrome|Microsoft\\Edge)\\User Data(\\.*)?$", re.IGNORECASE),
        re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles(\\.*)?$", re.IGNORECASE),
        re.compile(r"^[a-zA-Z]:\\Users\\[^\\]+\\AppData\\Roaming\\Microsoft\\Protect(\\.*)?$", re.IGNORECASE),
        re.compile(r".*[\/\\](\.env|\.git-credentials|\.netrc)$", re.IGNORECASE),
    ]
    ```
  - **Policy Enforcement:** Any operation (including read, search, copy, move) targeting paths matching `_PROTECTED_CREDENTIAL_PATTERNS` **CANNOT be routine**. It is classified as `MANDATORY_OPERATOR_APPROVAL` requiring a valid `PolicyEngine` approval token. Access without an approval token fails closed with:
    `APPROVAL_REQUIRED: SENSITIVE_CREDENTIAL_STORE: Access to credential repository '...' requires explicit operator approval.`

---

### Finding 2: UNC Path Injection & NetNTLM Hash Leakage Risk [MAJOR]
- **Goldfish Concern:** `Path(raw).resolve()` does not strip remote UNC paths (e.g. `\\attacker.com\share`). On Windows, calling `stat()` or opening a UNC path triggers an automatic outbound SMB handshake, leaking the user's NetNTLM v2 hash over the network and hanging the process if the host is offline.
- **Elephant Resolution:**
  - The Path Resolver in `UnifiedFilesystemService` enforces strict rejection of all remote UNC network paths, raw device namespaces, and drive-relative ambiguities:
    ```python
    clean = raw_path.strip().strip('"').strip("'")
    if clean.startswith(("\\\\", "//")):
        raise PermissionError(f"UNC_VIOLATION: Remote UNC network paths are prohibited for security: '{raw_path}'")
    if re.match(r"^(\\\\(\.|\?)|\\\\\?\\)", clean):
        raise PermissionError(f"NAMESPACE_VIOLATION: Device namespace paths are prohibited: '{raw_path}'")
    # Reject drive-relative paths like 'C:folder' (missing slash after colon)
    if re.match(r"^[a-zA-Z]:[^\\]", clean):
        raise PermissionError(f"DRIVE_RELATIVE_VIOLATION: Drive-relative paths are ambiguous and prohibited: '{raw_path}'")
    ```
  - Only paths that resolve to a valid local drive letter prefix (`^[a-zA-Z]:\\`) on a mounted drive are permitted.

---

### Finding 3: Destructive Overwrite / Truncation in "Routine Write" Tier [MAJOR]
- **Goldfish Concern:** Classifying `write_file` on existing files as routine allows silent file truncation or replacement of critical source code without approval.
- **Elephant Resolution:**
  - `UnifiedFilesystemService.write_file` distinguishes between creating a new file vs. overwriting an existing non-empty file.
  - When overwriting an existing file:
    1. If the existing file size $> 0$, the service automatically creates an atomic, timestamped backup snapshot in `.optimus/backups/<relative_or_hashed_path>_<timestamp>.bak` prior to writing.
    2. The backup path and SHA256 digest are attached to the `ToolResult.evidence` ledger.
    3. If the overwrite target is a critical configuration file (`.json`, `.yaml`, `.ini`, `.toml` in root/config directories), it additionally requires operator approval.

---

### Finding 4: Recursive Symlink / Junction Cycles in `search_files` [MAJOR]
- **Goldfish Concern:** Windows user profiles contain directory junctions that point back to parent trees (e.g., `Application Data` $\to$ `AppData\Local`), causing infinite loops, stack overflow, or `WinError 206` (path too long).
- **Elephant Resolution:**
  - `UnifiedFilesystemService.search_files` adopts cycle-safe, non-recursive junction traversal:
    1. Sets `follow_symlinks=False` on directory iterators (`os.scandir`).
    2. Maintains a `visited_real_paths: set[str]` tracking the canonical real path of any directory prior to descending.
    3. Imposes a strict maximum recursion depth (`max_depth = 12`).
    4. Catches `OSError` / `WinError 206` on path expansion without aborting the search.

---

### Finding 5: Developer Agent Workspace Confinement Regression [MAJOR]
- **Goldfish Concern:** Unconditionally granting cross-drive write access across all tools would eliminate the workspace guardrails of `DeveloperAgent`, allowing it to mutate files outside its assigned repository.
- **Elephant Resolution:**
  - `UnifiedFilesystemService` enforces **Contextual Confinement Scopes**:
    ```python
    def check_access(self, target: Path, operation: str, context: dict | None = None) -> tuple[bool, str]:
        ctx = context or {}
        # Confinement check for workspace-scoped callers
        workspace_root = ctx.get("workspace_root")
        is_mutating = operation in ("write", "create_file", "create_folder", "delete", "move")
        if workspace_root and is_mutating and not ctx.get("cross_workspace_mutations"):
            ws_path = Path(workspace_root).resolve()
            if target != ws_path and ws_path not in target.parents:
                return False, f"WORKSPACE_CONFINEMENT_VIOLATION: Mutating operation '{operation}' on '{target}' escapes assigned workspace root '{ws_path}'"
    ```
  - Read/list operations may inspect other drives and reference files, but developer mutations remain strictly confined to `workspace_root`.

---

### Finding 6: Handling Broken Junctions in `list_directory` [NIT]
- **Goldfish Concern:** Calling `.stat()` or `.is_dir()` on broken junctions like `Documents\My Pictures` crashes directory listing with `[WinError 2]`.
- **Elephant Resolution:**
  - `list_directory` uses `os.scandir()` with `follow_symlinks=False`.
  - For each `DirEntry`:
    ```python
    try:
        is_link = entry.is_symlink()
        is_d = entry.is_dir(follow_symlinks=False)
        st = entry.stat(follow_symlinks=False)
        size = st.st_size
    except OSError:
        # Broken junction or inaccessible symlink
        entries.append(f"📁 [junction] {entry.name}/")
        continue
    ```
  - This eliminates unhandled `FileNotFoundError` / `PermissionError` and enables smooth directory listing of `C:\Users\<user>\Documents`.

---

## 3. RECONCILIATION MATRIX

| Finding ID | Severity | Status | Reconciled Architectural Mechanism |
|:---|:---:|:---:|:---|
| **Finding 1** | Blocker | **RESOLVED** | `_PROTECTED_CREDENTIAL_PATTERNS` approval token enforcement |
| **Finding 2** | Major | **RESOLVED** | Strict rejection of `\\` UNC paths and namespace prefixes |
| **Finding 3** | Major | **RESOLVED** | Pre-write atomic backup snapshotting in `.optimus/backups/` |
| **Finding 4** | Major | **RESOLVED** | Cycle detection (`visited_real_paths`), `follow_symlinks=False`, `max_depth=12` |
| **Finding 5** | Major | **RESOLVED** | Contextual write confinement honoring `context["workspace_root"]` |
| **Finding 6** | Nit | **RESOLVED** | `os.scandir(follow_symlinks=False)` with junction exception protection |
