# OPTIMUS Security Architecture & Threat Model

## 1. Security Principles

OPTIMUS implements defense-in-depth across the agent execution lifecycle:

1. **Zero-Trust Tool Execution**: Models and autonomous agents are untrusted entities. An LLM cannot execute tools or modify system state directly; every action must pass through the `ToolRegistry` and capability validation checks.
2. **Strict Boundary Confinement**: Filesystem tools enforce canonical path resolution preventing path traversal (`../`) and boundary escapes beyond declared root directories.
3. **Anti-Peer-to-Peer Rule**: Agents cannot delegate tasks or tool invocations to other agents. All coordination is strictly hierarchical via the `CentralOrchestrator`.
4. **Non-Overridable System Policies**: LLM prompts, tool inputs, or external files cannot override system capability restrictions or bypass human approval gates.
5. **Fail-Closed Design**: Any unhandled error, unknown tool, missing permission, or expired approval immediately terminates execution in a failed/paused state.

---

## 2. Capability-Based Access Control (CBAC)

### Capability Tiers

Tools are categorized into six strictly ordered operational risk tiers:

| Tier | Rank | Description | Permitted Agents |
|---|---|---|---|
| `READ_ONLY` | 1 | Non-mutating read operations (read files, inspect ast, calculate, read system telemetry) | All agents |
| `WORKSPACE_WRITE` | 2 | Scoped filesystem modifications inside authorized workspace directories | `filesystem_agent`, `code_intelligence`, `data_intelligence` |
| `NETWORK_EXTERNAL` | 3 | External read-only network access (web search, citation extraction) | `researcher` |
| `EXECUTION` | 4 | Subprocess invocation, test runner, script evaluation inside sandbox | `code_intelligence`, `executor` |
| `SYSTEM_MUTATING` | 5 | Process termination, environment variable modifications | `system_diagnosis`, `executor` |
| `SYSTEM_DESTRUCTIVE` | 5 | Irreversible actions (disk formatting, database dropping, bulk deletion) | **Blocked without human approval** |

### Policy Enforcement Mechanics
- When an agent requests a tool invocation, `ToolRegistry.execute()` evaluates:
  1. Tool existence in registry.
  2. Inclusion in the caller agent's `allowed_tools` allowlist.
  3. Tier ranking: `tool_tier_rank <= max_allowed_tier_rank`.
  4. Input parameter schema conformity.
- If any check fails, execution aborts with a structured failure (`TOOL_NOT_PERMITTED` or `TIER_VIOLATION`) and an audit event is emitted to `EventBus`.

---

## 3. Filesystem Sandboxing & Boundary Confinement

- **Path Resolution**: `_resolve_safe_path(raw_path, allowed_roots)` canonicalizes paths using `Path.resolve()`.
- **Traversal Prevention**: Relative paths containing traversal sequences (`../../`) and absolute paths outside the `allowed_roots` envelope are strictly rejected with a `PermissionError` (`PATH_VIOLATION`).
- **Temporary Workspaces**: Multi-step file manipulation tasks execute within isolated temporary scratchpads before verified deliverables are promoted to persistent semantic storage.

---

## 4. Secret Redaction & Credential Quarantine

The `sanitize_tool_output()` engine intercepts and strips sensitive tokens before tool results are persisted or forwarded to UI and LLM contexts:
- **API Keys**: OpenAI (`sk-...`), GitHub (`ghp_...`), Anthropic, Google API keys (`[REDACTED_API_KEY]`).
- **Bearer Tokens**: JWT and Authorization Bearer headers (`[REDACTED_TOKEN]`).
- **Passwords & Secrets**: JSON and configuration fields matching `password`, `secret`, or `api_key` (`[REDACTED]`).
- **Output Truncation**: Tool outputs exceeding 50,000 characters are safely truncated to prevent memory exhaustion and context window overflow attacks.

---

## 5. Human-in-the-Loop Approval Gates

1. **Enforcement**: Any workflow step or planning action flagged with `is_destructive=True` or `requires_approval=True` halts execution at state `WAITING_APPROVAL`.
2. **Anti-LLM Bypass**: Only explicit human operator invocation of `ApprovalManager.approve(approval_id, approver="admin")` can resolve a pending gate. Model output, tool scripts, or simulated inputs cannot approve a request.
3. **Timeout & Expiration**: Approval requests have explicit lifespans (default: 3600 seconds, customizable per step). Expired requests transition to `EXPIRED` status, permanently preventing subsequent approval.

---

## 6. Cryptographic Evidence Integrity

- **Evidence Ledger**: `EvidenceLedger` stores postcondition verification records with SHA-256 integrity digests computed across:
  - Input postcondition spec
  - Actual observed system outcome
  - Microsecond verification timestamp
  - Previous record digest (Merkle-style chaining)
- **Tamper Detection**: `verify_ledger_integrity()` sweeps the persistent ledger on startup to ensure historical records have not been modified or truncated.
- **Outcome Ledger Immutability**: `OutcomeStore` persists execution outcomes atomically. Past outcomes cannot be altered via external calls.

---

## 7. Dynamic Tool Revocation

- `ToolRegistry.unregister(tool_name)` / `ToolRegistry.revoke(tool_name)` allows runtime revocation of compromised or deprecated tools.
- Once revoked, subsequent invocation attempts immediately fail with `UNKNOWN_TOOL` without impacting other registered capabilities.
