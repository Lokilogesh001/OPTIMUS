# OPTIMUS Visual Task Verification & Autonomous Computer Operation — Independent Goldfish Architecture Review

**Target Document:** `docs/audits/autonomous-verification/elephant-investigation.md`  
**Reviewer:** Independent Goldfish Architecture & Verification Reviewer (`egm-design`)  
**Workspace:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main` | **Target Platform:** Windows 11 AMD64, Python 3.13.16  
**Verdict:** **CONDITIONAL PASS** (Framework accepted subject to 7 Binding Architectural Mandates)

---

## 1. Executive Assessment

The Elephant investigation (`docs/audits/autonomous-verification/elephant-investigation.md`) correctly diagnoses a critical architectural vulnerability in OPTIMUS: **the systemic conflation of action dispatch with real-world task success.**

Our independent audit of the codebase confirms this pathology across all interaction tiers:
1. `core/application_intelligence/interaction_router.py` (L258): Equates `act_res.ok` (Windows OS accepted the mouse event) with `status="done"`.
2. `core/application_intelligence/adapters/browser.py` (L171-L178, L188-L200): Dispatches `webbrowser.open()`, sleeps 500ms, and unconditionally reports `navigation_verified=True`; simulates downloads by writing dummy strings to disk.
3. `core/orchestrator/mre.py` (L642-L660): In `MREOutcomeVerifier`, postconditions such as `process_running` and `window_focused` unconditionally return `True` or mirror `tool_result.is_success`.
4. `core/vision/task_supervisor.py` (L180-L200): Fabricates synthetic evidence hashes (`ev_vis_...`) and claims success whenever screen dHash changes, ignoring whether the requested state was achieved.
5. `core/verification/engine.py` (L287-L315): Completely lacks verifiers for desktop windows, browser states, or UI elements; unhandled tasks default to a logical pass with zero physical verification.

While the Elephant proposal correctly establishes the closed-loop execution topology (`Pre-Observation -> Action -> Screen Settle -> Post-Observation -> Independent Verification -> Recovery`), the proposal in its current state contains **severe technical blind spots** regarding Windows Desktop Window Manager (DWM) composition, Chromium accessibility tree behavior, dynamic YouTube player components, and Qt event loop responsiveness. 

The proposal is granted a **CONDITIONAL PASS**, contingent upon strict implementation of the binding mandates detailed herein.

---

## 2. Review of the 7 Mandatory Architecture Questions

### Objective 1: Separation of Execution from Verification
*Question:* Does the proposed closed-loop architecture genuinely decouple the agent's claim of execution from independent verification? How will we ensure LLM output or `act_res.ok` is never accepted as proof of success?

**Finding:**
In the proposed diagram, Step 4 (`Action Execution`) is distinct from Step 8 (`Independent Postcondition Verification`). However, the proposal does not detail how this will be enforced at the boundary of `CentralOrchestrator` and `MREPipeline`.
Currently in `core/orchestrator/orchestrator.py` (L1374-L1382), the deterministic fast-path executes tools and immediately publishes `is_verified=True` with `verdict="VERIFIED_DETERMINISTIC"` and `evidence={}`, completely bypassing `_verify_deliverables()`.

**Mandate:**
1. Neither LLM text, agent summaries, tool exit codes, nor `act_res.ok` from `core/screenact.py` shall ever set `subtask.status = SubtaskStatus.COMPLETED` or `task.status = TaskStatus.COMPLETED`.
2. Every subtask MUST transition through `SubtaskStatus.VERIFYING`.
3. Completion requires an independently generated `VerificationRecord` bearing `status=VerificationStatus.PASS`, signed with a SHA-256 integrity hash and written to `EvidenceLedger`.
4. If a subtask produces no verifiable physical or visual postconditions, it MUST NOT receive a fabricated `PASS`. It must be explicitly tagged as `ACTION_EXECUTED_UNVERIFIED` in the ledger.

---

### Objective 2: Postcondition Type Contracts
*Question:* Does the proposal cleanly extend `core/verification/` with dedicated verifiers for `DESKTOP_WINDOW`, `BROWSER_PAGE`, `MEDIA_PLAYBACK`, and `UI_CONTROL`? Are the contracts mathematically unambiguous and enforceable?

**Finding:**
`core/verification/types.py` currently defines only `FILESYSTEM`, `PROCESS`, `DATA`, and `COMMAND`. The Elephant proposal recommends adding `DesktopWindowVerifier`, `BrowserPageVerifier`, `MediaPlaybackVerifier`, and `UIControlVerifier`. However, the specifications for these verifiers lack rigorous field-level validation contracts.

**Mandate:**
The 4 new `PostconditionType` members must be formally added to `core/verification/types.py` with the following non-negotiable contract schemas:

```python
# 1. DESKTOP_WINDOW
spec = {
    "app_name": str,               # e.g., "notepad.exe"
    "title_pattern": str,          # Regex or substring match
    "must_be_active": bool,        # Window must be GetForegroundWindow()
    "must_be_visible": bool,       # IsWindowVisible() and not IsIconic()
    "check_responsive": bool,      # IsHungAppWindow(hwnd) == False
    "min_dimensions": (int, int),  # (min_width, min_height)
}

