# Reconciled Architecture Specification: Google Workspace MCP & Native Windows System Monitor

**Milestone:** OPTIMUS Google Workspace & System Monitor Integration  
**Date:** 2026-11-22  
**Status:** Approved for Implementation (EGM Phase 3)  
**Baseline Review:** Independent Goldfish Audit (`docs/egm/workspace-sysmonitor/elephant-architecture-investigation.md`)  

---

## 1. Resolution of Independent Goldfish Findings

Every finding from the adversarial audit has been assigned an explicit, non-negotiable remediation contract:

| ID | Severity | Finding Summary | Reconciled Resolution |
| :--- | :--- | :--- | :--- |
| **SEC-01** | **CRITICAL** | Silent PolicyEngine bypass on mutating tool name disparity | Update `PRIVILEGED_APPROVAL_PATTERNS` in `core/security/policy_engine.py` to match `gmail_send_message`, `calendar_create_event`, and namespaced variants. Normalise all tool names in `WorkspaceMCPAdapter` to standard OPTIMUS schemas. |
| **SEC-02** | **HIGH** | Stdio subprocess stderr pipe deadlock & GUI freezes | Dedicated background thread continuously drains `stderr` into a bounded `collections.deque(maxlen=500)`. All JSON-RPC calls are bounded by strict 10.0s timeouts. |
| **SEC-03** | **HIGH** | Windows zombie subprocess leakage on parent crash | Assign child processes to a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Add `atexit` handlers and explicit termination in adapter lifecycle. |
| **SEC-04** | **MEDIUM** | Credential exposure via process environment blocks | Transmit OAuth tokens dynamically via standard input handshake during session initialization, avoiding ambient environment variable leaks. |
| **SEC-05** | **CRITICAL** | Missing defanging in Calendar event payloads | Enforce `core.outcome.untrusted(source="google_calendar", content=...)` across all calendar titles, descriptions, locations, and attendee notes before storage or LLM context ingestion. |
| **SEC-06** | **HIGH** | Discrepant quarantine tags and partial email wrapping | Standardise universally on `core.outcome.untrusted(source="gmail", content=...)` with authoritative `<<<UNTRUSTED>>>` markers across full message bodies, subjects, and headers. |
| **SEC-07** | **HIGH** | OAuth scope escalation and lack of progressive consent | Onboarding and default operation enforce strictly least-privilege read-only scopes (`gmail.readonly`, `calendar.readonly`). Mutating scopes (`gmail.send`, `calendar.events`) require explicit operator action and progressive consent. |
| **SEC-08** | **HIGH** | Cryptographic hash mismatch in PolicyEngine ApprovalTokens | Canonical tool arguments (`to`, `subject`, `body` for email; `title`, `start_time`, `end_time` for calendar) are preserved and validated by PolicyEngine before translation to upstream JSON-RPC structures. |
| **PERF-01** | **HIGH** | Synchronous WMI and CPU sampling blocking caller threads | `WindowsSystemMonitorAdapter` runs a background sampling daemon (2.0s refresh cycle). Tool callers retrieve cached metrics via `O(1)` memory lookups (0ms latency). WMI queries call `pythoncom.CoInitialize()`. Catch `0x8004100e` gracefully. |
| **SEC-09** | **MEDIUM** | Process command-line credential leakage | Strictly exclude `p.cmdline()` from process diagnostics. Run all process summaries through `sanitize_tool_output()`. |
| **REL-01** | **MEDIUM** | Permanent NVML disabling on transient driver TDR errors | Catch NVML lost / uninitialized errors, reset handles, and apply backoff retry (30s) instead of permanent disqualification. Return `has_gpu=False` gracefully. |
| **REL-02** | **HIGH** | Concurrent OAuth refresh token rotation race condition | OPTIMUS host process is the sole authority for token refresh under `oauth_refresh_lock`. Validated tokens are injected into MCP; MCP does not execute independent refresh flows. |
| **REL-03** | **MEDIUM** | ToolRegistry name collision during benchmarking | Register M12 and Workspace MCP side-by-side using `mcp_*` namespace or dynamic provider dispatch (`personal_intelligence.provider = "native" | "mcp"`), allowing simultaneous benchmarking. |
| **TEST-01** | **MEDIUM** | Offline test suite degradation via subprocess coupling | `WorkspaceMCPAdapter` implements hermetic mock transport when `OPTIMUS_MOCK_NETWORK=1`, returning deterministic fixtures without network or subprocess overhead. |

