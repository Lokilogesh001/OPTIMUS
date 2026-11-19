"""core/memory/types.py — Type definitions and data models for OPTIMUS Memory Subsystem.

Defines:
- MemoryCategory enum: 9 refined M13 categories + backward-compatible operational aliases.
- SensitivityLevel enum: Sensitivity classifications (PUBLIC, INTERNAL, SENSITIVE, RESTRICTED).
- MemoryProvenance enum: Explicit source provenance tracking (EXPLICIT_USER, INFERRED, etc.).
- ExpirationPolicy enum: Ephemeral and long-term lifecycle policies.
- MemoryEntry dataclass: Atomic memory record with confidence, provenance, and verification flags.
- ContextEnvelope dataclass: Scoped, role-filtered context package for specialized agents.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryCategory(str, Enum):
    """Operational and cognitive memory classification for OPTIMUS."""

    # ── M13 Refined Cognitive Categories ──────────────────────────────────────
    USER_PREFERENCES = "USER_PREFERENCES"
    STABLE_USER_CONTEXT = "STABLE_USER_CONTEXT"
    ACTIVE_PROJECTS = "ACTIVE_PROJECTS"
    LONG_TERM_GOALS = "LONG_TERM_GOALS"
    TECHNICAL_ENVIRONMENT = "TECHNICAL_ENVIRONMENT"
    IMPORTANT_DECISIONS = "IMPORTANT_DECISIONS"
    ONGOING_COMMITMENTS = "ONGOING_COMMITMENTS"
    TEMPORARY_CONVERSATION_CONTEXT = "TEMPORARY_CONVERSATION_CONTEXT"
    HISTORICAL_INFORMATION = "HISTORICAL_INFORMATION"

    # ── Legacy Operational Backward-Compatibility Aliases ─────────────────────
    WORKING = "WORKING"
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PROCEDURAL = "PROCEDURAL"


LEGACY_CATEGORY_MAP: dict[str, MemoryCategory] = {
    "WORKING": MemoryCategory.TEMPORARY_CONVERSATION_CONTEXT,
    "EPISODIC": MemoryCategory.HISTORICAL_INFORMATION,
    "SEMANTIC": MemoryCategory.STABLE_USER_CONTEXT,
    "PROCEDURAL": MemoryCategory.TECHNICAL_ENVIRONMENT,
}


def normalize_category(cat: MemoryCategory | str) -> MemoryCategory:
    """Normalize legacy 4-tier category values into refined 9-tier M13 categories."""
    raw = cat.value if isinstance(cat, MemoryCategory) else str(cat)
    if raw in LEGACY_CATEGORY_MAP:
        return LEGACY_CATEGORY_MAP[raw]
    try:
        return MemoryCategory(raw)
    except ValueError:
        return MemoryCategory.STABLE_USER_CONTEXT


class SensitivityLevel(str, Enum):
    """Sensitivity classification governing encryption and visibility boundaries."""
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SENSITIVE = "SENSITIVE"
    RESTRICTED = "RESTRICTED"
    CONFIDENTIAL = "CONFIDENTIAL"


class MemoryProvenance(str, Enum):
    """Provenance origin verifying how a memory fact was acquired."""
    EXPLICIT_USER = "EXPLICIT_USER"
    USER_EXPLICIT = "USER_EXPLICIT"
    PERSISTED_PREFERENCE = "PERSISTED_PREFERENCE"
    VERIFIED_DELIVERABLE = "VERIFIED_DELIVERABLE"
    INFERRED_HYPOTHESIS = "INFERRED_HYPOTHESIS"
    INFERRED = "INFERRED"
    OBSERVED = "OBSERVED"
    MODEL_GENERATED = "MODEL_GENERATED"


class ExpirationPolicy(str, Enum):
    """Lifecycle retention policy governing automatic expiration."""
    NEVER = "NEVER"
    SESSION = "SESSION"
    TTL_SECONDS = "TTL_SECONDS"
    SHORT_TERM_HOURS = "SHORT_TERM_HOURS"
    UNTIL_SUPERSEDED = "UNTIL_SUPERSEDED"


def new_memory_id(prefix: str = "mem") -> str:
    """Generate a unique timestamped memory entry identifier."""
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


@dataclass
class MemoryEntry:
    """Individual atomic memory item with provenance, security roles, and verification status."""
    entry_id: str = field(default_factory=new_memory_id)
    category: MemoryCategory = MemoryCategory.STABLE_USER_CONTEXT
    key: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "orchestrator"
    task_id: str | None = None
    subtask_id: str | None = None
    is_verified: bool = False
    verification_verdict: str | None = None
    confidence: float = 1.0                          # 1.0 for explicit user statements, lower for inferences
    confidence_score: float | None = None
    tags: list[str] = field(default_factory=list)
    access_roles: list[str] = field(default_factory=lambda: ["*"])  # ["*"] = public to all agents
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # ── M13 Lifecycle & Governance Fields ─────────────────────────────────────
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    provenance: MemoryProvenance = MemoryProvenance.EXPLICIT_USER
    expiration_policy: ExpirationPolicy = ExpirationPolicy.NEVER
    expires_at: float | None = None
    superseded_by: str | None = None
    version: int = 1
    canonical_topic: str = ""

    def __post_init__(self) -> None:
        if self.confidence_score is not None:
            self.confidence = self.confidence_score
        if self.expires_at is not None and self.expiration_policy == ExpirationPolicy.NEVER:
            self.expiration_policy = ExpirationPolicy.TTL_SECONDS

    @property
    def value(self) -> str:
        """Alias property returning content for preference compatibility."""
        return self.content

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "category": self.category.value if isinstance(self.category, MemoryCategory) else str(self.category),
            "key": self.key,
            "content": self.content,
            "metadata": self.metadata,
            "source": self.source,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "is_verified": self.is_verified,
            "verification_verdict": self.verification_verdict,
            "confidence": self.confidence,
            "tags": self.tags,
            "access_roles": self.access_roles,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sensitivity": self.sensitivity.value if isinstance(self.sensitivity, SensitivityLevel) else str(self.sensitivity),
            "provenance": self.provenance.value if isinstance(self.provenance, MemoryProvenance) else str(self.provenance),
            "expiration_policy": self.expiration_policy.value if isinstance(self.expiration_policy, ExpirationPolicy) else str(self.expiration_policy),
            "expires_at": self.expires_at,
            "superseded_by": self.superseded_by,
            "version": self.version,
            "canonical_topic": self.canonical_topic,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        d = dict(data)
        if "category" in d:
            try:
                d["category"] = MemoryCategory(d["category"])
            except ValueError:
                d["category"] = normalize_category(d["category"])
        if "sensitivity" in d:
            try:
                d["sensitivity"] = SensitivityLevel(d["sensitivity"])
            except ValueError:
                d["sensitivity"] = SensitivityLevel.INTERNAL
        if "provenance" in d:
            try:
                d["provenance"] = MemoryProvenance(d["provenance"])
            except ValueError:
                d["provenance"] = MemoryProvenance.EXPLICIT_USER
        if "expiration_policy" in d:
            try:
                d["expiration_policy"] = ExpirationPolicy(d["expiration_policy"])
            except ValueError:
                d["expiration_policy"] = ExpirationPolicy.NEVER
        return cls(**d)

    def is_accessible_by(self, agent_role: str) -> bool:
        """Check whether a specific agent role is permitted to view this memory entry."""
        if "*" in self.access_roles:
            return True
        clean_role = agent_role.lower().strip()
        return clean_role in [r.lower().strip() for r in self.access_roles]

    def is_expired(self, current_time: float | None = None) -> bool:
        """Check if memory entry has passed its expiration timestamp."""
        now = current_time or time.time()
        if self.expires_at is not None:
            return now > self.expires_at
        if self.expiration_policy == ExpirationPolicy.NEVER:
            return False
        return False


@dataclass
class ContextEnvelope:
    """Scoped, isolated context package constructed specifically for an agent's subtask."""
    task_id: str
    subtask_id: str
    agent_name: str
    goal: str
    subtask_title: str
    subtask_description: str
    prerequisite_results: dict[str, Any] = field(default_factory=dict)
    verified_deliverables: list[dict[str, Any]] = field(default_factory=list)
    working_notes: list[str] = field(default_factory=list)
    semantic_facts: list[dict[str, str]] = field(default_factory=list)
    procedural_hints: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "agent_name": self.agent_name,
            "goal": self.goal,
            "subtask_title": self.subtask_title,
            "subtask_description": self.subtask_description,
            "prerequisite_results": self.prerequisite_results,
            "verified_deliverables": self.verified_deliverables,
            "working_notes": self.working_notes,
            "semantic_facts": self.semantic_facts,
            "procedural_hints": self.procedural_hints,
            "created_at": self.created_at,
        }
