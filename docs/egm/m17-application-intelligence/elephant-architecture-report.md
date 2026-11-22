# OPTIMUS M17 — Universal Application Intelligence & Autonomous Computer Operation
## Elephant Architecture & Engineering Specification Report

**Document ID:** EGM-M17-SPEC-001  
**Author:** Principal Windows Systems Architect & Chief AI Engineer (Elephant Persona)  
**Date:** 2026-11-22  
**Status:** PROPOSED FOR GOLDFISH ADVERSARIAL REVIEW  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`

---

## 1. Executive Mission & System Vision

OPTIMUS Milestone 17 (M17) transitions OPTIMUS from an assistant that executes fixed tools and pre-programmed scripts into an **extensible, general-purpose autonomous Windows desktop agent**. The platform must be capable of:
1. **Discovering** installed applications and OS capabilities dynamically across the host system.
2. **Understanding** application interfaces, capabilities, and file associations without hardcoded keyword bindings.
3. **Operating** applications through an intelligent **5-tier interaction hierarchy** (Native API -> App Integrations -> Windows UI Automation -> Browser Automation -> Visual Computer Use).
4. **Learning** unfamiliar applications autonomously by inspecting accessibility/UI trees, forming candidate action plans, testing them under safe bounds, observing state transitions, and persisting verified skills.
5. **Operating Real-World Applications** reliably:
   - **WhatsApp**: contact resolution, conversation selection, message drafting, human-approval gating, delivery verification.
   - **Web Browser & Search**: multi-tab orchestration, web queries, cross-source comparison, content extraction, download verification.
   - **Documents & Files**: intelligent file-type association (PDF, DOCX, XLSX, PPTX, TXT, CSV, images, code), target application launch, and document load verification.
   - **Development Tools**: VS Code project opening, workspace inspection, bounded shell execution, test runner diagnosis.
6. **Preserving Central Governance**: strictly respecting CBAC permissions, `PolicyEngine` approval tokens for consequential mutations, NTFS boundaries, and prompt-injection defenses for untrusted UI text.

---

## 2. Universal Application Intelligence Subsystem Architecture

The new subsystem is isolated in `core/application_intelligence/`:

```
core/application_intelligence/
├── __init__.py                # Public exports and convenience accessors
├── types.py                   # Canonical data models (AppDefinition, InteractionHierarchy, ActionContract)
├── discovery.py               # Windows discovery engine (Start Menu, Registry, Running Processes, Associations)
├── registry.py                # ApplicationRegistry singleton (caching, query by name/ext/id, running state)
├── capabilities.py            # Formal capability descriptors, parameter schemas, pre/post-conditions
├── adapters/
│   ├── __init__.py            # Adapter registry and factory
│   ├── base.py                # BaseApplicationAdapter lifecycle (Discover, Connect, Inspect, Execute, etc.)
│   ├── whatsapp.py            # WhatsAppAdapter (WhatsApp Desktop / Web automation with approval gate)
│   ├── browser.py             # BrowserAdapter (Web search, multi-source research, tab/window reuse)
│   ├── document.py            # DocumentAdapter (File type routing, associated app launch, load verification)
│   └── devtools.py            # DevToolsAdapter (VS Code, Windows Terminal, Git, Python test diagnosis)
├── interaction_router.py      # Dynamic 5-tier method selector with fallback and recovery
├── learning_engine.py         # Autonomous Application Learning Engine (UIA tree exploration & skill synthesis)
└── workflow_store.py          # Bridges learned workflows into existing SkillRegistry and SkillManifest
```

---

## 3. Detailed Component Specifications

### 3.1 Data Models (`core/application_intelligence/types.py`)

- **`InteractionMethod(str, Enum)`**:
  - `P1_NATIVE_API`: Official API, SDK, CLI, or IPC protocol.
  - `P2_APP_INTEGRATION`: Dedicated application adapter / connector.
  - `P3_UI_AUTOMATION`: Windows UI Automation accessibility tree (UIA, Win32 controls, menus, hotkeys).
  - `P4_BROWSER_AUTOMATION`: Browser CDP / DevTools / DOM automation.
  - `P5_VISUAL_COMPUTER_USE`: Screen observation, OCR, coordinate click/type via `core/computer_use/`.
- **`AppCategory(str, Enum)`**:
  - `COMMUNICATION`, `BROWSER`, `DOCUMENT_VIEWER`, `OFFICE`, `DEVELOPMENT`, `SYSTEM_UTILITY`, `MEDIA`, `OTHER`.
- **`AppDefinition`**:
  - `app_id: str` (e.g. `com.whatsapp.desktop`, `microsoft.vscode`, `google.chrome`, `adobe.acrobat`).
  - `name: str` (e.g. `WhatsApp`, `Visual Studio Code`, `Google Chrome`).
  - `aliases: list[str]` (e.g. `["code", "vscode"]`, `["chrome"]`, `["whatsapp", "wa"]`).
  - `executable_path: str | None`.
  - `package_id: str | None` (for UWP/AppX packages).
  - `category: AppCategory`.
  - `supported_methods: list[InteractionMethod]`.
  - `associated_extensions: list[str]` (e.g. `[".py", ".ts", ".json"]` or `[".pdf"]`).
  - `is_running: bool`.
  - `main_hwnd: int | None`.
  - `pids: list[int]`.
  - `metadata: dict[str, Any]`.
- **`ActionContract`**:
  - `action_id: str`, `target_app: str`, `operation: str`, `parameters: dict[str, Any]`.
  - `preconditions: list[str]`, `expected_outcome: str`, `requires_approval: bool`.
  - `verification_strategy: str` (e.g. `window_title`, `process_running`, `ui_element_exists`, `file_created`).
- **`ExecutionEvidence`**:
  - `action_id: str`, `is_success: bool`, `method_used: InteractionMethod`.
  - `actual_outcome: str`, `evidence_data: dict[str, Any]`, `error_message: str | None`.
- **`LearnedWorkflow`**:
  - `workflow_id: str`, `target_app: str`, `goal: str`, `action_sequence: list[dict]`.
  - `validation_state: str` (`DISCOVERED`, `UNVERIFIED`, `TESTED`, `VERIFIED_SKILL`).
  - `preconditions: list[str]`, `recovery_hints: list[str]`.

---

### 3.2 Discovery Engine (`core/application_intelligence/discovery.py`)

Discovers installed Windows applications using multiple authoritative OS mechanisms:
1. **Start Menu Programs**:
   - Enumerates `.lnk` and `.appref-ms` files in `%ProgramData%\Microsoft\Windows\Start Menu\Programs` and `%AppData%\Microsoft\Windows\Start Menu\Programs`. Resolves shortcut targets via `winshell` or `win32com.client` or native binary shell link parsing.
2. **Windows Registry App Paths**:
   - `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths`
   - `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths`
   - Extracts binary paths for command names (e.g. `chrome.exe`, `code.cmd`, `notepad.exe`).
3. **Windows Uninstall Registry (Installed Software Inventory)**:
   - `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
   - `HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall`
   - `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
   - Reads `DisplayName`, `DisplayVersion`, `InstallLocation`, `DisplayIcon`.
4. **Running Windows & Processes**:
   - Cross-references discovered applications with running PIDs and active HWNDs via `EnumWindows` and `psutil`.
5. **File Extension Associations**:
   - Queries `HKCR\<ext>` -> ProgID -> `Shell\Open\Command` to resolve default and candidate applications for file types.
6. **Safety Boundary**:
   - Rejects executables discovered in temporary, download, or untrusted user directories (`%TEMP%`, `AppData\Local\Temp`).

---

### 3.3 Application Registry (`core/application_intelligence/registry.py`)

- `ApplicationRegistry` singleton:
  - Maintains the in-memory graph of all discovered and registered applications.
  - Exposes fast lookups:
    - `find_app(query: str) -> AppDefinition | None`: Searches by `app_id`, `name`, `aliases`, or binary basename.
    - `find_by_extension(ext: str) -> list[AppDefinition]`: Finds applications registered to open `.pdf`, `.docx`, `.py`, etc.
    - `list_running_apps() -> list[AppDefinition]`: Returns all currently open applications with window handles.
    - `refresh(force: bool = False)`: Triggers background discovery refresh with thread safety.

---

### 3.4 Standardized Adapter Lifecycle (`core/application_intelligence/adapters/base.py`)

Every application adapter inherits from `BaseApplicationAdapter` and implements 8 standardized lifecycle methods:
1. **`discover() -> bool`**: Verifies whether the application is installed or reachable.
2. **`connect() -> bool`**: Establishes session, launches process, or attaches to existing window.
3. **`inspect() -> dict[str, Any]`**: Inspects current application state (active view, open document, window title, UI tree).
4. **`execute(action: ActionContract) -> ExecutionEvidence`**: Dispatches the action through the appropriate tier.
5. **`observe() -> dict[str, Any]`**: Captures post-action state via UIA or visual screenshot.
6. **`verify(contract: ActionContract, evidence: ExecutionEvidence) -> bool`**: Asserts physical outcome against expected contract.
7. **`recover(error: str) -> bool`**: Bounded recovery (dismiss modal dialog, focus window, re-navigate).
8. **`disconnect() -> None`**: Detaches cleanly without killing user sessions unless requested.

---

### 3.5 Real-World Application Adapters

1. **`WhatsAppAdapter` (`adapters/whatsapp.py`)**:
   - Supports WhatsApp Desktop and WhatsApp Web.
   - Discovers running instances or launches via registered protocol `whatsapp://` or desktop binary.
   - **Contact Resolution**: Exact or fuzzy search for target contact. If ambiguous (>1 match), raises `AmbiguousRecipientError` with candidate list for user clarification.
   - **Message Composition**: Fills message input with exact drafted text.
   - **Human Authorization Gate**: If action is `send_message`, mandates human confirmation or valid `ApprovalToken` before sending. Never sends blind messages.
   - **Delivery Verification**: Asserts that message element appears in the conversation stream.

