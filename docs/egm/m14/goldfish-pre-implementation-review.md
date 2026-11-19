# EGM Stage 2: Independent Adversarial Pre-Implementation Review — Milestone 14 (Verified Self-Improvement)

**Milestone:** Milestone 14: Verified Self-Improvement  
**Target Repository:** `D:\project\srrr\Mark-LVII-main\Mark-LVII-main`  
**Auditor:** Independent Adversarial Security & Architecture Auditor (Goldfish Persona)  
**Target Specifications:** `docs/egm/m14/elephant-investigation.md`, `docs/egm/m14/technical-specification.md`  
**Date:** 2026-11-19  
**Status:** **CHANGES REQUIRED (Blocked pending remediation of 3 Critical, 4 High, 3 Medium, 2 Low findings)**  

---

## 1. Executive Summary & Audit Overview

The proposed M14 architecture introduces a 15-stage self-improvement lifecycle with clear state tracking and multi-axis evaluation. However, an adversarial stress-test of the technical specification against the existing OPTIMUS codebase reveals **critical security boundary contradictions, Windows-specific filesystem lock vulnerabilities, sandbox isolation escapes, and silent policy engine auto-approval gaps**. 

If implemented as originally drafted, the self-improvement subsystem would suffer from:
1. **Core runtime takeover risk** due to conflicting specification of installation directories (`core/skills/` vs Invariant 5).
2. **Silent approval bypass** in `PolicyEngine` because skill promotion is not recognized as a privileged operation under MODERATE/ADVANCED profiles.
3. **Flawed atomic promotion on Windows** because `os.replace` cannot replace directories on Windows Win32 filesystems.
4. **Subprocess sandbox escape** on Windows due to lack of filesystem/network isolation and platform-incompatible Unix `resource` memory limits.
5. **Self-approval vulnerability** resulting from naive substring filtering in `ApprovalManager`.

Below is the exhaustive adversarial audit detailing **12 findings** categorized by severity, along with mandatory remediation requirements.

---

## 2. Summary of Findings

| ID | Title | Severity | Affected Subsystem / File |
|:---|:---|:---|:---|
| **SEC-01** | Core Runtime Immutability Violation & Inconsistent Installation Target | **CRITICAL** | `docs/egm/m14/technical-specification.md` §2.7, Invariant 5 |
| **SEC-02** | PolicyEngine Lacks Self-Improvement Rules (Silent Auto-Approval Gap) | **CRITICAL** | `core/security/policy_engine.py`, Spec §2.7, Invariant 3 |
| **SEC-03** | Subprocess Sandbox Lacks Windows OS-Level Isolation (Filesystem & Network Escape) | **CRITICAL** | `docs/egm/m14/technical-specification.md` §2.5, Invariant 4 |
| **SEC-04** | Programmatic Self-Approval Bypass via Heuristic Approver Filtering | **HIGH** | `core/workflows/approval.py` L65, Spec §2.7, Invariant 2 |
| **SEC-05** | Windows Directory Replace Failure (`os.replace` Win32 Limitation) | **HIGH** | Spec §2.7, Invariant 8, `core/self_improvement/promotion_engine.py` |
| **SEC-06** | Unix-Specific `resource` Memory Limit Assumption Crashes on Windows | **HIGH** | Spec §2.5, `core/self_improvement/sandbox_evaluator.py` |
| **SEC-07** | Orphaned Zombie Subprocesses & Staging Locks on Windows Cancellation | **HIGH** | Spec §2.5, Invariant 9, `core/self_improvement/sandbox_evaluator.py` |
| **SEC-08** | Single `content_sha256` Hash Fails Multi-File Packages (TOCTOU Hazard) | **MEDIUM** | Spec §2.1, Invariant 7, `core/skills/package.py` |
| **SEC-09** | Unbounded Remediation Loop in Lifecycle State Machine (Denial of Service) | **MEDIUM** | Spec §1, §2.1, `core/self_improvement/coordinator.py` |
| **SEC-10** | Missing Terminal State for Rollback Failures (`ROLLBACK_FAILED`) | **MEDIUM** | Spec §2.1, §2.7, `core/self_improvement/promotion_engine.py` |
| **SEC-11** | Gap Detector Lacks Burst Coalescing & Signature Deduplication | **LOW** | Spec §2.2, `core/self_improvement/gap_detector.py` |
| **SEC-12** | Undefined Staging Directory Retention and Cleanup Lifecycle | **LOW** | Spec §2.4, §2.8, `core/self_improvement/staging_generator.py` |

---

## 3. Detailed Adversarial Findings & Remediation Requirements

### SEC-01 [CRITICAL]: Core Runtime Immutability Violation & Inconsistent Installation Target
- **Affected Scope:** `docs/egm/m14/technical-specification.md` (§2.7, Invariant 5)
- **Technical Description:**
  Section 2.7 permitted installing into `skills/` or `core/skills/`. This violates Invariant 5 ("core runtime immutable") and risks code injection into Python package namespace.
- **Remediation Requirement:**
  Mandate that `PromotionEngine` targets exclusively `data/skills/installed/{skill_id}/{version}/` and reuses `NativeSkillInstaller`. Enforce path assertion rejecting any target inside `core/`, `config/`, `.git/`, or `memory/`.

### SEC-02 [CRITICAL]: PolicyEngine Lacks Self-Improvement Rules (Silent Auto-Approval Gap)
- **Affected Scope:** `core/security/policy_engine.py`, Spec §2.7, Invariant 3
- **Technical Description:**
  `PRIVILEGED_APPROVAL_PATTERNS` in `core/security/policy_engine.py` lacked patterns for self-improvement promotion, causing MODERATE and ADVANCED profiles to auto-authorize skill promotions without human approval.
