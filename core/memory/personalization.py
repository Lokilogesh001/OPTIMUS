"""core/memory/personalization.py — Transparent Personalization & Preference Learning Engine.

Implements:
- Safe preference learning from explicit statements, repeated corrections, and approvals.
- Strict prohibition of sensitive profiling (religion, politics, health, biometrics).
- Deterministic conflict resolution (explicit instructions override learned assumptions).
- User transparency controls: list_preferences, correct_preference, forget_preference.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.memory.lifecycle import MemoryLifecycleManager
from core.memory.store import MemoryStore
from core.memory.types import (
    MemoryCategory,
    MemoryEntry,
    MemoryProvenance,
    SensitivityLevel,
)

logger = logging.getLogger("optimus.memory.personalization")

# Prohibited sensitive profiling patterns (Strictly forbidden from automated learning)
_PROHIBITED_SENSITIVE_PATTERNS = [
    re.compile(r"\b(religion|religious|christian|muslim|jewish|hindu|atheist|faith)\b", re.IGNORECASE),
    re.compile(r"\b(politics|political|democrat|republican|liberal|conservative|voting)\b", re.IGNORECASE),
    re.compile(r"\b(medical|diagnosis|disease|illness|disability|prescription|therapy|health)\b", re.IGNORECASE),
    re.compile(r"\b(biometric|dna|fingerprint|face_scan|retina)\b", re.IGNORECASE),
    re.compile(r"\b(sexual_orientation|ethnicity|race|caste)\b", re.IGNORECASE),
]

_EXPLICIT_PREFERENCE_REGEXES = [
    re.compile(r"(?:always|prefer to|please always|make sure to)\s+([^.!?\n]{5,100})", re.IGNORECASE),
    re.compile(r"(?:never|do not ever|don't)\s+([^.!?\n]{5,100})", re.IGNORECASE),
    re.compile(r"(?:my preferred|i prefer)\s+([^.!?\n]{5,100})", re.IGNORECASE),
]

_CORRECTION_REGEXES = [
    re.compile(r"(?:no|wrong|incorrect|don't do that)[,.]?\s+(?:use|do|switch to)\s+([^.!?\n]{3,100})", re.IGNORECASE),
    re.compile(r"(?:instead of|rather than)\s+[^,.]*,\s*(?:use|do)\s+([^.!?\n]{3,100})", re.IGNORECASE),
]


class PersonalizationEngine:
    """Manages transparent, user-auditable preferences and personalization rules."""

    def __init__(
        self,
        memory_store: MemoryStore | MemoryLifecycleManager | None = None,
        lifecycle_mgr: MemoryLifecycleManager | None = None,
    ) -> None:
        if isinstance(memory_store, MemoryLifecycleManager):
            self.lifecycle_mgr = memory_store
            self.memory_store = memory_store.store
        else:
            self.memory_store = memory_store or MemoryStore()
            self.lifecycle_mgr = lifecycle_mgr or MemoryLifecycleManager(self.memory_store)

    @staticmethod
    def is_prohibited_sensitive_topic(text: str) -> bool:
        """Check if candidate text attempts to profile prohibited sensitive domains."""
        clean = (text or "").replace("_", " ").replace("-", " ")
        return any(pat.search(clean) for pat in _PROHIBITED_SENSITIVE_PATTERNS)

    def extract_preferences_from_text(self, text: str, is_explicit: bool = True) -> list[MemoryEntry]:
        """Alias method matching test harness contract."""
        return self.extract_and_learn_preferences(text)

    def extract_and_learn_preferences(self, user_text: str) -> list[MemoryEntry]:
        """Inspect user interaction text for explicit preferences and corrections."""
        learned: list[MemoryEntry] = []
        if self.is_prohibited_sensitive_topic(user_text):
            logger.info("Personalization skipped: Prohibited sensitive topic detected.")
            return []

        # 1. Detect Explicit Preferences
        for pat in _EXPLICIT_PREFERENCE_REGEXES:
            match = pat.search(user_text)
            if match:
                pref_content = match.group(1).strip()
                topic = re.sub(r"[^a-zA-Z0-9]+", "_", pref_content[:30].lower()).strip("_")
                entry = self.learn_preference(
                    topic=topic,
                    content=pref_content,
                    is_explicit=True,
                    confidence=1.0,
                )
                if entry:
                    learned.append(entry)

        # 2. Detect User Corrections
        for pat in _CORRECTION_REGEXES:
            match = pat.search(user_text)
            if match:
                corr_content = match.group(1).strip()
                topic = re.sub(r"[^a-zA-Z0-9]+", "_", corr_content[:30].lower()).strip("_")
                entry = self.correct_preference(
                    topic=topic,
                    new_content=corr_content,
                )
                if entry:
                    learned.append(entry)

        return learned

    def learn_preference(
        self,
        topic: str,
        content: str,
        is_explicit: bool = True,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry | None:
        """Store or update a user preference record."""
        if self.is_prohibited_sensitive_topic(content) or self.is_prohibited_sensitive_topic(topic):
            logger.warning("Rejected learning preference on prohibited sensitive domain: %s", topic)
            return None

        clean_topic = topic.strip().lower()
        key = f"pref_{clean_topic}"
        prov = MemoryProvenance.EXPLICIT_USER if is_explicit else MemoryProvenance.PERSISTED_PREFERENCE

        success, entry, msg = self.lifecycle_mgr.ingest_candidate(
            category=MemoryCategory.USER_PREFERENCES,
            key=key,
            content=content.strip(),
            canonical_topic=clean_topic,
            provenance=prov,
            confidence=confidence,
            sensitivity=SensitivityLevel.INTERNAL,
            metadata=metadata or {},
            tags=["preference", clean_topic],
            force_override=is_explicit,
        )

        if success and entry:
            logger.info("Learned user preference for '%s' (explicit=%s): %s", clean_topic, is_explicit, msg)
            return entry

        logger.debug("Preference learning skipped for '%s': %s", clean_topic, msg)
        return None

    def record_preference(
        self,
        key: str,
        value: str,
        is_explicit: bool = True,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Store a user preference record, returning boolean status."""
        entry = self.learn_preference(
            topic=key,
            content=value,
            is_explicit=is_explicit,
            confidence=confidence,
            metadata=metadata,
        )
        return entry is not None

    def correct_preference(self, topic: str, new_content: str) -> MemoryEntry | None:
        """User correction: explicitly overrides prior assumption or preference on topic."""
        clean_topic = topic.strip().lower()
        return self.learn_preference(
            topic=clean_topic,
            content=new_content,
            is_explicit=True,
            confidence=1.0,
            metadata={"corrected_by_user": True},
        )

    def list_preferences(self, include_superseded: bool = False) -> list[MemoryEntry]:
        """Return all user preferences for transparent UI inspection and review."""
        return self.memory_store.list_category(
            MemoryCategory.USER_PREFERENCES,
            limit=100,
            include_superseded=include_superseded,
        )

    def forget_preference(self, topic: str) -> bool:
        """User-requested deletion of preferences on a specific topic."""
        clean_topic = topic.strip().lower()
        deleted = self.lifecycle_mgr.forget_topic(clean_topic)
        return deleted > 0

