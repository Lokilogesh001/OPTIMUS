"""core/memory/lifecycle.py — 10-Stage Memory Lifecycle & Conflict Engine for OPTIMUS.

Implements:
1. Candidate Identification & Normalization
2. Relevance Evaluation
3. Sensitivity Classification
4. Confidence Assessment
5. Deduplication (Deterministic Topic/Content Hashing)
6. Atomically Governed Persistence
7. Multi-Index Retrieval
8. Updating & Conflict Reconciliation (Rule 1: Explicit Beats Inferred, Rule 2: Recency with Provenance)
9. Expiration & Confidence Decay (Rule 3: Inferred Decay, TTL eviction for Temporary Context)
10. Cryptographic Shredding & User Deletion
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from enum import Enum
from typing import Any

from core.memory.store import MemoryStore
from core.memory.types import (
    ExpirationPolicy,
    MemoryCategory,
    MemoryEntry,
    MemoryProvenance,
    SensitivityLevel,
    new_memory_id,
    normalize_category,
)

logger = logging.getLogger("optimus.memory.lifecycle")


class ConflictResolutionResult(str, Enum):
    """Result of memory conflict resolution and deduplication evaluation."""
    CREATED = "CREATED"
    DEDUPLICATED = "DEDUPLICATED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class SweepResult(int):
    """Integer return value indicating total evicted items, with dictionary detail access."""
    def __new__(cls, expired: int, details: dict[str, int] | None = None):
        val = super().__new__(cls, expired)
        val.details = details or {}
        return val

    def __getitem__(self, item: str) -> int:
        return self.details.get(item, 0)


class MemoryConflictError(Exception):
    """Raised when an update conflicts with existing higher-confidence ground truth."""


class MemoryLifecycleManager:
    """Orchestrates candidate filtering, conflict resolution, decay, and lifecycle states."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    @staticmethod
    def compute_content_hash(category: MemoryCategory, topic: str, content: str) -> str:
        """Compute deterministic SHA-256 hash over normalized topic and content."""
        cat_str = category.value if isinstance(category, MemoryCategory) else str(category)
        norm_topic = (topic or "").strip().lower()
        norm_content = re.sub(r"\s+", " ", (content or "").strip().lower())
        payload = f"{cat_str}:{norm_topic}:{norm_content}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def ingest_memory(
        self,
        entry: MemoryEntry,
        force_override: bool = False,
    ) -> tuple[MemoryEntry, ConflictResolutionResult]:
        """Convenience method ingesting a MemoryEntry directly through the lifecycle pipeline."""
        topic = (entry.canonical_topic or entry.key).strip().lower()
        clean_content = entry.content.strip()
        cat = normalize_category(entry.category)
        content_hash = self.compute_content_hash(cat, topic, clean_content)

        # 1. Deduplication Check
        existing_entries = self.store.get_by_topic(topic, include_superseded=False)
        for existing in existing_entries:
            if self.compute_content_hash(existing.category, existing.canonical_topic or existing.key, existing.content) == content_hash:
                existing.updated_at = time.time()
                self.store.put(existing)
                return existing, ConflictResolutionResult.DEDUPLICATED

        # 2. Conflict Check against existing ground truth on same topic
        now = time.time()
        was_superseded = False
        max_v = max([e.version for e in existing_entries], default=0) if existing_entries else 0
        for existing in existing_entries:
            # Rule 1: Explicit Beats Inferred
            if existing.confidence > entry.confidence and not force_override:
                return existing, ConflictResolutionResult.REJECTED

            # Rule 2: Recency with Provenance
            existing.superseded_by = entry.entry_id
            existing.updated_at = now
            self.store.put(existing)
            was_superseded = True

        entry.version = (max_v + 1) if was_superseded else 1
        entry.canonical_topic = topic
        entry.created_at = now
        entry.updated_at = now
        saved = self.store.put(entry)
        result = ConflictResolutionResult.SUPERSEDED if was_superseded else ConflictResolutionResult.CREATED
        return saved, result

    # ── Stage 1 to 5: Candidate Ingestion & Validation ────────────────────────

    def ingest_candidate(
        self,
        category: MemoryCategory | str,
        key: str,
        content: str,
        canonical_topic: str = "",
        provenance: MemoryProvenance = MemoryProvenance.EXPLICIT_USER,
        confidence: float = 1.0,
        sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL,
        expiration_policy: ExpirationPolicy = ExpirationPolicy.NEVER,
        ttl_seconds: float | None = None,
        source: str = "orchestrator",
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        force_override: bool = False,
    ) -> tuple[bool, MemoryEntry | None, str]:
        """Process candidate through lifecycle stages 1-8.

        Returns:
            (success: bool, entry: MemoryEntry | None, status_message: str)
        """
        cat = normalize_category(category)
        clean_content = content.strip()
        if not clean_content:
            return False, None, "REJECTED: Empty content."

        # Stage 2: Relevance Evaluation
        if len(clean_content) < 3:
            return False, None, "REJECTED: Content below minimal length threshold."

        # Stage 3: Sensitivity Classification
        # Auto-elevate sensitivity if content contains sensitive cues
        content_low = clean_content.lower()
        if any(w in content_low for w in ("password", "api_key", "secret", "token", "ssn", "credentials")):
            sensitivity = SensitivityLevel.RESTRICTED

        # Stage 4: Confidence Assessment
        # Explicit user statements are 1.0; inferences capped at 0.75
        if provenance == MemoryProvenance.INFERRED_HYPOTHESIS and confidence > 0.80:
            confidence = 0.75

        # Stage 5: Deduplication Check
        topic = (canonical_topic or key).strip().lower()
        content_hash = self.compute_content_hash(cat, topic, clean_content)

        existing_entries = self.store.get_by_topic(topic, include_superseded=False)
        for existing in existing_entries:
            # Check identical content hash
            if self.compute_content_hash(existing.category, existing.canonical_topic or existing.key, existing.content) == content_hash:
                # Refresh confirmation timestamp
                existing.updated_at = time.time()
                existing.metadata["last_confirmed_at"] = time.time()
                self.store.put(existing)
                return True, existing, "DEDUPLICATED: Refreshed confirmation of existing identical memory."

        # Stage 8: Updating & Conflict Reconciliation (Rule 1 & Rule 2)
        now = time.time()
        expires_at = (now + ttl_seconds) if (ttl_seconds and expiration_policy == ExpirationPolicy.TTL_SECONDS) else None

        # Check existing entries on the same topic for conflict
        for existing in existing_entries:
            # Rule 1: Explicit Beats Inferred
            # If existing entry has higher confidence than candidate, refuse silent overwrite
            if existing.confidence > confidence and not force_override:
                logger.warning(
                    "Conflict detected: Candidate '%s' (conf=%.2f, prov=%s) cannot overwrite existing '%s' (conf=%.2f, prov=%s).",
                    key, confidence, provenance.value, existing.key, existing.confidence, existing.provenance.value,
                )
                return False, existing, (
                    f"CONFLICT_REJECTED: Existing ground truth on topic '{topic}' has higher confidence "
                    f"({existing.confidence:.2f} > {confidence:.2f}). Explicit user confirmation required."
                )

            # Rule 2: Recency with Provenance
            # Higher or equal confidence candidate supersedes the older entry
            existing.superseded_by = f"pending_superseded_by_{now}"
            existing.updated_at = now
            self.store.put(existing)

        # Stage 6: Persistence
        entry = MemoryEntry(
            entry_id=new_memory_id(f"mem_{cat.value.lower()[:3]}"),
            category=cat,
            key=key,
            content=clean_content,
            metadata=metadata or {},
            source=source,
            confidence=confidence,
            sensitivity=sensitivity,
            provenance=provenance,
            expiration_policy=expiration_policy,
            expires_at=expires_at,
            tags=tags or [],
            canonical_topic=topic,
            created_at=now,
            updated_at=now,
        )

        # Finalize superseded link with concrete entry_id
        for existing in existing_entries:
            if existing.superseded_by and existing.superseded_by.startswith("pending_superseded_by_"):
                existing.superseded_by = entry.entry_id
                self.store.put(existing)

        saved = self.store.put(entry)
        return True, saved, "STORED: Successfully persisted and reconciled."

    # ── Stage 9: Expiration Sweeper & Confidence Decay ────────────────────────

    def sweep_expired_and_decay(
        self,
        current_time: float | None = None,
        decay_interval_days: float = 7.0,
        decay_amount: float = 0.05,
        decay_rate: float | None = None,
    ) -> SweepResult:
        """Sweep expired TTL entries and apply confidence decay to unconfirmed inferences."""
        if decay_rate is not None:
            decay_amount = decay_rate

        now = current_time or time.time()
        counts = {"expired": 0, "decayed": 0, "archived": 0}

        all_entries = self.store.list_all(limit=10000, include_superseded=True)
        decay_cutoff = now - (decay_interval_days * 86400.0)

        for entry in all_entries:
            # 1. Evict expired entries (e.g. TEMPORARY_CONVERSATION_CONTEXT)
            if entry.is_expired(now):
                self.store.delete(entry.entry_id)
                counts["expired"] += 1
                continue

            # 2. Rule 3: Confidence Decay for Inferred Memories
            if entry.provenance in (MemoryProvenance.INFERRED_HYPOTHESIS, MemoryProvenance.INFERRED) and not entry.superseded_by:
                last_confirmed = entry.metadata.get("last_confirmed_at", entry.created_at)
                if last_confirmed < decay_cutoff:
                    # Decay confidence
                    entry.confidence = max(0.1, round(entry.confidence - decay_amount, 2))
                    entry.metadata["last_decayed_at"] = now
                    # If confidence falls below 0.35, archive/supersede
                    if entry.confidence < 0.35:
                        entry.superseded_by = "ARCHIVED_DUE_TO_LOW_CONFIDENCE"
                        counts["archived"] += 1
                    else:
                        counts["decayed"] += 1
                    self.store.put(entry)

        return SweepResult(counts["expired"], counts)

    # ── Stage 10: Cryptographic Shredding & Deletion ─────────────────────────

    def forget_topic(self, canonical_topic: str) -> int:
        """User-requested forgetting: delete all memories associated with a topic."""
        topic_norm = (canonical_topic or "").strip().lower()
        entries = self.store.get_by_topic(topic_norm, include_superseded=True)
        deleted = 0
        for e in entries:
            if self.store.delete(e.entry_id):
                deleted += 1
        logger.info("User requested forget for topic '%s': purged %d entries.", topic_norm, deleted)
        return deleted

