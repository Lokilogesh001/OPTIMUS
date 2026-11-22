# Elephant Architecture Investigation: Google Workspace MCP & Native Windows System Monitor Integrations

**Milestone:** OPTIMUS Google Workspace & System Monitor Integration  
**Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Date:** 2026-11-22  
**Status:** In Progress (EGM Phase 1)  

---

## 1. Executive Summary & Objectives

The objective is to integrate two high-value capability sets into OPTIMUS's native architecture without duplicating existing subsystems, creating parallel orchestrators, or compromising security:

1. **Google Workspace MCP Integration:** Providing unified, secure Gmail and Google Calendar access via a managed local MCP server running over standard input/output (`stdio`), adhering to least-privilege OAuth scopes, read-only defaults, bounded timeouts, prompt-injection defanging, and strict routing through `ToolRegistry` and `PolicyEngine`.
2. **First-Party Native Windows System Monitor Adapter:** Providing comprehensive, real-time host hardware and process telemetry via `psutil` (CPU, RAM, disk, network, battery, top processes), `pynvml` (NVIDIA GPU utilization, VRAM, temperature, power, clocks), and optional `LibreHardwareMonitor` sensors, strictly enforced as read-only, non-destructive, with zero unnecessary background polling and seamless CPU-only fallback.
3. **Unified Integrations UI:** Providing a consolidated, uncluttered Control Center section displaying connection health, sensor telemetry, OAuth status, and permission boundaries.
4. **Empirical Performance Comparison:** Benchmarking M12 direct provider adapters against the Google Workspace MCP bridge to establish latency, memory, accuracy, and reliability metrics.

---

## 2. Upstream Google Workspace MCP Inspection & Pinned Release

### 2.1 Upstream Source & License Verification
- **Target Upstream:** `taylorwilsdon/google_workspace_mcp` (distributed on PyPI as `workspace-mcp`).
- **Official Repository:** `https://github.com/taylorwilsdon/google_workspace_mcp`
- **License:** **MIT License** (Per-file and repository root: permissive open-source, fully commercial and private use compatible).
- **Maintenance & Health:** Highly active, production-grade MCP implementation supporting Python 3.10+, Anthropic MCP specification 2.0+, and FastMCP 4.0+.
- **Selected & Pinned Release:** **`workspace-mcp==2.6.0`** (and compatible FastMCP stdio server architecture).

### 2.2 Upstream Transport & Security Model
- **Transport:** Local `stdio` transport. The MCP server process communicates strictly over standard input/output with the OPTIMUS host process. No open network ports, no local HTTP listener vulnerabilities, and no external daemon exposure.
- **Dependency Footprint:** Uses `google-api-python-client>=2.201.0`, `google-auth-oauthlib>=1.2.2`, `fastmcp>=4.1.2`, and `pydantic>=2.9.0`. All core dependencies are already present in OPTIMUS's virtual environment (`D:\project\optimus\.venv`).
- **OAuth Architecture:** Implements Google OAuth 2.0 PKCE desktop flow. Supports refresh token exchange and least-privilege scope configuration.
- **Bounded Tool Registration:** Exposes progressive tool tiers (`core`, `extended`, `complete`). For OPTIMUS, only the necessary Gmail and Google Calendar tools are mapped and exposed:
  - `gmail_list_messages` / `gmail_get_message` / `gmail_get_status`
  - `calendar_list_events` / `calendar_find_conflicts` / `calendar_get_status`
  - Mutating operations (`gmail_send_message`, `calendar_create_event`) are strictly disabled by default and require explicit operation-bound `ApprovalToken` challenge authorization through `PolicyEngine`.

---

## 3. Review of Existing OPTIMUS Subsystems

### 3.1 M12 Personal Intelligence (Gmail & Calendar)
- **Files:** `core/personal_intelligence/providers/gmail.py`, `core/personal_intelligence/providers/google_calendar.py`, `core/personal_intelligence/tools.py`.
- **Credential Storage:** Stores OAuth tokens in `CredentialVault` (`core/security/credentials.py`) under keys `gmail_{account_id}_token`, `gmail_{account_id}_refresh`, `gcal_{account_id}_token`, `gcal_{account_id}_refresh`, and client credentials under `google_oauth_client_id` and `google_oauth_client_secret`.
- **OAuth Scopes:** 
  - Read: `https://www.googleapis.com/auth/gmail.readonly`, `https://www.googleapis.com/auth/calendar.readonly`.
  - Mutate (Privileged): `https://www.googleapis.com/auth/gmail.send`, `https://www.googleapis.com/auth/calendar.events`.
- **Quarantine:** All retrieved external message bodies are defanged using `core.outcome.untrusted` markers to prevent indirect prompt injection.
- **Compatibility & Migration Strategy:** Retain M12 direct adapters as primary fallback and benchmark baseline. Bridge M12 credential storage to the Workspace MCP server so users do not need to re-authenticate if credentials already exist.

### 3.2 M20 Plugin & Security Hardening
- **Files:** `core/plugin_loader.py`, `core/orchestrator/tools.py`, `core/security/policy_engine.py`.
- **AST Screening:** Prohibits executable top-level statements inside classes, dynamic decorators, and dangerous modules.
- **PolicyEngine Gate:** All operations must evaluate `PolicyEngine.evaluate()`. Fails closed on any error.
- **Timeout Budget:** Thread pool execution budgets strictly enforce non-blocking unjoined cancellation (`shutdown(wait=False, cancel_futures=True)`) on timeout to prevent GUI freezes.
- **Secret Redaction:** `sanitize_tool_output()` redacts Gemini keys (`AIzaSy...`), OpenAI keys (`sk-...`), GitHub tokens, and bearer tokens from all returned tool results.

