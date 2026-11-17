"""core/memory/types.py — Type definitions and data models for OPTIMUS Memory Subsystem.

Defines:
- MemoryCategory enum: WORKING, EPISODIC, SEMANTIC, PROCEDURAL.
- MemoryEntry dataclass: Typed record with metadata, provenance, and verification flags.
- ContextEnvelope dataclass: Scoped, role-filtered context package for specialized agents.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryCategory(str, Enum):
    """Four-tier operational memory classification for OPTIMUS."""
    WORKING = "WORKING"        # Current task scratchpad, intermediate subtask I/O
    EPISODIC = "EPISODIC"      # Past task executions, session history, audit trail
    SEMANTIC = "SEMANTIC"      # Verified knowledge, confirmed deliverables, ground truth
    PROCEDURAL = "PROCEDURAL"  # Reusable workflows, plan templates, agent procedures


def new_memory_id(prefix: str = "mem") -> str:
    """Generate a unique timestamped memory entry identifier."""
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


@dataclass
class MemoryEntry:
    """Individual atomic memory item with provenance, security roles, and verification status."""
    entry_id: str
    category: MemoryCategory
    key: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "orchestrator"                     # e.g., 'orchestrator', 'agent:calculator', 'subtask:sub_xxx'
    task_id: str | None = None
    subtask_id: str | None = None
    is_verified: bool = False                        # Only independently verified items have is_verified=True
    verification_verdict: str | None = None
    confidence: float = 1.0                          # 1.0 for verified/orchestrator items, lower for unverified
    tags: list[str] = field(default_factory=list)
    access_roles: list[str] = field(default_factory=lambda: ["*"])  # ["*"] = public to all agents, or specific roles
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEntry:
        d = dict(data)
        if "category" in d:
            d["category"] = MemoryCategory(d["category"])
        return cls(**d)

    def is_accessible_by(self, agent_role: str) -> bool:
        """Check whether a specific agent role is permitted to view this memory entry."""
        if "*" in self.access_roles:
            return True
        clean_role = agent_role.lower().strip()
        return clean_role in [r.lower().strip() for r in self.access_roles]


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
