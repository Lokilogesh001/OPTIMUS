"""core/self_improvement/gap_detector.py — Capability Gap Identification Engine.

Implements:
- Telemetry ingestion from mission failures, tool errors, and explicit user instructions.
- SEC-11: Burst coalescing and signature deduplication with a 1-hour debounce window.
- Anti-profiling security filter prohibiting learning sensitive personal attributes.
- Traceable empirical evidence logging with confidence and frequency thresholds.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from typing import Any

from core.self_improvement.storage import SelfImprovementStore
from core.self_improvement.types import CapabilityGap

logger = logging.getLogger("optimus.self_improvement.gap_detector")

# Prohibited sensitive profiling patterns (Strictly forbidden from automated skill generation)
_PROHIBITED_SENSITIVE_PATTERNS = [
    re.compile(r"\b(religion|religious|christian|muslim|jewish|hindu|atheist|faith)\b", re.IGNORECASE),
    re.compile(r"\b(politics|political|democrat|republican|liberal|conservative|voting|election)\b", re.IGNORECASE),
    re.compile(r"\b(medical|diagnosis|disease|illness|disability|prescription|therapy|health|patient)\b", re.IGNORECASE),
    re.compile(r"\b(biometric|dna|fingerprint|face_scan|retina|voiceprint)\b", re.IGNORECASE),
    re.compile(r"\b(sexual_orientation|ethnicity|race|caste)\b", re.IGNORECASE),
]


class CapabilityGapDetector:
    """Detects, coalesces, and validates capability gaps from operational telemetry."""

    def __init__(self, store: SelfImprovementStore | None = None) -> None:
        self.store = store or SelfImprovementStore.instance()

    @staticmethod
    def is_prohibited_sensitive_topic(text: str) -> bool:
        """Check if candidate text attempts to profile prohibited sensitive domains."""
        clean = (text or "").replace("_", " ").replace("-", " ")
        return any(pat.search(clean) for pat in _PROHIBITED_SENSITIVE_PATTERNS)

    @staticmethod
    def compute_signature(category: str, description: str) -> str:
        """SEC-11: Deterministic signature for burst coalescing and deduplication."""
        norm_cat = (category or "").strip().lower()
        norm_desc = re.sub(r"\s+", " ", (description or "").strip().lower()[:120])
        return hashlib.sha256(f"{norm_cat}:{norm_desc}".encode()).hexdigest()[:16]

    def record_observation(
        self,
        category: str,
        description: str,
        evidence: dict[str, Any] | None = None,
        is_explicit_user_feedback: bool = False,
        confidence: float = 0.60,
        impact_score: float = 5.0,
    ) -> CapabilityGap | None:
        """Record an observed capability gap with burst coalescing and sensitivity screening."""
        clean_desc = description.strip()
        if not clean_desc:
            logger.debug("Rejected empty capability gap description.")
            return None

        # 1. Anti-Profiling Security Shield
        if self.is_prohibited_sensitive_topic(clean_desc):
            logger.warning("Rejected capability gap observation on prohibited sensitive topic: %s", clean_desc)
            return None

        now = time.time()
        sig = self.compute_signature(category, clean_desc)

        # 2. SEC-11: Burst Coalescing & Deduplication
        existing_gaps = self.store.list_gaps(status="OPEN")
        for gap in existing_gaps:
            if gap.signature == sig:
                # Within debounce window or ongoing gap: coalesce
                gap.frequency += 1
                gap.last_observed_at = now
                if is_explicit_user_feedback:
                    gap.confidence = 1.0
                else:
                    gap.confidence = min(1.0, round(gap.confidence + 0.10, 2))
                gap.impact_score = max(gap.impact_score, impact_score)
                if evidence:
                    gap.source_evidence.append(evidence)
                    if len(gap.source_evidence) > 20:
                        gap.source_evidence.pop(0)

                self.store.save_gap(gap)
                logger.info("Coalesced capability gap '%s' (frequency=%d, confidence=%.2f)", gap.gap_id, gap.frequency, gap.confidence)
                return gap

        # 3. Create new CapabilityGap
        initial_confidence = 1.0 if is_explicit_user_feedback else confidence
        gap_id = f"gap_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
        new_gap = CapabilityGap(
            gap_id=gap_id,
            category=category.upper().strip(),
            description=clean_desc,
            source_evidence=[evidence] if evidence else [],
            frequency=1,
            confidence=initial_confidence,
            impact_score=impact_score,
            signature=sig,
            first_observed_at=now,
            last_observed_at=now,
            status="OPEN",
            metadata={"is_explicit_user_feedback": is_explicit_user_feedback},
        )
        self.store.save_gap(new_gap)
        logger.info("Registered new capability gap '%s': %s", gap_id, clean_desc)
        return new_gap

    def get_actionable_gaps(self) -> list[CapabilityGap]:
        """Return gaps that meet threshold criteria for proposal generation.

        Requires:
        - Either explicit user directive (frequency >= 1, confidence == 1.0)
        - Or automated recurring failure with frequency >= 2 and confidence >= 0.70.
        """
        actionable: list[CapabilityGap] = []
        for gap in self.store.list_gaps(status="OPEN"):
            is_explicit = gap.metadata.get("is_explicit_user_feedback", False)
            if is_explicit or (gap.frequency >= 2 and gap.confidence >= 0.70):
                actionable.append(gap)
        return actionable