### 3.3 Existing System Monitoring
- **Files:** `core/runtime/hardware.py`, `core/sandbox/tools_system.py`, `actions/system_monitor.py`.
- **Current State:**
  - `core/runtime/hardware.py`: `HardwareDetector` collects static hardware profiles (CPU model, physical/logical cores, RAM total/available, GPU model, CUDA availability).
  - `core/sandbox/tools_system.py`: Basic `system_info`, `cpu_status`, `memory_status`, `disk_status`, `gpu_status`, and `process_diagnostics`.
  - `actions/system_monitor.py`: Background monitoring script with ctypes NVML fallback.
- **Gaps to Address:**
  - No unified first-party telemetry adapter that encapsulates `psutil`, `pynvml`, and `LibreHardwareMonitor` with TTL-based caching.
  - GPU telemetry does not expose comprehensive VRAM breakdown, power draw, temperature, and fan speeds in a structured format.
  - Process diagnostics lacks sorting by both CPU and Memory, and process command-line collection is not guarded.
  - Missing battery, network I/O, and disk I/O metrics.

---

## 4. Architecture of the Native Windows System Monitor Adapter

The first-party adapter `core/system_monitor/adapter.py` will provide a unified interface:

```
+---------------------------------------------------------------------------------+
|                       WindowsSystemMonitorAdapter                               |
|                                                                                 |
|  +------------------+    +-----------------------+    +----------------------+  |
|  |  psutil Engine   |    |    pynvml Engine      |    | LibreHardwareMonitor |  |
|  | - CPU % (Total,  |    | - NVIDIA GPU Model    |    | - Advanced WMI       |  |
|  |   Per-Core)      |    | - VRAM Total/Used/Free|    |   Sensors (Optional) |  |
|  | - RAM & Virtual  |    | - GPU & Mem Util %    |    | - Motherboard Temps  |  |
|  | - Disk Space/IO  |    | - Temperature & Power |    | - Fan Speeds         |  |
|  | - Network IO     |    | - Graceful Fallback   |    | - Graceful Handling  |  |
|  | - Battery Status |    |   if no NVIDIA GPU    |    |   of 0x8004100e      |  |
|  | - Top Processes  |    |                       |    |   (Not Installed)    |  |
|  +------------------+    +-----------------------+    +----------------------+  |
|                                                                                 |
|  +---------------------------------------------------------------------------+  |
|  |                         TTL Sampling Cache (2.0s)                         |  |
|  +---------------------------------------------------------------------------+  |
+---------------------------------------------------------------------------------+
```

### Safety & Invariants
1. **Read-Only Invariant:** Zero process termination (`kill()`, `terminate()`), zero command-line collection (`p.cmdline()` avoided for privacy), zero system setting modification.
2. **No Excessive Background Polling:** Sampling occurs on-demand with a 2.0-second TTL cache to prevent CPU churn.
3. **Graceful Degradation:** If NVIDIA GPU is not present or NVML fails, `has_gpu` is set to `False` and CPU-only metrics are returned cleanly without crashing.
4. **LibreHardwareMonitor Fault Tolerance:** WMI namespace `root/LibreHardwareMonitor` is queried wrapped in try-except; COM error `0x8004100e` (Invalid Namespace) is treated as expected non-installed status and returns empty sensor list.

---

## 5. Architecture of the Managed Google Workspace MCP Bridge

```
[User Request] 
      │
      ▼
[CentralOrchestrator]
      │
      ▼
[ToolRegistry] ─────────► [PolicyEngine Evaluation] ──(Mutating?)──► [ApprovalToken Challenge]
      │ (Authorized)
      ▼
[WorkspaceMCPAdapter] (Managed Local Stdio Transport)
      │
      ├─► Stdio Process: `python -m core.workspace_mcp.server --transport stdio`
      │        │
      │        ├─► Google APIs (Gmail v1, Calendar v3)
      │        └─► OAuth Credential Storage (Keyring/CredentialVault)
      │
      ▼
[Untrusted Content Quarantine & Secret Redaction]
      │
      ▼
[Assistant Response]
```

### Security Invariants
1. **Default Mode:** `READ_ONLY`. Tools `get_gmail_status`, `list_gmail_messages`, `get_email_details`, `summarize_email`, `get_calendar_view`, `find_calendar_conflicts` execute without operator prompts.
2. **Mutating Gating:** `send_email`, `create_calendar_event`, `delete_calendar_event` require explicit single-use `ApprovalToken` tokens issued by the operator.
3. **Untrusted Data Marking:** Returned email bodies and calendar descriptions are tagged with `[UNTRUSTED_CONTENT]` markers so the LLM brain never executes directives inside user emails.
4. **Credential Isolation:** The MCP server receives OAuth credentials via environment variables or direct memory injection during startup. No tokens are written to disk in plain text or logged to stderr/stdout.

---

## 6. EGM Step 2 Plan: Independent Goldfish Design Review

To ensure zero cognitive bias, an independent Goldfish reviewer (`egm-design`) will be invoked with a cold context to review:
1. Threat model and attack surface of the local stdio MCP server.
2. Prompt injection quarantine across email and calendar payloads.
3. Least-privilege scope enforcement and token refresh race conditions.
4. Telemetry safety: prevention of denial-of-service via WMI or NVML sampling.
5. Migration safety: ensuring M12 remains intact and functional during and after integration.
