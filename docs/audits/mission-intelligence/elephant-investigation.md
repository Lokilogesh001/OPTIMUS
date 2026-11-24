# OPTIMUS Universal Mission Intelligence & Dynamic Execution Engine — Elephant Investigation

**Date:** 2026-11-24  
**Author:** Principal Systems Architect & Engineering Team  
**Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Git Base Commit:** `104a7db` (Clean working tree)  
**Python Runtime:** `D:\project\optimus\.venv\Scripts\python.exe`  

---

## 1. Executive Summary & Objective

The objective of this initiative is to elevate OPTIMUS from a tool-calling desktop assistant into an autonomous, mission-oriented personal agentic AI system. The user expresses objectives naturally (e.g. *"Find my important emails from the last two days"*, *"Open Spotify and play Blinding Lights"*, *"Organize research papers by topic"*), and OPTIMUS must autonomously:
1. Parse multi-intent natural language objectives into structured entities, temporal bounds, constraints, and formal completion criteria.
2. Classify missions across taxonomy dimensions, complexity tiers (T1–T4), risk levels (LOW, MEDIUM, HIGH, CRITICAL), and authorization requirements.
3. Discover available capabilities dynamically via a formal registry without tool hallucinations or missing dependency crashes.
4. Formulate validated, cycle-free Directed Acyclic Graph (DAG) execution plans with parallel/sequential steps, variable passing, data contracts, and conditional branching.
5. Coordinate specialized domain agents under strict hierarchical control through `CentralOrchestrator` and `MissionExecutionEngine`.
6. Adapt dynamically to runtime failures via root-cause failure diagnosis, alternative capability routing, prerequisite injection, and safe replanning.
7. Independently verify physical outcomes and real-world side effects (process existence, audio playback, file hashes, test suites) before claiming completion.
8. Enforce local-first execution, strict resource governance (timeouts, step limits, recursion bounds, token budgets), and loop detection.

---

## 2. Baseline Architecture Audit

### 2.1 Existing Subsystems Analysis

| Subsystem | Location | Current Capabilities | Architectural Gaps & Limitations |
|---|---|---|---|
| **Mission State Machine** | `core/missions/engine.py` | 7-stage state machine (`UNDERSTAND` -> `PLAN` -> `VALIDATE` -> `APPROVAL` -> `EXECUTE` -> `VERIFY` -> `COMPLETE`); pause/resume/cancel; thread worker; persistence via `MissionStore`. | Lacks variable flow between subtasks; does not enforce token/time budgets or loop detection; recovery delegates to basic step retries without DAG surgery. |
| **Mission Planner** | `core/missions/planner.py` | Regex-based template matching for 5 mission types (Research, System, Developer, Computer Use, Composite); basic DFS cycle detection. | Cannot parse multi-intent compound goals; no temporal constraint extraction; does not support dynamic variable passing (`$step_id.output`); no conditional branching. |
| **Mission Coordinator** | `core/missions/coordinator.py` | Bounded thread pool for concurrent subtasks; policy engine checks; invokes `ToolRegistry.execute()` and specialized agents. | Tool argument resolution is hardcoded heuristic lookup (`_resolve_tool_arguments`); lacks structured input/output JSON schemas and context isolation/prompt injection sanitization. |
| **Failure Recovery** | `core/missions/recovery.py` | Failure diagnosis (`TRANSIENT`, `ENVIRONMENTAL`, `PERMISSIONS_DENIED`, etc.); static `TOOL_FALLBACKS` and `AGENT_FALLBACKS`. | No dynamic alternative capability discovery; cannot inject prerequisite remediation steps into active DAG; no loop detection for repeated failing calls. |
| **Central Orchestrator** | `core/orchestrator/orchestrator.py` | Hybrid hierarchical authority; fast-path MRE; task store; tool registry; verification engine integration. | Mission lifecycle and task lifecycle operate partially in parallel; needs explicit unified bridge to `MissionExecutionEngine` for complex multi-agent objectives. |
| **Specialized Agents** | `core/agents/` | 8 canonical agents: Researcher, Developer, System Diagnosis, File/Execution, Data Intelligence, Verifier, Planner, Computer Use. | Context passing contains raw shared memory without prompt-injection sanitization for untrusted data (e.g. untrusted email or web content). |
| **Verification Engine** | `core/verification/` | 8 pluggable verifiers: Filesystem, Process, Data, Command, DesktopWindow, BrowserPage, MediaPlayback, UIControl; tamper-evident evidence ledger. | Needs turnkey integration with mission postcondition contracts across media playback (Spotify), test suite execution, and file content hashes. |
| **Tool Registry & Policy** | `core/orchestrator/tools.py`, `core/security/policy_engine.py` | 101 registered tools across 6 CBAC tiers; AST-based calculator; zero-eval math; credential masking. | Capabilities lack formal input/output schemas with typed variable bindings, reversibility declarations, and compensation actions. |

---

## 3. Detailed Gap Analysis & Requirements

