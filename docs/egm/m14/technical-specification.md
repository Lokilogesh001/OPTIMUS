# EGM Stage 1: Technical Specification — Milestone 14 (Verified Self-Improvement)

**Project:** OPTIMUS — Autonomous Personal Agentic AI Assistant  
**Milestone:** Milestone 14: Verified Self-Improvement  
**Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Base Commit:** `cdcca2e`  
**Author:** Lead Systems Architect (Elephant Persona)  
**Date:** 2026-11-19  
**Status:** SPECIFICATION COMPLETE  

---

## 1. System Architecture Overview

The Verified Self-Improvement system is governed by a 15-stage deterministic lifecycle state machine:

```
[Observe] ────────> [Identify Gap] ────> [Propose] ──────> [Specify] ────> [Generate]
                                                                              │
                                                                              ▼
[Goldfish Review] <── [Evaluate] <───── [Test] <────── [Isolate] <────────────┘
        │
        ├──> (Changes Required / Blocked) ──> [Rejected / Remediate]
        ▼
[Policy Validation] ──> [Approval Gate] ──> [Promote] ──> [Monitor]
                                                │              │
                                                │ (Degradation detected)
                                                ▼              ▼
                                           [Rollback] <────────┘
```

---

## 2. Core Subsystem Specifications

### 2.1 Data Contracts (`core/self_improvement/types.py`)

#### LifecycleState Enum
- `OBSERVED`: Raw operational signal recorded.
- `GAP_IDENTIFIED`: Validated, evidence-backed capability gap detected.
- `PROPOSED`: Formal improvement proposal generated.
- `SPECIFIED`: Acceptance criteria, permissions, and test plan specified.
- `GENERATED`: Code generated in staging directory.
- `ISOLATED`: Confined staging workspace prepared.
- `TESTING`: Sandboxed test suite actively executing.
- `EVALUATED`: Empirical test evidence collected and hashed.
- `GOLDFISH_REVIEW`: Adversarial multi-axis review underway.
- `POLICY_VALIDATION`: Central PolicyEngine authorization verified.
- `AWAITING_APPROVAL`: Operator authorization requested (if privileged).
- `PROMOTED`: Atomically installed into production registry.
- `ACTIVE`: Available for agent execution.
- `MONITORING`: Production telemetry and error rate monitored.
- `ROLLBACK_REQUESTED`: Regression detected, rollback initiated.
- `ROLLED_BACK`: Successfully reverted to previous known-good version.
- `REJECTED`: Blocked by policy, review, or operator.
- `FAILED`: Execution, test, or promotion failure.

#### CapabilityGap Dataclass
- `gap_id`: str (e.g., `gap_20261119_a1b2c3`)
- `category`: str (`TOOL_FAILURE`, `SKILL_LIMITATION`, `USER_CORRECTION`, `MISSION_FAILURE`, `UNSUPPORTED_REQUEST`)
- `description`: str
- `source_evidence`: list[dict[str, Any]] (mission_id, subtask_id, error_message, user_query, timestamp)
- `frequency`: int (count of observed occurrences)
- `confidence`: float (0.0 to 1.0)
- `impact_score`: float (0.0 to 10.0)
- `first_observed_at`: float
- `last_observed_at`: float
- `status`: str (`OPEN`, `IN_PROGRESS`, `RESOLVED`, `IGNORED`)
- `metadata`: dict[str, Any]

