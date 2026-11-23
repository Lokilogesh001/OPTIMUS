# OPTIMUS Reconciled Architecture Specification: Universal Visual Task Verification & Autonomous Computer Operation

**Milestone:** Priority 0 — Universal Visual Task Verification & Closed-Loop Autonomous Operation  
**Authoritative Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Target Environment:** Python 3.13.16 (Windows 11 AMD64, 16 Cores, 16 GB RAM)  
**Baseline Commit:** `ca89686`  
**Status:** **APPROVED & RECONCILED** (Incorporates all 7 Goldfish Binding Mandates)

---

## 1. Executive Summary & Architectural Concordance

Following the Elephant Architectural Investigation and the adversarial Goldfish Architecture Review (`docs/audits/autonomous-verification/goldfish-architecture-review.md`), this document formalizes the binding design and execution specification for Priority 0: **Universal Visual Task Verification and Autonomous Computer Operation**.

The core principle of this architecture is:
> **Execution is an intent; observation is a hypothesis; only independent physical and visual verification produces proof.**
> Neither LLM text generation, agent planning claims, tool exit codes, nor OS mouse/keyboard delivery (`act_res.ok`) shall ever certify task completion. Every terminal state must be backed by an immutable, SHA-256 signed evidence record in the tamper-evident `EvidenceLedger`.

---

## 2. Reconciliation of Binding Architectural Mandates

Every mandate issued by the Goldfish reviewer is reconciled with a concrete implementation blueprint:

| Mandate | Mandate Summary | Reconciled Implementation Specification |
|---|---|---|
| **M-1** | **Zero Simulated Verifications** | Delete all mock outcomes in `BrowserAdapter`, `MREOutcomeVerifier`, and `task_supervisor.py`. Replace dummy string writes and hardcoded `True` returns with real OS/DOM/UIA queries. |
| **M-2** | **4 New Verifiers in `core/verification/`** | Implement `DesktopWindowVerifier`, `BrowserPageVerifier`, `MediaPlaybackVerifier`, and `UIControlVerifier` in `core/verification/verifiers.py` with typed, schema-validated contracts and register them in `VerificationEngine`. |
| **M-3** | **Terminal Evidence Classification** | Expand `VerificationStatus` in `core/verification/types.py` with: `COMPLETED_VERIFIED`, `COMPLETED_WITH_LIMITATIONS`, `ACTION_EXECUTED_UNVERIFIED`, `FAILED`, `BLOCKED`, `CANCELLED`. Backward-compatible alias `PASS` -> `COMPLETED_VERIFIED`, `FAIL` -> `FAILED`. |
| **M-4** | **Temporal Playback Verification** | `MediaPlaybackVerifier` enforces two-point sampling ($\Delta t \ge 1.5\text{s}$), asserts $t_1 > t_0$, checks for ad container presence (`.ytp-ad-player-overlay`, "Skip Ad"), and verifies player error absence. |
| **M-5** | **Honest Minimization Protocol** | Strict separation: Minimized/background windows are inspected via Win32 metadata (`IsIconic`, `GetWindowTextW`, `IsHungAppWindow`). Visual bitmap capture and UIA element walks for minimized windows are prohibited without explicit foreground restoration (`focus_window(hwnd, restore_minimized=True)`). |
| **M-6** | **Hard Idempotency Firewall** | `Postcondition` and subtasks include `is_idempotent: bool`. Idempotent operations retry at most 2 times (`MAX_RETRIES = 2`). Non-idempotent operations (payment, send, submit, delete) immediately abort recovery on failure and transition to `BLOCKED`. |
| **M-7** | **Main-Thread Decoupling & COM Safety** | Verification and actuation routines execute off the Qt main thread on dedicated worker threads with `CoInitializeEx(None, COINIT_MULTITHREADED)` initialization. UI updates occur via signals or EventBus. |

---

## 3. Detailed Subsystem Specifications

### 3.1 Type System & Evidence Ledger Expansion (`core/verification/types.py`)

#### New `PostconditionType` Enum Values:
```python
class PostconditionType(str, Enum):
    FILESYSTEM = "FILESYSTEM"
    PROCESS = "PROCESS"
    DATA = "DATA"
    COMMAND = "COMMAND"
    DESKTOP_WINDOW = "DESKTOP_WINDOW"    # Native window existence, visibility, focus, responsiveness
    BROWSER_PAGE = "BROWSER_PAGE"        # Browser active tab, URL, domain, loaded state
    MEDIA_PLAYBACK = "MEDIA_PLAYBACK"    # Audio/video player state, temporal time progress, ad absence
    UI_CONTROL = "UI_CONTROL"            # Specific UI Automation element role, value, enabled state
```

