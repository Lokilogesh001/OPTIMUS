"""core/self_improvement/proposal_generator.py — Improvement Proposal Generator.

Translates verified capability gaps into structured improvement specifications with:
- Risk classification (LOW, MEDIUM, HIGH, CRITICAL).
- Scope confinement (skills, tool wrappers; strictly forbidding core runtime tampering).
- SEC-04: Bound author_agent_id to prevent self-approval.
- SEC-09: Bounded remediation loop limits.
- Formal acceptance criteria and deterministic test plans.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from typing import Any

from core.self_improvement.storage import SelfImprovementStore
from core.self_improvement.types import (
    CapabilityGap,
    ImprovementProposal,
    LifecycleState,
)

logger = logging.getLogger("optimus.self_improvement.proposal_generator")

FORBIDDEN_SCOPE_PATTERNS = [
    re.compile(r"\b(policy_engine|command_guard|approval_manager|permissions|security)\b", re.IGNORECASE),
    re.compile(r"\b(core/security|core/orchestrator|core/missions)\b", re.IGNORECASE),
]


class ImprovementProposalGenerator:
    """Generates structured, validated improvement proposals from capability gaps."""

    def __init__(self, store: SelfImprovementStore | None = None) -> None:
        self.store = store or SelfImprovementStore.instance()

    @staticmethod
    def classify_risk(required_capabilities: list[str], required_permissions: list[str], scope: str) -> str:
        """Classify proposal risk level based on required capabilities and permissions."""
        caps = [c.lower() for c in required_capabilities]
        perms = [p.lower() for p in required_permissions]

        if any(p in ("admin", "root", "unrestricted_shell", "credential_access") for p in perms):
            return "CRITICAL"
        if any(c in ("network_connect", "disk_write_unbounded", "process_kill") for c in caps):
            return "HIGH"
        if scope == "TOOL_WRAPPER" or any("tool" in c for c in caps):
            return "MEDIUM"
        return "LOW"

    def create_proposal(
        self,
        gap: CapabilityGap,
        title: str,
        problem_statement: str,
        expected_outcome: str,
        scope: str = "SKILL",
        required_capabilities: list[str] | None = None,
        required_permissions: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        author_agent_id: str = "self_improvement_generator",
    ) -> ImprovementProposal:
        """Create and validate a structured ImprovementProposal."""
        # 1. Scope & Security Boundary Check
        full_text = f"{title} {problem_statement} {scope}"
        if any(pat.search(full_text) for pat in FORBIDDEN_SCOPE_PATTERNS):
            raise ValueError(
                "FORBIDDEN_SCOPE_VIOLATION: Self-improvement proposals cannot modify core runtime or security engines."
            )

        ALLOWED_SCOPES = {"SKILL", "TOOL_WRAPPER", "WORKFLOW_TEMPLATE"}
        clean_scope = scope.strip().upper()
        if clean_scope not in ALLOWED_SCOPES:
            raise ValueError(
                f"INVALID_SCOPE: Scope '{scope}' is not permitted. Must be one of {sorted(ALLOWED_SCOPES)}."
            )

        caps = required_capabilities or []
        perms = required_permissions or []
        risk = self.classify_risk(caps, perms, scope)

        now = time.time()
        proposal_id = f"prop_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"

        # Default acceptance criteria if not provided
        criteria = acceptance_criteria or [
            "Artifact satisfies standard SkillManifest schema and frontmatter",
            "Functional test executes without unhandled exceptions",
            "Adversarial input test handles empty and malformed payloads gracefully",
            "Privilege boundary test proves code cannot access external credentials or unauthorized paths",
        ]

        test_plan: dict[str, Any] = {
            "sandbox_type": "SUBPROCESS_ISOLATED",
            "timeout_seconds": 10.0,
            "test_cases": [
                {"name": "test_schema_validity", "category": "SYNTAX"},
                {"name": "test_functional_behavior", "category": "FUNCTIONAL"},
                {"name": "test_adversarial_resilience", "category": "SECURITY"},
                {"name": "test_privilege_boundaries", "category": "PRIVILEGE"},
            ],
        }

        # Deterministic provenance hash
        prov_payload = f"{proposal_id}:{gap.gap_id}:{title}:{problem_statement}:{risk}:{author_agent_id}"
        prov_hash = hashlib.sha256(prov_payload.encode("utf-8")).hexdigest()

        proposal = ImprovementProposal(
            proposal_id=proposal_id,
            gap_id=gap.gap_id,
            version="1.0.0",
            title=title.strip(),
            problem_statement=problem_statement.strip(),
            expected_outcome=expected_outcome.strip(),
            acceptance_criteria=criteria,
            scope=scope.upper(),
            required_capabilities=caps,
            required_permissions=perms,
            risk_level=risk,
            dependencies=[],
            test_plan=test_plan,
            rollback_strategy="BACKUP_RESTORE",
            state=LifecycleState.SPECIFIED,
            created_at=now,
            updated_at=now,
            provenance_hash=prov_hash,
            author_agent_id=author_agent_id,
            remediation_count=0,
            max_remediations=2,
            metadata={"gap_category": gap.category, "impact_score": gap.impact_score},
        )

        # Update gap status
        gap.status = "IN_PROGRESS"
        self.store.save_gap(gap)
        self.store.save_proposal(proposal)

        logger.info("Generated improvement proposal '%s' for gap '%s' (risk=%s)", proposal_id, gap.gap_id, risk)
        return proposal
