# OPTIMUS — Autonomous AI System

[![License: Proprietary](https://img.shields.io/badge/License-Proprietary%20%E2%80%94%20All%20Rights%20Reserved-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.13-cyan.svg)](https://www.python.org/)
[![UI: PyQt6](https://img.shields.io/badge/UI-PyQt6%20Mission%20Control-00d4ff.svg)](https://riverbankcomputing.com/software/pyqt/)
[![Architecture: Hybrid Multi-Agent](https://img.shields.io/badge/Architecture-Autonomous%20Hierarchical%20Agents-blueviolet.svg)](#architecture--component-responsibilities)

> **OPTIMUS** is an independently developed autonomous personal AI system created by **Logeshwaran V**. It can hear, see, speak, plan, verify, and orchestrate complex tasks on your computer using a multi-agent engine, cryptographic verification ledger, dynamic workflow scheduler, and adaptive model intelligence gateway with both local and cloud inference.
>
> **Copyright © 2026 Logeshwaran V. All rights reserved.**  
> This software is proprietary. Unauthorized copying, modification, redistribution, or commercial use without prior written authorization is prohibited. See [`LICENSE`](LICENSE) for full terms.

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
- [Basic Usage](#-basic-usage)
- [Current Development Status](#-current-development-status)
- [License & Third-Party Acknowledgements](#-license--third-party-acknowledgements)

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

### Option 1: Windows Installer (Recommended)
1. Download **`OPTIMUS_Setup.exe`** from the [GitHub Releases](https://github.com/Lokilogesh001/OPTIMUS/releases/tag/v1.1.0-stable) page.
2. Run **`OPTIMUS_Setup.exe`** and follow the on-screen setup wizard to install OPTIMUS.
3. Launch **OPTIMUS** from the Start Menu or Desktop shortcut and enter your API keys / local provider settings when prompted.

### Option 2: From Source (Developers)

1. **Clone the Repository**
```bash
git clone https://github.com/Lokilogesh001/OPTIMUS.git
cd OPTIMUS
```

2. **Configure Virtual Environment**
```bash
# Create environment
python -m venv .venv

# Activate on Windows (PowerShell):
.\.venv\Scripts\Activate.ps1

# Activate on Linux/macOS:
source .venv/bin/activate
```

3. **Install Dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure Environment & Keys**
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

5. **Launch OPTIMUS**
```bash
python main.py
```

To run automated tests:
```bash
python -m pytest tests/
```

---

## 💻 Basic Usage

- **Voice & Command HUD**: Speak naturally using Push-to-Talk (`Ctrl+Alt` default) or type directives directly in the **Assistant Conversation & Directive Stream**.
- **Switch Views (`F2`)**: Press `F2` (or click the **OPTIMUS** header toggle) to switch between the **Classic HUD** and the **OPTIMUS Control Center** (Dashboard, Missions, Execution Graph, Integration Graph, Memory Graph, Approvals, Settings).
- **Keyboard Shortcuts**: `F4` toggles microphone mute, `F11` toggles fullscreen, and `Esc` interrupts active speech.
- **Human-in-the-Loop Approvals**: Sensitive actions (e.g., sending/replying to emails or mutating files outside workspace bounds) pause in `WAITING_FOR_APPROVAL` until explicitly confirmed on-screen.

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

## 📄 License & Third-Party Acknowledgements

**OPTIMUS Software License — Proprietary Software (All Rights Reserved)**  
**Copyright © 2026 Logeshwaran V**

All original source code, architecture, user interface elements, and documentation in this repository are proprietary and protected by copyright. Without prior written authorization from the copyright holder, you may not copy, modify, redistribute, sublicense, or commercially distribute this software. See the [`LICENSE`](LICENSE) file for the complete license terms.

### Third-Party Acknowledgements
- **MediaPipe Canonical Face Model (`core/face_model.obj`)**: Copyright Google LLC, licensed under the [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0).
- **Third-Party Python & Qt Dependencies**: PyQt6, NumPy, Playwright, Requests, `psutil`, and other packages listed in `requirements.txt` remain subject to their respective open-source licenses.
