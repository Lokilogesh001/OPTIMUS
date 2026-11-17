# OPTIMUS Architecture & Technical Specification

## 1. Executive System Overview

**OPTIMUS** (Operational Task Intelligence & Multi-Agent Unified System) transforms Mark-LVII into a sovereign, production-grade autonomous agent architecture. It provides an enterprise execution engine connecting desktop voice/visual presentation, autonomous planning, DAG workflows, specialized agents, physical postcondition verification, and persistent cryptographic memory.

```
+─────────────────────────────────────────────────────────────────────────────+
|                        OPTIMUS CONTROL CENTER & UI                          |
|             (PyQt6 Presentation Layer · Cinematic JARVIS HUD)               |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
|                    COMPONENT 8: UNIFIED INTERACTION RUNTIME                  |
|     (Conversational Turns · Voice Bridge · Intent Classifier · Sessions)     |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │ (Tasks / Complex Goals)
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
|             COMPONENT 9: ADAPTIVE PLANNING & SELF-IMPROVEMENT               |
|      (Goal Decomposition · Strategy Selection · Validation · Replanning)     |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │ (Validated DAG Execution Plans)
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
|                  COMPONENT 1: CENTRAL EXECUTION ORCHESTRATOR                |
|      (TaskStore · EventBus · Anti-P2P Governance · Authority Facade)        |
+──────────────────┬──────────────────────────────────────┬───────────────────+
                   │                                      │
                   ▼                                      ▼
+──────────────────────────────────────+  +───────────────────────────────────+
| COMPONENT 6: AUTONOMOUS WORKFLOWS    |  | COMPONENT 5: MODEL GATEWAY        |
| (DAG Engine · Approvals · Stages)    |  | (Privacy Router · Fallbacks)      |
+──────────────────┬───────────────────+  +──────────────────┬────────────────+
                   │                                         │
                   ▼                                         ▼
+─────────────────────────────────────────────────────────────────────────────+
|        COMPONENT 4: SPECIALIZED AGENT LIBRARY & CAPABILITY SANDBOX          |
|    (CodeIntel · DataIntel · SystemDiag · Researcher · Executor · Tools)     |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │ (Tool Results & Deliverables)
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
|        COMPONENT 3: INDEPENDENT VERIFICATION & EVIDENCE ENGINE              |
|   (Filesystem · Process · Data · Command Verifiers · Tamper-evident Ledger) |
+──────────────────────────────────────┬──────────────────────────────────────+
                                       │ (Verified Physical Deliverables)
                                       ▼
+─────────────────────────────────────────────────────────────────────────────+
|                 COMPONENT 2: MEMORY & SHARED CONTEXT BUS                    |
|    (Working · Episodic · Semantic · Procedural · Atomic Multi-Tier Store)   |
+─────────────────────────────────────────────────────────────────────────────+
```

---

## 2. Core Execution Authority & Invariants

1. **Central Execution Authority**: All task execution passes strictly through the central execution chain: `UI -> Interaction -> Planning -> Orchestrator -> Workflows -> Agents -> Verification -> Memory`. No specialized agent may directly invoke another agent (anti-peer-to-peer rule).
2. **Capability-Based Access Control (CBAC)**: Tools are partitioned into strict capability tiers (`READ_ONLY`, `WORKSPACE_WRITE`, `NETWORK_EXTERNAL`, `EXECUTION`, `SYSTEM_MUTATING`, `SYSTEM_DESTRUCTIVE`). Agents only receive access to explicitly allowlisted tools within their authorized tier envelope.
3. **Physical Postcondition Verification**: No task or workflow step is marked complete without independent empirical verification by the `VerificationEngine` (verifying existence, size, cryptographic digest, and process state).
4. **Append-Only Evidence Ledger**: All execution outcomes and physical evidence are stored in an append-only, tamper-evident ledger (`EvidenceLedger` / `OutcomeStore`).
5. **Human-in-the-Loop Safeguards**: Destructive actions strictly mandate human operator approval via `ApprovalManager` and can never be approved by model text or LLM instructions.

---

## 3. Subsystem Breakdown & Component Directory Map

### Component 1: Core Orchestrator Runtime (`core/orchestrator/`)
- `orchestrator.py`: CentralOrchestrator managing task lifecycles, subtask delegation, and coordination.
- `runtime.py`: TaskStore, TaskRecord, Subtask, ExecutionPlan, and atomic JSON persistence.
- `events.py`: Typed EventBus facilitating decoupled publish/subscribe communications.
- `tools.py`: ToolRegistry, capability tier checking, AST safe evaluator, and secret sanitizer.
- `desktop.py`: Desktop integration facade and thread-safe singleton initialization.

### Component 2: Memory & Context Bus (`core/memory/`)
- `manager.py`: OptimusMemoryManager providing unified access to multi-category memory and prompt context building.
- `store.py`: MemoryStore supporting atomic disk persistence with Windows retry backoff.
- `context_bus.py`: ContextBus managing scoped, role-filtered context envelopes between subtasks.
- `types.py`: MemoryCategory (`WORKING`, `EPISODIC`, `SEMANTIC`, `PROCEDURAL`) and MemoryEntry dataclass.
- `window_manager.py`: Context window token budgeting and memory summarization.

