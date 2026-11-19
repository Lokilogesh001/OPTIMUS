"""core/self_improvement/types.py — Strongly Typed Data Contracts for Verified Self-Improvement.

Defines:
- LifecycleState: 17 deterministic lifecycle phases from observation to rollback.
- CapabilityGap: Evidence-backed limitation or failure detection.
- ImprovementProposal: Formal change specification with risk classification and acceptance criteria.
- GeneratedArtifact: Staged code package with Merkle root and per-file SHA-256 hashes.
- EvaluationResult: Multi-axis test execution and sandbox telemetry evidence.
- GoldfishFinding & GoldfishReviewReport: Independent adversarial audit verdicts.
- PromotionRecord: Versioned installation history with rollback snapshots.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class LifecycleState(str, Enum):
    """The 17 deterministic phases of the Verified Self-Improvement lifecycle."""
    OBSERVED = "OBSERVED"
    GAP_IDENTIFIED = "GAP_IDENTIFIED"
    PROPOSED = "PROPOSED"
    SPECIFIED = "SPECIFIED"
    GENERATED = "GENERATED"
    ISOLATED = "ISOLATED"
    TESTING = "TESTING"
    EVALUATED = "EVALUATED"
    GOLDFISH_REVIEW = "GOLDFISH_REVIEW"
    POLICY_VALIDATION = "POLICY_VALIDATION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PROMOTED = "PROMOTED"
    ACTIVE = "ACTIVE"
    MONITORING = "MONITORING"
    ROLLBACK_REQUESTED = "ROLLBACK_REQUESTED"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


# Valid state transitions ensuring strictly governed linear and rollback progressions
VALID_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.OBSERVED: {LifecycleState.GAP_IDENTIFIED, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.GAP_IDENTIFIED: {LifecycleState.PROPOSED, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.PROPOSED: {LifecycleState.SPECIFIED, LifecycleState.GENERATED, LifecycleState.ISOLATED, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.SPECIFIED: {LifecycleState.GENERATED, LifecycleState.ISOLATED, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.GENERATED: {LifecycleState.ISOLATED, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.ISOLATED: {LifecycleState.TESTING, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.TESTING: {LifecycleState.EVALUATED, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.EVALUATED: {LifecycleState.GOLDFISH_REVIEW, LifecycleState.REJECTED, LifecycleState.FAILED},
    LifecycleState.GOLDFISH_REVIEW: {
        LifecycleState.POLICY_VALIDATION,
        LifecycleState.SPECIFIED,  # Remediation cycle: re-specify / re-generate
        LifecycleState.REJECTED,
        LifecycleState.FAILED,
    },
    LifecycleState.POLICY_VALIDATION: {
        LifecycleState.AWAITING_APPROVAL,
        LifecycleState.PROMOTED,  # If LOW/routine risk not requiring manual approval
        LifecycleState.REJECTED,
        LifecycleState.FAILED,
    },
    LifecycleState.AWAITING_APPROVAL: {
        LifecycleState.POLICY_VALIDATION,
        LifecycleState.PROMOTED,
        LifecycleState.REJECTED,
        LifecycleState.FAILED,
    },
    LifecycleState.PROMOTED: {LifecycleState.ACTIVE, LifecycleState.ROLLBACK_REQUESTED, LifecycleState.FAILED},
    LifecycleState.ACTIVE: {LifecycleState.MONITORING, LifecycleState.ROLLBACK_REQUESTED},
    LifecycleState.MONITORING: {LifecycleState.ACTIVE, LifecycleState.ROLLBACK_REQUESTED},
    LifecycleState.ROLLBACK_REQUESTED: {LifecycleState.ROLLED_BACK, LifecycleState.ROLLBACK_FAILED},
    LifecycleState.ROLLED_BACK: set(),
    LifecycleState.ROLLBACK_FAILED: set(),
    LifecycleState.REJECTED: set(),
    LifecycleState.FAILED: set(),
}


@dataclass
class CapabilityGap:
    """An empirical capability limitation, missing tool, or recurring failure."""
    gap_id: str
    category: str                    # "TOOL_FAILURE", "SKILL_LIMITATION", "USER_CORRECTION", "MISSION_FAILURE"
    description: str
    source_evidence: list[dict[str, Any]] = field(default_factory=list)
    frequency: int = 1
    confidence: float = 0.5          # 0.0 to 1.0 (requires >= 0.70 to propose)
    impact_score: float = 5.0        # 0.0 to 10.0
    signature: str = ""              # Deterministic deduplication hash
    first_observed_at: float = field(default_factory=time.time)
    last_observed_at: float = field(default_factory=time.time)
    status: str = "OPEN"             # "OPEN", "IN_PROGRESS", "RESOLVED", "IGNORED"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityGap:
        return cls(**data)


@dataclass
class ImprovementProposal:
    """Formal, auditable specification for a verified self-improvement."""
    proposal_id: str
    gap_id: str
    version: str = "1.0.0"
    title: str = ""
    problem_statement: str = ""
    expected_outcome: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    scope: str = "SKILL"             # "SKILL", "TOOL_WRAPPER", "WORKFLOW_TEMPLATE"
    required_capabilities: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    risk_level: str = "LOW"          # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    dependencies: list[str] = field(default_factory=list)
    test_plan: dict[str, Any] = field(default_factory=dict)
    rollback_strategy: str = "BACKUP_RESTORE"
    state: LifecycleState = LifecycleState.PROPOSED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    provenance_hash: str = ""
    author_agent_id: str = "self_improvement_generator"  # SEC-04: explicitly bound to prevent self-approval
    remediation_count: int = 0                           # SEC-09: bounded remediation cycles
    max_remediations: int = 2                            # SEC-09: maximum allowable remediation loops
    metadata: dict[str, Any] = field(default_factory=dict)

    def can_transition_to(self, new_state: LifecycleState) -> bool:
        """Verify whether new_state is a permitted progression from current state."""
        return new_state in VALID_TRANSITIONS.get(self.state, set())

    def transition_to(self, new_state: LifecycleState) -> None:
        """Execute state transition with deterministic validation."""
        if not self.can_transition_to(new_state):
            raise ValueError(
                f"ILLEGAL_LIFECYCLE_TRANSITION: Cannot transition proposal '{self.proposal_id}' "
                f"from {self.state.value} to {new_state.value}."
            )
        self.state = new_state
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImprovementProposal:
        d = dict(data)
        if "state" in d and isinstance(d["state"], str):
            d["state"] = LifecycleState(d["state"])
        return cls(**d)


@dataclass
class GeneratedArtifact:
    """Staged candidate code artifact with full package integrity tracking."""
    artifact_id: str
    proposal_id: str
    artifact_type: str               # "SKILL_MD", "SKILL_PACKAGE", "TOOL_CODE"
    staging_path: str
    content_sha256: str = ""         # Entrypoint file SHA-256
    files_checksums: dict[str, str] = field(default_factory=dict)  # SEC-08: rel_path -> SHA-256
    package_integrity_hash: str = "" # SEC-08: Merkle root of files_checksums
    generated_code: str = ""
    author_model: str = "local_orchestrator"
    generated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeneratedArtifact:
        return cls(**data)


@dataclass
class EvaluationResult:
    """Empirical test execution evidence gathered inside an isolated sandbox."""
    evaluation_id: str
    artifact_id: str
    sandbox_type: str = "SUBPROCESS_ISOLATED"
    test_suite_results: dict[str, Any] = field(default_factory=dict)
    tests_passed: int = 0
    tests_failed: int = 0
    logs: str = ""
    resource_metrics: dict[str, float] = field(default_factory=dict)
    passed: bool = False
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationResult:
        return cls(**data)


@dataclass
class GoldfishFinding:
    """Specific adversarial audit finding detected during review."""
    finding_id: str
    severity: str                    # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    category: str                    # "SPEC_COMPLIANCE", "SECURITY_BOUNDARY", "RELIABILITY", "MAINTAINABILITY", "TEST_COVERAGE", "SIDE_EFFECTS"
    description: str
    remediation: str
    disposition: str = "ACCEPTED"    # "ACCEPTED", "REMEDIATED", "DEFERRED", "REJECTED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoldfishReviewReport:
    """Independent adversarial review verdict and finding ledger."""
    review_id: str
    proposal_id: str
    artifact_id: str
    reviewer_role: str = "Independent Adversarial Auditor"
    verdict: str = "APPROVED"        # "APPROVED", "CHANGES_REQUIRED", "BLOCKED"
    findings: list[GoldfishFinding] = field(default_factory=list)
    reviewed_at: float = field(default_factory=time.time)
    signature: str = ""

    @property
    def has_blocking_findings(self) -> bool:
        """CRITICAL and HIGH findings strictly block promotion."""
        return any(f.severity in ("CRITICAL", "HIGH") and f.disposition != "REMEDIATED" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "proposal_id": self.proposal_id,
            "artifact_id": self.artifact_id,
            "reviewer_role": self.reviewer_role,
            "verdict": self.verdict,
            "findings": [f.to_dict() for f in self.findings],
            "reviewed_at": self.reviewed_at,
            "signature": self.signature,
            "has_blocking_findings": self.has_blocking_findings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldfishReviewReport:
        findings = [
            GoldfishFinding(**f) if isinstance(f, dict) else f
            for f in data.get("findings", [])
        ]
        return cls(
            review_id=str(data["review_id"]),
            proposal_id=str(data["proposal_id"]),
            artifact_id=str(data["artifact_id"]),
            reviewer_role=str(data.get("reviewer_role", "Independent Adversarial Auditor")),
            verdict=str(data.get("verdict", "APPROVED")),
            findings=findings,
            reviewed_at=float(data.get("reviewed_at", time.time())),
            signature=str(data.get("signature", "")),
        )


@dataclass
class PromotionRecord:
    """Historical audit record documenting the atomic promotion and active versioning."""
    promotion_id: str
    proposal_id: str
    artifact_id: str
    production_target: str
    promoted_version: str
    previous_version: str | None = None
    approval_token_id: str | None = None
    approver: str = ""
    promoted_at: float = field(default_factory=time.time)
    status: str = "ACTIVE"           # "ACTIVE", "SUPERSEDED", "ROLLED_BACK", "ROLLBACK_FAILED"
    rollback_snapshot_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromotionRecord:
        return cls(**data)
