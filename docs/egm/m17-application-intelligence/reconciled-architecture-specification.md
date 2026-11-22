# OPTIMUS M17 — Universal Application Intelligence & Autonomous Computer Operation
## Reconciled Architecture & Implementation Specification

**Document ID:** EGM-M17-SPEC-002 (Reconciled)  
**Authors:** Principal Systems Architect & Senior Security Auditor  
**Date:** 2026-11-22  
**Status:** RECONCILED & APPROVED FOR DIRECT CODE IMPLEMENTATION  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Branch:** `main`

---

## 1. Summary of Adversarial Review Reconciliation

Every finding from the adversarial Goldfish review has been reconciled into the architecture:

| Finding ID | Severity | Reconciliation Implementation Invariant |
|---|---|---|
| **CRIT-1** | **Critical** | `discovery.py` integrates virtual shell folder `shell:AppsFolder`, UWP/MSIX package querying (`Get-AppxPackage` / WinRT), and protocol registration (`HKCR\<protocol>`) so modern packaged apps (WhatsApp Desktop, Windows Terminal, Calculator) are discovered seamlessly. |
| **CRIT-2** | **Critical** | Physical actuation strictly flows through `ToolRegistry.execute()` and `PolicyEngine.evaluate_operation()`. No raw Win32/input primitives are dispatched directly by `InteractionRouter`. Immutable Deny rules, CBAC tiers, single-use `ApprovalToken` nonces, and audit trails remain 100% airtight. |
| **CRIT-3** | **Critical** | Deeply anchors Tier 3 (UI Automation) and Tier 5 (Visual Computer Use) on existing `core/screenview.py` and `core/screenact.py`. Eliminates code duplication, inherits DPI normalization, Unicode folding, and password-field safety. |
| **CRIT-4** | **Critical** | Implements `DesktopInteractionLock` (reentrant process-wide mutex with 30s timeout) to eliminate focus stealing and keystroke interleaving across concurrent orchestrator threads. |
| **CRIT-5** | **Critical** | Constrains `learning_engine.py` to a **Strict Two-Phase Boundary**: Phase 1 is **Passive Zero-Click Exploration** (tree extraction only); Phase 2 is **Active Goal-Directed Execution** matching explicit semantic target paths with negative control filters (`delete`, `format`, `wipe`, `send`, `pay` are hard-blocked). |
| **HIGH-1** | **High** | Establishes clear MRE partition: high-frequency fast-path aliases (`open <app>`) execute in <1ms; complex natural language directives yield to `CentralOrchestrator` planner to extract entities using structured tool schemas. |
| **HIGH-2** | **High** | `InteractionRouter` implements an **Atomic State Verification Protocol**: stepwise checkpoints, dirty input reset (`Ctrl+A -> Backspace` / `Esc`), and view re-inspection via `screenview.look()` before fallback execution. |
| **HIGH-3** | **High** | Ingested UI/web text is wrapped in `<untrusted_external_content source="{app}" window="{title}">...</untrusted_external_content>`. System prompts enforce that untrusted UI text cannot spawn agent commands or override policy. |
| **HIGH-4** | **High** | Consequential communication operations implement the standard OPTIMUS **Suspension & Approval Resumption Cycle** (`SUSPENDED_APPROVAL` status, interactive GUI challenge dialog, `ApprovalToken` injection). |
| **HIGH-5** | **High** | Learned workflows are registered in `WorkflowStore` with `SkillTrustLevel.UNVERIFIED`. Self-promotion to `VERIFIED` is strictly prohibited without operator review or multi-session test validation. |
| **HIGH-6** | **High** | `discovery.py` queries process token integrity levels (`win32security`). If target application is elevated above OPTIMUS, `InteractionRouter` flags `UIPI_ELEVATION_BARRIER` rather than failing opaque clicks. |
| **MED-1** | **Medium** | Renamed hierarchy to `AppInteractionTier` (`TIER_1_NATIVE_API`, `TIER_2_ADAPTER`, `TIER_3_UI_AUTOMATION`, `TIER_4_BROWSER_DOM`, `TIER_5_VISUAL_ACTUATION`). |
| **MED-2** | **Medium** | `BrowserAdapter` directly consumes `BrowserIntelligence` and `UnifiedVisionService` rather than duplicating tab enumeration. |
| **MED-3** | **Medium** | Entity resolution returns typed `ResolutionResult` (`MATCHED`, `AMBIGUOUS`, `NOT_FOUND`) with candidate lists and relevance scores. |
| **MED-4** | **Medium** | `ExecutionEvidence` aligns directly with `core/outcome.py` (`status` in `done`, `unverified`, `failed` + machine-readable fact strings). |
| **LOW-1** | **Low** | `ApplicationRegistry` implements TTL caching (60s for static applications, 2s for running processes). |
| **LOW-2** | **Low** | `BaseApplicationAdapter.recover()` explicitly detects `ROLE_DIALOG` via `screenview.look()` and safely dismisses with `Esc` or Cancel. |

