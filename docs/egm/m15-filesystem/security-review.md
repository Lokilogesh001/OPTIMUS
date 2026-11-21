# OPTIMUS Milestone 15 — Security Review & Boundary Analysis

## 1. Security Architecture & Threat Model

Milestone 15 replaces ad-hoc, inconsistent application-level allowlists with a defense-in-depth, centralized filesystem security service (`UnifiedFilesystemService`).

### 1.1 Threat Matrix & Mitigation

| Threat Vector | Attack Mechanism | Pre-M15 Vulnerability | M15 Security Control |
|---|---|---|---|
| **NTLM Credential Theft via UNC Path** | Outbound SMB negotiation (`\\evil-host\share`) triggered by `Path.resolve()` leaks NetNTLMv2 hashes. | Not checked; vulnerable to SSRF/Hash theft if agent asked to read UNC path. | Strict regex regex check in `resolve_path()` blocks all `\\` and `//` prefixes before calling `resolve()`. |
| **Win32 Device Namespace Traversal** | `\\?\GLOBALROOT\...` or `\\.\COM1` bypasses standard path length and canonical checks. | Potential sandbox escape. | Explicit rejection of `\\?\`, `\\.\`, and `\??\` device prefixes. |
| **Sensitive Credential Exposure** | Agent reading SSH private keys, AWS tokens, browser sqlite cookies, or `.env` files. | Allowed if within user profile folder because `_SAFE_ROOTS` was `Path.home()`. | Dedicated blocklist in `_PROTECTED_CREDENTIAL_PATTERNS` requiring explicit, tamper-proof approval token. |
| **Accidental System Destruction** | Agent deleting or overwriting system libraries under `C:\Windows` or `Program Files`. | Blocked solely if outside user profile, but no protection if run in root contexts. | Mandatory approval gate in `_PROTECTED_SYSTEM_DIRECTORIES` for any mutating action (`write`, `delete`, `move`). |
| **Developer Workspace Breakout** | Code-writing subagents modifying files across the operating system or other projects. | No agent-specific distinction; all had identical access. | Contextual `workspace_root` confinement: Developer subagents are locked to project directories unless explicitly cleared. |
| **Data Loss via Inadvertent Overwrite** | Overwriting existing non-empty user files during refactors or code generation. | No automatic backup; unrecoverable loss. | Pre-write atomic backup snapshotting with SHA-256 integrity hashing into `.optimus/backups/`. |
| **Denial of Service via Junction Cycles** | Recursive NTFS junctions or symlinks triggering infinite search loops. | Search tool hangs or crashes with stack overflow. | Canonical real-path cycle detection (`visited_real_paths`) and strict `max_depth = 12` bounding. |
| **Crash on Broken Windows Junctions** | Windows 11 legacy junctions (e.g. `My Pictures` in OneDrive) throwing `WinError 2` during `.stat()`. | Crashes entire directory listing. | Non-dereferencing enumeration (`os.scandir(follow_symlinks=False)`) with junction exception trapping. |

---

## 2. Windows Invariants & Least Privilege
- **NTFS ACL Integrity:** `UnifiedFilesystemService` executes in the security context of the current Windows user token. It does not tamper with, disable, or bypass Windows NTFS Access Control Lists.
- **UAC Boundaries:** Operations requiring Administrator elevation are not silently escalated; they fail cleanly with `ACCESS_DENIED: [WinError 5] Operating system access denied`.
- **Classification Separation:** Error messages clearly separate host operating system access denials (`ACCESS_DENIED`) from OPTIMUS governance policy denials (`POLICY_DENIED`).