2. **`BrowserAdapter` (`adapters/browser.py`)**:
   - Integrates with Chrome, Edge, and Brave.
   - Reuses existing browser windows/tabs where available via `BrowserIntelligence`.
   - **Web Search**: Queries duckduckgo / google, extracts search results, fetches top informative links.
   - **Source Comparison**: Aggregates information from multiple sources, compares specifications/claims, and structures a coherent response with citations.
   - **Navigation & Downloads**: Opens requested URLs, triggers and monitors downloads, verifies downloaded file hash and location.

3. **`DocumentAdapter` (`adapters/document.py`)**:
   - Handles PDF, DOCX, XLSX, PPTX, TXT, CSV, images, and code.
   - Resolves target files using `UnifiedFilesystemService.search_files`.
   - Disambiguates multiple candidates when the query is ambiguous ("that report").
   - Resolves appropriate installed viewer/editor using Windows file associations or explicit preferred tools (e.g. Acrobat/Edge for PDF, Excel for XLSX, VS Code for code).
   - Launches via `WindowsRuntimeBroker` and verifies window title or PID has loaded the document.

4. **`DevToolsAdapter` (`adapters/devtools.py`)**:
   - Integrates with VS Code, Windows Terminal, PowerShell, and Git.
   - Opens project workspaces (`code <folder>`), verifies workspace file tree.
   - Executes approved commands in bounded developer environments.
   - Inspects test suites (e.g. `pytest`, `npm test`) and parses terminal stdout/stderr for intelligent error diagnosis.