# 2. BROWSER_PAGE
spec = {
    "expected_domain": str,        # e.g., "youtube.com"
    "url_pattern": str,            # Regex for Omnibox URL
    "title_pattern": str,          # Regex for active tab title
    "prohibited_patterns": list,   # ["404 Not Found", "ERR_NAME_NOT_RESOLVED", "This site can't be reached"]
    "wait_settle_timeout": float,  # Max seconds to wait for page stop/reload cycle
}

# 3. MEDIA_PLAYBACK
spec = {
    "player_selector_role": str,   # Role in accessibility tree
    "assert_playing": bool,        # True = playing, False = paused
    "time_delta_sampling": bool,   # Mandates 2-point time progression check (t1 > t0)
    "min_progress_seconds": float, # Minimum playback advance (e.g. 1.0s)
    "prohibited_overlays": list,   # ["Video unavailable", "Ad in", "Skip Ad"]
}

# 4. UI_CONTROL
spec = {
    "role": str,                   # Button, Edit, Checkbox, Tab, etc.
    "name_pattern": str,           # Element name / accessibility label regex
    "expected_value": str | None,  # Expected value text if applicable
    "is_enabled": bool,            # Element must be interactive
    "is_focused": bool | None,     # Focus state requirement
}
```

---

### Objective 3: Continuous Desktop Awareness & Minimization Reality
*Question:* How does the architecture handle minimized windows (where visual bitmaps cannot update), background windows, and collapsed browser tab groups honestly without guessing or inventing state?

**Finding:**
This is the single largest technical flaw in naive visual agent designs. Under Windows Desktop Window Manager (DWM), **minimized windows (`IsIconic(hwnd) == True`) do NOT render client bitmaps to the desktop framebuffer.** `mss` and GDI `BitBlt` capture black rectangles or stale memory surfaces. Furthermore, `GetWindowRect` on a minimized window returns out-of-bounds sentinel coordinates (e.g., `-32000, -32000`). Similarly, collapsed Chromium tab groups omit hidden tab items from the accessible UIA hierarchy.

**Mandate:**
1. **Strict Dichotomy of Observability:**
   - *OS Metadata State* (`HWND`, PID, process name, window title, `IsIconic()`, `IsWindowVisible()`, `IsHungAppWindow()`) is discoverable out-of-process via Win32 API for minimized and background windows.
   - *Visual Bitmaps & UIA Subtrees* are strictly unobservable for minimized windows.
2. **No Hallucinated Visual Proof:** The verifier MUST NOT attempt visual pixel diffs on minimized windows. If a task requires visual verification of window content, the execution loop MUST explicitly invoke `focus_window(hwnd, restore_minimized=True)` before visual verification is attempted.
3. **Collapsed Tab Groups:** If a target tab belongs to a collapsed group (`is_group_collapsed=True`), the agent cannot read its inner DOM/UIA elements. The agent must first expand the group via `UIA ExpandCollapsePattern` or bring it to the foreground before evaluating postconditions.

---

### Objective 4: YouTube Search & Playback Verification
*Question:* Is the proposed YouTube playback verification algorithm (inspecting watch URL, player container, pause button presence, time progression, error overlay checks) technically sound and robust against YouTube's dynamic web components?

**Finding:**
The Elephant proposal's algorithm (`URL check + player container + pause button + time progression + no error overlay`) is conceptually on the right path, but vulnerable to 3 real-world failure modes:
1. **Pre-Roll / Mid-Roll Advertisements:** An advertisement on YouTube plays within the exact same watch URL (`youtube.com/watch?v=...`) and displays a moving progress bar and "Pause" button. A single time-check will falsely verify playback when the user is actually watching an ad.
2. **Localization Brittleness:** Accessibility labels for the player control change by language (e.g., English "Pause (k)", Spanish "Pausar (k)", German "Pause (k)"). Relying on string literals will break on non-English locales.
3. **Autoplay Blocking:** Modern Chromium blocks unmuted autoplay without prior user interaction. The video will stall at `0:00` with the "Play" button displayed.

**Mandate:**
`MediaPlaybackVerifier` for YouTube must enforce:
1. **Two-Point Temporal Sampling ($\Delta t$):** Record `current_time` at $T_0$, sleep 1.5 seconds, record `current_time` at $T_1$. Assert $T_1 > T_0$.
2. **Ad-State Detection:** Check for presence of ad markers (`.ytp-ad-player-overlay`, "Skip Ad", "Ad 1 of 2", `.ytp-ad-text`). If an ad is active, playback of the *target video* is NOT verified; the agent must wait or actuate the skip button.
3. **Locale-Agnostic State:** Identify the play/pause button by its role (`ROLE_BUTTON`) and toggle state / SVG path identifier / aria-keys (`(k)`), not rigid language strings.
4. **Error Overlay Gate:** Explicitly verify absence of `.ytp-error` and text banners containing "Video unavailable", "Private video", or "Sign in to confirm your age".

---

### Objective 5: Bounded Autonomous Recovery & Idempotency
*Question:* Are non-idempotent actions (sending messages, payments, deletions, submissions) strictly protected from auto-retry loops? How is the max retry count (e.g. 2) enforced?

**Finding:**
`core/vision/task_supervisor.py` defines `_NON_IDEMPOTENT_KEYWORDS` based on heuristic English string matching (`submit`, `send`, `pay`, `delete`). But in `core/orchestrator/orchestrator.py` (L1513-L1541), if `_verify_deliverables` fails, the orchestrator immediately sets `task.status = TaskStatus.FAILED` with zero recovery attempts. Recovery is currently dead code.

**Mandate:**
1. **Contract-Level Idempotency Flag:** Idempotency MUST NOT rely solely on regex word matching. `ActionContract` and `Postcondition` must feature an explicit `is_idempotent: bool` field, declared by the planner and audited by `PolicyEngine`.
2. **Strict Retry Escrow:** Safe actions (navigation, window focus, tab switch, scroll, re-read) may be retried up to a hard ceiling of `MAX_RETRIES = 2`.
3. **Non-Idempotent Firewall:** Any action involving message dispatch, financial payment, database mutation, file deletion, or form submission (`is_idempotent=False` or flagged by `PolicyEngine`) MUST immediately abort recovery upon verification failure, set status to `BLOCKED`, and demand operator intervention.
4. **Recovery Location:** The recovery loop must reside directly within the orchestrator's subtask executor (`_execute_subtask_with_recovery`), ensuring state rollbacks (e.g. pressing `Escape` to close modals) occur before each retry attempt.

---

### Objective 6: Performance & Main-Thread Safety
*Question:* Will running verification loops, UIA queries, and screenshot diffs induce GUI stuttering or freeze the Qt event loop? What caching, throttling, or background thread model must be mandated?

**Finding:**
`core/screenview.py` UI Automation walks (`look()`) take between 200ms and 1500ms on complex applications (e.g., browser with dozens of tabs). Screenshot capture and hashing take 30-50ms. If executed directly on the thread driving PyQt/PySide (`gui/optimus/`), the UI will freeze and Windows will mark the process as "(Not Responding)". Furthermore, Windows COM requires careful thread-apartment initialization (`CoInitializeEx`).

**Mandate:**
1. **Dedicated Worker Thread:** All screen observation, actuation, UIA traversal, and postcondition verification loops MUST run on a dedicated background worker (`TaskExecutionWorker` / `QThread`), communicating with the UI solely via thread-safe Qt signals or the `EventBus`.
2. **COM Apartment Initialization:** Every worker thread touching Windows UI Automation must invoke `ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)` on entry and `CoUninitialize()` on exit.
3. **Scoped UIA Queries:** Full desktop tree scans are prohibited during verification. Verifiers must scope `screenview` inspection strictly to the target window `HWND` bounding rectangle.
4. **Throttled Polling:** Settle loops must poll with an exponential or fixed 250ms sleep, capping total verification wait time at 5.0s per step.

---

### Objective 7: Acceptance Test Matrix (14 Real-World Operations)
*Question:* Does the proposed architecture support passing all 14 acceptance tests on real desktop operations?

**Assessment Matrix:**

| # | Test Operation | Proposal Capability | Codebase Gap to Close | Verdict |
|---|---|---|---|---|
| 1 | Launch App & Verify Window Focus | Covered by `DesktopWindowVerifier` | Wire into `MREOutcomeVerifier` and `orchestrator.py` | **PASS (Ready)** |
| 2 | Restore Window from Minimized | Covered by `DesktopWindowVerifier` | Mandate `SW_RESTORE` + `IsIconic() == False` check | **PASS (Ready)** |
| 3 | Browser Navigate & Verify Active URL | Covered by `BrowserPageVerifier` | Replace `webbrowser.open()` stub in `browser.py` with real Omnibox read | **PASS (Ready)** |
| 4 | YouTube Search & Video Playback | Covered by `MediaPlaybackVerifier` | Implement 2-point time progression & ad overlay detection | **CONDITIONAL** |
| 5 | Switch Browser Tab across Windows | Covered by `BrowserPageVerifier` | Enforce window foreground activation before tab select | **PASS (Ready)** |
| 6 | Background / Minimized Tab Discovery | Covered by `BrowserIntelligence` | Acknowledge domain hint heuristics for background tabs | **PASS (Ready)** |
| 7 | File Persistence & SHA-256 Check | Fully operational in `FilesystemVerifier` | Already passing in `test_verification_engine.py` | **PASS (Verified)** |
| 8 | File Read within Allowed Roots | Fully operational | Already passing | **PASS (Verified)** |
| 9 | UI Button Click & State Transition | Covered by `UIControlVerifier` | Replace `act_res.ok` done status with observed postcondition | **PASS (Ready)** |
| 10 | Dismiss Blocking Dialog & Recover | Covered by Recovery Protocol | Wire modal dismiss (`Escape` / click Close) into retry loop | **PASS (Ready)** |
| 11 | Non-Idempotent Action Guardrail | Covered by Idempotency Gate | Block auto-retry when `is_idempotent=False` | **PASS (Ready)** |
| 12 | Dead-Click Detection | Covered by `task_supervisor.py` | Connect supervisor diagnostic into orchestrator failure branch | **PASS (Ready)** |
| 13 | Tamper-Evident Evidence Ledger | Fully operational in `EvidenceLedger` | Expand status enums to include `COMPLETED_VERIFIED` | **PASS (Verified)** |
| 14 | Compound Multi-Step Workflow | Covered by Closed-Loop Orchestrator | Ensure every step has an independent signed evidence record | **PASS (Ready)** |

**Summary:** All 14 tests are achievable under the closed-loop architecture once the stubs in `browser.py`, `interaction_router.py`, and `mre.py` are replaced by the dedicated verifiers.

---

## 3. Critical Risks and Blind Spots (Audit Findings)

### Finding 1 [BLOCKER]: Fast-Path and MRE Short-Circuiting Verification
- **Affected Scope:** `core/orchestrator/mre.py` (L642-L660), `core/orchestrator/orchestrator.py` (L1374-L1382).
- **Flaw:** `MREOutcomeVerifier` returns `True` for `process_running` and `window_focused` without querying Win32 HWNDs. Fast-path marks tasks `COMPLETED` with empty evidence.
- **Recommendation:** Replace `MREOutcomeVerifier` stubs with direct delegation to `VerificationEngine.verify_postcondition()`. Fast-path must execute verification before declaring `COMPLETED`.

### Finding 2 [BLOCKER]: Simulated Execution in Browser Adapter
- **Affected Scope:** `core/application_intelligence/adapters/browser.py` (L170-L207).
- **Flaw:** Navigation calls `webbrowser.open()` and returns simulated success. Downloads write a fake text file rather than verifying browser download state.
- **Recommendation:** Refactor `BrowserAdapter` to interact with real browser instances via UIA / window management, read the Omnibox URL post-navigation, and verify real filesystem downloads via `FilesystemVerifier`.

### Finding 3 [MAJOR]: Visual Verification Phantom on Minimized Windows
- **Affected Scope:** `core/vision/screen_observer.py`, `core/vision/task_supervisor.py`.
- **Flaw:** Attempting screen observation or pixel diffing on minimized windows produces black/stale frames, leading to false dead-click anomalies or false transition reports.
- **Recommendation:** Add explicit check: if `window.is_minimized`, visual diffing is bypassed. Window state must be verified via Win32 metadata, or the window must be restored via `focus_window()` prior to visual inspection.

### Finding 4 [MAJOR]: False Playback Verification from Pre-Roll Ads on YouTube
- **Affected Scope:** `core/verification/verifiers.py` (`MediaPlaybackVerifier`).
- **Flaw:** Video player presence and time progression trigger `PASS` during ad playback, certifying success before the requested video ever plays.
- **Recommendation:** Mandate ad banner / skip button absence check and 2-point time delta sampling ($\Delta t \ge 1.5s$) in `MediaPlaybackVerifier`.

### Finding 5 [MAJOR]: UI Freezing on Main Thread during UIA Inspections
- **Affected Scope:** `core/vision/service.py`, `gui/optimus/`.
- **Flaw:** Running deep UIA walks and settle delays synchronously blocks the GUI thread.
- **Recommendation:** Enforce asynchronous background execution for all verification and task execution workflows, utilizing Qt signals for UI updates.

---

## 4. Binding Architectural Mandates

1. **Mandate M-1 (Zero Simulated Verifications):**  
   Delete all mock outcomes in `BrowserAdapter`, `MREOutcomeVerifier`, and `task_supervisor.py`. Every outcome must correspond to an authentic OS/DOM/UIA observation.
2. **Mandate M-2 (New Verifier Implementations):**  
   Implement `DesktopWindowVerifier`, `BrowserPageVerifier`, `MediaPlaybackVerifier`, and `UIControlVerifier` directly in `core/verification/verifiers.py`, fully integrated into `VerificationEngine`.
3. **Mandate M-3 (Terminal Evidence Classification):**  
   Update `core/verification/types.py` to support explicit terminal statuses: `COMPLETED_VERIFIED`, `COMPLETED_WITH_LIMITATIONS`, `ACTION_EXECUTED_UNVERIFIED`, `FAILED`, `BLOCKED`, `CANCELLED`.
4. **Mandate M-4 (Temporal Playback Verification):**  
   `MediaPlaybackVerifier` must sample at two distinct points in time ($t_0$ and $t_1$) separated by at least 1.5 seconds to confirm non-zero playback progress and assert ad absence.
5. **Mandate M-5 (Honest Minimization Protocol):**  
   Disallow visual capture of minimized windows. Window existence must be verified via Win32 `IsWindowVisible` / `IsIconic`; visual verification requires explicit foreground activation.
6. **Mandate M-6 (Hard Idempotency Firewall):**  
   Enforce `MAX_RETRIES = 2` for idempotent actions. Prohibit automated retries for non-idempotent operations (`is_idempotent=False`), immediately escalating to `BLOCKED`.
7. **Mandate M-7 (Main-Thread Decoupling & COM Safety):**  
   All verification routines must run on worker threads with initialized COM multi-threaded apartments, preventing Qt GUI stalls.

---

## 5. Explicit Verdict

### **CONDITIONAL PASS**

The Elephant closed-loop architecture investigation is conceptually sound, addresses the core structural flaw of the agent, and accurately identifies the necessary verification stages. Proceed to implementation strictly in accordance with the **7 Binding Architectural Mandates** above.

*Report submitted by Goldfish Architecture Reviewer (`egm-design`). Ready for Elephant Implementation Plan Formulation.*
