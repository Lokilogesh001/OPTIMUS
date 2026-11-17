"""core/memory/manager.py — Unified Memory & Context Management Facade for OPTIMUS.

Binds:
- Persistent atomic multi-category MemoryStore.
- Scoped multi-agent SharedContextBus.
- Adaptive ContextWindowManager with compaction and verified artifact preservation.
- Integration bridge to Mark-LVII's memory_manager.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from core.memory.context_bus import SharedContextBus
from core.memory.store import MemoryStore
from core.memory.types import (
    MemoryCategory,
    MemoryEntry,
    new_memory_id,
)
from core.memory.window_manager import ContextWindowManager

_BASE = Path(__file__).resolve().parent.parent.parent
_lock = threading.Lock()
_manager: OptimusMemoryManager | None = None


def default_storage_dir() -> Path:
    return _BASE / "memory" / "optimus"


class OptimusMemoryManager:
    """Unified memory and context bus subsystem connecting orchestrator, agents, and stores."""

    def __init__(self, storage_root: Path | str | None = None, sync_legacy: bool = True) -> None:
        self.storage_dir = Path(storage_root or default_storage_dir())
        self.store = MemoryStore(self.storage_dir, sync_legacy=sync_legacy)
        self.context_bus = SharedContextBus(self.store)
        self.window_manager = ContextWindowManager()

    # ── Category-Specific Memory Convenience Methods ─────────────────────────

    def remember_working(
        self,
        key: str,
        content: str,
        task_id: str | None = None,
        subtask_id: str | None = None,
        source: str = "orchestrator",
        metadata: dict[str, Any] | None = None,
        access_roles: list[str] | None = None,
    ) -> MemoryEntry:
        """Store transient working state for an active task execution."""
        entry = MemoryEntry(
            entry_id=new_memory_id("wrk"),
            category=MemoryCategory.WORKING,
            key=key,
            content=content,
            metadata=dict(metadata or {}),
            source=source,
            task_id=task_id,
            subtask_id=subtask_id,
            is_verified=False,
            confidence=0.7,
            tags=["working", source],
            access_roles=list(access_roles or ["*"]),
        )
        return self.store.put(entry)

    def remember_episodic(
        self,
        key: str,
        content: str,
        task_id: str | None = None,
        source: str = "orchestrator",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Store historical records of completed tasks and user interaction sessions."""
        entry = MemoryEntry(
            entry_id=new_memory_id("epi"),
            category=MemoryCategory.EPISODIC,
            key=key,
            content=content,
            metadata=dict(metadata or {}),
            source=source,
            task_id=task_id,
            is_verified=True,
            confidence=1.0,
            tags=["history", "task_execution", source],
            access_roles=["*"],
        )
        return self.store.put(entry)

    def remember_semantic(
        self,
        key: str,
        content: str,
        is_verified: bool = True,
        verification_verdict: str | None = None,
        task_id: str | None = None,
        subtask_id: str | None = None,
        source: str = "orchestrator",
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        """Store trusted ground-truth facts, verified assertions, and deliverable records."""
        entry = MemoryEntry(
            entry_id=new_memory_id("sem"),
            category=MemoryCategory.SEMANTIC,
            key=key,
            content=content,
            metadata=dict(metadata or {}),
            source=source,
            task_id=task_id,
            subtask_id=subtask_id,
            is_verified=is_verified,
            verification_verdict=verification_verdict,
            confidence=1.0 if is_verified else 0.5,
            tags=list(tags or ["semantic", "verified" if is_verified else "unverified"]),
            access_roles=["*"],
        )
        return self.store.put(entry)

    def remember_procedural(
        self,
        key: str,
        content: str,
        domain: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Store reusable workflows, plan templates, and procedural rules."""
        entry = MemoryEntry(
            entry_id=new_memory_id("prc"),
            category=MemoryCategory.PROCEDURAL,
            key=key,
            content=content,
            metadata=dict(metadata or {}),
            source="orchestrator",
            is_verified=True,
            confidence=1.0,
            tags=["procedure", "workflow", domain],
            access_roles=["*"],
        )
        return self.store.put(entry)

    # ── Context Envelope & Prompt Construction ───────────────────────────────

    def build_subtask_prompt_context(
        self,
        task_id: str,
        subtask_id: str,
        agent_name: str,
        goal: str,
        subtask_title: str,
        subtask_description: str,
        prerequisite_subtask_ids: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Generate a scoped, compacted context prompt block for an agent subtask."""
        envelope = self.context_bus.create_envelope(
            task_id=task_id,
            subtask_id=subtask_id,
            agent_name=agent_name,
            goal=goal,
            subtask_title=subtask_title,
            subtask_description=subtask_description,
            prerequisite_subtask_ids=prerequisite_subtask_ids,
        )
        return self.window_manager.format_envelope_for_prompt(envelope, max_tokens=max_tokens)


def ensure(storage_root: Path | str | None = None) -> OptimusMemoryManager:
    """Retrieve or initialize the OptimusMemoryManager singleton."""
    global _manager
    with _lock:
        if _manager is None:
            _manager = OptimusMemoryManager(storage_root=storage_root)
        return _manager


def get_memory_manager() -> OptimusMemoryManager:
    """Convenience getter for the active memory manager."""
    return ensure()
