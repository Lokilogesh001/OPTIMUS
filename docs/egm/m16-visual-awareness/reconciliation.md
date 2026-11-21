# Milestone 16: Always-On Visual Awareness, Task Supervision & Desktop Control
## Stage 4: EGM Reconciliation Report

**Date:** 2026-11-22  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`  
**Review Status:** Reconciled & Accepted for Implementation  

---

## 1. Summary of Goldfish Review & Concrete Resolutions

| Finding ID | Severity | Core Issue | Concrete Resolution Implemented in Code |
| :--- | :---: | :--- | :--- |
| **BLOCKER 1** | **CRITICAL** | Supervisor autonomous actuation violates Central Orchestrator & PolicyEngine authority | Re-architected `AutonomousTaskSupervisor` as a strictly passive observer and diagnostic verifier. It outputs `SupervisionStepResult` containing anomalies and `recommended_action`. Actuation and recovery are strictly delegated back to `CentralOrchestrator`, which checks `PolicyEngine` and executes authorized tools. |
| **BLOCKER 2** | **CRITICAL** | False assumption that background tab URLs exist in UIA accessibility tree | Redefined `BrowserTabInfo`: Split metadata into verified attributes (`title`, `browser`, `window_hwnd`, `is_active`, `is_pinned`, `group_name`) and conditional attributes (`url: str \| None`, `domain: str \| None`). URLs are only guaranteed for the active tab via the Omnibox. Domain extraction for background tabs uses `domain_hint`. Passive enumeration does not mutate collapsed groups. |
| **BLOCKER 3** | **CRITICAL** | Win32 `SetForegroundWindow` focus lockouts and maximized state corruption | Implemented the complete Win32 foreground attachment protocol using `AttachThreadInput`, simulated benign key event entitlement, and state preservation: `IsZoomed` -> `SW_SHOWMAXIMIZED`, `IsIconic` -> `SW_RESTORE`, visible -> `SW_SHOW`. |
| **MAJOR 1** | **HIGH** | CPU thrashing, GIL lock, and cursor-blinking false change churn | Implemented ROI cropping (hashing only active window rect) and enforced a strict 2.0-second cooldown on full UIA tree walks to prevent cursor blinking and clock ticks from thrashing the CPU. |
| **MAJOR 2** | **HIGH** | Premature dead-click detection (<500ms) causing double-submit hazards | Extended dead-click observation window to 1.5–2.5s, added `user32.IsHungAppWindow(hwnd)` check, and tagged actions with `is_idempotent`. Non-idempotent actions ("Submit", "Send", "Pay", "Delete") are never auto-retried; they escalate to the operator. |
| **MAJOR 3** | **HIGH** | Unbounded memory growth from high-frequency screenshot retention | Enforced ephemeral raw bitmap lifecycles (freed immediately after dHash computation), downscaled UI thumbnails to max 480x270 JPEG bytes, and bounded state/event history to `collections.deque(maxlen=30)`. |
| **MAJOR 4** | **HIGH** | Visual prompt injection and in-browser credential exposure | Wrapped all extracted desktop text in `<untrusted_desktop_context>` tags, implemented universal UIA `IsPassword` property checking and token regex redaction (`sk-...`, `Bearer ...`), and blanked capture buffers when authentication windows are focused. |
| **NIT 1** | **LOW** | Hard singleton lifecycle impeding test isolation | Added `reset_for_testing()` on `UnifiedVisionService` and supported dependency injection across `CentralOrchestrator`. |

---

## 2. Updated Core Specification Sign-Off

The technical specification in `docs/egm/m16-visual-awareness/technical-specification.md` has been updated with these concrete invariants. With all blockers and high-severity findings fully resolved, implementation may proceed under Phase 5.
