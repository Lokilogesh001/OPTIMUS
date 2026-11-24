# OPTIMUS Universal Mission Intelligence & Dynamic Execution Engine — Reconciled Architecture Specification

**Document Version:** 2.0.0 (Post-Goldfish Reconciliation)  
**Status:** Approved for Implementation  
**Date:** 2026-11-24  
**Author:** Principal Systems Architect & Engineering Team  
**Reviewer Reconciled:** Design Critic Goldfish (Findings CRIT-01 through LOW-03)  
**Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Git Base Commit:** `104a7db`  

---

## 1. Executive Reconciliation Summary

This document supersedes Version 1.0.0 and incorporates full, binding architectural resolutions for all 20 findings raised in the independent Goldfish review (`egm-design`).

### Reconciliation Matrix

| Issue Code | Finding Summary | Reconciled Architectural Resolution |
|---|---|---|
| **CRIT-01** | Signature mismatch between Coordinator and Specialized Agents (`run()` vs `execute()`) causing silent fake mock execution. | Refactored `MissionCoordinator.execute_subtask` to construct a typed `AgentInputContract` and invoke `agent.execute(contract, tool_registry)`. Removed fake mock string returns. |
| **CRIT-02** | RCE vulnerability in conditional branch evaluation. | Implemented `SafeASTEvaluator` utilizing restricted Python AST grammar (`ast.Expression`, `ast.Compare`, `ast.BoolOp`, constants, identifiers) with zero `eval()` / `exec()`. |
| **CRIT-03** | Ineffective prompt injection defense via unescaped tags. | Introduced dynamic cryptographic nonce wrappers (`<untrusted_data nonce="...">`) with full XML/HTML character escaping and taint tracking flag (`is_untrusted: True`) in `MissionMemory`. |
| **CRIT-04** | Missing Saga / Rollback engine leaves system in dirty corrupted state. | Implemented `SagaCoordinator` maintaining an append-only `compensation_stack`. On failure or cancellation of mutating missions, compensation actions are executed in reverse topological order. |
| **CRIT-05** | Static DAG cannot handle collection fan-out (e.g. moving N files). | Implemented collection-aware tool contracts (`batch_move_files`, `batch_process`) accepting lists natively, plus dynamic child sub-DAG expansion for mapped items. |
| **HIGH-01** | Deadlock on skipped conditional branches. | Implemented Skip Propagation: skipped nodes propagate `SKIPPED` to downstream nodes, and nodes support `join_policy: "ALL_COMPLETED" \| "ONE_COMPLETED" \| "ALL_RESOLVED"`. |
| **HIGH-02** | Thread safety race conditions on `MissionRecord` during concurrent execution. | Thread-confined worker returns: workers return `(timeline_events, evidence_items, output)` which are merged exclusively by the coordinator main thread under an `RLock` prior to saving. |
| **HIGH-03** | Cooperative cancellation cannot terminate blocking tools. | Introduced `CancellationToken` and process handle tracking (`Popen.terminate()` / `Popen.kill()`) for all external subprocesses and command verifiers. |
| **HIGH-04** | Naive loop detection fails on dynamic arguments. | Implemented Semantic State-Aware Loop Detector hashing `(tool_name, normalized_args, failure_signature)` with sliding n-gram window (n=2, 3, 4). |
| **HIGH-05** | `MediaPlaybackVerifier` lacks Windows Core Audio integration (WASAPI). | Integrated Windows Core Audio WASAPI session inspection via `ctypes` (`IMMDeviceEnumerator`, `IAudioSessionManager2`, `IAudioMeterInformation`) to measure real-time audio peak levels for Spotify. |
| **HIGH-06** | Inverted risk policy (code editing was MEDIUM, file move was HIGH). | Re-classified tracked code mutations (`dev_file_write`, script execution) as **`HIGH`** or **`CRITICAL`** requiring approval gates and git safety checks. Re-classified non-destructive file moves with Saga compensation as **`MEDIUM`**. |
| **MED-01** | State machine lacks `WAITING_FOR_CLARIFICATION` state. | Added `WAITING_FOR_CLARIFICATION` state between `UNDERSTAND` and `PLAN` for ambiguous queries. |
| **MED-02** | False greens in deliverable verification. | Upgraded `DataVerifier` with semantic schema validation (e.g., verifying laptop price <= 1200, 14-inch display in Scenario D). |
| **MED-03** | Missing parent directory auto-provisioning. | Standardized `write_file` and filesystem tools to auto-create missing parent directories (`parent.mkdir(parents=True, exist_ok=True)`). |
| **MED-04** | Blind retry of non-idempotent capabilities. | Enforced idempotency check: only `IDEMPOTENT` operations auto-retry on transient errors. `NON_IDEMPOTENT` operations halt and request user confirmation. |
| **MED-05** | Token budget governance unmonitored. | Added `TokenAccountingGovernor` with cumulative token tracker and hard abort threshold. |
| **MED-06** | Software engineering missions lack whole-suite regression check. | Implemented two-stage verification for developer missions: targeted test pass + smoke test pass. |
| **LOW-01** | Timezone ambiguity in temporal parsing. | Anchored all temporal constraint normalization to local system timezone with UTC offset. |
| **LOW-02** | Untyped `completion_criteria`. | Defined typed `CompletionCriterion` dataclass with strictly typed assertion strategies. |
| **LOW-03** | Nonsensical static fallback mappings (`web_search -> read_file`). | Replaced static dictionary with dynamic semantic lookup against `UniversalCapabilityRegistry`. |

