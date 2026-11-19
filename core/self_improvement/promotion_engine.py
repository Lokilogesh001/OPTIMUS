"""core/self_improvement/promotion_engine.py — Policy-Governed Promotion & Rollback Engine.

Implements:
- SEC-01: Strict path confinement to data/skills/installed/{id}/{version}/ (forbidding core/ writes).
- SEC-02: Mandatory PolicyEngine authorization; requiring ApprovalToken for HIGH/CRITICAL risk promotions.
- SEC-04: Strict self-approval rejection (verifying operator_id != proposal.author_agent_id).
- SEC-05: Windows two-phase transactional atomic directory replacement with bounded retry loops.
- SEC-08: Pre-promotion Merkle package integrity re-verification.
- SEC-10: Deterministic rollback with terminal ROLLBACK_FAILED state handling and automatic disablement.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import time
from pathlib import Path

from core.security.policy_engine import ApprovalToken, OperationRequest, PolicyEngine
from core.self_improvement.storage import SelfImprovementStore
from core.self_improvement.types import (
    GeneratedArtifact,
    GoldfishReviewReport,
    ImprovementProposal,
    LifecycleState,
    PromotionRecord,
)
from core.skills.models import SkillCategory, SkillManifest, SkillTrustLevel
from core.skills.registry import SkillRegistry

logger = logging.getLogger("optimus.self_improvement.promotion_engine")

FORBIDDEN_DESTINATION_ROOTS = {"core", "config", ".git", "memory"}


class SecurityViolationError(Exception):
    """Raised when an operation violates mandatory security invariants."""


class PromotionError(Exception):
    """Raised when promotion fails due to policy, review, or filesystem errors."""


class PromotionEngine:
    """Governs the atomic, policy-validated promotion and rollback of verified self-improvements."""

    def __init__(
        self,
        store: SelfImprovementStore | None = None,
        policy_engine: PolicyEngine | None = None,
        skill_registry: SkillRegistry | None = None,
        production_root: Path | str | None = None,
    ) -> None:
        self.store = store or SelfImprovementStore.instance()
        self.policy_engine = policy_engine or PolicyEngine.instance()
        self.skill_registry = skill_registry or SkillRegistry.instance()

        if production_root is None:
            production_root = Path(__file__).resolve().parent.parent.parent / "data" / "skills" / "installed"
        self.production_root = Path(production_root)
        self.production_root.mkdir(parents=True, exist_ok=True)

        self.backup_root = Path(__file__).resolve().parent.parent.parent / "memory" / "self_improvement" / "backups"
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def validate_destination_safety(self, destination: Path) -> None:
        """SEC-01: Hard path assertion ensuring destination never breaches protected core."""
        dest_resolved = destination.resolve()
        for part in dest_resolved.parts:
            if part.lower() in FORBIDDEN_DESTINATION_ROOTS:
                raise SecurityViolationError(
                    f"IMMUTABLE_CORE_VIOLATION: Promotion target '{dest_resolved}' breaches protected root '{part}'."
                )

        if not str(dest_resolved).startswith(str(self.production_root.resolve())):
            raise SecurityViolationError(
                f"BOUNDARY_VIOLATION: Promotion destination '{dest_resolved}' escapes production root '{self.production_root}'."
            )

    @staticmethod
    def verify_package_integrity(staging_dir: Path, expected_hash: str) -> None:
        """SEC-08: Recalculate per-file hashes and verify Merkle root immediately prior to promotion."""
        if not staging_dir.is_dir():
            raise FileNotFoundError(f"Staging directory not found: {staging_dir}")

        files_checksums: dict[str, str] = {}
        for root, dirs, files in os.walk(staging_dir):
            # Exclude cache and hidden metadata directories
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
            for file_name in files:
                if file_name.endswith((".pyc", ".pyo", ".tmp")) or file_name.startswith("."):
                    continue
                file_path = Path(root) / file_name
                rel_path = file_path.relative_to(staging_dir).as_posix()
                content = file_path.read_bytes()
                files_checksums[rel_path] = hashlib.sha256(content).hexdigest()

        sorted_keys = sorted(files_checksums.keys())
        merkle_payload = "|".join(f"{k}:{files_checksums[k]}" for k in sorted_keys)
        actual_hash = hashlib.sha256(merkle_payload.encode("utf-8")).hexdigest()

        if actual_hash != expected_hash:
            raise SecurityViolationError(
                f"PACKAGE_TAMPERING_DETECTED: Pre-promotion hash mismatch. Expected {expected_hash}, computed {actual_hash}."
            )

    def promote_artifact(
        self,
        proposal: ImprovementProposal,
        artifact: GeneratedArtifact,
        review: GoldfishReviewReport,
        approval_token: ApprovalToken | None = None,
    ) -> PromotionRecord:
        """Atomically promote a verified candidate artifact into production."""
        proposal.transition_to(LifecycleState.POLICY_VALIDATION)
        self.store.save_proposal(proposal)

        # 1. Independent Review Verification Gate
        if review.verdict != "APPROVED" or review.has_blocking_findings:
            proposal.transition_to(LifecycleState.REJECTED)
            self.store.save_proposal(proposal)
            raise PromotionError(
                f"PROMOTION_BLOCKED: Goldfish review verdict is '{review.verdict}' with blocking findings."
            )

        # 2. SEC-04: Self-Approval Prevention Check
        if approval_token:
            if approval_token.operator_id == proposal.author_agent_id:
                proposal.transition_to(LifecycleState.REJECTED)
                self.store.save_proposal(proposal)
                raise SecurityViolationError(
                    f"SELF_APPROVAL_VIOLATION: Generator agent '{proposal.author_agent_id}' cannot approve its own proposal."
                )
            if any(k in approval_token.operator_id.lower() for k in ("system", "daemon", "generator", "llm", "ai", "model", "agent")):
                proposal.transition_to(LifecycleState.REJECTED)
                self.store.save_proposal(proposal)
                raise SecurityViolationError(
                    f"AUTOMATED_APPROVAL_VIOLATION: Automated identity '{approval_token.operator_id}' cannot grant promotion."
                )

        # 3. SEC-02: PolicyEngine Authorization Gate
        op_request = OperationRequest(
            tool_name="promote_skill",
            agent_name="promotion_engine",
            arguments={"proposal_id": proposal.proposal_id, "risk_level": proposal.risk_level},
            approval_token=approval_token,
        )
        verdict = self.policy_engine.evaluate(op_request)

        if not verdict.is_authorized:
            if verdict.requires_approval and approval_token is None:
                proposal.transition_to(LifecycleState.AWAITING_APPROVAL)
                self.store.save_proposal(proposal)
                raise PromotionError(
                    f"APPROVAL_REQUIRED: Promotion for '{proposal.proposal_id}' (risk={proposal.risk_level}) requires human operator approval."
                )
            proposal.transition_to(LifecycleState.REJECTED)
            self.store.save_proposal(proposal)
            raise PromotionError(f"POLICY_DENIED: {verdict.reason}")

        # 4. SEC-08: Pre-promotion Merkle Integrity Verification
        staging_dir = Path(artifact.staging_path).resolve()
        self.verify_package_integrity(staging_dir, artifact.package_integrity_hash)

        # 5. SEC-01 & SEC-05: Destination Path Confinement & Two-Phase Installation
        skill_id = proposal.proposal_id.replace("prop_", "skill_")
        dest_dir = self.production_root / skill_id / proposal.version
        self.validate_destination_safety(dest_dir)

        backup_id = f"bak_{skill_id}_{proposal.version}_{int(time.time())}"
        backup_dir = self.backup_root / skill_id / backup_id

        # Phase 1: Backup existing installation if present
        previous_version = None
        if dest_dir.exists():
            dest_dir.parent.mkdir(parents=True, exist_ok=True)
            self._move_with_retry(dest_dir, backup_dir)
            previous_version = proposal.version

        # Phase 2: Copy staging tree to production destination
        try:
            dest_dir.parent.mkdir(parents=True, exist_ok=True)
            self._copy_tree_with_retry(staging_dir, dest_dir)
            # Post-copy integrity check on destination (prevents TOCTOU or partial write)
            self.verify_package_integrity(dest_dir, artifact.package_integrity_hash)
        except Exception as ex:
            # Phase 3 Rollback on failure
            logger.error("Promotion failed during file copy: %s. Reverting backup...", ex)
            if backup_dir.exists():
                self._move_with_retry(backup_dir, dest_dir)
            proposal.transition_to(LifecycleState.FAILED)
            self.store.save_proposal(proposal)
            raise PromotionError(f"TRANSACTION_FAILED: Installation failed and reverted: {ex}") from ex

        # 6. Register into SkillRegistry
        entrypoint = dest_dir / "SKILL.md"
        instructions = entrypoint.read_text(encoding="utf-8") if entrypoint.is_file() else artifact.generated_code

        manifest = SkillManifest(
            id=skill_id,
            name=proposal.title or skill_id,
            description=proposal.problem_statement or "Verified self-improved skill.",
            version=proposal.version,
            source_path=str(dest_dir),
            instructions=instructions,
            content_sha256=artifact.content_sha256,
            enabled=True,
            trust_level=SkillTrustLevel.VERIFIED,
            category=SkillCategory.ENGINEERING,
            provenance="self_improvement",
            is_installed=True,
            installed_path=str(dest_dir),
            installed_at=time.time(),
        )
        self.skill_registry.register(manifest, overwrite=True)

        # 7. Record Promotion & Advance State
        promotion_id = f"prom_{secrets.token_hex(4)}"
        record = PromotionRecord(
            promotion_id=promotion_id,
            proposal_id=proposal.proposal_id,
            artifact_id=artifact.artifact_id,
            production_target=str(dest_dir),
            promoted_version=proposal.version,
            previous_version=previous_version,
            approval_token_id=approval_token.token_id if approval_token else None,
            approver=approval_token.operator_id if approval_token else "policy_auto",
            promoted_at=time.time(),
            status="ACTIVE",
            rollback_snapshot_id=str(backup_dir) if backup_dir.exists() else None,
        )

        proposal.transition_to(LifecycleState.PROMOTED)
        proposal.transition_to(LifecycleState.ACTIVE)
        self.store.save_proposal(proposal)
        self.store.save_promotion(record)

        logger.info("Successfully promoted proposal '%s' to '%s' (promotion_id=%s)", proposal.proposal_id, dest_dir, promotion_id)
        return record

    def rollback(self, proposal_id: str) -> bool:
        """SEC-10: Deterministic rollback reverting promoted artifact to previous known-good backup."""
        proposal = self.store.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(f"Proposal '{proposal_id}' not found.")

        # Find associated promotion record
        promotions = [p for p in self.store.list_promotions() if p.proposal_id == proposal_id]
        if not promotions:
            raise ValueError(f"No promotion record found for proposal '{proposal_id}'.")

        promotion = promotions[0]
        skill_id = proposal.proposal_id.replace("prop_", "skill_")
        target_dir = Path(promotion.production_target)

        proposal.transition_to(LifecycleState.ROLLBACK_REQUESTED)
        self.store.save_proposal(proposal)

        try:
            # 1. Restore previous backup snapshot if available
            if promotion.rollback_snapshot_id and Path(promotion.rollback_snapshot_id).exists():
                backup_dir = Path(promotion.rollback_snapshot_id)
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                self._move_with_retry(backup_dir, target_dir)
                logger.info("Restored backup from '%s' to '%s'.", backup_dir, target_dir)
            else:
                # No previous version: disable and remove promoted directory
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)
                self.skill_registry.unregister(skill_id)

            # 2. Update status
            promotion.status = "ROLLED_BACK"
            proposal.transition_to(LifecycleState.ROLLED_BACK)
            self.store.save_promotion(promotion)
            self.store.save_proposal(proposal)
            logger.info("Rollback complete for proposal '%s'.", proposal_id)
            return True

        except Exception as ex:  # noqa: BLE001  # SEC-10: Emergency circuit breaker must catch any failure
            # SEC-10: On rollback failure, transition to ROLLBACK_FAILED and disable in registry
            logger.critical("ROLLBACK_FAILURE for '%s': %s. Disabling in registry...", proposal_id, ex)
            self.skill_registry.set_enabled(skill_id, False)
            promotion.status = "ROLLBACK_FAILED"
            proposal.transition_to(LifecycleState.ROLLBACK_FAILED)
            self.store.save_promotion(promotion)
            self.store.save_proposal(proposal)
            return False

    # ── Windows File Lock Retry Helpers (SEC-05) ──────────────────────────────

    @staticmethod
    def _move_with_retry(src: Path, dst: Path, max_retries: int = 10) -> None:
        """Move path with exponential backoff retries for Windows file locks."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        base_delay = 0.05
        for attempt in range(max_retries):
            try:
                shutil.move(str(src), str(dst))
                return
            except (PermissionError, FileExistsError, OSError):
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt) + (secrets.randbelow(15) / 1000.0))

    @staticmethod
    def _copy_tree_with_retry(src: Path, dst: Path, max_retries: int = 10) -> None:
        """Copy tree with exponential backoff retries for Windows file locks."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        base_delay = 0.05
        for attempt in range(max_retries):
            try:
                shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
                return
            except (PermissionError, FileExistsError, OSError):
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt) + (secrets.randbelow(15) / 1000.0))
