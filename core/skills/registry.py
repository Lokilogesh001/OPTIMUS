"""core/skills/registry.py — Persistent & Thread-Safe Skill Registry for OPTIMUS.

Maintains:
- Canonical catalogue of discovered and registered skills.
- Enabled / Disabled status per skill.
- Trust levels and security audit records.
- Historical execution records.
- Automatic disk persistence in config/skills_registry.json.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from core.skills.models import (
    SkillCategory,
    SkillExecutionRecord,
    SkillManifest,
    SkillTrustLevel,
)

logger = logging.getLogger("optimus.skills.registry")


class SkillRegistry:
    """Thread-safe persistent skill registry with querying and telemetry tracking."""

    _instance: SkillRegistry | None = None
    _instance_lock = threading.Lock()

    def __init__(self, storage_path: Path | str | None = None) -> None:
        if storage_path is None:
            storage_path = Path(__file__).resolve().parent.parent.parent / "config" / "skills_registry.json"
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._skills: dict[str, SkillManifest] = {}
        self._execution_history: list[SkillExecutionRecord] = []

        self.load()

    @classmethod
    def instance(cls) -> SkillRegistry:
        """Global thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register(self, manifest: SkillManifest, overwrite: bool = False) -> bool:
        """Register a skill into the registry, preventing accidental duplicates."""
        with self._lock:
            sid = manifest.id.lower().strip()
            if sid in self._skills and not overwrite:
                # Update instructions/hash if modified, but preserve user enabled state
                existing = self._skills[sid]
                existing.instructions = manifest.instructions
                existing.content_sha256 = manifest.content_sha256
                existing.category = manifest.category
                existing.tags = list(set(existing.tags + manifest.tags))
                self.save()
                return False

            self._skills[sid] = manifest
            self.save()
            return True

    def unregister(self, skill_id: str) -> bool:
        """Remove a skill from the registry."""
        with self._lock:
            sid = skill_id.lower().strip()
            removed = self._skills.pop(sid, None) is not None
            if removed:
                self.save()
            return removed

    def get(self, skill_id: str) -> SkillManifest | None:
        """Fetch a registered skill manifest by id."""
        with self._lock:
            return self._skills.get(skill_id.lower().strip())

    def list_skills(
        self,
        enabled_only: bool = False,
        category: SkillCategory | None = None,
        trust_level: SkillTrustLevel | None = None,
        search_query: str = "",
    ) -> list[SkillManifest]:
        """Query registered skills with optional filtering."""
        with self._lock:
            results = list(self._skills.values())

            if enabled_only:
                results = [s for s in results if s.enabled]
            if category:
                results = [s for s in results if s.category == category]
            if trust_level:
                results = [s for s in results if s.trust_level == trust_level]
            if search_query:
                q = search_query.strip().lower()
                results = [
                    s for s in results
                    if q in s.id or q in s.name.lower() or q in s.description.lower() or any(q in t for t in s.tags)
                ]

            return results

    def set_enabled(self, skill_id: str, enabled: bool) -> bool:
        """Enable or disable a registered skill."""
        with self._lock:
            s = self.get(skill_id)
            if not s:
                return False
            # Blocked skills cannot be enabled without changing trust level
            if enabled and s.trust_level == SkillTrustLevel.BLOCKED:
                return False
            s.enabled = enabled
            self.save()
            return True

    def set_trust_level(self, skill_id: str, trust_level: SkillTrustLevel) -> bool:
        """Update trust ranking for a skill."""
        with self._lock:
            s = self.get(skill_id)
            if not s:
                return False
            s.trust_level = trust_level
            if trust_level == SkillTrustLevel.BLOCKED:
                s.enabled = False
            self.save()
            return True

    def record_execution(self, record: SkillExecutionRecord) -> None:
        """Record skill execution history and update usage counters."""
        with self._lock:
            self._execution_history.append(record)
            s = self.get(record.skill_id)
            if s:
                s.execution_count += 1
                s.last_executed_at = record.timestamp
            # Bound history in memory
            if len(self._execution_history) > 500:
                self._execution_history = self._execution_history[-500:]
            self.save()

    def get_execution_history(self, skill_id: str | None = None, limit: int = 50) -> list[SkillExecutionRecord]:
        """Retrieve recent skill execution records."""
        with self._lock:
            history = self._execution_history
            if skill_id:
                history = [h for h in history if h.skill_id == skill_id.lower()]
            return history[-limit:]

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        """Persist registered skills to storage path."""
        try:
            data = {
                "skills": {sid: m.to_dict() for sid, m in self._skills.items()},
                "history": [h.to_dict() for h in self._execution_history[-100:]],
                "saved_at": time.time(),
            }
            self.storage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save skills registry: {e}")

    def load(self) -> None:
        """Load registered skills from disk."""
        if not self.storage_path.is_file():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            raw_skills = data.get("skills", {})
            for sid, sdata in raw_skills.items():
                self._skills[sid] = SkillManifest.from_dict(sdata)

            raw_history = data.get("history", [])
            self._execution_history = [
                SkillExecutionRecord(**h) for h in raw_history if isinstance(h, dict)
            ]
        except Exception as e:
            logger.warning(f"Could not load skills registry from disk: {e}")