---

## 2. Reconciled Component Architecture

```mermaid
graph TD
    User([User Natural Language Objective]) --> IntentParser[Intent & Multi-Intent Parsing Engine]
    IntentParser -- Ambiguous Query --> Clarification[WAITING_FOR_CLARIFICATION State]
    IntentParser -- Valid Intent --> Classifier[Mission Classification & Risk Scorer]
    Classifier --> CapReg[Universal Dynamic Capability Registry]
    CapReg --> DAGPlanner[Dynamic DAG Mission Planner]
    DAGPlanner --> Governance[Resource, Token & Loop Governance]
    Governance --> StateMachine[Central Mission Lifecycle & State Machine]
    StateMachine --> Saga[Saga Rollback Coordinator]
    StateMachine --> Coordinator[Specialized Agent Coordinator]
    Coordinator --> AgentContract[AgentInputContract -> agent.execute()]
    Coordinator --> PolicyEngine[Authoritative Policy Engine]
    PolicyEngine --> Tools[Local Tools & External Subprocesses]
    Tools --> OutcomeEngine[Outcome Verification Engine]
    OutcomeEngine --> WASAPI[Windows Core Audio WASAPI Verifier]
    OutcomeEngine --> Verifiers[Filesystem / Process / Data / Command Verifiers]
    OutcomeEngine -- Verification Failed --> Replanner[Adaptive Replanner & Prerequisite Injector]
    Replanner -- DAG Surgery --> StateMachine
    OutcomeEngine -- Verified Outcome --> StateMachine
    StateMachine --> Store[(MissionStore & EvidenceLedger)]
    StateMachine --> Completed([Verified Complete])
```

---

## 3. Data Contracts & Interfaces

### 3.1 Typed Completion Criteria & Intent Models (`core/missions/intent.py`)
```python
class CriterionType(str, Enum):
    FILESYSTEM = "FILESYSTEM"
    PROCESS_RUNNING = "PROCESS_RUNNING"
    MEDIA_PLAYBACK = "MEDIA_PLAYBACK"
    COMMAND_EXIT = "COMMAND_EXIT"
    DATA_SCHEMA = "DATA_SCHEMA"

@dataclass
class CompletionCriterion:
    criterion_type: CriterionType
    target: str
    expected_value: Any = None
    parameters: dict[str, Any] = field(default_factory=dict)

@dataclass
class SingleIntent:
    intent_id: str
    action_verb: str
    target_domain: str
    entities: list[IntentEntity]
    temporal_constraints: list[TemporalConstraint]
    completion_criteria: list[CompletionCriterion]
    is_mutating: bool
    description: str

@dataclass
class ParsedMissionIntent:
    raw_query: str
    primary_intent: SingleIntent
    secondary_intents: list[SingleIntent]
    composition_mode: str  # SEQUENTIAL, PARALLEL, CONDITIONAL, SINGLE
    global_constraints: dict[str, Any]
    requires_disambiguation: bool = False
    clarification_prompt: str = ""
```

---

### 3.2 Safe AST Condition Evaluator (`core/missions/planner.py`)
```python
class SafeASTEvaluator:
    """Safely evaluates condition expressions without eval() or arbitrary code execution."""
    
    ALLOWED_OPS = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }
    
    @classmethod
    def evaluate(cls, expression: str, context: dict[str, Any]) -> bool:
        tree = ast.parse(expression, mode="eval")
        return bool(cls._eval_node(tree.body, context))
```

---

### 3.3 Prompt Injection Defense & Cryptographic Nonce Boundaries (`core/missions/coordinator.py`)
```python
def wrap_untrusted_data(content: str, source_label: str) -> str:
    """Wrap external untrusted data with cryptographic nonces and XML escaping."""
    nonce = uuid.uuid4().hex[:12]
    escaped_content = (
        content.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return (
        f'<untrusted_data source="{source_label}" nonce="{nonce}">\n'
        f'{escaped_content}\n'
        f'</untrusted_data nonce="{nonce}">'
    )
```

---

