# OPTIMUS Operations & Runtime Guide

## 1. System Requirements & Prerequisites

- **Operating System**: Windows 10/11 (64-bit).
- **Python**: 3.10 to 3.13.
- **Hardware Profile**:
  - Minimum: 8GB System RAM, Quad-Core CPU.
  - Recommended: 16GB System RAM, NVIDIA GPU with >= 4GB VRAM (e.g., RTX 3050 Laptop).
- **Optional External Services**:
  - Ollama running locally at `http://localhost:11434` for sovereign offline inference.
  - OpenAI / Anthropic / Google Gemini API credentials for cloud intelligence.

---

## 2. Startup Instructions

### Standard Desktop Mode (PyQt6 Control Center)
```powershell
# Navigate to project repository
cd D:\project\srrr\Mark-LVII-main\Mark-LVII-main

# Run using the Python virtual environment
d:\project\optimus\.venv\Scripts\python.exe -m gui.optimus
# Or run with main desktop launcher
d:\project\optimus\.venv\Scripts\python.exe main.py
```

### Keyboard Shortcuts & Desktop HUD Controls
- `F2`: Toggle between Cinematic JARVIS HUD and the OPTIMUS Control Center.
- `Escape`: Close active overlay dialog or dismiss full-screen HUD.
- Navigation bar tabs allow direct switching between all 11 subsystems:
  - `[0] DASHBOARD`: High-level operational gauges and live telemetry.
  - `[1] COMMAND`: Command center prompt input and directive submission.
  - `[2] WORKFLOWS`: Interactive DAG execution graph and step status.
  - `[3] AGENTS`: Registered agent roster and capability envelopes.
  - `[4] INTELLIGENCE`: Active model gateway providers and circuit breakers.
  - `[5] MEMORY`: Multi-category memory viewer and semantic search.
  - `[6] VERIFY`: Cryptographic evidence ledger and verification log.
  - `[7] APPROVALS`: Human authorization gate with badge counter.
  - `[8] SYSTEM`: Hardware resource monitors (CPU, GPU, RAM, Disks, Processes).
  - `[9] SETTINGS`: Routing policy toggles and theme settings.
  - `[10] PLANNING`: Adaptive DAG plan viewer, outcome metrics, and optimizer.

---

## 3. Configuration & Routing Policies

Environment variables can be configured via a `.env` file in the project root:

| Variable | Description | Default |
|---|---|---|
| `OPTIMUS_STORAGE_ROOT` | Base directory for persistent memory and ledgers | `./memory` |
| `OPTIMUS_ROUTING_POLICY` | Default model routing policy | `LOCAL_FIRST` |
| `OLLAMA_BASE_URL` | Endpoint for local Ollama models | `http://localhost:11434` |
| `OPENAI_API_KEY` | Optional cloud API key for OpenAI models | (None) |
| `ANTHROPIC_API_KEY` | Optional cloud API key for Anthropic models | (None) |
| `GEMINI_API_KEY` | Optional cloud API key for Google Gemini | (None) |

### Operational Routing Policies
- `LOCAL_FIRST`: Dispatches to local models; falls back to cloud only when required capabilities exceed local models.
- `PRIVACY_STRICT`: Unconditionally confines all prompt data and images to local models. Cloud egress is strictly blocked.
- `CLOUD_PREFERRED`: Dispatches complex reasoning tasks to high-parameter cloud models.
- `COST_OPTIMIZED`: Minimizes blended per-token cost across registered providers.
- `PERFORMANCE_OPTIMIZED`: Routes requests to models with lowest empirical latency and error rates.

---

## 4. Telemetry & Health Monitoring

The `SystemDiagnosisAgent` and `OptimusServiceBridge` continuously monitor:
- **CPU Utilization & Core States**: Tracked via `psutil`.
- **Memory (RAM) Footprint**: Free vs. active system memory.
- **GPU Telemetry (NVIDIA RTX)**: Polled via `nvidia-smi` / PyNVML for VRAM allocation and temperature.
- **Circuit Breakers**: Individual provider failure rates tracked in `IntelligenceGateway`. If a provider fails 3 consecutive times, its circuit opens to prevent hanging requests.

---

## 5. Troubleshooting & Failsafes

### Issue 1: VRAM Exhaustion / Out of Memory on RTX 3050 (4GB)
- **Symptom**: Local model fails to allocate context memory or Ollama crashes.
- **Remedy**: Switch active local model to `qwen3.5:2b` or `qwen3.5:4b-q4_K_M` in Settings, or set `OPTIMUS_ROUTING_POLICY=LOCAL_FIRST` with cloud fallback enabled.

### Issue 2: Workflow Halted at "WAITING_APPROVAL"
- **Symptom**: Workflow stops progress and orange badge appears on Approvals tab.
- **Cause**: Step is marked `is_destructive=True` or `requires_approval=True`.
- **Remedy**: Navigate to the `APPROVALS` section (Tab 7) in the Control Center and click "Authorize Action", or call `ApprovalManager.approve(approval_id, approver="admin")`.

### Issue 3: Corrupt JSON File Warning on Startup
- **Symptom**: `[TaskStore] Failed loading task ...` or `[OutcomeStore] Failed to load outcomes ...`
- **Cause**: Unexpected power outage or abrupt process termination during disk write.
- **Remedy**: OPTIMUS utilizes atomic file replacement (`.tmp` -> replace) so corrupted files are safely ignored and logged. Valid tasks and memories remain fully intact.

---

## 6. Clean Shutdown Procedure

1. In the GUI, click the close button or invoke window close (`Ctrl+Q`).
2. The `OptimusServiceBridge` deactivates poller timers and completes in-flight event queues.
3. Persistent memories and outcome ledgers are atomically flushed to disk.
4. Any running subprocess or background task receives a graceful termination signal.