### GAP-01: Natural Language Multi-Intent Understanding
- **Current State:** `MissionPlanner.plan` matches user input against simplistic regex keywords.
- **Requirement:** A dedicated `MissionIntentParser` that extracts:
  - Primary goal vs secondary goals.
  - Compound intents (sequential: "do X and then Y", parallel: "do X while Y").
  - Entities (file paths, application names, URLs, process names, people, file patterns).
  - Temporal constraints ("last 2 days", "today", "before Friday").
  - Operational constraints (read-only vs mutating, max budget, timeout).
  - Explicit completion criteria (formal assertions).

### GAP-02: Multi-Dimensional Mission Classification
- **Current State:** Risk level is computed as binary `"HIGH"` or `"LOW"`.
- **Requirement:** A formal `MissionClassifier` evaluating:
  - Mission Type: Research, Software Eng, Desktop Automation, System Admin, File/Data Mgmt, Personal Intel, Compound.
  - Complexity Tier: T1 (Single-step), T2 (Deterministic multi-step), T3 (Dynamic multi-agent), T4 (Open-ended exploratory).
  - Risk Level: LOW (read-only), MEDIUM (reversible writes), HIGH (destructive writes, external comms), CRITICAL (system configs, credentials).
  - Authorization Level: Auto-execute vs Pre-execution confirmation vs Step-by-step confirmation.
  - Resource Budget: Maximum duration, step limits, worker limits, token allocations.

### GAP-03: Universal Dynamic Capability Registry
- **Current State:** Tools are listed in `ToolRegistry` as `ToolDefinition`, but lack idempotency flags, compensation actions, prerequisite checks, and formal variable schemas.
- **Requirement:** `UniversalCapabilityRegistry` providing:
  - Canonical capability descriptor wrapping native tools, agents, app adapters, and MCP tools.
  - Input/Output JSON schemas with type validation.
  - Idempotency guarantees (`IDEMPOTENT`, `NON_IDEMPOTENT`, `CONDITIONALLY_IDEMPOTENT`).
  - Reversibility declarations with designated compensation actions (undo).
  - Dynamic discovery and alternative lookup (preventing hallucinated tools).

### GAP-04: Dynamic DAG Mission Planner with Variable Passing
- **Current State:** Plans are flat lists of `MissionSubtask` with basic `depends_on`. No variable references between steps.
- **Requirement:** `DynamicDAGPlanner` supporting:
  - Variable passing via JSON path / template syntax (e.g. `{{subtask_1.output.matching_files}}`).
  - Data contracts between upstream outputs and downstream inputs.
  - Automatic injection of verification steps after mutating actions.
  - Automatic insertion of approval gates before HIGH/CRITICAL actions.
  - Strict cycle detection, reachability validation, and unbound variable rejection.

### GAP-05: Specialist Agent Coordination & Secure Context Isolation
- **Current State:** Shared memory dumps all previous outputs into every agent's execution context.
- **Requirement:**
  - Context isolation: Workers receive only their required input variables.
  - Untrusted data sanitization: External email contents, web scrapes, and file contents are tagged as untrusted to defend against prompt injection.
  - Bounded concurrency: Max parallel worker pool enforced.

### GAP-06: Dynamic Replanning & Failure Recovery
- **Current State:** Recovery engine modifies in-place or retries up to 3 times, but cannot prune downstream steps, substitute alternate tools, or inject prerequisite steps (e.g. creating a missing directory).
- **Requirement:**
  - Deep root-cause diagnosis.
  - Alternative capability substitution via Capability Registry.
  - Prerequisite step injection into active DAG.
  - Bounded retry guardrails and loop detection.

### GAP-07: Independent Real-World Outcome Verification
- **Current State:** Subtask verification is triggered only if files look like file paths.
- **Requirement:**
  - Formal postcondition contracts attached to steps:
    * `PROCESS_RUNNING`: Process name, PID, memory check.
    * `MEDIA_PLAYBACK`: Active playback session, track title match, audio state.
    * `FILESYSTEM`: Path exists, size > 0, SHA256 checksum, content regex pattern.
    * `COMMAND_EXIT`: Test runner exit code 0, passed test count > 0.
  - Evidence ledger recording tamper-evident audit records.

### GAP-08: Execution Governance & Safety Guardrails
- **Current State:** Missions run indefinitely until thread finishes or is cancelled manually.
- **Requirement:**
  - Hard timeouts per subtask and whole mission.
  - Step recursion limits (max 50 steps).
  - Loop detection: detect repeated identical actions or circular failures.
  - Graceful cancellation and cleanup of background threads.

---

## 4. Invariant Boundaries & Security Constraints

1. **Local-First Invariant:** All core planning, classification, DAG generation, tool execution, and verification run strictly locally on the user's workstation.
2. **Hierarchical Authority:** Central Orchestrator is the single source of authority. No agent-to-agent peer delegation.
3. **No False Greens:** Execution success is never accepted based solely on tool return text; physical evidence must be verified.
4. **No Hallucinated Tools:** Planners can only emit subtasks mapped to verified capabilities in the Capability Registry.
5. **No Destructive Actions Without Approval:** Mutating/destructive actions (file deletions, process kills, system modifications) require explicit user authorization.
6. **Preserve Mark-LVII & UI 2.0:** All existing classes, methods, and UI integrations must remain fully compatible.
