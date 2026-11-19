"""core/self_improvement/storage.py — Multiprocess-Safe Atomic Storage for Self-Improvement.

Provides:
- Atomic JSON persistence using process-unique temporary files and atomic os.replace.
- Exponential backoff retry logic for Windows file lock and sharing violation resilience.
- In-memory thread-safe indexing and persistence for Gaps, Proposals, Evaluations, Reviews, and Promotions.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.self_improvement.types import (
    CapabilityGap,
    EvaluationResult,
    GeneratedArtifact,
    GoldfishReviewReport,
    ImprovementProposal,
    PromotionRecord,
)

logger = logging.getLogger("optimus.self_improvement.storage")


def _atomic_write_json(target_path: Path, data: Any, max_retries: int = 10) -> None:
    """Atomic file replacement with Windows exponential backoff."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f".{target_path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")

    base_delay = 0.02
    for attempt in range(max_retries):
        try:
            os.replace(temp_path, target_path)
            return
        except (PermissionError, FileExistsError, OSError):
            if attempt == max_retries - 1:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            time.sleep(base_delay * (2 ** attempt) + (secrets.randbelow(10) / 1000.0))


class SelfImprovementStore:
    """Thread-safe persistent store for self-improvement records."""

    _instance: SelfImprovementStore | None = None
    _instance_lock = threading.Lock()

    def __init__(self, root_dir: Path | str | None = None) -> None:
        if root_dir is None:
            root_dir = Path(__file__).resolve().parent.parent.parent / "memory" / "self_improvement"
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._gaps: dict[str, CapabilityGap] = {}
        self._proposals: dict[str, ImprovementProposal] = {}
        self._artifacts: dict[str, GeneratedArtifact] = {}
        self._evaluations: dict[str, EvaluationResult] = {}
        self._reviews: dict[str, GoldfishReviewReport] = {}
        self._promotions: dict[str, PromotionRecord] = {}

        self.load_all()

    @classmethod
    def instance(cls, root_dir: Path | str | None = None) -> SelfImprovementStore:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(root_dir=root_dir)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance for testing isolation."""
        with cls._instance_lock:
            cls._instance = None


    # ── Disk Paths ────────────────────────────────────────────────────────────

    @property
    def gaps_file(self) -> Path:
        return self.root_dir / "gaps.json"

    @property
    def proposals_file(self) -> Path:
        return self.root_dir / "proposals.json"

    @property
    def artifacts_file(self) -> Path:
        return self.root_dir / "artifacts.json"

    @property
    def evaluations_file(self) -> Path:
        return self.root_dir / "evaluations.json"

    @property
    def reviews_file(self) -> Path:
        return self.root_dir / "reviews.json"

    @property
    def promotions_file(self) -> Path:
        return self.root_dir / "promotions.json"

    # ── Persistence IO ────────────────────────────────────────────────────────

    def load_all(self) -> None:
        with self._lock:
            # Gaps
            if self.gaps_file.is_file():
                try:
                    raw = json.loads(self.gaps_file.read_text(encoding="utf-8"))
                    self._gaps = {item["gap_id"]: CapabilityGap.from_dict(item) for item in raw}
                except Exception as ex:
                    logger.warning("Failed loading gaps: %s", ex)

            # Proposals
            if self.proposals_file.is_file():
                try:
                    raw = json.loads(self.proposals_file.read_text(encoding="utf-8"))
                    self._proposals = {item["proposal_id"]: ImprovementProposal.from_dict(item) for item in raw}
                except Exception as ex:
                    logger.warning("Failed loading proposals: %s", ex)

            # Artifacts
            if self.artifacts_file.is_file():
                try:
                    raw = json.loads(self.artifacts_file.read_text(encoding="utf-8"))
                    self._artifacts = {item["artifact_id"]: GeneratedArtifact.from_dict(item) for item in raw}
                except Exception as ex:
                    logger.warning("Failed loading artifacts: %s", ex)

            # Evaluations
            if self.evaluations_file.is_file():
                try:
                    raw = json.loads(self.evaluations_file.read_text(encoding="utf-8"))
                    self._evaluations = {item["evaluation_id"]: EvaluationResult.from_dict(item) for item in raw}
                except Exception as ex:
                    logger.warning("Failed loading evaluations: %s", ex)

            # Reviews
            if self.reviews_file.is_file():
                try:
                    raw = json.loads(self.reviews_file.read_text(encoding="utf-8"))
                    self._reviews = {item["review_id"]: GoldfishReviewReport.from_dict(item) for item in raw}
                except Exception as ex:
                    logger.warning("Failed loading reviews: %s", ex)

            # Promotions
            if self.promotions_file.is_file():
                try:
                    raw = json.loads(self.promotions_file.read_text(encoding="utf-8"))
                    self._promotions = {item["promotion_id"]: PromotionRecord.from_dict(item) for item in raw}
                except Exception as ex:
                    logger.warning("Failed loading promotions: %s", ex)

    # ── Capability Gaps ───────────────────────────────────────────────────────

    def save_gap(self, gap: CapabilityGap) -> None:
        with self._lock:
            self._gaps[gap.gap_id] = gap
            data = [g.to_dict() for g in self._gaps.values()]
            _atomic_write_json(self.gaps_file, data)

    def get_gap(self, gap_id: str) -> CapabilityGap | None:
        with self._lock:
            return self._gaps.get(gap_id)

    def list_gaps(self, status: str | None = None) -> list[CapabilityGap]:
        with self._lock:
            gaps = list(self._gaps.values())
            if status:
                gaps = [g for g in gaps if g.status.upper() == status.upper()]
            return sorted(gaps, key=lambda g: g.last_observed_at, reverse=True)

    # ── Improvement Proposals ─────────────────────────────────────────────────

    def save_proposal(self, proposal: ImprovementProposal) -> None:
        with self._lock:
            proposal.updated_at = time.time()
            self._proposals[proposal.proposal_id] = proposal
            data = [p.to_dict() for p in self._proposals.values()]
            _atomic_write_json(self.proposals_file, data)

    def get_proposal(self, proposal_id: str) -> ImprovementProposal | None:
        with self._lock:
            return self._proposals.get(proposal_id)

    def list_proposals(self) -> list[ImprovementProposal]:
        with self._lock:
            return sorted(self._proposals.values(), key=lambda p: p.created_at, reverse=True)

    # ── Generated Artifacts ───────────────────────────────────────────────────

    def save_artifact(self, artifact: GeneratedArtifact) -> None:
        with self._lock:
            self._artifacts[artifact.artifact_id] = artifact
            data = [a.to_dict() for a in self._artifacts.values()]
            _atomic_write_json(self.artifacts_file, data)

    def get_artifact(self, artifact_id: str) -> GeneratedArtifact | None:
        with self._lock:
            return self._artifacts.get(artifact_id)

    # ── Evaluations ───────────────────────────────────────────────────────────

    def save_evaluation(self, evaluation: EvaluationResult) -> None:
        with self._lock:
            self._evaluations[evaluation.evaluation_id] = evaluation
            data = [e.to_dict() for e in self._evaluations.values()]
            _atomic_write_json(self.evaluations_file, data)

    def get_evaluation(self, evaluation_id: str) -> EvaluationResult | None:
        with self._lock:
            return self._evaluations.get(evaluation_id)

    # ── Reviews ───────────────────────────────────────────────────────────────

    def save_review(self, review: GoldfishReviewReport) -> None:
        with self._lock:
            self._reviews[review.review_id] = review
            data = [r.to_dict() for r in self._reviews.values()]
            _atomic_write_json(self.reviews_file, data)

    def get_review(self, review_id: str) -> GoldfishReviewReport | None:
        with self._lock:
            return self._reviews.get(review_id)

    # ── Promotions ────────────────────────────────────────────────────────────

    def save_promotion(self, promotion: PromotionRecord) -> None:
        with self._lock:
            self._promotions[promotion.promotion_id] = promotion
            data = [p.to_dict() for p in self._promotions.values()]
            _atomic_write_json(self.promotions_file, data)

    def get_promotion(self, promotion_id: str) -> PromotionRecord | None:
        with self._lock:
            return self._promotions.get(promotion_id)

    def list_promotions(self) -> list[PromotionRecord]:
        with self._lock:
            return sorted(self._promotions.values(), key=lambda p: p.promoted_at, reverse=True)