- **Remediation Requirement:**
  Add `"Self-improvement promotion"` regex `r"^(promote_skill|install_skill|self_improvement_.*)$"` to `PRIVILEGED_APPROVAL_PATTERNS`. Mandate `ApprovalToken` for HIGH and CRITICAL risk self-improvements.

### SEC-03 [CRITICAL]: Subprocess Sandbox Lacks Windows OS-Level Isolation
- **Affected Scope:** `docs/egm/m14/technical-specification.md` (§2.5, Invariant 4)
- **Technical Description:**
  On Windows, subprocesses inherit full filesystem ACLs and network access unless explicitly blocked.
- **Remediation Requirement:**
  Restrict `PYTHONPATH` exclusively to staging directory and standard library. Pass dummy offline proxies (`HTTP_PROXY=http://127.0.0.1:0`, `HTTPS_PROXY=http://127.0.0.1:0`). Prohibit dynamic imports and socket/network modules in static code sanitization.

### SEC-04 [HIGH]: Programmatic Self-Approval Bypass via Heuristic Approver Filtering
- **Affected Scope:** `core/workflows/approval.py` (L65), Spec §2.7, Invariant 2
- **Technical Description:**
  Substring filtering (`"llm"`, `"ai"`, `"model"`) in `ApprovalManager` does not stop an agent from passing `approver="operator"`.
- **Remediation Requirement:**
  Track `author_agent_id` on proposals. In `PromotionEngine`, reject any approval token where `token.operator_id == proposal.author_agent_id` or equals automated identities (`"system"`, `"daemon"`, `"generator"`).

### SEC-05 [HIGH]: Windows Directory Replace Failure (`os.replace` Win32 Limitation)
- **Affected Scope:** Spec §2.7, Invariant 8, `core/self_improvement/promotion_engine.py`
- **Technical Description:**
  `os.replace` on directories fails on Windows when the destination directory already exists.
- **Remediation Requirement:**
  Implement two-phase atomic directory promotion with bounded retry loops for Windows file locks:
  1. Rename existing version to `installed/{id}/{version}.bak.{timestamp}`.
  2. Move staging directory to `installed/{id}/{version}`.
  3. Clean up `.bak` on success or restore on failure.

### SEC-06 [HIGH]: Unix-Specific `resource` Memory Limit Assumption Crashes on Windows
- **Affected Scope:** Spec §2.5, `core/self_improvement/sandbox_evaluator.py`
- **Technical Description:**
  `import resource` is unavailable on Windows and raises `ModuleNotFoundError`.
- **Remediation Requirement:**
  Guard with platform check: on Windows, monitor memory via background watchdog polling or Job Objects, avoiding Unix `resource` imports.

### SEC-07 [HIGH]: Orphaned Zombie Subprocesses & Staging Locks on Windows Cancellation
- **Affected Scope:** Spec §2.5, Invariant 9, `core/self_improvement/sandbox_evaluator.py`
- **Technical Description:**
  `proc.terminate()` on Windows leaves child process trees orphaned, keeping file locks open on staging directory.
- **Remediation Requirement:**
  On Windows, invoke `taskkill /F /T /PID {proc.pid}` before attempting `shutil.rmtree()`.

### SEC-08 [MEDIUM]: Single `content_sha256` Hash Fails Multi-File Packages (TOCTOU Hazard)
- **Affected Scope:** Spec §2.1, Invariant 7
- **Technical Description:**
  Single string hash fails to verify multi-file skill packages.
- **Remediation Requirement:**
  Record `files_checksums: dict[str, str]` and Merkle root `package_integrity_hash`. Recalculate and verify across the full staging tree before promotion.

### SEC-09 [MEDIUM]: Unbounded Remediation Loop in Lifecycle State Machine
- **Affected Scope:** Spec §1, §2.1, `core/self_improvement/coordinator.py`
- **Technical Description:**
  No limit on remediation cycles risks infinite oscillation and token waste.
- **Remediation Requirement:**
  Add `remediation_count: int = 0` and `max_remediations: int = 2`. Transition to `REJECTED` if exceeded.

### SEC-10 [MEDIUM]: Missing Terminal State for Rollback Failures (`ROLLBACK_FAILED`)
- **Affected Scope:** Spec §2.1, §2.7, `core/self_improvement/promotion_engine.py`
- **Technical Description:**
  If rollback fails, system has no state to record failure.
- **Remediation Requirement:**
  Add `ROLLBACK_FAILED` to `LifecycleState`. Disable skill in `SkillRegistry` and log alert to `EvidenceLedger`.

### SEC-11 [LOW]: Gap Detector Lacks Burst Coalescing & Signature Deduplication
- **Affected Scope:** Spec §2.2, `core/self_improvement/gap_detector.py`
- **Technical Description:**
  Repeated failures during outages generate duplicate proposals.
- **Remediation Requirement:**
  Implement deterministic gap signature with 1-hour debounce window.

### SEC-12 [LOW]: Undefined Staging Directory Retention and Cleanup Lifecycle
- **Affected Scope:** Spec §2.4, §2.8, `core/self_improvement/staging_generator.py`
- **Technical Description:**
  Staging directories accumulate indefinitely.
- **Remediation Requirement:**
  Clean staging immediately on `PROMOTED`. Prune `REJECTED`/`FAILED` after 7 days.

---

## 4. Verdict & Recommendation

**Audit Verdict: CHANGES REQUIRED (BLOCKED pending remediation of Critical & High findings)**
Proceed to Stage 3 Reconciliation to bind all 12 remediation commitments.