#### New `VerificationStatus` Enum Values:
```python
class VerificationStatus(str, Enum):
    PASS = "COMPLETED_VERIFIED"                         # Alias for backward compatibility
    COMPLETED_VERIFIED = "COMPLETED_VERIFIED"           # Indisputably confirmed by physical/visual proof
    COMPLETED_WITH_LIMITATIONS = "COMPLETED_WITH_LIMITATIONS" # Done, but non-critical condition unverified
    ACTION_EXECUTED_UNVERIFIED = "ACTION_EXECUTED_UNVERIFIED" # Action executed, but postcondition cannot be independently inspected
    FAILED = "FAILED"                                   # Postcondition assertion violated
    FAIL = "FAILED"                                     # Alias for backward compatibility
    BLOCKED = "BLOCKED"                                 # Action requires operator intervention / non-idempotent safety halt
    CANCELLED = "CANCELLED"                             # Operation aborted by user or timeout
    ERROR = "ERROR"                                     # Verifier itself crashed during inspection
```

#### Field Additions:
- `Postcondition.is_idempotent: bool = True`
- `VerificationRecord.is_verified: bool` (property: True if status == `COMPLETED_VERIFIED`)

---

### 3.2 Pluggable Verifiers Implementation (`core/verification/verifiers.py`)

#### 1. `DesktopWindowVerifier` (`verifier:desktop_window`)
- **Handled Type:** `PostconditionType.DESKTOP_WINDOW`
- **Spec Fields:**
  - `app_name: str` (e.g., "notepad.exe", "chrome.exe")
  - `title_pattern: str` (regex or substring)
  - `must_be_active: bool` (must match foreground HWND)
  - `must_be_visible: bool` (must be `IsWindowVisible` and not `IsIconic`)
  - `check_responsive: bool` (`IsHungAppWindow(hwnd) == False`)
  - `min_dimensions: tuple[int, int]` (optional minimum width & height)
- **Methodology:** Queries `win32gui` and `win32process` directly. Checks window rect via `GetWindowRect`. Returns clear, non-heuristic diagnostics.

#### 2. `BrowserPageVerifier` (`verifier:browser_page`)
- **Handled Type:** `PostconditionType.BROWSER_PAGE`
- **Spec Fields:**
  - `expected_domain: str` (e.g., "youtube.com", "github.com")
  - `url_pattern: str` (regex for active URL)
  - `title_pattern: str` (regex for active tab title)
  - `prohibited_patterns: list[str]` (e.g., ["404 Not Found", "This site can't be reached", "ERR_NAME_NOT_RESOLVED"])
  - `wait_settle_timeout: float` (default 5.0s, polling every 250ms)
- **Methodology:** Traverses the browser's UIA structure to inspect the active Omnibox/Address Bar (AutomationId `addressEditBox` or ControlType `Edit`) and tab titles. Verifies the absence of browser error banners.

#### 3. `MediaPlaybackVerifier` (`verifier:media_playback`)
- **Handled Type:** `PostconditionType.MEDIA_PLAYBACK`
- **Spec Fields:**
  - `assert_playing: bool = True`
  - `time_delta_sampling: bool = True`
  - `min_progress_seconds: float = 1.0`
  - `sample_interval_seconds: float = 1.5`
  - `prohibited_overlays: list[str]` (["Video unavailable", "Private video", "Sign in"])
  - `detect_ad_overlay: bool = True`
- **Methodology:**
  1. Inspects active window (must be browser or media player).
  2. Reads player controls and current timestamp $T_0$.
  3. Waits $1.5\text{s}$ and reads timestamp $T_1$.
  4. Confirms $T_1 - T_0 \ge 1.0\text{s}$.
  5. Inspects UIA accessibility elements for ad banners (`ytp-ad-player-overlay`, "Skip Ad", "Ad 1 of 2"). If an ad is present, reports `status=BLOCKED` or `FAILED` ("Ad playing; video not started").
  6. Inspects for video error banners.

#### 4. `UIControlVerifier` (`verifier:ui_control`)
- **Handled Type:** `PostconditionType.UI_CONTROL`
- **Spec Fields:**
  - `role: str` (Button, Edit, Checkbox, Text, etc.)
  - `name_pattern: str` (Regex or substring for accessibility label)
  - `expected_value: str | None` (Expected ValuePattern text)
  - `is_enabled: bool = True`
  - `is_focused: bool | None = None`
  - `window_target: int | str | None = None` (HWND or app name)