### Component 3: Verification & Evidence Engine (`core/verification/`)
- `engine.py`: VerificationEngine orchestrating postcondition evaluation across subtasks.
- `verifiers/`: Specialized plugin verifiers:
  - `filesystem.py`: File existence, size bounds, extension, and SHA-256 hash integrity.
  - `process.py`: PID status, application lifecycle, and local port socket verification.
  - `data.py`: JSON schema validation, CSV tabular integrity, and numerical assertions.
  - `command.py`: Process exit codes, stdout/stderr validation, and forbidden string detection.
- `ledger.py`: EvidenceLedger maintaining cryptographic SHA-256 chained evidence records.
- `types.py`: Postcondition, VerificationRecord, and VerificationStatus models.

### Component 4: Specialized Agent Library & Dynamic Tool Sandboxing (`core/agents/`, `core/sandbox/`)
- `base.py`: BaseAgent defining the execution contract and tool capability checks.
- `code_intel.py`: CodeIntelligenceAgent for AST code exploration, dependency discovery, and test execution.
- `sys_diag.py`: SystemDiagnosisAgent for CPU, GPU, memory, process, and socket diagnostics.
- `data_intel.py`: DataIntelligenceAgent for CSV/JSON processing and statistical computations.
- `researcher.py`: ResearchAgent for web search, content synthesis, and citation extraction.
- `executor.py`: ExecutionAgent for sandboxed script and command execution.
- `core/sandbox/`: Path resolution boundaries and environment variable quarantine.

### Component 5: Model & Intelligence Gateway (`core/intelligence/`)
- `gateway.py`: IntelligenceGateway providing provider-neutral chat completion and streaming.
- `router.py`: Intelligent ModelRouter implementing multi-tier fallback chains and policy enforcement.
- `circuit_breaker.py`: Fault-tolerant circuit breakers per provider (CLOSED, OPEN, HALF_OPEN).
- `registry.py`: ModelRegistry managing local and cloud model capabilities, context windows, and telemetry.
- `types.py`: RoutingPolicy (`LOCAL_FIRST`, `PRIVACY_STRICT`, `CLOUD_PREFERRED`, `COST_OPTIMIZED`, `PERFORMANCE_OPTIMIZED`).

### Component 6: Autonomous Workflow Engine (`core/workflows/`)
- `engine.py`: WorkflowEngine supporting DAG topological execution, step retry, and crash recovery.
- `resolver.py`: DependencyResolver using Kahn's algorithm for cycle detection and parallel execution stage calculation.
- `approval.py`: ApprovalManager enforcing explicit human-in-the-loop authorization gates.
- `conditions.py`: SafeConditionEvaluator supporting deterministic step conditions and artifact checks.
- `storage.py`: WorkflowStore managing workflow definitions and execution state persistence.

### Component 7: Control Center UI (`gui/optimus/`)
- `control_center.py`: OptimusControlCenterWidget hosting 11 unified sections in a QStackedWidget.
- `service_bridge.py`: Thread-safe PyQt6 bridge dispatching background engine events as Qt signals.
- `nav_bar.py`: OptimusNavBar with active indicator, live system badge, and approval counter.
- `section_*.py`: Specialized views for Dashboard, Command Center, Workflows, Agents, Intelligence, Memory, Verification, Approvals, System Telemetry, Settings, and Adaptive Planning.

### Component 8: Autonomous Interaction & Voice Intelligence (`core/interaction/`)
- `runtime.py`: InteractionRuntime unifying text, voice, and vision interactions.
- `session.py`: InteractionSessionManager persisting conversational turns and active task linkage.
- `router.py`: InteractionRouter performing deterministic intent classification.
- `voice_bridge.py`: VoiceIntelligenceBridge managing wake-word coordination, speech deduplication, and barge-in.
- `types.py`: InteractionModality, IntentCategory, and ChatMessage data structures.

### Component 9: Adaptive Planning & Self-Improvement (`core/planning/`)
- `goal_analyzer.py`: GoalAnalyzer computing complexity scores and decomposing multi-step goals into DAG plans.
- `planner.py`: AdaptivePlanner formulating structured execution steps with agent assignments and postconditions.
- `plan_validator.py`: PlanValidator deterministically verifying DAG integrity, CBAC tool allowlists, and approval gates.
- `strategy_selector.py`: StrategySelector selecting execution strategies based on empirical category history.
- `replanner.py`: AdaptiveReplanner diagnosing logical, environmental, tool, or timeout failures to generate versioned recovery plans.
- `optimizer.py`: EvidenceDrivenOptimizer identifying bottlenecks and recommending empirically grounded strategy shifts.
- `outcome_store.py`: OutcomeStore maintaining thread-safe persistent outcome ledgers with automatic procedural memory promotion.
- `adaptive_executor.py`: AdaptiveExecutionEngine managing self-healing plan execution with cooperative cancellation.

---

## 4. Hardware Optimization & Operational Targets

OPTIMUS is calibrated for balanced local execution:
- **Primary Target**: Windows 11, NVIDIA RTX 3050 Laptop GPU (4GB VRAM), 16GB System RAM.
- **Local Model Ensembles**:
  - `qwen3.5:2b` / `qwen3.5:4b-q4_K_M`: Fast local triage, classification, and conversational turns (~1.8GB VRAM footprint).
  - `qwen2.5-coder:7b` / `qwen3.5:9b`: Code intelligence and planning (CPU offload fallback when VRAM headroom is constrained).
- **VRAM Safeguards**:
  - Model requests exceeding context memory are offloaded to quantized CPU execution or cloud providers (if policy allows).
  - Under `PRIVACY_STRICT` policy, cloud egress is unconditionally blocked.
