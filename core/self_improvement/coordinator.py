"""core/self_improvement/coordinator.py — End-to-End Self-Improvement Lifecycle Coordinator.

Coordinates the 15-stage deterministic workflow:
Observe -> Identify Gap -> Propose -> Specify -> Generate -> Isolate -> Test -> Evaluate -> Goldfish Review -> Policy Validation -> Approval -> Promote -> Monitor -> Rollback

Enforces:
- SEC-09: Strict cap on remediation cycles (max_remediations = 2).
- SEC-12: Automatic staging directory cleanup upon promotion.
- Full telemetry logging and state query APIs for UI bridge integration.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from core.security.policy_engine import ApprovalToken
from core.self_improvement.gap_detector import CapabilityGapDetector
from core.self_improvement.goldfish_reviewer import SelfImprovementGoldfishReviewer
from core.self_improvement.promotion_engine import PromotionEngine
from core.self_improvement.proposal_generator import ImprovementProposalGenerator
from core.self_improvement.sandbox_evaluator import IsolatedSandboxEvaluator
from core.self_improvement.staging_generator import StagingCodeGenerator
from core.self_improvement.storage import SelfImprovementStore
from core.self_improvement.types import (
    CapabilityGap,
    EvaluationResult,
    GeneratedArtifact,
    GoldfishReviewReport,
    ImprovementProposal,
    LifecycleState,
    PromotionRecord,
)

logger = logging.getLogger("optimus.self_improvement.coordinator")


class SelfImprovementCoordinator:
    """Central orchestrator managing the verified self-improvement lifecycle."""

    _instance: SelfImprovementCoordinator | None = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        store: SelfImprovementStore | None = None,
        gap_detector: CapabilityGapDetector | None = None,
        proposal_gen: ImprovementProposalGenerator | None = None,
        staging_gen: StagingCodeGenerator | None = None,
        sandbox_eval: IsolatedSandboxEvaluator | None = None,
        reviewer: SelfImprovementGoldfishReviewer | None = None,
        promotion_engine: PromotionEngine | None = None,
    ) -> None:
        self.store = store or SelfImprovementStore.instance()
        self.gap_detector = gap_detector or CapabilityGapDetector(self.store)
        self.proposal_gen = proposal_gen or ImprovementProposalGenerator(self.store)
        self.staging_gen = staging_gen or StagingCodeGenerator(store=self.store)
        self.sandbox_eval = sandbox_eval or IsolatedSandboxEvaluator(self.store)
        self.reviewer = reviewer or SelfImprovementGoldfishReviewer(self.store)
        self.promotion_engine = promotion_engine or PromotionEngine(self.store)
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> SelfImprovementCoordinator:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Stage 1 & 2: Observe & Identify Gap ───────────────────────────────────

    def observe_gap(
        self,
        category: str,
        description: str,
        evidence: dict[str, Any] | None = None,
        is_explicit: bool = False,
        confidence: float = 0.60,
        impact_score: float = 5.0,
    ) -> CapabilityGap | None:
        """Observe operational signals and record or coalesce capability gaps."""
        with self._lock:
            return self.gap_detector.record_observation(
                category=category,
                description=description,
                evidence=evidence,
                is_explicit_user_feedback=is_explicit,
                confidence=confidence,
                impact_score=impact_score,
            )

    # ── Stage 3 & 4: Propose & Specify ────────────────────────────────────────

    def formulate_proposal(
        self,
        gap_id: str,
        title: str,
        problem_statement: str,
        expected_outcome: str,
        scope: str = "SKILL",
        required_capabilities: list[str] | None = None,
        required_permissions: list[str] | None = None,
        author_agent_id: str = "self_improvement_generator",
    ) -> ImprovementProposal:
        """Formulate a structured improvement proposal from an identified gap."""
        with self._lock:
            gap = self.store.get_gap(gap_id)
            if not gap:
                raise KeyError(f"Capability gap '{gap_id}' not found.")

            return self.proposal_gen.create_proposal(
                gap=gap,
                title=title,
                problem_statement=problem_statement,
                expected_outcome=expected_outcome,
                scope=scope,
                required_capabilities=required_capabilities,
                required_permissions=required_permissions,
                author_agent_id=author_agent_id,
            )

    # ── Stage 5 & 6: Generate & Isolate ───────────────────────────────────────

    def generate_and_stage(
        self,
        proposal_id: str,
        entrypoint_content: str,
        auxiliary_files: dict[str, str] | None = None,
        artifact_type: str = "SKILL_MD",
    ) -> GeneratedArtifact:
        """Stage candidate code artifact within confined staging workspace."""
        with self._lock:
            proposal = self.store.get_proposal(proposal_id)
            if not proposal:
                raise KeyError(f"Proposal '{proposal_id}' not found.")

            return self.staging_gen.stage_artifact(
                proposal=proposal,
                entrypoint_content=entrypoint_content,
                auxiliary_files=auxiliary_files,
                artifact_type=artifact_type,
            )

    # ── Stage 7 & 8: Test & Evaluate ──────────────────────────────────────────

    def evaluate(self, proposal_id: str) -> EvaluationResult:
        """Execute deterministic test suites inside isolated sandbox."""
        with self._lock:
            proposal = self.store.get_proposal(proposal_id)
            if not proposal:
                raise KeyError(f"Proposal '{proposal_id}' not found.")

            artifacts = [a for a in self.store._artifacts.values() if a.proposal_id == proposal_id]
            if not artifacts:
                raise ValueError(f"No staged artifact found for proposal '{proposal_id}'.")

            artifact = artifacts[-1]
            return self.sandbox_eval.evaluate_artifact(proposal, artifact)

    # ── Stage 9: Independent Goldfish Review ──────────────────────────────────

    def review(self, proposal_id: str) -> GoldfishReviewReport:
        """Conduct independent adversarial Goldfish review across 6 axes."""
        with self._lock:
            proposal = self.store.get_proposal(proposal_id)
            if not proposal:
                raise KeyError(f"Proposal '{proposal_id}' not found.")

            artifacts = [a for a in self.store._artifacts.values() if a.proposal_id == proposal_id]
            evaluations = [e for e in self.store._evaluations.values() if e.artifact_id == artifacts[-1].artifact_id]

            if not artifacts or not evaluations:
                raise ValueError(f"Incomplete artifact or evaluation for proposal '{proposal_id}'.")

            return self.reviewer.review_candidate(proposal, artifacts[-1], evaluations[-1])

    # ── Stage 10, 11, 12: Policy Validation, Approval, & Promote ──────────────

    def promote(
        self,
        proposal_id: str,
        approval_token: ApprovalToken | None = None,
    ) -> PromotionRecord:
        """Validate policy and atomically promote verified artifact into production."""
        with self._lock:
            proposal = self.store.get_proposal(proposal_id)
            if not proposal:
                raise KeyError(f"Proposal '{proposal_id}' not found.")

            artifacts = [a for a in self.store._artifacts.values() if a.proposal_id == proposal_id]
            reviews = [r for r in self.store._reviews.values() if r.proposal_id == proposal_id]

            if not artifacts or not reviews:
                raise ValueError(f"Incomplete artifact or review for proposal '{proposal_id}'.")

            record = self.promotion_engine.promote_artifact(
                proposal=proposal,
                artifact=artifacts[-1],
                review=reviews[-1],
                approval_token=approval_token,
            )

            # SEC-12: Prune staging workspace following promotion
            self.staging_gen.cleanup_staging(proposal_id)
            return record

    # ── Stage 13, 14, 15: Monitor & Rollback ───────────────────────────────────

    def rollback(self, proposal_id: str) -> bool:
        """Execute deterministic rollback to previous known-good version."""
        with self._lock:
            return self.promotion_engine.rollback(proposal_id)

    # ── Autonomous End-to-End Lifecycle Execution ─────────────────────────────

    def run_full_improvement_cycle(
        self,
        category: str,
        description: str,
        generated_content: str,
        evidence: dict[str, Any] | None = None,
        is_explicit: bool = True,
        approval_token: ApprovalToken | None = None,
        author_agent_id: str = "self_improvement_generator",
    ) -> tuple[bool, str, PromotionRecord | None]:
        """Execute the complete 15-stage self-improvement cycle end-to-end.

        Returns:
            (success: bool, status_message: str, promotion_record: PromotionRecord | None)
        """
        with self._lock:
            # 1. Observe
            gap = self.observe_gap(
                category=category,
                description=description,
                evidence=evidence,
                is_explicit=is_explicit,
            )
            if not gap:
                return False, "OBSERVATION_REJECTED: Sensitive topic or invalid gap.", None

            # 2. Propose & Specify
            try:
                title = f"Skill: {description[:40]}"
                proposal = self.formulate_proposal(
                    gap_id=gap.gap_id,
                    title=title,
                    problem_statement=description,
                    expected_outcome=f"Resolve {category} limitation.",
                    author_agent_id=author_agent_id,
                )
            except ValueError as ve:
                return False, f"PROPOSAL_REJECTED: {ve}", None

            # 3. Generate & Stage
            try:
                _artifact = self.generate_and_stage(
                    proposal_id=proposal.proposal_id,
                    entrypoint_content=generated_content,
                    artifact_type="SKILL_MD",
                )
            except Exception as ex:
                proposal.transition_to(LifecycleState.FAILED)
                self.store.save_proposal(proposal)
                return False, f"STAGING_FAILED: {ex}", None

            # 4. Evaluate in Sandbox
            eval_result = self.evaluate(proposal.proposal_id)
            if not eval_result.passed:
                # SEC-09: Check remediation count
                proposal.remediation_count += 1
                if proposal.remediation_count > proposal.max_remediations:
                    proposal.transition_to(LifecycleState.REJECTED)
                    self.store.save_proposal(proposal)
                    return False, "EVALUATION_FAILED: Exceeded max remediation cycles.", None

            # 5. Independent Goldfish Review
            review_report = self.review(proposal.proposal_id)
            if review_report.verdict != "APPROVED":
                return False, f"GOLDFISH_REVIEW_BLOCKED: Verdict={review_report.verdict}", None

            # 6. Policy Validation & Promotion
            try:
                promotion_record = self.promote(proposal.proposal_id, approval_token=approval_token)
                return True, f"PROMOTED_SUCCESSFULLY: {promotion_record.production_target}", promotion_record
            except Exception as p_ex:
                return False, f"PROMOTION_HALTED: {p_ex}", None