- **Methodology:** Scopes UIA tree search strictly to the target window `HWND` to avoid whole-screen performance penalties. Confirms element presence, interactive state, and value.

---

### 3.3 Closed-Loop Task Verification Coordinator (`core/vision/task_verifier.py`)

A new module `ClosedLoopTaskVerifier` that acts as the single authoritative executor for desktop operations:
```python
class ClosedLoopTaskVerifier:
    """Coordinates Closed-Loop Execution: Snapshot -> Act -> Settle -> Observe -> Verify -> Recover."""
```
- **Lifecycle:**
  1. **Pre-Observation:** Capture foreground window, monitor state, and dHash.
  2. **Actuation Lock:** Acquire `DesktopInteractionLock` to prevent voice or telemetry interruptions.
  3. **Actuation Dispatch:** Deliver keyboard or mouse event via `core.screenact`.
  4. **Screen Settle:** Wait up to $250\text{ms}-500\text{ms}$ for Windows DWM and UI compositing to stabilize.
  5. **Post-Observation:** Capture new foreground window, active tab, UIA subtree, and dHash.
  6. **Independent Verification:** Delegate to `VerificationEngine.verify_postcondition()`.
  7. **Bounded Recovery:**
     - If verified $\to$ write `COMPLETED_VERIFIED` to `EvidenceLedger` and return.
     - If failed and `is_idempotent == False` $\to$ write `BLOCKED` to `EvidenceLedger` and stop.
     - If failed and `is_idempotent == True` and `retry_count < 2`:
       * Diagnose anomaly (unexpected popup $\to$ send `Escape`; lost focus $\to$ `focus_window()`; slow loading $\to$ wait $2\text{s}$).
       * Re-execute actuation and re-verify.
     - If retries exhausted $\to$ write `FAILED` with diagnostic reason.

---

### 3.4 Integration & Refactoring Plan

1. **`core/application_intelligence/adapters/browser.py`:**
   - Remove simulated download string creation. Use real browser file downloads with `FilesystemVerifier` checking `Downloads/`.
   - In `execute(command="navigate", ...)`, remove `time.sleep(0.5)` dummy return. Invoke `BrowserPageVerifier` to confirm page navigation and domain arrival.
2. **`core/application_intelligence/interaction_router.py`:**
   - In `_execute_visual_tier`, wrap actuation in `ClosedLoopTaskVerifier` instead of returning `status="done"` on `act_res.ok`.
3. **`core/orchestrator/mre.py`:**
   - Replace dummy boolean returns in `MREOutcomeVerifier` with invocations of `VerificationEngine`.
4. **`core/orchestrator/orchestrator.py`:**
   - In subtask completion checks, ensure `VerificationRecord` is written to `EvidenceLedger` before transitioning to `SubtaskStatus.COMPLETED`.

---

## 4. UI Status Surface Integration

`gui/optimus/section_vision.py` will be enriched with a real-time **Verification Status Card**:
- Current Operational Phase: `IDLE`, `EXECUTING`, `OBSERVING`, `VERIFYING`, `RECOVERING`.
- Current Target: Window HWND / Browser Tab / UI Element.
- Verdict: `COMPLETED_VERIFIED` (Green), `RECOVERING` (Yellow), `FAILED` (Red), `BLOCKED` (Orange).
- Cryptographic Proof: Truncated SHA-256 evidence hash linked to `EvidenceLedger`.

---

## 5. Acceptance Test Verification Plan

A dedicated test suite `tests/test_closed_loop_verification_acceptance.py` will execute the 14 real-world operational scenarios:
1. Application Launch & Window Focus.
2. Window Minimization & Honest Restoration Verification.
3. Browser Navigation & URL Verification.
4. YouTube Search & Temporal Media Playback Verification.
5. Browser Tab Switching across Windows.
6. Background & Discarded Tab Discovery.
7. File Persistence & Cryptographic Hash Matching.
8. Scoped Filesystem Access & Path Traversal Prevention.
9. UI Button Actuation & State Transition Verification.
10. Modal Dismissal & Recovery from Blocking Dialogs.
11. Idempotency Firewall & Non-Idempotent Action Halting.
12. Dead-Click Detection & Anomaly Diagnosis.
13. Immutable Evidence Ledger Integrity & Tamper Rejection.
14. End-to-End Multi-Step Task Execution with Physical Evidence.

---

*Specification locked and reconciled with Goldfish mandates. Proceeding to Phase 2 implementation.*
