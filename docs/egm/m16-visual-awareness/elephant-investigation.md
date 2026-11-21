# Milestone 16: Always-On Visual Awareness, Task Supervision & Desktop Control
## Stage 1: Elephant Investigation Report

**Date:** 2026-11-22  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`  
**Author:** Principal Windows Systems Architect & AI Agent Systems Engineer  

---

## 1. Executive Summary & Problem Analysis

Milestone 16 expands OPTIMUS from an agent that acts blind or queries screenshots on demand into a system with **always-on visual awareness**, an **autonomous task visual supervisor**, **complete Windows window intelligence**, and **comprehensive browser tab intelligence**.

Currently, OPTIMUS has several isolated visual and system components:
1. `core/screenview.py`: A robust cross-platform accessibility tree extractor with logical coordinate scaling, UI Automation/PyWinAuto support on Windows, and OCR fallback (`rapidocr_onnxruntime` / `paddleocr`).
2. `core/screenact.py`: Actuator driving mouse and keyboard inputs strictly targeted at logical UI elements.
3. `actions/screen_processor.py`: Snapshot capturer (`mss`, `PIL.ImageGrab`) with image compression.
4. `actions/computer_control.py`: Basic actions (`click`, `type`, `focus_window`, `screen_find`), where `focus_window` only performs fuzzy title matching via pygetwindow/psutil without deep window hierarchy or minimized restoration.
5. `actions/browser_control.py`: Playwright-based browser driver, but lacks discovery of existing native browser windows, tabs, tab groups, vertical tabs, or discarded tabs.
6. `core/windows_runtime/`: Privileged Execution Broker with process and filesystem management, but without native HWND window enumeration, monitor geometry, or window state restoration.
7. `core/orchestrator/`: Central orchestrator running subtasks sequentially, but lacking a closed-loop visual supervisor that validates UI state transitions after each action.
8. `gui/optimus/`: Cinematic blue PyQt6 dashboard with 17 sections, but lacking a dedicated Vision Command Center.

---

## 2. Codebase Inventory & Reusability Assessment

| Existing Component | Path | Current Capabilities | Reusability in M16 | Gaps / Missing Capabilities |
| :--- | :--- | :--- | :---: | :--- |
| **Accessibility Tree** | `core/screenview.py` | UI Automation tree parser, logical coordinate normalization, role mapping | **High (Direct Reuse)** | Currently synchronous and only called on demand; lacks continuous event hooks or change diffing. |
| **Actuator** | `core/screenact.py` | Mouse/keyboard actions with failsafe and logical coordinate targets | **High (Direct Reuse)** | Lacks closed-loop feedback from an active supervisor after an action fires. |
| **Screen Grabber** | `actions/screen_processor.py` | Multi-monitor screen grabbing via `mss`, PIL fallback | **High (Direct Reuse)** | Does not compute differential perceptual hashes (dHash) to avoid duplicate frame processing. |
| **Computer Control** | `actions/computer_control.py` | Keyboard, mouse, text entry, basic focus | **Medium (Refactor/Enhance)** | `focus_window` does not restore minimized windows or handle Win32 window z-order / monitor placement. |
| **Browser Control** | `actions/browser_control.py` | Playwright browser automation | **Medium (Supplement)** | Operates inside its own isolated Playwright profile; cannot see the user's real open browser tabs or tab groups. |
| **Windows Runtime** | `core/windows_runtime/` | Named pipe broker, process lifecycle | **High (Extend)** | Needs window enumeration (`EnumWindows`, `IsIconic`, `ShowWindow`) and monitor topology tools. |
| **Central Orchestrator** | `core/orchestrator/orchestrator.py` | Subtask execution, tool dispatch | **High (Integrate)** | Needs a visual supervisor hook in `_execute_subtask` to observe UI transitions before/after actions. |
| **Optimus GUI** | `gui/optimus/` | Dashboard, Nav bar, Service Bridge | **High (Extend)** | Missing a dedicated `VISION` section (Index 17) displaying live preview, window inventory, and tab stacks. |

---

## 3. Detailed Architectural Blueprint

### 3.1 Always-On Screen Awareness Subsystem (`core/vision/screen_observer.py`)
- **Event-Driven & Adaptive Polling:** Uses Win32 event hooks (`SetWinEventHook` or lightweight polling of `GetForegroundWindow` / window rects at 2–5 Hz) combined with adaptive capture rate.
- **Differential Change Detection:** Computes 64-bit difference hash (dHash) and structural UI element comparison. If the frame has not meaningfully changed (< 2% hamming distance and identical active window), frame analysis is skipped.
- **Multi-Monitor Awareness:** Enumerates all physical displays via `EnumDisplayMonitors` / `mss.monitors`.
- **Privacy & Safety Redaction:** Excludes sensitive apps (1Password, Bitwarden, KeePass), credential prompts (Windows Security, UAC, browser password fields), and banking/financial domains. Sensitive screen areas are masked before visual inspection or persistence.

### 3.2 Autonomous Task Visual Supervisor (`core/vision/task_supervisor.py`)
- Implements the **10-Step Execution Cycle**:
  1. Capture pre-action desktop state.
  2. Parse active application and UI controls via `core/screenview.py`.
  3. Predict expected next state (e.g. dialog opened, text inserted, window activated).
  4. Observe action execution.
  5. Check if action succeeded (element response, focus change).
  6. Diff actual UI against expected UI.
  7. Detect anomalies: dead clicks, disabled buttons, unexpected pop-ups, slow loading/spinners, application crashes, wrong windows/tabs.
  8. Execute recovery strategy: wait for loading, safe retry with bounded backoff (max 3), dismiss blocking modal, re-focus window, or escalate to human approval if destructive.
  9. Inform orchestrator of next action.
  10. Provide physical visual verification evidence (`MREOutcomeVerifier`).

### 3.3 Complete Windows Window Intelligence (`core/vision/window_intelligence.py`)
- Win32 API implementation using `ctypes.windll.user32`:
  - `EnumWindows` & `EnumChildWindows`: Enumerate top-level and owned windows.
  - `IsWindowVisible`, `IsIconic` (minimized), `IsZoomed` (maximized): Accurately classify window states.
  - `GetWindowTextW`, `GetClassNameW`, `GetWindowThreadProcessId`: Resolve title, class, PID, and executable path.
  - `MonitorFromWindow`, `GetMonitorInfoW`: Map window to physical monitor.
  - `ShowWindow(hwnd, SW_RESTORE / SW_MAXIMIZE / SW_MINIMIZE)`: Deterministically restore minimized windows.
  - `SetForegroundWindow`, `BringWindowToTop`: Safely bring window to focus without focus stealing glitches.
  - **Deduplication:** Prevents launching duplicate instances of an application when an existing window can be activated.

### 3.4 Complete Browser Tab Intelligence (`core/vision/browser_intelligence.py`)
- Inspects browser window hierarchies for Chrome, Edge, and Firefox using Windows UI Automation accessibility tree elements (Role: `ROLE_TAB`, `ROLE_BUTTON`, `ROLE_LIST`).
- Discovers:
  - Active and background tabs across all open browser windows.
  - Minimized browser window tabs.
  - Tab groups (named, collapsed, expanded).
  - Vertical tabs (Edge/Chrome).
  - Pinned tabs and discarded/sleeping tabs.
- Capabilities:
  - `list_tabs()`: Full inventory of open tabs with titles, URLs/domains, window HWNDs, active/pinned/group state.
  - `switch_tab(tab_id_or_title)`: Selects tab control via UI Automation invoke pattern or clicks center logical coordinate, restoring the browser window if minimized.
  - `find_tabs(query)`: Semantic and keyword matching across titles, URLs, and domains.
- Context & Stack Classifier (`core/vision/context_classifier.py`):
  - Classifies tabs into functional clusters: Research, Development, Documentation, Communications, Reference.
  - Detects duplicate tabs.

### 3.5 OPTIMUS UI — Vision Command Center (`gui/optimus/section_vision.py`)
- Added to `OptimusNavBar` as `("VISION", "👁")`.
- Live preview canvas showing active desktop/monitor thumbnail.
- Active window HUD and full window list with state indicators (`[MIN]`, `[MAX]`, `[ACTIVE]`).
- Full browser tab inventory with domain pills and group tags.
- Visual Supervisor card showing state (`IDLE`, `SUPERVISING`, `RECOVERING`, `PAUSED`) with live event timeline.
- Privacy controls: Pause/Resume toggle, Application Exclusions, Domain Exclusions, Clear Context.

---

## 4. Next Step
Proceed to Stage 2: Technical Specification.
