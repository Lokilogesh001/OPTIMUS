# EGM Stage 4: Implementation Plan — Milestone 14 (Verified Self-Improvement)

**Project:** OPTIMUS — Autonomous Personal Agentic AI Assistant  
**Milestone:** Milestone 14: Verified Self-Improvement  
**Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Base Commit:** `cdcca2e`  
**Author:** Lead Systems Architect (Elephant Persona)  
**Date:** 2026-11-19  
**Status:** READY FOR EXECUTION  

---

## 1. Phased Implementation Sequence

### Phase 1: Security Foundation & Policy Integration
- Update `core/security/policy_engine.py`: Add `promote_skill`, `install_skill`, and `self_improvement_.*` to `PRIVILEGED_APPROVAL_PATTERNS`.
- Implement `core/self_improvement/types.py`: All data contracts, enums, `LifecycleState` (with `ROLLBACK_FAILED`), `ImprovementProposal` (with `author_agent_id`, `max_remediations = 2`), and `GeneratedArtifact` (with `files_checksums`, `package_integrity_hash`).
- Implement `core/self_improvement/storage.py`: Thread-safe, multi-process atomic JSON persistence in `memory/self_improvement/`.

### Phase 2: Observation & Specification
- Implement `core/self_improvement/gap_detector.py`: Detect gaps from failed missions, tool errors, and user feedback; burst coalescing; frequency threshold `>= 2` or explicit user directive; confidence `>= 0.70`; sensitive topic prohibition.
- Implement `core/self_improvement/proposal_generator.py`: Generate structured proposals; classify risk (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`); validate scope (strictly reject attempts to mutate core runtime).

### Phase 3: Staging & Sandbox Evaluation
- Implement `core/self_improvement/staging_generator.py`: Staging in `memory/self_improvement/staging/{proposal_id}/`; strict path boundary check; reject symlinks; static AST sanitizer (banning dynamic imports, network libraries, core path references); compute `files_checksums` and package Merkle root.
- Implement `core/self_improvement/sandbox_evaluator.py`: Isolated subprocess execution; offline proxy environment (`HTTP_PROXY=http://127.0.0.1:0`); `PYTHONPATH` restricted to staging dir and standard library; Windows `taskkill /F /T` process tree cleanup; multi-axis test suites (syntax, functional, adversarial input, privilege boundary); fail-closed execution.

### Phase 4: Independent Review & Promotion Governance
- Implement `core/self_improvement/goldfish_reviewer.py`: Automated adversarial audit across 6 axes; classify Critical/High/Medium/Low; CRITICAL/HIGH strictly block promotion.
- Implement `core/self_improvement/promotion_engine.py`: Integration with `PolicyEngine` and `ApprovalManager`; self-approval rejection (`operator_id != author_agent_id`); target confinement strictly to `data/skills/installed/{id}/{version}/`; two-phase atomic directory installation on Windows with `.bak` rollback; deterministic rollback functionality.
- Implement `core/self_improvement/coordinator.py`: 15-stage lifecycle state machine with valid transition matrix and telemetry.

### Phase 5: UI Dashboard Integration
- Implement `gui/optimus/section_self_improvement.py`: Dedicated Self-Improvement section displaying capability gaps, active proposals, test evidence, Goldfish findings, approval gates, and rollback actions.
- Update `gui/optimus/nav_bar.py`, `control_center.py`, and `service_bridge.py`: Wire new section into the navigation bar and bridge.

### Phase 6: Verification & Test Suite
- Author `tests/test_m14_self_improvement.py`: 20+ comprehensive unit and integration tests covering all 16 required areas.
- Run complete regression across all existing milestones (M1–M13).
- Author remaining EGM documentation (`implementation-report.md`, `security-review.md`, `test-evidence.md`, `goldfish-post-implementation-review.md`, `acceptance-report.md`).
- Git commit & push.
