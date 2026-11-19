"""core/self_improvement — Verified Self-Improvement Subsystem for OPTIMUS.

Governed self-improvement through capability gap detection, sandboxed staging evaluation,
independent adversarial review, PolicyEngine validation, human authorization, and atomic promotion/rollback.
"""

from __future__ import annotations

from core.self_improvement.coordinator import SelfImprovementCoordinator
from core.self_improvement.gap_detector import CapabilityGapDetector
from core.self_improvement.goldfish_reviewer import SelfImprovementGoldfishReviewer
from core.self_improvement.promotion_engine import (
    PromotionEngine,
    PromotionError,
    SecurityViolationError,
)
from core.self_improvement.proposal_generator import ImprovementProposalGenerator
from core.self_improvement.sandbox_evaluator import IsolatedSandboxEvaluator
from core.self_improvement.staging_generator import (
    SecuritySanitizationError,
    StagingCodeGenerator,
    StagingPathEscapeError,
)
from core.self_improvement.storage import SelfImprovementStore
from core.self_improvement.types import (
    CapabilityGap,
    EvaluationResult,
    GeneratedArtifact,
    GoldfishFinding,
    GoldfishReviewReport,
    ImprovementProposal,
    LifecycleState,
    PromotionRecord,
)

__all__ = [
    "CapabilityGap",
    "CapabilityGapDetector",
    "EvaluationResult",
    "GeneratedArtifact",
    "GoldfishFinding",
    "GoldfishReviewReport",
    "ImprovementProposal",
    "ImprovementProposalGenerator",
    "IsolatedSandboxEvaluator",
    "LifecycleState",
    "PromotionEngine",
    "PromotionError",
    "PromotionRecord",
    "SecuritySanitizationError",
    "SecurityViolationError",
    "SelfImprovementCoordinator",
    "SelfImprovementGoldfishReviewer",
    "SelfImprovementStore",
    "StagingCodeGenerator",
    "StagingPathEscapeError",
]
