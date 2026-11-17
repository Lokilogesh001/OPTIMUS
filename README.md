# OPTIMUS — An Autonomous Personal Agentic AI Assistant

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-blue.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-cyan.svg)](https://www.python.org/)
[![UI: PyQt6](https://img.shields.io/badge/UI-PyQt6%20Cinematic%20HUD-00d4ff.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Architecture: Hybrid Multi-Agent](https://img.shields.io/badge/Architecture-Autonomous%20Hierarchical%20Agents-blueviolet.svg)](#architecture--component-responsibilities)

> **OPTIMUS** is an autonomous personal AI system that can hear, see, speak, plan, verify, and orchestrate complex tasks on your computer. Evolved from the desktop foundation of [Mark-LVII by @FatihMakes](https://www.youtube.com/@FatihMakes), OPTIMUS introduces a production-hardened multi-agent engine, cryptographic verification ledger, dynamic workflow scheduler, and adaptive model intelligence gateway with both local and cloud inference.

---

## 📑 Table of Contents

- [Overview](#-overview)
- [Architecture & Component Responsibilities](#-architecture--component-responsibilities)
- [Hybrid Hierarchical Orchestration](#-hybrid-hierarchical-orchestration)
- [Model & Intelligence Gateway](#-model--intelligence-gateway)
- [Specialized Agent System & Sandboxing](#-specialized-agent-system--sandboxing)
- [Memory & Shared Context Bus](#-memory--shared-context-bus)
- [Autonomous Workflow Engine](#-autonomous-workflow-engine)
- [Verification & Cryptographic Evidence Engine](#-verification--cryptographic-evidence-engine)
- [Voice & Multimodal Interaction](#-voice--multimodal-interaction)
- [OPTIMUS Control Center UI](#-optimus-control-center-ui)
- [Hardware Requirements & Resource Efficiency](#-hardware-requirements--resource-efficiency)
- [Supported Model Providers](#-supported-model-providers)
- [Installation & Setup](#-installation--setup)
- [Current Development Status](#-current-development-status)
- [Attribution & License](#-attribution--license)

---

## ⚡ Overview

OPTIMUS bridges high-level goal reasoning with deterministic desktop execution. Rather than acting as a simple chatbot, OPTIMUS operates as a **hierarchical multi-agent coordinator**:

1. **Decomposes Complex Goals**: Breaks ambiguous, long-horizon user intents into verified subtasks.
2. **Assigns Specialized Agents**: Dispatches work to sandboxed worker personas (research, code generation, system monitoring, file processing).
3. **Executes Autonomous Workflows**: Models multi-step procedures as typed Directed Acyclic Graphs (DAGs) with automated retries and human-in-the-loop authorization gates.
4. **Enforces Privacy Boundaries**: Dynamically routes sensitive queries to local models (Ollama, local vLLM) while routing high-throughput reasoning to cloud providers.
5. **Guarantees Execution Integrity**: Validates every tangible deliverable through deterministic postconditions and commits evidence to a SHA-256 tamper-evident ledger.

---

## 🏛 Architecture & Component Responsibilities

```
                                  ┌──────────────────────────────┐
                                  │    OPTIMUS Control Center    │
                                  │  (PyQt6 Cinematic HUD / UI)  │
                                  └──────────────┬───────────────┘
                                                 │
                                     [Service Bridge / Qt Signals]
                                                 │
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    CENTRAL ORCHESTRATOR                                    │
│                         (Goal Planning, Task Routing, Event Bus)                            │
└────────┬──────────────────────┬──────────────────────┬───────────────────────┬──────────────┘
         │                      │                      │                       │
         ▼                      ▼                      ▼                       ▼
┌──────────────────┐  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────┐
│ Intelligence     │  │ Workflow Engine  │  │ Specialized Agents │  │ Shared Context Bus │
│ Gateway & Router │  │ (Typed DAGs &    │  │ & Dynamic Sandboxes│  │ & Memory Manager   │
│ (Cloud / Local)  │  │  Approval Gates) │  │ (Research, Code...)│  │ (Episodic/Semantic)│
└────────┬─────────┘  └────────┬─────────┘  └─────────┬──────────┘  └─────────┬──────────┘
         │                     │                      │                       │
         └─────────────────────┼──────────────────────┴───────────────────────┘
                               │
                               ▼
            ┌──────────────────────────────────────┐
            │    Verification & Evidence Engine    │
            │   (Deterministic Checks, SHA-256)    │
            └──────────────────────────────────────┘
```

The system comprises 10 hardened subsystems:

1. **Central Orchestrator (`core/orchestrator/`)**: Unifies task lifecycle, publishes typed domain events, coordinates subtask scheduling, and maintains task state machines.
2. **Memory & Context Bus (`core/memory/`)**: Tiered storage (working, episodic, semantic, procedural) with role-based context isolation and LRU bounded memory footprint.
3. **Verification & Evidence Engine (`core/verification/`)**: Validates deliverables via postcondition assertions (file existence, regex, checksums, test passes) and appends to an immutable cryptographic ledger.
4. **Specialized Agent Library & Sandboxing (`core/agents/`, `core/sandbox/`)**: Domain-specific personas (`Researcher`, `Coder`, `SystemOperator`, `Reviewer`) operating in restricted process sandboxes with strict execution timeouts.
5. **Model & Intelligence Gateway (`core/intelligence/`)**: Unified interface supporting OpenAI, Anthropic, Gemini, Groq, and Ollama with policy routing (`CLOUD_PREFERRED`, `LOCAL_FIRST`, `PRIVACY_STRICT`) and circuit breakers.
6. **Autonomous Workflow Engine (`core/workflows/`)**: Typed DAG execution engine with sequential/parallel step processing, backoff retries, and human authorization checkpoints.
7. **OPTIMUS Control Center (`gui/optimus/`, `gui/`)**: Cinematic blue/cyan interface featuring command submission, active agent monitoring, workflow visualizer, and system telemetry.
8. **Autonomous Interaction & Voice Intelligence (`core/interaction/`)**: Bidirectional low-latency audio via Gemini Live, wake-word detection, speech interruption handling, and screen vision processing.
9. **Adaptive Planning & Self-Improvement (`core/planning/`)**: Plan evaluation, bottleneck detection, dynamic replanning on step failure, and execution outcome tracking.
10. **System Integration & Release Engineering**: Production hardening, dependency boundary enforcement, process-specific telemetry attribution, and resource throttling.

---

## 🔀 Hybrid Hierarchical Orchestration

OPTIMUS employs a hierarchical delegation model:
- **Supervisor (Orchestrator)**: Evaluates input intent, selects strategies (Direct, ReAct, Plan-and-Solve, Multi-Agent, or Workflow), and generates a plan.
- **Workers (Specialized Agents)**: Execute isolated subtasks within permission-scoped sandboxes. Agents never receive unrestricted system access.
- **Dependency Flow**: Context and verified artifacts flow strictly between dependent steps through the `SharedContextBus`.

---

## 🧠 Model & Intelligence Gateway

The Intelligence Gateway abstracts model providers behind a uniform API supporting completions, streaming, tool-calling, and structured outputs:

- **Routing Policies**:
  - `CLOUD_PREFERRED`: Default operation for rapid conversational intelligence, reserving local compute.
  - `LOCAL_FIRST`: Prefers on-device inference engines (Ollama, local vLLM).
  - `PRIVACY_STRICT`: Hard boundary. If a request is flagged private or contains sensitive context, outbound cloud traffic is blocked; tasks execute purely locally.
  - `COST_OPTIMIZED` & `PERFORMANCE_OPTIMIZED`: Automated selection based on token budgeting and latency targets.
- **Circuit Breakers**: Detects provider timeouts or outages, automatically triggering fallback chains without crashing active workflows.

---

## 🛡 Verification & Cryptographic Evidence Engine

Any task claiming success must submit tangible proof:
- **Deterministic Postconditions**: Checks HTTP response codes, file creation, hash matching, or exit codes.
- **Cryptographic Ledger**: Records every execution outcome into an immutable SHA-256 block ledger (`memory/verification/`), guaranteeing traceability and eliminating hallucinated task completions.

---

## 🎙 Voice & Multimodal Interaction

- **Gemini Live Integration**: Direct full-duplex conversational audio streaming with server-side VAD (Voice Activity Detection).
- **Speech Interruption**: Instant cancellation of active speech synthesis when the user speaks.
- **Screen & Vision Processing**: Fallback-hardened screen capture with automatic image compression for visual context reasoning.
- **Session Resumption**: Persists conversation context and resumes seamlessly across network hiccups or application restarts.

---

## 🖥 OPTIMUS Control Center UI

The desktop interface provides a unified JARVIS-inspired command HUD:
- **Cinematic Visual Style**: Deep navy/black palette (`#030712`), electric cyan accents (`#00d4ff`), and glassmorphic panels.
- **Holographic Centerpiece**: 3D animated holographic face and reactor core driven by live audio levels.
- **Command & Voice Center**: Real-time message transcription, audio visualizers, and quick action bars.
- **Agent & Workflow DAG Monitor**: Visual tracking of active worker agents, step statuses, and pending human approvals.
- **Telemetry & Process Attribution**: Lightweight, cached metrics for CPU, RAM, NVML GPU utilization, and disk space.

---

## ⚡ Hardware Requirements & Resource Efficiency

OPTIMUS is optimized for standard consumer workstations and laptops:

| Component | Minimum Specification | Recommended Specification |
|---|---|---|
| **Operating System** | Windows 10/11, macOS 13+, Ubuntu 22.04+ | Windows 11 (64-bit) |
| **CPU** | 4-Core x86_64 or Apple Silicon | 8-Core / 16-Thread (AMD Ryzen 7 / Intel Core i7) |
| **System RAM** | 8 GB | 16 GB |
| **GPU / VRAM** | Integrated Graphics | NVIDIA RTX 3050 (4 GB VRAM) or higher |
| **Microphone & Audio** | Any standard input/output | Noise-cancelling headset or array mic |

### Live Measured Footprint
- **OPTIMUS Process RAM**: ~320 MB RSS (only **~1.98%** of total system RAM on a 16 GB machine).
- **Headless CLI / Bridge**: ~102 MB RSS.
- **Normalized CPU Utilization**: ~3.2% across 16 logical cores during idle animation.
- **Background Worker Threads**: 48 idle worker threads at 0.00% CPU.

---

## 🔌 Supported Model Providers

| Provider | Type | Capabilities Supported | Default Endpoint |
|---|---|---|---|
| **Google Gemini Live** | Cloud | Audio streaming, text, vision, tools | Cloud API |
| **Ollama** | Local | Text completion, function calling, private embeddings | `http://localhost:11434` |
| **OpenAI** | Cloud | GPT-4o, structured outputs, tool use | `https://api.openai.com/v1` |
| **Anthropic** | Cloud | Claude 3.5 Sonnet, long-context reasoning | `https://api.anthropic.com/v1` |
| **Local vLLM / LM Studio** | Local | OpenAI-compatible local model serving | `http://localhost:8000/v1` |

---

## 🚀 Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/Lokilogesh001/OPTIMUS.git
cd OPTIMUS
```

### 2. Configure Virtual Environment
```bash
# Create environment
python -m venv .venv

# Activate on Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# Activate on Linux/macOS:
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment & Keys
Copy the example templates and add your API credentials:
```bash
cp .env.example .env
cp config/api_keys.json.example config/api_keys.json
```

Edit `config/api_keys.json` with your Gemini API key (obtainable from [Google AI Studio](https://aistudio.google.com/)):
```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "assistant_name": "OPTIMUS",
  "user_name": "Sir",
  "ui_color": "#00d4ff",
  "hud_style": "face"
}
```

### 5. Launch OPTIMUS
```bash
python main.py
```

To run without voice interaction or in test mode:
```bash
python -m pytest tests/
```

---

## 📈 Current Development Status

- [x] **Component 1**: Central Orchestrator & Task Scheduling
- [x] **Component 2**: Tiered Memory Store & Shared Context Bus
- [x] **Component 3**: Verification & Cryptographic Ledger Engine
- [x] **Component 4**: Specialized Agent Library & Process Sandboxing
- [x] **Component 5**: Model & Intelligence Gateway with Failover
- [x] **Component 6**: Autonomous Workflow DAG Engine & Approval Gates
- [x] **Component 7**: OPTIMUS Control Center UI & System Telemetry
- [x] **Component 8**: Autonomous Voice & Multimodal Interaction
- [x] **Component 9**: Adaptive Planning & Outcome Store
- [x] **Component 10**: System Integration, Resource Hardening & Performance Optimization
- [ ] **Upcoming**: Hardware-accelerated OpenGL canvas rendering for the 3D HUD avatar.
- [ ] **Upcoming**: Distributed multi-node agent execution over secure RPC.

*Note: OPTIMUS is an active personal AI research and automation platform. Features undergoing development are marked accordingly.*

---

## 📄 Attribution & License

OPTIMUS is distributed under the **[Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](https://creativecommons.org/licenses/by-nc/4.0/)** license.

### Upstream Attributions
- **Mark-LVII Base Architecture**: Developed by **[@FatihMakes](https://www.youtube.com/@FatihMakes)**. Foundational HUD layout, audio pipeline, and desktop integration derived with appreciation from Mark-LVII.
- **MediaPipe Canonical Face Model (`core/face_model.obj`)**: Copyright Google LLC, licensed under the [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0).
