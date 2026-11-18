"""core/proactive/notifications.py — Persistent Notification Store & Delivery Manager.

Implements Phase 6 of Milestone 10: Notification Center backend.
- Persists notification records in `memory/notifications/<id>.json`.
- Prevents notification spam with anti-duplication deduplication windows.
- Supports read, dismiss, snooze, accept, and reject transitions.
- Exposes unread counts for HUD/NavBar notification badge counters.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from core.proactive.types import NotificationRecord, RecommendationPriority

logger = logging.getLogger("optimus.proactive.notifications")


class NotificationManager:
    """Thread-safe persistent notification delivery manager."""

    def __init__(self, storage_dir: Path | str | None = None, max_history: int = 200) -> None:
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path.cwd() / "memory" / "notifications"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_history = max_history
        self._lock = threading.RLock()
        self._notifications: dict[str, NotificationRecord] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load stored notifications from disk."""
        with self._lock:
            for file_path in self.storage_dir.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    rec = NotificationRecord.from_dict(data)
                    self._notifications[rec.notification_id] = rec
                except Exception as ex:
                    logger.warning("Failed to load notification file %s: %s", file_path, ex)

    def _persist(self, rec: NotificationRecord) -> None:
        """Atomically persist a notification record to disk."""
        target_path = self.storage_dir / f"{rec.notification_id}.json"
        temp_path = self.storage_dir / f"{rec.notification_id}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
                f.flush()
            if target_path.exists():
                target_path.unlink()
            temp_path.rename(target_path)
        except Exception as ex:
            logger.error("Failed to persist notification %s: %s", rec.notification_id, ex)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def add_notification(
        self,
        title: str,
        message: str,
        category: str = "RECOMMENDATION",
        priority: RecommendationPriority = RecommendationPriority.MEDIUM,
        source: str = "proactive_engine",
        evidence: dict[str, Any] | None = None,
        recommendation_id: str | None = None,
        actions: list[dict[str, str]] | None = None,
        dedup_window_seconds: float = 300.0,
    ) -> NotificationRecord:
        """Create and deliver a notification, deduplicating identical recent alerts."""
        with self._lock:
            now = time.time()
            # Anti-spam deduplication check
            for existing in self._notifications.values():
                if (
                    existing.title == title
                    and existing.message == message
                    and not existing.dismissed
                    and (now - existing.created_at) < dedup_window_seconds
                ):
                    # Update timestamp and evidence instead of creating duplicate
                    existing.created_at = now
                    if evidence:
                        existing.evidence.update(evidence)
                    self._persist(existing)
                    return existing

            rec = NotificationRecord(
                title=title,
                message=message,
                category=category,
                priority=priority,
                source=source,
                created_at=now,
                evidence=evidence or {},
                recommendation_id=recommendation_id,
                actions=actions or [],
            )

            self._notifications[rec.notification_id] = rec
            self._persist(rec)

            # Bound history size
            if len(self._notifications) > self.max_history:
                sorted_recs = sorted(self._notifications.values(), key=lambda r: r.created_at)
                excess = len(self._notifications) - self.max_history
                for old in sorted_recs[:excess]:
                    self._notifications.pop(old.notification_id, None)
                    p = self.storage_dir / f"{old.notification_id}.json"
                    if p.exists():
                        try:
                            p.unlink()
                        except Exception:
                            pass

            return rec

    def post(
        self,
        title: str,
        message: str,
        category: str = "RECOMMENDATION",
        priority: RecommendationPriority = RecommendationPriority.MEDIUM,
        source: str = "proactive_engine",
        evidence: dict[str, Any] | None = None,
        recommendation_id: str | None = None,
        actions: list[dict[str, str]] | None = None,
        dedup_window_seconds: float = 300.0,
    ) -> NotificationRecord:
        """Convenience alias for add_notification."""
        return self.add_notification(
            title=title,
            message=message,
            category=category,
            priority=priority,
            source=source,
            evidence=evidence,
            recommendation_id=recommendation_id,
            actions=actions,
            dedup_window_seconds=dedup_window_seconds,
        )

    def mark_read(self, notification_id: str) -> bool:
        """Alias for mark_as_read."""
        return self.mark_as_read(notification_id)

    def unread_count(self) -> int:
        """Alias for get_unread_count."""
        return self.get_unread_count()

    def list_notifications(
        self,
        include_dismissed: bool = False,
        priority_filter: str | None = None,
        category_filter: str | None = None,
        limit: int = 100,
    ) -> list[NotificationRecord]:
        """List notification records sorted newest first."""
        with self._lock:
            now = time.time()
            results: list[NotificationRecord] = []
            for rec in self._notifications.values():
                if not include_dismissed and not rec.is_visible(now):
                    continue
                if priority_filter and rec.priority.value != priority_filter:
                    continue
                if category_filter and rec.category != category_filter:
                    continue
                results.append(rec)

            # Sort by priority first (CRITICAL > HIGH > MEDIUM > LOW), then newest created_at
            priority_rank = {
                RecommendationPriority.CRITICAL.value: 4,
                RecommendationPriority.HIGH.value: 3,
                RecommendationPriority.MEDIUM.value: 2,
                RecommendationPriority.LOW.value: 1,
            }
            results.sort(key=lambda r: (priority_rank.get(r.priority.value, 0), r.created_at), reverse=True)
            return results[:limit]

    def get_notification(self, notification_id: str) -> NotificationRecord | None:
        with self._lock:
            return self._notifications.get(notification_id)

    def mark_as_read(self, notification_id: str) -> bool:
        with self._lock:
            rec = self._notifications.get(notification_id)
            if rec and not rec.read:
                rec.read = True
                self._persist(rec)
                return True
            return False

    def mark_all_read(self) -> int:
        with self._lock:
            count = 0
            for rec in self._notifications.values():
                if not rec.read:
                    rec.read = True
                    self._persist(rec)
                    count += 1
            return count

    def dismiss(self, notification_id: str) -> bool:
        with self._lock:
            rec = self._notifications.get(notification_id)
            if rec and not rec.dismissed:
                rec.dismissed = True
                self._persist(rec)
                return True
            return False

    def snooze(self, notification_id: str, minutes: int = 30, duration_seconds: float | None = None) -> bool:
        with self._lock:
            rec = self._notifications.get(notification_id)
            if rec:
                offset = duration_seconds if duration_seconds is not None else (minutes * 60)
                rec.snoozed_until = time.time() + offset
                self._persist(rec)
                return True
            return False

    def get_unread_count(self) -> int:
        with self._lock:
            now = time.time()
            return sum(1 for r in self._notifications.values() if not r.read and r.is_visible(now))


_GLOBAL_NOTIFICATIONS: NotificationManager | None = None


def get_notification_manager() -> NotificationManager:
    global _GLOBAL_NOTIFICATIONS
    if _GLOBAL_NOTIFICATIONS is None:
        _GLOBAL_NOTIFICATIONS = NotificationManager()
    return _GLOBAL_NOTIFICATIONS