---

### 3.6 Universal Interaction Router (`core/application_intelligence/interaction_router.py`)

- Coordinates operation dispatch using the strict 5-tier priority ladder:
  - Checks if Priority 1 (Native API/CLI) is available for the application.
  - If not, checks Priority 2 (Dedicated Adapter).
  - If not, uses Priority 3 (Windows UI Automation tree inspection & control manipulation).
  - If not, uses Priority 4 (Browser Automation for web targets).
  - If not, falls back to Priority 5 (Visual Computer Use via `core/computer_use/`).
- Implements automatic fallback: If Priority 2/3 fails or encounters an unhandled control, logs failure and attempts Priority 5 with Visual Supervisor.

---

### 3.7 Autonomous Application Learning Engine (`core/application_intelligence/learning_engine.py`)

- **Exploration & Discovery Phase**:
  - Connects to an unfamiliar application's top-level window.
  - Traverses the UI Automation accessibility tree: identifies menu bars, tabs, toolbars, buttons, text fields, lists, and dialogs.
  - Filters out decorative or layout containers, building an operational semantic map of the application.
- **Goal Mapping & Candidate Workflow Construction**:
  - Given a user objective (e.g. "Export project to PDF"), matches goal semantics against identified button labels, menu items, or keyboard shortcuts (`File` -> `Export` -> `PDF`).
  - Synthesizes a structured `ProposedWorkflow` containing sequential action steps with preconditions and expected transitions.
- **Safe Exploratory Execution & Observation**:
  - Executes safe, read-only or reversible exploratory steps under the supervision of `TaskSupervisor`.
  - Captures pre- and post-action UI snapshots and dhash visual diffs.
- **Verification & Reusable Skill Promotion**:
  - If the candidate workflow achieves the target state and passes validation, strips user-specific PII/credentials.
  - Wraps the procedure into a verified `SkillManifest` and registers it directly into `SkillRegistry` via `workflow_store.py`.
  - Future tasks invoking this workflow reuse the validated procedure deterministically without re-exploring from scratch.

