# OPTIMUS Milestone 18 — Advanced Execution Framework Integration
## Reconciled Architecture Specification

**Document ID:** EGM-M18-RECON-001  
**Author:** Principal Windows Systems Architect & AI Systems Engineer  
**Milestone:** M18 — Advanced Execution Framework Integration  
**Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Date:** 2026-11-22  
**Status:** **AUTHORITATIVE & RECONCILED (All 12 Goldfish Audit Findings Resolved)**  
**Audit Reference:** EGM-M18-AUDIT-001 (Auditor subagent `700da204-1117-4f4e-8e8c-a71bb214430a`)

---

### 1. Executive Reconciliation Summary

This specification reconciles the Elephant Architecture Report (`EGM-M18-ARCH-001`) with the findings from the independent Goldfish adversarial security and architecture audit. All 12 identified findings (**4 Critical**, **5 High**, **3 Medium**) are resolved with concrete, non-bypassable architectural mechanisms.

---

### 2. Detailed Findings Reconciliation Matrix

| Finding ID | Severity | Problem Statement | Reconciled Architectural Resolution | Affected Code Modules |
|---|---|---|---|---|
| **CRIT-1** | Critical | `DesktopInteractionLock` race condition: `_lock.release()` executed before popping `_owner_stack`, causing stack corruption on thread switch. | Reorder release sequence: `_owner_stack` is modified while the mutex is strictly held. Verify `_owner_stack[-1] == self.owner` before popping; only then release the underlying mutex. | `core/application_intelligence/desktop_lock.py` |
| **CRIT-2** | Critical | `ActionContract` & `ExecutionRequest` lack `approval_token`, creating a policy bypass gap for privileged framework operations. | Add `approval_token: str \| None = None`, `session_nonce: str \| None = None`, `caller_identity: str = "orchestrator"` to `ActionContract` and `ExecutionRequest`. Enforce `PolicyEngine.instance().evaluate_operation()` barrier in `InteractionRouter` and all execution providers before actuation. | `core/application_intelligence/types.py`, `core/application_intelligence/interaction_router.py`, `core/application_intelligence/frameworks/base.py` |
| **CRIT-3** | Critical | Prompt injection tag mismatch: `<untrusted_external_content>` diverges from active prompt (`core/prompt.txt`) and `core/outcome.py`. | Standardize exclusively on `core.outcome.untrusted(source, text)` which uses `<<<UNTRUSTED source=...>>> ... <<<END UNTRUSTED>>>`. Eliminate custom XML wrappers across all browser and UI extractions. | `core/application_intelligence/adapters/browser.py`, `core/application_intelligence/frameworks/browser/` |
| **CRIT-4** | Critical | OmniParser guaranteed CUDA OOM on RTX 3050 (4 GB VRAM total, ~2.4 GB free) and 20s+ CPU latency with Florence-2. | Implement a strict VRAM pre-flight check (`torch.cuda.mem_get_info()`, requiring $\ge 3.0$ GB free VRAM). If insufficient, automatically route to an optimized INT8 ONNX Runtime model or lightweight YOLOv8-nano + local OCR pipeline (<150ms on CPU). Keep OmniParser lazily loaded and disabled by default until benchmarked. | `core/application_intelligence/frameworks/omniparser/` |
| **HIGH-1** | High | UFO² `AppAgent` and Stagehand `act()` internal LLM loops usurping `CentralOrchestrator` authority and token budgets. | Strip out and disable internal LLM agentic loops. Restrict `UFODesktopBackend` to stateless WinCOM and UIA action schemas. Constrain `StagehandBrowserIntelligence.act()` to single atomic actions (one click/fill/nav) resolved to an explicit locator without multi-step autonomous branching. | `core/application_intelligence/frameworks/ufo/ufo_backend.py`, `core/application_intelligence/frameworks/browser/stagehand_intelligence.py` |
| **HIGH-2** | High | Unkillable native COM calls causing worker thread starvation when Office apps display modal dialogs. | Isolate WinCOM and pywinauto automation inside dedicated worker execution contexts or bounded process workers with hard timeout kills (`TerminateProcess`), preventing thread pool starvation. | `core/application_intelligence/frameworks/pywinauto/`, `core/application_intelligence/frameworks/ufo/` |
| **HIGH-3** | High | COM dual-library threading conflict (`comtypes` vs `pythoncom` / STA apartment isolation). | Implement unified COM apartment context manager `com_apartment()` that initializes both `pythoncom.CoInitialize()` and `comtypes.CoInitialize()`. Enforce strict thread affinity (no sharing raw COM pointers across threads). | `core/application_intelligence/frameworks/base.py`, `core/application_intelligence/frameworks/ufo/wincom_bridge.py` |
| **HIGH-4** | High | Superficial `press("escape")` rollback fails atomic state guarantees across COM documents, web forms, and UIA values. | Implement polymorphic `rollback(contract, evidence)` on `BaseExecutionProvider`. WinCOM calls document `Undo()` / `Close(SaveChanges=False)`; Playwright clears input fields / cancels downloads; pywinauto restores cached control values. | `core/application_intelligence/frameworks/base.py`, `core/application_intelligence/interaction_router.py` |
| **HIGH-5** | High | Unchecked self-promotion of workflows in `WorkflowStore`: passing `validation_state="VERIFIED_SKILL"` registered skill as `TRUSTED`. | Enforce that `save_workflow()` **always** forces `trust = SkillTrustLevel.UNVERIFIED`. Add separate `promote_workflow()` method requiring explicit human confirmation. | `core/application_intelligence/workflow_store.py` |
| **MED-1** | Medium | `DesktopInteractionLock` unconditionally acquired for headless browser and background tasks, stalling foreground UX. | Add `requires_desktop_lock: bool = True` property on `BaseExecutionProvider`. Headless Playwright and background WinCOM set this to `False`, allowing concurrent background execution without starving foreground desktop interactions. | `core/application_intelligence/frameworks/base.py`, `core/application_intelligence/interaction_router.py` |
| **MED-2** | Medium | Unsanitized DOM serialization in Stagehand `observe()` enables indirect prompt injection. | Implement a strict DOM pre-filter removing hidden elements, `<script>`, `<style>`, `<iframe>`, base64 URIs, and truncating text nodes (>500 chars). | `core/application_intelligence/frameworks/browser/stagehand_intelligence.py` |
| **MED-3** | Medium | Circuit breaker lacks Half-Open transition and non-mutating health probes. | Implement a formal 3-state Circuit Breaker (`CLOSED`, `OPEN`, `HALF_OPEN`) with a 60-second cooldown and non-mutating synthetic `health_probe()` probe. | `core/application_intelligence/frameworks/registry.py` |