### 3.4 Saga Rollback Coordinator (`core/missions/saga.py`)
```python
@dataclass
class CompensationRecord:
    subtask_id: str
    action_name: str
    compensation_capability_id: str
    compensation_args: dict[str, Any]
    executed_at: float = field(default_factory=time.time)

class SagaCoordinator:
    """Manages transactional compensations for mutating multi-step missions."""
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry
        self.compensation_stack: list[CompensationRecord] = []

    def record_mutation(self, rec: CompensationRecord) -> None:
        self.compensation_stack.append(rec)

    def rollback(self) -> list[ToolResult]:
        results = []
        while self.compensation_stack:
            rec = self.compensation_stack.pop()
            res = self.tool_registry.execute(
                tool_name=rec.compensation_capability_id,
                arguments=rec.compensation_args,
                caller_agent="saga_coordinator",
            )
            results.append(res)
        return results
```

---

### 3.5 Real WASAPI Audio Playback Verifier (`core/verification/verifiers.py`)
```python
class MediaPlaybackVerifier(BaseVerifier):
    """Verifies audio playback via Windows Core Audio (WASAPI) and UIA session checks."""
    
    def verify(self, target: str, context: dict[str, Any]) -> VerificationRecord:
        # 1. Check if mock context present (for fast isolated tests)
        if "mock_playback_active" in context:
            return self._record_success(target, context)
            
        # 2. Native Windows WASAPI Peak Audio Inspection
        peak_level = self._get_windows_audio_peak_meter(process_name=target)
        if peak_level > 0.001:
            return self._record_success(target, {"peak_level": peak_level})
            
        # 3. Fallback to desktop window & UI Automation state
        return self._verify_window_playback_state(target)
```

---

## 4. Reconciled Six End-to-End Practical Scenarios

### Scenario A: Email Intelligence
- **Query:** *"Find my important emails from the last two days and summarize them."*
- **Disambiguation:** If no auth token, transition to `WAITING_FOR_CLARIFICATION`.
- **Taint Tracking:** Email content tagged `<untrusted_data nonce="...">` with `is_untrusted: True`.
- **Verification:** Assert summary is non-empty, contains key headers, and did not execute injected instructions.

### Scenario B: Desktop Application Control (Spotify)
- **Query:** *"Open Spotify and play Blinding Lights."*
- **Execution:** Launch `Spotify.exe`, wait for UIA window readiness, search and play track.
- **Verification:** `ProcessVerifier` verifies PID and running status; `MediaPlaybackVerifier` verifies audio session peak meter > 0 via Windows Core Audio.

### Scenario C: Intelligent File Organization
- **Query:** *"Find all PDF files in my Downloads folder and organize them into Documents/Research."*
- **Collection Handling:** Discovered PDF files are processed via collection-aware `batch_move_files`.
- **Saga Safety:** Each moved file registers a compensation record (`move_file` back to original source).
- **Risk:** `MEDIUM` (Reversible file move with active Saga compensation).

### Scenario D: Research & Comparison
- **Query:** *"Research the top three lightweight 14-inch laptops under $1200, compare their specs, and compile a markdown comparison table."*
- **Verification:** `DataVerifier` parses markdown table and programmatically asserts:
  - Exactly 3 models compared.
  - For each model, `Price <= 1200` and `ScreenSize == 14`.
  - File exists and size > 500 bytes.

### Scenario E: Software Engineering (Bug Fix & Test)
- **Query:** *"Investigate the failing test in tests/test_calculator.py, fix the bug, run the test suite, and verify it passes."*
- **Risk Level:** **`HIGH`** (Source code modification).
- **Safety Gate:** Verify clean git working tree before patching; automatic backup branch created.
- **Verification:** Two-stage verification:
  1. Targeted test passes (`pytest tests/test_calculator.py`).
  2. Broader smoke test passes (`pytest tests/test_core_orchestrator.py`).

### Scenario F: Compound Mission (University Presentation)
- **Query:** *"Prepare for my presentation: find presentation guidelines in Downloads, search recent AI ethics articles, compile summary slides notes, and verify all deliverables."*
- **Directory Safety:** `write_file` automatically provisions `notes/` directory.
- **Parallelism:** Parallel subtasks for local file search and web search.
- **Verification:** `FilesystemVerifier` verifies `notes/ai_ethics_slides.md` exists with validated headers.

---

## 5. Acceptance Criteria

1. **Zero Fake Executions:** Every agent subtask executes `agent.execute(contract, tool_registry)`.
2. **Zero Eval Injections:** AST condition evaluator parses boolean expressions safely without `eval()`.
3. **Zero Injection Breakouts:** Delimiter nonce tests and tag escaping pass.
4. **Saga Recovery Proven:** Mutating mission abortion successfully rolls back side effects via compensation stack.
5. **Real Audio Verification:** Spotify playback verified with peak audio levels.
6. **100% Test Coverage:** All 6 practical scenarios pass automated regression and acceptance tests.
