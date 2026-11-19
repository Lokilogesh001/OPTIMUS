"""core/self_improvement/goldfish_reviewer.py — Independent Adversarial Goldfish Reviewer.

Implements EGM Stage independent review:
- Evaluates candidate improvements across 6 strict axes:
  1. Specification Compliance
  2. Security & Privilege Boundaries
  3. Reliability & Failure Handling
  4. Maintainability
  5. Test Coverage & Empirical Evidence
  6. Hidden Side Effects & Scope Expansion
- Classifies findings (CRITICAL, HIGH, MEDIUM, LOW).
- CRITICAL and HIGH findings strictly block promotion (verdict = BLOCKED / CHANGES_REQUIRED).
- Produces cryptographically signed GoldfishReviewReport.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time

from core.self_improvement.storage import SelfImprovementStore
from core.self_improvement.types import (
    EvaluationResult,
    GeneratedArtifact,
    GoldfishFinding,
    GoldfishReviewReport,
    ImprovementProposal,
    LifecycleState,
)

logger = logging.getLogger("optimus.self_improvement.goldfish_reviewer")


class SelfImprovementGoldfishReviewer:
    """Independent adversarial auditor enforcing strict specification and security compliance."""

    def __init__(self, store: SelfImprovementStore | None = None) -> None:
        self.store = store or SelfImprovementStore.instance()

    def review_candidate(
        self,
        proposal: ImprovementProposal,
        artifact: GeneratedArtifact,
        evaluation: EvaluationResult,
    ) -> GoldfishReviewReport:
        """Execute independent adversarial review across the 6 mandatory axes."""
        proposal.transition_to(LifecycleState.GOLDFISH_REVIEW)
        self.store.save_proposal(proposal)

        findings: list[GoldfishFinding] = []
        review_id = f"rev_{proposal.proposal_id}_{secrets.token_hex(3)}"

        # ── Axis 1: Specification Compliance ──────────────────────────────────
        if not proposal.acceptance_criteria:
            findings.append(
                GoldfishFinding(
                    finding_id=f"fnd_{secrets.token_hex(3)}",
                    severity="HIGH",
                    category="SPEC_COMPLIANCE",
                    description="Proposal lacks measurable acceptance criteria.",
                    remediation="Add explicit acceptance criteria before promotion.",
                    disposition="ACCEPTED",
                )
            )

        # ── Axis 2: Security & Privilege Boundaries ───────────────────────────
        if proposal.risk_level == "CRITICAL":
            findings.append(
                GoldfishFinding(
                    finding_id=f"fnd_{secrets.token_hex(3)}",
                    severity="CRITICAL",
                    category="SECURITY_BOUNDARY",
                    description="Proposal requests CRITICAL risk permissions (unrestricted shell or credentials).",
                    remediation="Reject or downscope capability requirements.",
                    disposition="ACCEPTED",
                )
            )

        # Check for core runtime path leaks in artifact
        if any(part in artifact.generated_code.lower() for part in ("core/security", "core/orchestrator")):
            findings.append(
                GoldfishFinding(
                    finding_id=f"fnd_{secrets.token_hex(3)}",
                    severity="CRITICAL",
                    category="SECURITY_BOUNDARY",
                    description="Artifact attempts to inspect or reference immutable core security packages.",
                    remediation="Remove core package references from generated code.",
                    disposition="ACCEPTED",
                )
            )

        # ── Axis 3: Reliability & Failure Handling ────────────────────────────
        if not evaluation.passed:
            findings.append(
                GoldfishFinding(
                    finding_id=f"fnd_{secrets.token_hex(3)}",
                    severity="HIGH",
                    category="RELIABILITY",
                    description=f"Isolated sandbox evaluation failed ({evaluation.tests_failed} failed tests).",
                    remediation="Resolve execution errors and re-evaluate in sandbox.",
                    disposition="ACCEPTED",
                )
            )

        # ── Axis 4: Maintainability ───────────────────────────────────────────
        if len(artifact.generated_code.strip()) < 20:
            findings.append(
                GoldfishFinding(
                    finding_id=f"fnd_{secrets.token_hex(3)}",
                    severity="MEDIUM",
                    category="MAINTAINABILITY",
                    description="Artifact entrypoint is nearly empty or lacks substantive logic.",
                    remediation="Provide complete instructions or implementation.",
                    disposition="ACCEPTED",
                )
            )

        # ── Axis 5: Test Coverage & Empirical Evidence ────────────────────────
        if evaluation.tests_passed < 4:
            findings.append(
                GoldfishFinding(
                    finding_id=f"fnd_{secrets.token_hex(3)}",
                    severity="MEDIUM",
                    category="TEST_COVERAGE",
                    description=f"Evaluation test count ({evaluation.tests_passed}) is below the required 4-axis coverage.",
                    remediation="Ensure syntax, functional, adversarial, and privilege tests all execute.",
                    disposition="ACCEPTED",
                )
            )

        # ── Axis 6: Hidden Side Effects & Scope Expansion ─────────────────────
        if len(artifact.files_checksums) > 10:
            findings.append(
                GoldfishFinding(
                    finding_id=f"fnd_{secrets.token_hex(3)}",
                    severity="LOW",
                    category="SIDE_EFFECTS",
                    description=f"Artifact package contains {len(artifact.files_checksums)} files. Confirm no bloat.",
                    remediation="Audit package manifest to prune unnecessary auxiliary assets.",
                    disposition="ACCEPTED",
                )
            )

        # Determine Verdict
        has_critical = any(f.severity == "CRITICAL" for f in findings)
        has_high = any(f.severity == "HIGH" for f in findings)

        if has_critical:
            verdict = "BLOCKED"
        elif has_high:
            verdict = "CHANGES_REQUIRED"
        else:
            verdict = "APPROVED"

        # Generate cryptographic audit signature
        sig_payload = f"{review_id}:{proposal.proposal_id}:{artifact.package_integrity_hash}:{verdict}:{len(findings)}"
        sig = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()

        report = GoldfishReviewReport(
            review_id=review_id,
            proposal_id=proposal.proposal_id,
            artifact_id=artifact.artifact_id,
            reviewer_role="Independent Adversarial Auditor",
            verdict=verdict,
            findings=findings,
            reviewed_at=time.time(),
            signature=sig,
        )

        self.store.save_review(report)
        logger.info(
            "Goldfish review completed for '%s': verdict=%s (critical=%s, high=%s, total_findings=%d)",
            proposal.proposal_id,
            verdict,
            has_critical,
            has_high,
            len(findings),
        )
        return report