---

## 2. Target Component Hierarchy

```
core/application_intelligence/
├── __init__.py                # Package exports, singleton accessors
├── types.py                   # AppInteractionTier, AppDefinition, ResolutionResult, ActionContract, ExecutionEvidence
├── desktop_lock.py            # DesktopInteractionLock (reentrant, thread-safe, timeout-guarded)
├── discovery.py               # Multi-source discovery (Win32, Start Menu, Registry, MSIX/UWP, Protocols, Assoc)
├── registry.py                # ApplicationRegistry (TTL-cached inventory, lookup, integrity level checks)
├── capabilities.py            # Standard capability definitions, schemas, negative control patterns
├── adapters/
│   ├── __init__.py            # Adapter registry and factory
│   ├── base.py                # BaseApplicationAdapter lifecycle, modal dialog recovery, screenview anchor
│   ├── whatsapp.py            # WhatsAppAdapter (contact resolution, drafting, human approval gate, verification)
│   ├── browser.py             # BrowserAdapter (web search, multi-source synthesis, navigation, downloads)
│   ├── document.py            # DocumentAdapter (association routing, composite match scoring, load verify)
│   └── devtools.py            # DevToolsAdapter (VS Code, Windows Terminal, PowerShell, test diagnosis)
├── interaction_router.py      # 5-tier router, atomic state verification, fallback ladder, ToolRegistry integration
├── learning_engine.py         # 2-phase learning engine (Passive Zero-Click vs Active Goal-Directed)
└── workflow_store.py          # Bridges learned workflows to SkillRegistry with UNVERIFIED initial trust
```

---

## 3. Integration Plan with Core OPTIMUS Services

1. **`core/orchestrator/tools.py`**:
   - Register standardized application tools:
     - `app_discover_inventory`: Discover/query installed and running applications.
     - `app_launch_or_activate`: Launch or bring an application to foreground safely with `DesktopInteractionLock`.
     - `app_execute_action`: Execute an application action contract through `InteractionRouter`.
     - `app_resolve_entity`: Disambiguate contacts or documents.
     - `app_learn_workflow`: Passive inspection and candidate workflow extraction.
2. **`core/orchestrator/mre.py`**:
   - Fast path: Recognize `open/launch <registered_app_alias>` in <1ms via `ApplicationRegistry`.
   - Multi-parameter natural language directives ("text John ...", "search for ... and compare", "open that PDF"): yield to `CentralOrchestrator` planner to extract parameters via structured schemas.
3. **`core/orchestrator/orchestrator.py`**:
   - Enhance planner to dispatch application tools with structured arguments.
   - Enclose ingested screen/web text in `<untrusted_external_content>...</untrusted_external_content>`.
   - Handle `SUSPENDED_APPROVAL` status when `WhatsAppAdapter` or destructive actions require human confirmation.
4. **`gui/optimus/`**:
   - Create `gui/optimus/section_applications.py`: High-density inventory, running processes, adapter cards, learned workflows, and interactive approval prompts.
   - Wire into `gui/optimus/nav_bar.py` (line glyph `⎈` under `EXECUTION & CONTROL`) and `gui/optimus/control_center.py`.

---

## 4. Architectural Sign-Off

The specification is reconciled, hardened against adversarial vulnerabilities, and ready for code implementation.
