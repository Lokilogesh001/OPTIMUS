"""core/memory/store.py — Persistent Atomic Multi-Category Memory Store for OPTIMUS.

Provides:
- Persistent JSON storage for 9 refined M13 categories + backward-compatible operational categories.
- Atomic file writes with Windows retry backoff and dirty category persistence.
- In-memory indices by ID, (category, key), and canonical_topic for sub-millisecond retrieval.
- Access role enforcement preventing unauthorized cross-domain leakage.
- Optional synchronization bridge with Mark-LVII legacy long_term.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.memory.types import MemoryCategory, MemoryEntry, normalize_category

logger = logging.getLogger("optimus.memory.store")


def _atomic_write_json(target_path: Path, data: Any, max_retries: int = 5) -> None:
    """Atomically write JSON data using unique tempfile replace with Windows retry backoff."""
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


class MemoryStore:
    """Thread-safe persistent memory repository supporting multi-category storage and search."""

    def __init__(self, storage_root: Path | str | None = None, sync_legacy: bool = True) -> None:
        if storage_root is None:
            storage_root = Path.home() / ".optimus" / "memory"
        self.root = Path(storage_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sync_legacy = sync_legacy
        self._lock = threading.RLock()
        self._entries: dict[str, MemoryEntry] = {}
        # category -> key -> entry_id index
        self._cat_key_index: dict[MemoryCategory, dict[str, str]] = {
            cat: {} for cat in MemoryCategory
        }
        # canonical_topic -> list of entry_ids
        self._topic_index: dict[str, list[str]] = {}
        self._search_cache: dict[tuple, list[MemoryEntry]] = {}
        self._dirty_categories: set[MemoryCategory] = set()

        self._load_all()

    def _get_category_file(self, category: MemoryCategory) -> Path:
        cat_name = category.value.lower() if isinstance(category, MemoryCategory) else str(category).lower()
        return self.root / f"{cat_name}.json"

    def _load_all(self) -> None:
        """Hydrate in-memory index from disk across all category files."""
        with self._lock:
            # Check all category files
            for cat in MemoryCategory:
                file_path = self._get_category_file(cat)
                if not file_path.is_file():
                    continue
                try:
                    raw_data = json.loads(file_path.read_text(encoding="utf-8"))
                    if isinstance(raw_data, list):
                        for item in raw_data:
                            entry = MemoryEntry.from_dict(item)
                            self._index_entry(entry)
                except Exception as e:
                    logger.warning("Failed loading category '%s': %s", cat.value, e)

    def _index_entry(self, entry: MemoryEntry) -> None:
        """Internal index updater."""
        self._entries[entry.entry_id] = entry
        if entry.category not in self._cat_key_index:
            self._cat_key_index[entry.category] = {}
        self._cat_key_index[entry.category][entry.key] = entry.entry_id

        topics = set()
        if entry.canonical_topic:
            topics.add(entry.canonical_topic.strip().lower())
        if entry.key:
            topics.add(entry.key.strip().lower())

        for topic in topics:
            if topic:
                if topic not in self._topic_index:
                    self._topic_index[topic] = []
                if entry.entry_id not in self._topic_index[topic]:
                    self._topic_index[topic].append(entry.entry_id)

    def _unindex_entry(self, entry: MemoryEntry) -> None:
        """Internal index remover."""
        self._entries.pop(entry.entry_id, None)
        if entry.category in self._cat_key_index:
            self._cat_key_index[entry.category].pop(entry.key, None)
        topics = set()
        if entry.canonical_topic:
            topics.add(entry.canonical_topic.strip().lower())
        if entry.key:
            topics.add(entry.key.strip().lower())

        for topic in topics:
            if topic and topic in self._topic_index:
                if entry.entry_id in self._topic_index[topic]:
                    self._topic_index[topic].remove(entry.entry_id)

    def _persist_category(self, category: MemoryCategory) -> None:
        """Persist all entries of a specific category to its atomic JSON file."""
        entries = [
            e.to_dict() for e in self._entries.values() if e.category == category
        ]
        file_path = self._get_category_file(category)
        _atomic_write_json(file_path, entries)
        self._dirty_categories.discard(category)

    # ── CRUD Operations ──────────────────────────────────────────────────────

    def put(self, entry: MemoryEntry) -> MemoryEntry:
        """Store or update a memory entry atomically."""
        with self._lock:
            entry.updated_at = time.time()
            self._index_entry(entry)
            self._search_cache.clear()
            self._persist_category(entry.category)

            # Optional bridge to Mark-LVII legacy long_term.json
            if self.sync_legacy and entry.category in (MemoryCategory.SEMANTIC, MemoryCategory.STABLE_USER_CONTEXT):
                self._sync_to_legacy(entry)

            return entry

    def get(self, entry_id: str, caller_role: str | None = None) -> MemoryEntry | None:
        """Fetch memory entry by ID with access control enforcement."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if not entry:
                return None
            if caller_role and not entry.is_accessible_by(caller_role):
                return None
            return entry

    def get_by_key(
        self,
        category: MemoryCategory,
        key: str,
        caller_role: str | None = None,
    ) -> MemoryEntry | None:
        """Fetch memory entry by category and key."""
        with self._lock:
            entry_id = self._cat_key_index.get(category, {}).get(key)
            if not entry_id:
                return None
            return self.get(entry_id, caller_role=caller_role)

    def get_by_topic(self, canonical_topic: str, include_superseded: bool = False) -> list[MemoryEntry]:
        """Fetch all memory entries under a canonical topic."""
        topic_norm = (canonical_topic or "").strip().lower()
        with self._lock:
            entry_ids = self._topic_index.get(topic_norm, [])
            results = []
            for eid in entry_ids:
                entry = self._entries.get(eid)
                if entry:
                    if not include_superseded and entry.superseded_by:
                        continue
                    results.append(entry)
            return results

    def delete(self, entry_id: str) -> bool:
        """Remove a memory entry by ID."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if not entry:
                return False
            self._unindex_entry(entry)
            self._search_cache.clear()
            self._persist_category(entry.category)
            return True

    def update(self, entry_id: str, updates: dict[str, Any]) -> MemoryEntry | None:
        """Update fields of an existing memory entry."""
        with self._lock:
            entry = self._entries.get(entry_id)
            if not entry:
                return None

            old_category = entry.category
            old_key = entry.key
            old_topic = entry.canonical_topic

            for k, v in updates.items():
                if hasattr(entry, k) and k != "entry_id":
                    setattr(entry, k, v)

            entry.updated_at = time.time()
            self._search_cache.clear()

            # Handle index updates if category, key, or topic changed
            if entry.category != old_category or entry.key != old_key or entry.canonical_topic != old_topic:
                self._cat_key_index.get(old_category, {}).pop(old_key, None)
                if old_topic and old_topic in self._topic_index:
                    if entry_id in self._topic_index[old_topic]:
                        self._topic_index[old_topic].remove(entry_id)
                self._index_entry(entry)
                self._persist_category(old_category)

            self._persist_category(entry.category)
            return entry

    # ── Search & Retrieval ───────────────────────────────────────────────────

    def search(
        self,
        query: str,
        category: MemoryCategory | None = None,
        tags: list[str] | None = None,
        limit: int = 10,
        min_confidence: float = 0.0,
        caller_role: str | None = None,
        include_superseded: bool = False,
        include_expired: bool = False,
    ) -> list[MemoryEntry]:
        """Lexical search across memory entries with category, tag, and permission filtering."""
        cache_key = (
            query or "",
            category.value if category else None,
            tuple(sorted(tags)) if tags else None,
            limit,
            min_confidence,
            caller_role,
            include_superseded,
            include_expired,
        )

        with self._lock:
            if cache_key in self._search_cache:
                return list(self._search_cache[cache_key])

            words = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]
            tag_set = {t.lower() for t in tags} if tags else None
            now = time.time()

            scored_entries: list[tuple[float, MemoryEntry]] = []

            for entry in self._entries.values():
                # 1. Superseded filter
                if not include_superseded and entry.superseded_by:
                    continue

                # 2. Expired filter
                if not include_expired and entry.is_expired(now):
                    continue

                # 3. Category filter
                if category is not None and entry.category != category:
                    continue

                # 4. Confidence filter
                if entry.confidence < min_confidence:
                    continue

                # 5. Access role filter
                if caller_role is not None and not entry.is_accessible_by(caller_role):
                    continue

                # 6. Tag filter
                if tag_set is not None:
                    entry_tags = {t.lower() for t in entry.tags}
                    if not tag_set.intersection(entry_tags):
                        continue

                # 7. Score calculation
                score = 0.0
                hay_key = entry.key.lower()
                hay_content = entry.content.lower()
                hay_topic = (entry.canonical_topic or "").lower()
                hay_tags = [t.lower() for t in entry.tags]

                if not words:
                    score = 1.0 + (entry.updated_at / 1e10)
                else:
                    for w in words:
                        if w == hay_key:
                            score += 15.0
                        elif w in hay_key:
                            score += 8.0
                        if hay_topic and (w == hay_topic or w in hay_topic):
                            score += 12.0
                        if any(w == t for t in hay_tags):
                            score += 10.0
                        elif any(w in t for t in hay_tags):
                            score += 5.0
                        if w in hay_content:
                            score += 4.0

                if entry.is_verified:
                    score += 5.0

                score *= max(0.1, entry.confidence)

                if score > 0.0:
                    scored_entries.append((score, entry))

            scored_entries.sort(key=lambda t: (t[0], t[1].updated_at), reverse=True)
            results = [entry for _, entry in scored_entries[:limit]]

            if len(self._search_cache) >= 128:
                self._search_cache.clear()
            self._search_cache[cache_key] = results

            return list(results)

    def list_all(self, limit: int = 100, include_superseded: bool = False) -> list[MemoryEntry]:
        """Return all memories across categories sorted by recency."""
        with self._lock:
            entries = [
                e for e in self._entries.values()
                if include_superseded or not e.superseded_by
            ]
            entries.sort(key=lambda e: e.updated_at, reverse=True)
            return entries[:limit]

    def list_category(
        self,
        category: MemoryCategory,
        limit: int = 50,
        caller_role: str | None = None,
        include_superseded: bool = False,
    ) -> list[MemoryEntry]:
        """Return all memories in a given category sorted by recency."""
        with self._lock:
            entries = [
                e for e in self._entries.values()
                if e.category == category
                and (caller_role is None or e.is_accessible_by(caller_role))
                and (include_superseded or not e.superseded_by)
            ]
            entries.sort(key=lambda e: e.updated_at, reverse=True)
            return entries[:limit]

    def clear_working(self, task_id: str | None = None) -> int:
        """Clear temporary working memory entries."""
        with self._lock:
            to_delete = []
            for entry_id, entry in self._entries.items():
                if entry.category in (MemoryCategory.WORKING, MemoryCategory.TEMPORARY_CONVERSATION_CONTEXT):
                    if task_id is None or entry.task_id == task_id:
                        to_delete.append(entry_id)

            for eid in to_delete:
                e = self._entries.pop(eid, None)
                if e:
                    self._unindex_entry(e)

            if to_delete:
                self._persist_category(MemoryCategory.TEMPORARY_CONVERSATION_CONTEXT)
                self._persist_category(MemoryCategory.WORKING)

            return len(to_delete)

    def count(self) -> int:
        """Return total active indexed memory entries."""
        with self._lock:
            return len(self._entries)

    # ── Legacy Integration Bridge ────────────────────────────────────────────

    def _sync_to_legacy(self, entry: MemoryEntry) -> None:
        """Optionally sync verified semantic knowledge into Mark-LVII's long_term.json."""
        try:
            from memory import memory_manager
            category = "projects" if "project" in entry.tags else "notes"
            memory_manager.remember(key=entry.key, value=entry.content, category=category)
        except Exception:
            pass