---

### 3. Detailed Technical Architecture & Contracts

#### 3.1 Base Execution Provider (`core/application_intelligence/frameworks/base.py`)
```python
class ProviderHealthStatus(str, Enum):
    AVAILABLE = "available"      # Circuit CLOSED: Ready for execution
    DEGRADED = "degraded"        # High latency / intermittent failures
    UNAVAILABLE = "unavailable"  # Circuit OPEN: Bypassed for cooldown period
    PROBING = "probing"          # Circuit HALF_OPEN: Testing synthetic probe

@dataclass
class ExecutionRequest:
    action_id: str
    target_app: str
    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    approval_token: str | None = None          # Reconciles CRIT-2
    session_nonce: str | None = None           # Reconciles CRIT-2
    caller_identity: str = "orchestrator"      # Reconciles CRIT-2
    timeout: float = 15.0

@dataclass
class ExecutionResponse:
    action_id: str
    status: str = "done"                       # "done", "unverified", "failed"
    facts: list[str] = field(default_factory=list)
    evidence_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    duration_ms: float = 0.0

class BaseExecutionProvider(ABC):
    """Abstract base class for all external framework execution providers."""
    name: str = "base_provider"
    requires_desktop_lock: bool = True         # Reconciles MED-1

    @abstractmethod
    def health_probe(self) -> bool:
        """Non-mutating synthetic check of provider availability (reconciles MED-3)."""
        pass

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        """Execute a single atomic action (reconciles HIGH-1)."""
        pass

    def rollback(self, request: ExecutionRequest, response: ExecutionResponse) -> bool:
        """Polymorphic provider-specific state rollback (reconciles HIGH-4)."""
        return False
```

#### 3.2 Desktop Concurrency Reentrancy Fix (`core/application_intelligence/desktop_lock.py`)
Reconciles **CRIT-1**:
```python
def release(self) -> None:
    """Release the desktop interaction lock, preserving stack integrity under reentrancy."""
    if self._acquired:
        try:
            # Reconciles CRIT-1: Mutex is strictly held while popping the stack
            if DesktopInteractionLock._owner_stack:
                if DesktopInteractionLock._owner_stack[-1] == self.owner:
                    DesktopInteractionLock._owner_stack.pop()
                else:
                    try:
                        DesktopInteractionLock._owner_stack.remove(self.owner)
                    except ValueError:
                        pass
        finally:
            self._acquired = False
            self._lock.release()
```