---

## 2. Component Architecture & File Layout

```
Mark-LVII-main/
├── core/
│   ├── system_monitor/
│   │   ├── __init__.py
│   │   ├── adapter.py              # First-party Windows System Monitor Adapter (psutil + pynvml + WMI)
│   │   ├── types.py                # Telemetry dataclasses (CpuVitals, MemoryVitals, GpuVitals, ProcessMetric)
│   │   └── tools.py                # Tool definitions for ToolRegistry dispatch
│   ├── workspace_mcp/
│   │   ├── __init__.py
│   │   ├── adapter.py              # Managed Stdio Workspace MCP Adapter with Job Object & async stderr
│   │   ├── client.py               # JSON-RPC 2.0 stdio client protocol engine
│   │   ├── server.py               # Local FastMCP / stdio Google Workspace server
│   │   ├── mock_transport.py       # Deterministic hermetic offline transport for test suites
│   │   └── tools.py                # Tool definitions for ToolRegistry dispatch
│   ├── personal_intelligence/
│   │   ├── providers/
│   │   │   └── google_calendar.py  # Hardened with core.outcome.untrusted (SEC-05)
│   │   └── tools.py                # Hardened calendar conflict and summarize tools
│   ├── security/
│   │   └── policy_engine.py        # Updated PRIVILEGED_APPROVAL_PATTERNS (SEC-01)
│   └── orchestrator/
│       ├── tools.py                # Registration of system monitor & workspace tools
│       ├── mre.py                  # Intent classification for all 7 acceptance queries
│       └── mre_types.py            # Expanded TaskCategory and OutcomeContract types
├── gui/
│   └── optimus/
│       ├── section_integrations.py # Unified Integrations UI (Gmail, Calendar, System, GPU, OAuth)
│       ├── control_center.py       # Registration of Integrations section
│       └── nav_bar.py              # Added Integrations navigation tab
└── tests/
    ├── test_system_monitor_adapter.py    # Unit & hardware tests (psutil, pynvml, WMI)
    ├── test_workspace_mcp_adapter.py     # MCP stdio client, server, mock transport, security
    ├── test_workspace_security_audit.py  # Prompt injection, least-privilege, and ApprovalToken tests
    ├── test_mre_acceptance_queries.py    # All 7 user queries end-to-end verification
    └── benchmark_workspace_m12_vs_mcp.py # Latency, memory, and reliability benchmark
```

---

## 3. Detailed Technical Specifications

### 3.1 Windows System Monitor Adapter (`core/system_monitor/`)
1. **Background Sampling Thread:**
   - Samples every 2.0s:
     - `psutil.cpu_percent(interval=None)` (non-blocking).
     - `psutil.cpu_percent(interval=None, percpu=True)`.
     - `psutil.virtual_memory()` and `psutil.swap_memory()`.
     - `psutil.disk_partitions()` and `psutil.disk_usage()`.
     - `psutil.net_io_counters()` and `psutil.sensors_battery()`.
     - Top processes sorted by memory % and CPU % (excluding `cmdline`).
   - NVML sampling:
     - `pynvml.nvmlDeviceGetUtilizationRates(handle)`
     - `pynvml.nvmlDeviceGetMemoryInfo(handle)`
     - `pynvml.nvmlDeviceGetTemperature(handle, 0)`
     - `pynvml.nvmlDeviceGetPowerUsage(handle)`
     - `pynvml.nvmlDeviceGetFanSpeed(handle)`
     - Transient error handling: on `NVML_ERROR_GPU_IS_LOST`, reset handle and back off 30s.
   - WMI sampling (LibreHardwareMonitor):
     - Safely test namespace `root/LibreHardwareMonitor`. On COM error `0x8004100e` or `ImportError`, record `lhm_available = False` without raising.
2. **Instant Caller Reads:**
   - `adapter.get_vitals()` returns a frozen dataclass snapshot from memory (`O(1)`).
3. **Registered Tools:**
   - `system_telemetry`: Full telemetry report.
   - `memory_status`: Dedicated RAM / swap inspection.
   - `gpu_status`: Dedicated NVIDIA GPU / VRAM inspection.
   - `process_diagnostics`: Top resource-consuming processes.