---

### 3.8 Natural Language Task Understanding Integration (`core/orchestrator/`)

- Upgrade `MREClassifier` and `MREPipeline` (`core/orchestrator/mre.py`):
  - Add semantic application task recognition:
    - Communication intent: `text <recipient> <message>`, `send message on whatsapp`, `message <recipient>`.
    - Web research intent: `search for <query> and compare`, `find information on <topic>`.
    - Document open intent: `open <document_name>`, `view that pdf`, `open my report`.
    - Development intent: `open <project> in vs code`, `run tests for <module>`, `continue coding`.
    - Dynamic application launch: resolves target application names against `ApplicationRegistry` dynamically instead of fixed 7-item regex.
  - Add context reference resolution:
    - Resolves references like `"this file"`, `"that PDF"`, `"there"`, `"previous window"`, `"continue"`.
  - Add ambiguity detection:
    - Detects multiple contact or file candidates and triggers clarification prompts rather than guessing.

---

### 3.9 UI Integration (`gui/optimus/section_applications.py`)

- Dedicated **APPLICATIONS** workspace tab:
  - **Toolbar**: `REFRESH INVENTORY`, `LEARN UNFAMILIAR APP`, `RUN APPLICATION TASK`, `TEST ADAPTERS`.
  - **Discovered Applications Table**: Name, App ID, Category, Supported Methods, Status (Installed / Running), Associated Extensions.
  - **Running Instances Deck**: Active window titles, PIDs, current interaction tier.
  - **Adapters & Health Panel**: Status of WhatsApp, Browser, Document, and DevTools adapters.
  - **Learned Application Skills Panel**: Catalog of autonomous workflows learned and verified by OPTIMUS, with options to inspect, test, or re-verify.
- Adheres strictly to the refined de-slopped command center design tokens: Deep Midnight Navy (`#070b14`), Charcoal surfaces (`#0c1322`), 4px rounded rectangular panels, hairline borders (`#1e293b`), and geometric unicode glyphs (`⎈` / `◫`).

---

## 4. Security & Safety Architecture

1. **CBAC & Tier Enforcement**:
   - `APP_DISCOVERY`, `APP_INSPECT`, `APP_ACTIVATE`: Tier 1 (Routine / Auto-approved).
   - `APP_LAUNCH`, `BROWSER_NAVIGATE`, `FILE_OPEN`: Tier 2 (Standard / Monitored).
   - `SEND_EXTERNAL_MESSAGE`, `APP_MUTATE_DATA`, `PURCHASE_ACTION`: Tier 3 (Privileged / Critical — Requires `ApprovalToken` and explicit human confirmation).
2. **Untrusted UI Text Sanitization**:
   - Any text read from on-screen application windows, web pages, or email/message bodies is treated as untrusted external content.
   - Sanitized to prevent prompt injection attacks from malicious web pages or documents attempting to issue unauthorized OPTIMUS directives.
3. **Exploratory Safety Boundaries**:
   - During autonomous learning, actions that could cause data loss (e.g. Delete, Format, Wipe, Overwrite) or financial transactions are strictly blocked by safety tripwires.

---

## 5. Acceptance Verification Plan (20 Test Scenarios)

The milestone verification will execute all 20 mandatory acceptance tests specified in the prompt:
1. Discover installed applications.
2. Open an application that is not already running.
3. Activate an existing application instance.
4. Open a file using the correct associated application.
5. Find and open a document when multiple folders are involved.
6. Open WhatsApp.
7. Resolve a contact correctly (with ambiguity handling).
8. Compose and send an authorized test message.
9. Verify the resulting message state.
10. Search the web and return useful sourced results.
11. Open a browser tab and navigate to a requested page.
12. Open a PDF in an appropriate application.
13. Open a development project in VS Code.
14. Execute a test and inspect its actual result.
15. Operate an unfamiliar application using controlled discovery.
16. Convert a successful workflow into a reusable skill.
17. Reuse the learned skill in a subsequent task.
18. Detect and recover from an application interaction failure.
19. Preserve permissions and workspace boundaries.
20. Resume an interrupted multi-application task.

---

## 6. EGM Workflow & Review Gate

This specification is submitted to the independent Goldfish reviewer (`egm-design`) for adversarial review. Implementation will proceed only after all critical and high-severity findings are identified, analyzed, and reconciled.