#### ImprovementProposal Dataclass
- `proposal_id`: str (e.g., `prop_20261119_d4e5f6`)
- `gap_id`: str
- `version`: str (semver, e.g. "1.0.0")
- `title`: str
- `problem_statement`: str
- `expected_outcome`: str
- `acceptance_criteria`: list[str]
- `scope`: str (`SKILL`, `TOOL_WRAPPER`, `WORKFLOW_TEMPLATE`)
- `required_capabilities`: list[str]
- `required_permissions`: list[str]
- `risk_level`: str (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`)
- `dependencies`: list[str]
- `test_plan`: dict[str, Any]
- `rollback_strategy`: str
- `state`: LifecycleState
- `created_at`: float
- `updated_at`: float
- `provenance_hash`: str
- `metadata`: dict[str, Any]

#### GeneratedArtifact Dataclass
- `artifact_id`: str
- `proposal_id`: str
- `artifact_type`: str (`SKILL_MD`, `SKILL_PACKAGE`, `TOOL_CODE`)
- `staging_path`: str
- `content_sha256`: str
- `generated_code`: str
- `author_model`: str
- `generated_at`: float
- `metadata`: dict[str, Any]

#### EvaluationResult Dataclass
- `evaluation_id`: str
- `artifact_id`: str
- `sandbox_type`: str (`SUBPROCESS_ISOLATED`)
- `test_suite_results`: dict[str, Any]
- `tests_passed`: int
- `tests_failed`: int
- `logs`: str
- `resource_metrics`: dict[str, float]
- `passed`: bool
- `evaluated_at`: float

#### GoldfishFinding & GoldfishReviewReport Dataclasses
- Findings classify severity: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`.
- Categories: `SPEC_COMPLIANCE`, `SECURITY_BOUNDARY`, `RELIABILITY`, `MAINTAINABILITY`, `TEST_COVERAGE`, `SIDE_EFFECTS`.
- `GoldfishReviewReport` contains review verdict: `APPROVED`, `CHANGES_REQUIRED`, `BLOCKED`.

#### PromotionRecord Dataclass
- Tracks atomic promotion, version history, previous known-good backup snapshot, approval token ID, and active status.

---

### 2.2 Capability Gap Detector (`core/self_improvement/gap_detector.py`)
1. Analyzes telemetry from mission coordination, tool failures, and explicit user feedback.
2. Applies clustering and thresholding:
   - Minimum frequency threshold: `>= 2` distinct failures, OR an explicit user feedback directive.
   - Minimum confidence threshold: `>= 0.70`.
3. Strict Ethics Guard: Prohibits inferring gaps or skills related to sensitive personal characteristics (religion, politics, health, biometrics).
4. Persists verified gaps with full trace evidence.

### 2.3 Improvement Proposal Generator (`core/self_improvement/proposal_generator.py`)
1. Translates verified gaps into formal specifications.
2. Restricts proposal scope exclusively to declarative skills, instructions, or sandboxed wrappers. Any proposal attempting to modify core runtime files (`core/security/`, `core/orchestrator/`, `core/missions/`) is rejected immediately.
3. Classifies risk based on capability demands:
   - `LOW`: Pure instruction skills (markdown prompts, guidance).
   - `MEDIUM`: Tool-based skills using standard read-only or workspace tools.
   - `HIGH`: Skills requesting network search, disk write outside workspace, or process execution.
   - `CRITICAL`: Any attempt to access system credentials or OS administrative privileges.

### 2.4 Staging Generator & Workspace Isolation (`core/self_improvement/staging_generator.py`)
1. Confined workspace: Creates isolated staging directory at `memory/self_improvement/staging/{proposal_id}/`.
2. Path validation: All file operations are checked with `Path.resolve()`. Any path escaping the staging boundary raises `SecurityViolationError`. Symlinks are strictly prohibited.
3. Content sanitization: Scans generated code for dangerous primitives (`eval`, `exec`, `os.system`, `subprocess.Popen` without sandboxing, token theft patterns).
4. Computes SHA-256 content hash and attaches immutable provenance.

### 2.5 Isolated Sandbox Evaluator (`core/self_improvement/sandbox_evaluator.py`)
1. Execution confinement: Spawns tests inside a dedicated sub-environment with a stripped environment (scrubbed of API keys, master keys, production paths).
2. Resource governance: Enforces execution timeout (max 10 seconds per test) and memory limits.
3. Multi-axis test suites:
   - **Syntax & Schema Test:** Validates formatting, frontmatter, and AST syntax.
   - **Functional Execution Test:** Runs valid execution scenarios to verify expected behavior.
   - **Adversarial Input Test:** Tests resilient handling of malformed inputs, empty payloads, and boundary conditions.
   - **Privilege Boundary Test:** Verifies that attempting to read outside staging or access production secrets fails closed.
4. Fail-closed architecture: Any timeout, unhandled exception, or missing test evidence results in `passed = False`.

### 2.6 Independent Goldfish Reviewer (`core/self_improvement/goldfish_reviewer.py`)
1. Independent verification: Audits proposal, generated diff, test logs, and security scans without inheriting generator assertions.
2. Evaluates across the 6 mandatory axes:
   - Specification compliance
   - Security & privilege boundaries
   - Reliability & failure handling
   - Maintainability
   - Test coverage & evidence quality
   - Hidden side effects & unintended scope expansion
3. Enforces rule: Any `CRITICAL` or `HIGH` finding strictly blocks promotion (`verdict = BLOCKED` or `CHANGES_REQUIRED`).

### 2.7 Promotion & Rollback Engine (`core/self_improvement/promotion_engine.py`)
1. PolicyEngine & Approval Validation:
   - Verifies all automated tests passed.
   - Verifies Goldfish review verdict is `APPROVED`.
   - Checks `PolicyEngine`. For `HIGH` or `CRITICAL` risk artifacts, validates an active, unconsumed `ApprovalToken`.
   - Rejects self-approval: verifies `operator_id` is human, not the generator agent or LLM.
2. Atomic Promotion:
   - If an existing version of the skill exists, creates an immutable backup snapshot in `memory/self_improvement/backups/{skill_id}/{version}/`.
   - Atomically installs the artifact into the production skill directory (`skills/` or `core/skills/`) and registers it in `SkillRegistry`.
3. Deterministic Rollback:
   - `rollback(proposal_id)`: Reverts production skill to the previous backup snapshot, unregisters or updates `SkillRegistry`, and records rollback audit telemetry.

### 2.8 Self-Improvement Coordinator (`core/self_improvement/coordinator.py`)
- Coordinates the complete 15-stage workflow end-to-end with thread safety, state persistence, audit logging, and UI bridge hooks.

---

## 3. Mandatory Security Invariants Verification Plan

1. **Invariant 1 (Untrusted until approved):** Staged skills are never loaded by default in `SkillRegistry`.
2. **Invariant 2 (No self-approval):** `ApprovalManager` and `PromotionEngine` reject model/agent identities as approvers.
3. **Invariant 3 (Reviewer cannot bypass PolicyEngine):** Policy check is an independent mandatory prerequisite gate before promotion.
4. **Invariant 4 (No production secret access in staging):** Environment variables passed to sandbox evaluation are sanitized (`sanitize_environment()`).
5. **Invariant 5 (Core runtime immutable):** Staging generator and proposal generator strictly whitelist artifact destinations to `memory/self_improvement/staging/` and target skill directories. Modifications to `core/` files are rejected.
6. **Invariant 6 (Validated transitions):** State machine enforces deterministic transition table (`VALID_TRANSITIONS`).
7. **Invariant 7 (Hash & version verification):** SHA-256 hash verified before and after promotion.
8. **Invariant 8 (No partial promotion):** Atomic file write and replace (`os.replace`) ensures all-or-nothing activation.
9. **Invariant 9 (Safe cancellation):** Subprocesses and staging directories cleaned up on cancellation.
10. **Invariant 10 (Fail-closed):** Missing evidence, unavailable sandbox, or test failure blocks promotion.