#### 3.3 Authoritative Token & Invariant Enforcement in InteractionRouter
Reconciles **CRIT-2**, **MED-1**, **HIGH-4**:
```python
def dispatch(self, contract: ActionContract, ...) -> ExecutionEvidence:
    # 1. UIPI Elevation Barrier check
    app_def = self.registry.find_app(contract.target_app)
    if app_def and app_def.integrity_level == "High":
        return ExecutionEvidence(status="failed", error_message="UIPI_ELEVATION_BARRIER")

    # 2. Select execution provider via ProviderRegistry
    provider = self.provider_registry.select_provider(contract)
    
    # 3. PolicyEngine authorization gate (CRIT-2)
    if contract.requires_approval:
        from core.security.policy_engine import OperationRequest, PolicyEngine
        token_obj = None
        if contract.approval_token:
            token_obj = PolicyEngine.instance().get_token(contract.approval_token)
        verdict = PolicyEngine.instance().evaluate_operation(OperationRequest(
            tool_name=contract.operation,
            agent_name="application_agent",
            arguments=contract.parameters,
            approval_token=token_obj,
        ))
        if verdict.requires_approval or not verdict.is_authorized:
            return ExecutionEvidence(status="unverified", error_message=f"APPROVAL_REQUIRED: {verdict.reason}")

    # 4. Conditional DesktopInteractionLock acquisition (MED-1)
    lock_ctx = DesktopInteractionLock(owner=f"router_{contract.action_id}") if provider.requires_desktop_lock else nullcontext()
    
    with lock_ctx:
        resp = provider.execute(req)
        if not resp.is_success:
            # 5. Polymorphic provider-specific rollback (HIGH-4)
            provider.rollback(req, resp)
            # Physical fallback dismissal
            from core.screenact import press
            press("escape")
        return resp
```

#### 3.4 Microsoft UFO² Backend Constraint
Reconciles **HIGH-1**, **HIGH-2**, **HIGH-3**:
- Completely strip UFO² `HostAgent` and `AppAgent` autonomous reasoning loops.
- Wrap WinCOM calls in `com_apartment()` initializing both `pythoncom` and `comtypes`.
- Implement `wincom_bridge.py` supporting Word (`SaveAs`, `ExportAsFixedFormat`, `InsertParagraph`), Excel (`WriteCell`, `ReadCell`, `ExportCSV`), and PowerPoint (`ExportPDF`).
- Implement timeout-guarded execution with hard termination protection against hanging COM dialogues.

#### 3.5 Playwright & Stagehand Browser Execution
Reconciles **CRIT-3**, **HIGH-1**, **MED-2**:
- `PlaywrightBrowserBackend`: Manages persistent or isolated browser contexts. Provides deterministic locators and download verification with SHA-256.
- `StagehandBrowserIntelligence`:
  - `observe()`: Sanitizes DOM (strips hidden elements, scripts, styles, iframes) and locates actionable elements.
  - `act()`: Dispatches **single atomic actions** only. Forbids autonomous multi-step loops.
  - `extract()`: Returns structured data matching schema.
  - **Prompt Injection:** All text extracted or observed is wrapped via `core.outcome.untrusted(source="web", content=...)`.

#### 3.6 OmniParser Hardware Feasibility & Lazy Loading
Reconciles **CRIT-4**:
- Check GPU availability and free VRAM:
  ```python
  def is_gpu_capable() -> bool:
      if not torch.cuda.is_available(): return False
      free_bytes, total_bytes = torch.cuda.mem_get_info()
      return (free_bytes / (1024 ** 3)) >= 3.0  # Require 3.0 GB free VRAM
  ```
- If free VRAM < 3.0 GB (such as current RTX 3050 with ~2.4 GB free), do NOT load Florence-2 on GPU.
- Route to lightweight ONNX Runtime YOLOv8-nano + local OCR pipeline (<150ms on CPU).
- Maintain lazy loading: zero weights loaded at startup.
- Provider remains experimental and disabled by default.

#### 3.7 WorkflowStore Promotion Invariant
Reconciles **HIGH-5**:
- In `WorkflowStore.save_workflow()`, force `trust = SkillTrustLevel.UNVERIFIED` regardless of `workflow.validation_state`.
- Promotion to `TRUSTED` requires explicit invocation of `promote_workflow(workflow_id, human_confirmed=True)`.

---

### 4. Verification & Acceptance Scenarios

The test suite in `tests/test_advanced_framework_integration.py` will verify:
1. `DesktopInteractionLock` reentrancy pop under concurrency (CRIT-1).
2. `ApprovalToken` enforcement for privileged framework actions (CRIT-2).
3. `core.outcome.untrusted` format alignment (CRIT-3).
4. OmniParser 3.0 GB VRAM pre-flight check and INT8 ONNX fallback (CRIT-4).
5. UFO² and Stagehand stateless single-action execution without autonomous loops (HIGH-1).
6. COM dual-library apartment initialization without `0x800401f0` errors (HIGH-3).
7. Polymorphic `rollback()` execution on provider failure (HIGH-4).
8. Strict `UNVERIFIED` trust boundary in `WorkflowStore` (HIGH-5).
9. Headless Playwright concurrent execution without `DesktopInteractionLock` (MED-1).
10. Stagehand DOM sanitization pre-filtering hidden prompt injections (MED-2).
11. Circuit breaker transition (`CLOSED` -> `OPEN` -> `HALF_OPEN` -> `CLOSED`) with `health_probe()` (MED-3).
12. Playwright deterministic web search and download hash verification.
13. WinCOM Office document generation and PDF export.
14. pywinauto `ValuePattern` / `InvokePattern` execution without mouse movements.
15. End-to-end task execution combining Playwright, WinCOM, and DevTools.
