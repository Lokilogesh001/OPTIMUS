"""core/memory/context_bus.py — Shared Context Bus for OPTIMUS Multi-Agent Execution.

Coordinates:
- Scoped context isolation between independent tasks.
- Dependency passing between sequential subtasks.
- Controlled sharing of verified deliverables and artifacts.
- Role-based context filtering (agents never receive the whole database).
- Source attribution and provenance on all context items.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.memory.store import MemoryStore
from core.memory.types import (
    ContextEnvelope,
    MemoryCategory,
    MemoryEntry,
    new_memory_id,
)


class SharedContextBus:
    """Bus orchestrating isolated context envelopes and artifact sharing for specialized agents."""

    def __init__(self, memory_store: MemoryStore) -> None:
        self.store = memory_store
        self._lock = threading.RLock()
        # task_id -> list of subtask outputs and working notes
        self._task_working_context: dict[str, dict[str, Any]] = {}

    def isolate_task(self, task_id: str) -> None:
        """Initialize an isolated context partition for a task with LRU bounding."""
        with self._lock:
            # Prevent unbounded memory growth over long-running sessions
            if len(self._task_working_context) >= 50:
                oldest_keys = sorted(
                    self._task_working_context.keys(),
                    key=lambda k: self._task_working_context[k].get("created_at", 0),
                )
                for old_k in oldest_keys[: len(self._task_working_context) - 49]:
                    self._task_working_context.pop(old_k, None)

            self._task_working_context[task_id] = {
                "subtask_outputs": {},
                "deliverables": [],
                "working_notes": [],
                "created_at": time.time(),
            }

    def release_task(self, task_id: str) -> None:
        """Explicitly release an isolated context partition once the task has concluded."""
        with self._lock:
            self._task_working_context.pop(task_id, None)

    def publish_subtask_output(
        self,
        task_id: str,
        subtask_id: str,
        agent_name: str,
        key: str,
        output: str,
        is_verified: bool = False,
        verification_verdict: str | None = None,
        metadata: dict[str, Any] | None = None,
        access_roles: list[str] | None = None,
    ) -> MemoryEntry:
        """Record a subtask result with provenance and publish it to the context bus."""
        with self._lock:
            if task_id not in self._task_working_context:
                self.isolate_task(task_id)

            meta = dict(metadata or {})
            meta["subtask_id"] = subtask_id
            meta["agent_name"] = agent_name

            # Category determination: Verified results are promoted to SEMANTIC memory;
            # unverified outputs remain in WORKING memory.
            category = MemoryCategory.SEMANTIC if is_verified else MemoryCategory.WORKING
            confidence = 1.0 if is_verified else 0.7

            entry = MemoryEntry(
                entry_id=new_memory_id("ctx"),
                category=category,
                key=f"{task_id}:{subtask_id}:{key}",
                content=output,
                metadata=meta,
                source=f"agent:{agent_name}",
                task_id=task_id,
                subtask_id=subtask_id,
                is_verified=is_verified,
                verification_verdict=verification_verdict,
                confidence=confidence,
                tags=[agent_name, "subtask_output", "verified" if is_verified else "unverified"],
                access_roles=list(access_roles or ["*"]),
            )

            # Persist to memory store
            self.store.put(entry)

            # Update working context partition for task
            self._task_working_context[task_id]["subtask_outputs"][subtask_id] = {
                "agent": agent_name,
                "key": key,
                "output": output,
                "is_verified": is_verified,
                "verification_verdict": verification_verdict,
                "metadata": meta,
                "access_roles": entry.access_roles,
            }

            return entry

    def publish_deliverable(
        self,
        task_id: str,
        subtask_id: str,
        agent_name: str,
        file_path: str,
        byte_size: int,
        sha256_hash: str,
        verdict: str,
        evidence_id: str | None = None,
        **kwargs: Any,
    ) -> MemoryEntry:
        """Publish a verified deliverable artifact into trusted semantic memory."""
        with self._lock:
            if task_id not in self._task_working_context:
                self.isolate_task(task_id)

            artifact_meta = {
                "file_path": file_path,
                "byte_size": byte_size,
                "sha256": sha256_hash,
                "verdict": verdict,
                "agent": agent_name,
                "subtask_id": subtask_id,
                "evidence_id": evidence_id,
            }

            tags = ["deliverable", "artifact", agent_name, "verified"]
            if evidence_id:
                tags.append(evidence_id)

            entry = MemoryEntry(
                entry_id=new_memory_id("artifact"),
                category=MemoryCategory.SEMANTIC,
                key=f"deliverable:{task_id}:{subtask_id}",
                content=f"Verified artifact: {file_path} ({byte_size} bytes, SHA-256: {sha256_hash})",
                metadata=artifact_meta,
                source="orchestrator:verifier",
                task_id=task_id,
                subtask_id=subtask_id,
                is_verified=True,
                verification_verdict=verdict,
                confidence=1.0,
                tags=tags,
                access_roles=["*"],
            )

            self.store.put(entry)
            self._task_working_context[task_id]["deliverables"].append(artifact_meta)
            return entry

    def create_envelope(
        self,
        task_id: str,
        subtask_id: str,
        agent_name: str,
        goal: str,
        subtask_title: str,
        subtask_description: str,
        prerequisite_subtask_ids: list[str] | None = None,
    ) -> ContextEnvelope:
        """Construct a role-isolated context envelope for a specific subtask."""
        with self._lock:
            if task_id not in self._task_working_context:
                self.isolate_task(task_id)

            task_ctx = self._task_working_context[task_id]

            # 1. Prerequisite results (only authorized subtasks)
            prereq_results: dict[str, Any] = {}
            for st_id, output_data in task_ctx.get("subtask_outputs", {}).items():
                if prerequisite_subtask_ids is None or st_id in prerequisite_subtask_ids:
                    roles = output_data.get("access_roles", ["*"])
                    if "*" in roles or agent_name.lower() in [r.lower() for r in roles]:
                        prereq_results[st_id] = output_data

            # 2. Verified deliverables
            deliverables = list(task_ctx.get("deliverables", []))

            # 3. Working notes
            working_notes = list(task_ctx.get("working_notes", []))

            # 4. Relevant semantic facts (scoped search matching subtask keywords + deduplication)
            semantic_entries = self.store.search(
                query=f"{subtask_title} {subtask_description}",
                category=MemoryCategory.SEMANTIC,
                limit=3,
                min_confidence=0.8,
                caller_role=agent_name,
            )
            semantic_facts = []
            seen_keys: set[str] = set()
            for e in semantic_entries:
                if e.key in seen_keys:
                    continue
                seen_keys.add(e.key)
                # Deduplicate if content already present in prerequisite outputs
                if any(e.content in str(v.get("output", "")) for v in prereq_results.values()):
                    continue
                # Budget character length per fact
                truncated_fact = e.content[:1500] + ("... [truncated]" if len(e.content) > 1500 else "")
                semantic_facts.append({"key": e.key, "fact": truncated_fact, "source": e.source})

            # 5. Relevant procedural hints (e.g. calculation formats, file conventions)
            proc_entries = self.store.search(
                query=agent_name,
                category=MemoryCategory.PROCEDURAL,
                limit=2,
                caller_role=agent_name,
            )
            procedural_hints = [e.content[:800] for e in proc_entries]

            return ContextEnvelope(
                task_id=task_id,
                subtask_id=subtask_id,
                agent_name=agent_name,
                goal=goal,
                subtask_title=subtask_title,
                subtask_description=subtask_description,
                prerequisite_results=prereq_results,
                verified_deliverables=deliverables,
                working_notes=working_notes,
                semantic_facts=semantic_facts,
                procedural_hints=procedural_hints,
            )

    def get_task_context(self, task_id: str) -> dict[str, Any]:
        """Return full isolated context state for a task."""
        with self._lock:
            return dict(self._task_working_context.get(task_id, {}))
