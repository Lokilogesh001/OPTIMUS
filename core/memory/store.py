"""core/memory/store.py — Persistent Atomic Multi-Category Memory Store for OPTIMUS.

Provides:
- Persistent JSON storage for WORKING, EPISODIC, SEMANTIC, and PROCEDURAL memories.
- Atomic file writes with Windows retry backoff.
- In-memory indices for sub-millisecond search and retrieval.
- Access role enforcement preventing unauthorized role access.
- Optional synchronization bridge with Mark-LVII long_term.json.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.memory.types import MemoryCategory, MemoryEntry


def _atomic_write_json(target_path: Path, data: Any) -> None:
    """Atomically write JSON data using tempfile replace with Windows retry backoff."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.{secrets.token_hex(3)}.tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")

    # Retry backoff for Windows file locks
    for attempt in range(5):
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


class MemoryStore:
    """Thread-safe persistent memory repository supporting multi-category storage and search."""

    def __init__(self, storage_root: Path | str, sync_legacy: bool = True) -> None:
        self.root = Path(storage_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sync_legacy = sync_legacy
        self._lock = threading.RLock()
        self._entries: dict[str, MemoryEntry] = {}
        # category -> key -> entry_id index
        self._cat_key_index: dict[MemoryCategory, dict[str, str]] = {
            cat: {} for cat in MemoryCategory
        }
        self._load_all()

    def _get_category_file(self, category: MemoryCategory) -> Path:
        cat_name = category.value.lower() if isinstance(category, MemoryCategory) else str(category).lower()
        return self.root / f"{cat_name}.json"

    def _load_all(self) -> None:
        """Hydrate in-memory index from disk across all category files."""
        with self._lock:
            for cat in MemoryCategory:
                file_path = self._get_category_file(cat)
                if not file_path.is_file():
                    continue
                try:
                    raw_data = json.loads(file_path.read_text(encoding="utf-8"))
                    if isinstance(raw_data, list):
                        for item in raw_data:
                            entry = MemoryEntry.from_dict(item)
                            self._entries[entry.entry_id] = entry
                            self._cat_key_index[entry.category][entry.key] = entry.entry_id
                except Exception as e:
                    print(f"[MemoryStore] Failed loading category '{cat.value}': {e}")

    def _persist_category(self, category: MemoryCategory) -> None:
        """Persist all entries of a specific category to its atomic JSON file."""
        entries = [
            e.to_dict() for e in self._entries.values() if e.category == category
        ]
        file_path = self._get_category_file(category)
        _atomic_write_json(file_path, entries)

    # ── CRUD Operations ──────────────────────────────────────────────────────

    def put(self, entry: MemoryEntry) -> MemoryEntry:
        """Store or update a memory entry atomically."""
        with self._lock:
            entry.updated_at = time.time()
            self._entries[entry.entry_id] = entry
            self._cat_key_index[entry.category][entry.key] = entry.entry_id
            self._persist_category(entry.category)

            # Optional bridge to Mark-LVII legacy long_term.json
            if self.sync_legacy and entry.category == MemoryCategory.SEMANTIC:
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
        caller_role: str | None = None
    ) -> MemoryEntry | None:
        """Fetch memory entry by category and key."""
        with self._lock:
            entry_id = self._cat_key_index.get(category, {}).get(key)
            if not entry_id:
                return None
            return self.get(entry_id, caller_role=caller_role)

    def delete(self, entry_id: str) -> bool:
        """Remove a memory entry by ID."""
        with self._lock:
            entry = self._entries.pop(entry_id, None)
            if not entry:
                return False
            self._cat_key_index.get(entry.category, {}).pop(entry.key, None)
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

            for k, v in updates.items():
                if hasattr(entry, k) and k != "entry_id":
                    setattr(entry, k, v)

            entry.updated_at = time.time()

            # Handle index updates if category or key changed
            if entry.category != old_category or entry.key != old_key:
                self._cat_key_index.get(old_category, {}).pop(old_key, None)
                self._cat_key_index[entry.category][entry.key] = entry.entry_id
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
    ) -> list[MemoryEntry]:
        """Lexical search across memory entries with category, tag, and permission filtering."""
        with self._lock:
            words = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]
            tag_set = {t.lower() for t in tags} if tags else None

            scored_entries: list[tuple[float, MemoryEntry]] = []

            for entry in self._entries.values():
                # 1. Category filter
                if category is not None and entry.category != category:
                    continue

                # 2. Confidence filter
                if entry.confidence < min_confidence:
                    continue

                # 3. Access role filter
                if caller_role is not None and not entry.is_accessible_by(caller_role):
                    continue

                # 4. Tag filter
                if tag_set is not None:
                    entry_tags = {t.lower() for t in entry.tags}
                    if not tag_set.intersection(entry_tags):
                        continue

                # 5. Score calculation
                score = 0.0
                hay_key = entry.key.lower()
                hay_content = entry.content.lower()
                hay_tags = [t.lower() for t in entry.tags]

                if not words:
                    # Default: recency score if query is empty
                    score = 1.0 + (entry.updated_at / 1e10)
                else:
                    for w in words:
                        if w == hay_key:
                            score += 15.0
                        elif w in hay_key:
                            score += 8.0
                        if any(w == t for t in hay_tags):
                            score += 10.0
                        elif any(w in t for t in hay_tags):
                            score += 5.0
                        if w in hay_content:
                            score += 4.0

                # Verified boost
                if entry.is_verified:
                    score += 5.0

                # Confidence weighting
                score *= entry.confidence

                if score > 0.0:
                    scored_entries.append((score, entry))

            # Sort by score descending, then by updated_at descending
            scored_entries.sort(key=lambda t: (t[0], t[1].updated_at), reverse=True)
            return [entry for _, entry in scored_entries[:limit]]

    def list_all(self, limit: int = 100) -> list[MemoryEntry]:
        """Return all memories across categories sorted by recency."""
        with self._lock:
            entries = list(self._entries.values())
            entries.sort(key=lambda e: e.updated_at, reverse=True)
            return entries[:limit]

    def list_category(
        self,
        category: MemoryCategory,
        limit: int = 50,
        caller_role: str | None = None
    ) -> list[MemoryEntry]:
        """Return all memories in a given category sorted by recency."""
        with self._lock:
            entries = [
                e for e in self._entries.values()
                if e.category == category and (caller_role is None or e.is_accessible_by(caller_role))
            ]
            entries.sort(key=lambda e: e.updated_at, reverse=True)
            return entries[:limit]

    def clear_working(self, task_id: str | None = None) -> int:
        """Clear temporary working memory entries (optionally scoped to a specific task)."""
        with self._lock:
            to_delete = []
            for entry_id, entry in self._entries.items():
                if entry.category == MemoryCategory.WORKING:
                    if task_id is None or entry.task_id == task_id:
                        to_delete.append(entry_id)

            for eid in to_delete:
                e = self._entries.pop(eid, None)
                if e:
                    self._cat_key_index.get(MemoryCategory.WORKING, {}).pop(e.key, None)

            if to_delete:
                self._persist_category(MemoryCategory.WORKING)

            return len(to_delete)

    # ── Legacy Integration Bridge ────────────────────────────────────────────

    def _sync_to_legacy(self, entry: MemoryEntry) -> None:
        """Optionally sync verified semantic knowledge into Mark-LVII's long_term.json."""
        try:
            from memory import memory_manager
            # Sync to Mark-LVII notes or projects category
            category = "projects" if "project" in entry.tags else "notes"
            memory_manager.remember(key=entry.key, value=entry.content, category=category)
        except Exception:
            pass
