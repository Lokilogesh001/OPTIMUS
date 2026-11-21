# Milestone 16: Always-On Visual Awareness, Task Supervision & Desktop Control
## Stage 2: Technical Specification (Reconciled)

**Status:** APPROVED FOR IMPLEMENTATION (Post-Goldfish Reconciliation)  
**Date:** 2026-11-22  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`  

---

## 1. System Architecture Overview

```
                                      ┌─────────────────────────────────────────────────────────────┐
                                      │                     CENTRAL ORCHESTRATOR                    │
                                      │  (Sole Planning, Delegation, Policy & Actuation Authority)   │
                                      └──────────────┬──────────────────────────────▲───────────────┘
                                                     │                              │ Structured Diagnosis &
                                                     │ Tool Execution               │ Recovery Recommendations
                                                     ▼                              │
                                      ┌─────────────────────────────────────────────┴───────────────┐
                                      │                 AUTONOMOUS TASK SUPERVISOR                  │
                                      │     (Passive 10-Step Visual Observation & Verifier)         │
                                      └───────┬──────────────────────┬──────────────────────┬───────┘
                                              │                      │                      │
                   ┌──────────────────────────┴───┐       ┌──────────┴──────────┐   ┌───────┴────────────────────────┐
                   │   ALWAYS-ON SCREEN OBSERVER  │       │ WINDOW INTELLIGENCE │   │   BROWSER TAB INTELLIGENCE     │
                   │ • Event-driven & Adaptive    │       │ • EnumWindows       │   │ • Chrome, Edge, Firefox tabs   │
                   │ • ROI dHash change detection │       │ • State preservation│   │ • Omnibox active URL & domain  │
                   │ • Multi-monitor tracking     │       │ • AttachThreadInput │   │ • Background title & hints     │
                   │ • Universal credential mask  │       │ • Window activation │   │ • Non-mutating passive enum    │
                   └──────────────┬───────────────┘       └──────────┬──────────┘   └───────┬────────────────────────┘
                                  │                                  │                      │
                                  └──────────────────────────┬───────┴──────────────────────┘
                                                             │
                                                             ▼
                                      ┌─────────────────────────────────────────────────────────────┐
                                      │                   UNIFIED VISION SERVICE                    │
                                      │             (Service Facade & Lifecycle Manager)            │
                                      └──────────────────────────────┬──────────────────────────────┘
                                                                     │
                                      ┌──────────────────────────────┴──────────────────────────────┐
                                      │                  VISION COMMAND CENTER UI                   │
                                      │   (PyQt6 Cinematic HUD, Live Preview, Window/Tab Tables)    │
                                      └─────────────────────────────────────────────────────────────┘
```

---

## 2. Core Architectural Invariants

1. **Passive Task Supervision:**
   The `AutonomousTaskSupervisor` is strictly a passive observer and diagnostic verifier. It NEVER issues synthetic clicks, keystrokes, or direct OS actuations. Instead, it computes a structured diagnosis (`SupervisionStepResult`) with a recommended recovery intent (e.g. `recommended_action="refocus_window"`, `recommended_action="retry_idempotent_click"`), returning it to `CentralOrchestrator`. The Central Orchestrator evaluates the recommendation against `PolicyEngine`, verifies capability tiers, logs the side effect, and executes authorized tools.

2. **Browser Tab Information Separation:**
   Browser tab metadata in `BrowserTabInfo` is divided into verified attributes (`title`, `browser`, `window_hwnd`, `is_active`, `is_pinned`, `group_name`) and conditional attributes (`url: str | None`, `domain: str | None`, `domain_hint: str`). URLs are only guaranteed for the active tab (read from the Omnibox / address bar). Background tab domain identification uses title heuristics (`domain_hint`). Deep DOM/URL inspection across background tabs is delegated to Playwright/CDP (`actions/browser_control.py`). Passive enumeration NEVER mutates user state (no auto-expanding collapsed groups).

3. **Win32 Window Focus & State Preservation:**
   Window activation adheres strictly to Windows 10/11 foreground restrictions:
   - Queries `GetForegroundWindow()`, thread IDs, and calls `AttachThreadInput(cur_tid, fg_tid, True)`.
   - Sends a benign key event (`VK_MENU` or zero key event) to satisfy foreground input entitlement.
   - Preserves window geometry: checks `IsZoomed(hwnd)` and `IsIconic(hwnd)`. Maximized minimized windows are restored with `SW_SHOWMAXIMIZED`; normal minimized windows with `SW_RESTORE`; visible windows with `SW_SHOW`.
   - Calls `BringWindowToTop(hwnd)` and `SetForegroundWindow(hwnd)`.
   - Calls `AttachThreadInput(cur_tid, fg_tid, False)`.

4. **GIL Protection, ROI Cropping & Tree Cooldown:**
   - The observer avoids full-screen hashing by computing dHash over the active window's bounding rectangle (ROI).
   - Enforces a minimum 2.0-second cooldown between full UIA tree walks to prevent cursor blinks (1–2 Hz) and tray clocks from pinning CPU cores.

5. **Dead-Click Latency & Idempotency Safeguards:**
   - Dead-click confirmation window is extended to 1.5–2.5 seconds.
   - Evaluates process responsiveness via `user32.IsHungAppWindow(hwnd)`.
   - Actions are flagged with `is_idempotent`. Non-idempotent actions (e.g. buttons named "Submit", "Send", "Pay", "Delete") are NEVER automatically retried; they are escalated to the operator.

6. **Bounded Memory Ring Buffer:**
   - Raw full-resolution screen buffers are ephemeral and freed immediately after dHash computation.
   - UI thumbnails are downscaled immediately to max 480x270 JPEG bytes.
   - Desktop state and supervision event histories are bounded via `collections.deque(maxlen=30)`.

7. **Universal Credential Redaction & Prompt Injection Defenses:**
   - Universal secret masking: inspects UIA `IsPassword` property, masks credential dialogs, and redacts token patterns (`sk-...`, `Bearer ...`).
   - All extracted desktop text is wrapped in `<untrusted_desktop_context>` tags with instruction boundary prompts.

8. **Test Isolation:**
   - `UnifiedVisionService` provides `reset_for_testing()` and supports dependency injection in `CentralOrchestrator(vision_service=...)`.
