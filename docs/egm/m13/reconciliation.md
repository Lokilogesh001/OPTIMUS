# Stage 3 Reconciliation Agreement: OPTIMUS Milestone 13 (Personal Intelligence Refinement)

**Project:** OPTIMUS — Autonomous Personal Agentic AI Assistant  
**Milestone:** Milestone 13: Personal Intelligence Refinement  
**Base Commit:** `a156ba5`  
**Governance:** Mandatory Elephant-Goldfish Methodology (EGM)  
**Lead Systems Architect & Auditor:** Elephant & Goldfish Consensus  
**Date:** 2026-11-19  
**Status:** **100% ACCEPTED & BINDING DISPOSITION**

---

## 1. Executive Summary

This document establishes the authoritative technical disposition for all 10 findings (4 High, 4 Medium, 2 Low) raised during the Stage 2 independent Goldfish adversarial review ([goldfish_review.md](file:///d:/project/srrr/Mark-LVII-main/Mark-LVII-main/docs/egm/m13/goldfish_review.md)).

All 10 findings are **ACCEPTED** with concrete technical remediation designs. No findings are deferred or rejected. The implementation will directly incorporate these architectural solutions across the 5 core milestone areas.

---

## 2. Binding Findings Disposition Matrix

| Finding ID | Severity | Disposition | Core Remediation Commitment | Implementation File Target |
|:---|:---|:---|:---|:---|
| **HIGH-01** | High | **ACCEPTED** | Enforce strict domain & intent gating: `PersonalStore` entities are restricted to `PERSONAL_MANAGEMENT` intent queries or explicit personal tasks; general coding/ops agents cannot query personal communications. Apply mandatory PII masking (`mask_pii_string`). | `core/memory/retrieval.py` |
| **HIGH-02** | High | **ACCEPTED** | Eliminate naive substring blacklist. Implement structural XML escaping (`xml.sax.saxutils.escape` on `<`, `>`, `&`, `"`, `'`) and rigid system prompt encapsulation: model is explicitly instructed that `<context_item>` blocks are untrusted passive data that can NEVER be interpreted as instructions. | `core/memory/retrieval.py` |
| **HIGH-03** | High | **ACCEPTED** | Immutable subtask postconditions: orchestrator seals `expected_deliverables` and verification rules at planning time; worker agents cannot mutate them. `MissionCoordinator` mandates verification check before setting `COMPLETED`. Add sandboxed verification check without policy violation. | `core/missions/coordinator.py`, `core/agents/verifier.py` |
| **HIGH-04** | High | **ACCEPTED** | Integrate `AdaptiveResourceManager` concurrency semaphore: throttle concurrent local model executions to 1 (preventing CPU/VRAM thrashing). Provide valid degraded local fallback or clear policy rejection for all 10 task categories under `PRIVACY_STRICT`. | `core/intelligence/task_router.py` |
| **MED-01** | Medium | **ACCEPTED** | In `MemoryLifecycleManager`, enforce Rule 1 ("Explicit Beats Inferred"): reject lower-confidence overwrites unless explicitly confirmed. Populate `superseded_by` pointer. Normalize canonical keys with topic clustering to prevent lexical bypasses. | `core/memory/lifecycle.py`, `core/memory/store.py` |
| **MED-02** | Medium | **ACCEPTED** | Enforce TTL (default 24h) on `TEMPORARY_CONVERSATION_CONTEXT`. Add background/startup sweeper in `MemoryLifecycleManager` that evicts expired entries and applies confidence decay (Rule 3). | `core/memory/lifecycle.py` |
| **MED-03** | Medium | **ACCEPTED** | Unify storage boundaries: M12 `PersonalStore` remains the sole store for personal obligations and communications. `MemoryStore` holds system/project memories (`ACTIVE_PROJECTS`, `LONG_TERM_GOALS`). `core/memory/manager.py` acts as a unified facade without duplicating data. | `core/memory/manager.py`, `core/memory/store.py` |
| **MED-04** | Medium | **ACCEPTED** | Maintain dirty flags and debounced writes in `MemoryStore` to avoid rewriting full JSON files on every `put()`, ensuring <5.0ms query SLA under Windows file locks. | `core/memory/store.py` |
| **LOW-01** | Low | **ACCEPTED** | Extend `MemoryCategory` in `core/memory/types.py` directly with backward-compatible mapping for the 4 legacy categories. Prohibit creating a disjoint `refined_types.py`. | `core/memory/types.py` |
| **LOW-02** | Low | **ACCEPTED** | Enforce per-item token ceilings (max 400 tokens per memory candidate) and calibrated conservative estimation factor (2.8 chars/token) in context budgeting to prevent truncation. | `core/memory/retrieval.py` |

---

## 3. Concrete Implementation Blueprint

### 3.1 Type System & Schema Harmonization (`core/memory/types.py`)
Extend `MemoryCategory` enum in `core/memory/types.py` to support all 9 refined categories while maintaining backward compatibility with legacy categories:
```python
class MemoryCategory(str, Enum):
    # Refined M13 Categories
    USER_PREFERENCES = "USER_PREFERENCES"
    STABLE_USER_CONTEXT = "STABLE_USER_CONTEXT"
    ACTIVE_PROJECTS = "ACTIVE_PROJECTS"
    LONG_TERM_GOALS = "LONG_TERM_GOALS"
    TECHNICAL_ENVIRONMENT = "TECHNICAL_ENVIRONMENT"
    IMPORTANT_DECISIONS = "IMPORTANT_DECISIONS"
    ONGOING_COMMITMENTS = "ONGOING_COMMITMENTS"
    TEMPORARY_CONVERSATION_CONTEXT = "TEMPORARY_CONVERSATION_CONTEXT"
    HISTORICAL_INFORMATION = "HISTORICAL_INFORMATION"

    # Legacy Backward-Compatibility Aliases
    WORKING = "TEMPORARY_CONVERSATION_CONTEXT"
    EPISODIC = "HISTORICAL_INFORMATION"
    SEMANTIC = "STABLE_USER_CONTEXT"
    PROCEDURAL = "TECHNICAL_ENVIRONMENT"
```
Extend `MemoryEntry` with:
- `sensitivity`: `PUBLIC`, `INTERNAL`, `SENSITIVE`, `RESTRICTED`
- `provenance`: `EXPLICIT_USER`, `PERSISTED_PREFERENCE`, `VERIFIED_DELIVERABLE`, `INFERRED_HYPOTHESIS`, `MODEL_GENERATED`
- `expiration_policy`: `NEVER`, `SESSION`, `TTL_SECONDS`, `UNTIL_SUPERSEDED`
- `expires_at`: `float | None`
- `superseded_by`: `str | None`
- `version`: `int`

### 3.2 Memory Lifecycle & Conflict Engine (`core/memory/lifecycle.py`)
Implement `MemoryLifecycleManager`:
- Deduplication: Computes SHA-256 over normalized `(category, canonical_topic, content)`.
- Contradiction Check: Enforces Rule 1 (Explicit Beats Inferred). If new entry confidence < existing entry confidence on the same topic, reject overwrite and log warning.
- Provenance tracking: When an explicit statement updates a prior record, link `old.superseded_by = new.entry_id`.
- Confidence Decay & Sweeping: Method `sweep_expired_and_decay(now)` removes expired TTL entries and decays unconfirmed inferred records.

### 3.3 Context Awareness & Injection-Resistant Retrieval (`core/memory/retrieval.py`)
Implement `ContextRetriever`:
- Step 1-3: Intent classification (`PERSONAL_MANAGEMENT`, `CODE_DEV`, `SYSTEM_OPS`, `RESEARCH`, `GENERAL`).
- Step 4: Domain Gating (HIGH-01) — only query M12 `PersonalStore` if intent is `PERSONAL_MANAGEMENT` or explicitly authorized.
- Step 5-8: Scored candidate ranking with freshness boost and contradiction filtering.
- Step 9: Conservative budget allocation (max 3,000 tokens total, max 400 tokens per candidate).
- Step 10: Structural XML escaping (HIGH-02) via `xml.sax.saxutils.escape()` inside `<context_item>` blocks with strict passive-data system prompts.

### 3.4 Personalization & Preference Engine (`core/memory/personalization.py`)
Implement `PersonalizationEngine`:
- Extracts preferences from explicit instructions ("Always use X"), repeated corrections, and approvals.
- Enforces strict privacy invariants: zero profiling of religion, politics, health, or biometrics.
- Provides `list_preferences()`, `correct_preference()`, and `forget_preference(topic)` APIs.

### 3.5 Adaptive Model Router & Concurrency Guard (`core/intelligence/task_router.py`)
Implement `TaskAwareRouter`:
- Classifies incoming queries into 10 task categories.
- Routes simple conversation/extraction to fast models, complex reasoning/coding to heavy models.
- Integrates `AdaptiveResourceManager` concurrency semaphore: strictly throttles concurrent local model executions to 1 (HIGH-04).
- Records routing history and latency telemetry.

### 3.6 Independent Verification Coordinator (`core/missions/coordinator.py`, `core/agents/verifier.py`)
Refine `MissionCoordinator`:
- Subtask contracts sealed at plan time; worker cannot alter expected deliverables (HIGH-03).
- High-stakes subtasks invoke `VerifierAgent` with isolated context (deliverable files, specs, evidence) before marking `COMPLETED`.
- Verifier performs deterministic checks (file existence, syntax, test execution) without inheriting worker conversation history.

---

## 4. Acceptance Criteria & Test Plan

1. **Memory Lifecycle Tests:** Verify 10-stage lifecycle, 9 categories, Rule 1 conflict rejection, `superseded_by` pointer, and TTL expiration.
2. **Context Retrieval Tests:** Verify domain gating (coding tasks do not leak personal emails), prompt injection XML escaping, and token budgeting.
3. **Personalization Tests:** Verify preference learning, explicit overrides, transparent inspection, and user forgetting.
4. **Adaptive Routing Tests:** Verify task categorization, local concurrency semaphore, and circuit breaker fallback.
5. **Multi-Agent Coordination Tests:** Verify immutable verification contracts and independent verifier evaluation.
6. **Full Regression:** 100% pass rate across all existing test suites.

Proceeding directly to Stage 4 implementation.