### 3.2 Managed Google Workspace MCP Bridge (`core/workspace_mcp/`)
1. **Process Management & Windows Job Object:**
   - Child process launched with `ctypes.windll.kernel32.CreateJobObjectW`.
   - Configure `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (0x2000).
   - Async `stderr` reading thread draining into `collections.deque(maxlen=500)`.
2. **Dynamic In-Memory Credential Handshake:**
   - Host OPTIMUS sends initialized credentials over standard input handshake on startup; tokens are never placed in ambient environment variables.
3. **Single Authority Token Refresh:**
   - OPTIMUS host coordinates token refresh using `oauth_refresh_lock` and updates `CredentialVault`.
4. **Hermetic Mock Transport Mode:**
   - If `OPTIMUS_MOCK_NETWORK == "1"`, the adapter does not spawn an external process; it routes JSON-RPC messages to an in-memory mock handler returning deterministic email and calendar fixtures.
5. **Prompt Injection Quarantine:**
   - Every email body, snippet, subject, and calendar event field is passed through `core.outcome.untrusted(source=..., content=...)`.
6. **Registered Tools:**
   - `mcp_get_gmail_status`: Connection, account, and scope health.
   - `mcp_list_gmail_messages`: Retrieve and filter messages (with quarantine).
   - `mcp_get_email_details`: Retrieve full email details (with quarantine).
   - `mcp_summarize_email`: Concise email summary with defanging.
   - `mcp_get_calendar_view`: Range-based event query (with quarantine).
   - `mcp_find_calendar_conflicts`: Detect overlapping meetings.
   - `mcp_send_email`: Mutating email dispatch (approval gated).
   - `mcp_create_calendar_event`: Mutating calendar dispatch (approval gated).

### 3.3 Central Orchestrator & MRE Intent Mapping

All 7 required user acceptance queries map deterministically:

| Query | MRE Category | Priority | Mapped Tool |
| :--- | :--- | :--- | :--- |
| **"Check my unread Gmail messages."** | `EMAIL` | `P2_STRUCTURED_API` | `list_gmail_messages(is_unread=True)` |
| **"Summarize my emails from today."** | `EMAIL` | `P2_STRUCTURED_API` | `summarize_email(today_only=True)` |
| **"What's on my calendar tomorrow?"** | `CALENDAR` | `P2_STRUCTURED_API` | `get_calendar_view(day="tomorrow")` |
| **"Find conflicts in my schedule."** | `CALENDAR` | `P2_STRUCTURED_API` | `find_calendar_conflicts()` |
| **"How much RAM am I using?"** | `SYSTEM_ADMINISTRATION` | `P1_NATIVE_DETERMINISTIC` | `memory_status()` |
| **"What's my GPU utilization and VRAM usage?"** | `SYSTEM_ADMINISTRATION` | `P1_NATIVE_DETERMINISTIC` | `gpu_status()` |
| **"Which processes are consuming the most resources?"** | `SYSTEM_ADMINISTRATION` | `P1_NATIVE_DETERMINISTIC` | `process_diagnostics(top_n=8)` |

### 3.4 Unified Integrations UI (`gui/optimus/section_integrations.py`)
- Clean, dark-mode blue JARVIS dashboard style consistent with OPTIMUS theme (`C.BG`, `C.PRI`, `C.PANEL`).
- Cards:
  1. **Google Workspace (Gmail & Calendar):** Live status badge, connected account email, granted scopes, token expiration timer, last sync time, OAuth Setup button.
  2. **System Telemetry & Host Monitor:** CPU usage %, RAM load % (Used/Total GB), Storage drive status, Network I/O, Battery indicator.
  3. **NVIDIA GPU Monitor:** GPU model, VRAM load (Used/Total MB), GPU Compute %, Memory Controller %, Temp (°C), Power (W), Driver/CUDA version.
  4. **Active Permissions & Security Gates:** PolicyEngine status, ApprovalToken enforcement, Read-Only enforcement, Prompt injection quarantine active badge.
  5. **Operation Ledger:** Real-time log of last successful operations, latencies, and error diagnostics.

---

## 4. Benchmark & Performance Methodology
- Benchmark script `tests/benchmark_workspace_m12_vs_mcp.py` measures:
  - Latency across 50 iterations: Search, Fetch Details, Calendar View, Conflict Detection.
  - Memory consumption (RSS delta).
  - Accuracy and schema parity.
  - Error rate under simulated network jitter / rate limits.
  - Verification that M12 baseline is fully retained as fallback.
