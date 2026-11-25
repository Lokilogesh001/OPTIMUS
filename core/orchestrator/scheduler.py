"""core/orchestrator/scheduler.py — Unified Shared Action Scheduler & Resource Coordinator.

Coordinates exclusive and priority-queued access to shared desktop, UI, filesystem
opening, and audio resources across all OPTIMUS agents (File, Computer, Browser,
Application, Voice) without deadlocks, starvation, or duplicate execution.
"""

from __future__ import annotations

import contextlib
import heapq
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator

logger = logging.getLogger("optimus.orchestrator.scheduler")


class ResourceType(str, Enum):
    """Shared host and desktop resources requiring coordinated arbitration."""
    ACTIVE_WINDOW = "active_window"
    AUDIO_CAPTURE_PLAYBACK = "audio_capture_playback"
    FILE_OPENING = "file_opening"
    KEYBOARD_MOUSE = "keyboard_mouse"
    RUN_DIALOG = "run_dialog"
    SCREEN_INTERACTION = "screen_interaction"


_PHYSICAL_UI_RESOURCES = frozenset({
    ResourceType.RUN_DIALOG,
    ResourceType.KEYBOARD_MOUSE,
    ResourceType.ACTIVE_WINDOW,
    ResourceType.FILE_OPENING,
})


class ResourceAcquisitionError(Exception):
    """Raised on timeout, cancellation, or duplicate suppression."""


class TaskCancelledError(ResourceAcquisitionError):
    """Raised when a queued or active task is cancelled."""


class DuplicateActionError(ResourceAcquisitionError):
    """Raised when an identical idempotency key is executed within the suppression window."""


@dataclass(order=True)
class _WaitEntry:
    effective_priority: float
    seq: int
    task_id: str = field(compare=False)
    base_priority: int = field(compare=False)
    enqueued_at: float = field(compare=False)
    resources: tuple[ResourceType, ...] = field(compare=False)


class SharedActionScheduler:
    """Process-wide priority scheduler coordinating shared OPTIMUS resources."""

    _instance: SharedActionScheduler | None = None
    _singleton_lock = threading.RLock()

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.RLock())
        self._owners: dict[ResourceType, str] = {}
        self._owner_depth: dict[tuple[ResourceType, str], int] = {}
        self._acquired_at: dict[ResourceType, float] = {}
        self._wait_heap: list[_WaitEntry] = []
        self._seq: int = 0
        self._cancelled_tasks: set[str] = set()
        self._recent_idempotency: dict[str, float] = {}

    @classmethod
    def instance(cls) -> SharedActionScheduler:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._singleton_lock:
            cls._instance = None

    def cancel_task(self, task_id: str) -> None:
        """Mark a task cancelled and wake all waiting threads so they exit promptly."""
        with self._cond:
            self._cancelled_tasks.add(task_id)
            self._cond.notify_all()

    def clear_cancellation(self, task_id: str) -> None:
        with self._cond:
            self._cancelled_tasks.discard(task_id)

    def get_resource_owners(self) -> dict[str, str]:
        with self._cond:
            return {r.value: owner for r, owner in self._owners.items()}

    def _can_acquire_all(self, task_id: str, res_tuple: tuple[ResourceType, ...]) -> bool:
        for res in res_tuple:
            cur = self._owners.get(res)
            if cur is not None and cur != task_id:
                return False
        return True

    @contextlib.contextmanager
    def acquire(
        self,
        resources: ResourceType | list[ResourceType] | tuple[ResourceType, ...],
        *,
        task_id: str = "orchestrator",
        priority: int = 5,
        timeout: float = 15.0,
        idempotency_key: str | None = None,
        dedup_window_s: float = 1.5,
    ) -> Iterator[dict[str, Any]]:
        """Acquire one or more shared resources safely in canonical order.

        Prevents deadlocks via canonical ResourceType sorting, prevents starvation via
        wait-age priority boosting, enforces cancellation and bounded timeouts, and
        coordinates with DesktopInteractionLock for physical UI resources.
        """
        if isinstance(resources, ResourceType):
            res_list = [resources]
        else:
            res_list = sorted(set(resources), key=lambda r: r.value)
        res_tuple = tuple(res_list)

        now = time.monotonic()
        with self._cond:
            if task_id in self._cancelled_tasks:
                raise TaskCancelledError(f"Task '{task_id}' was cancelled before acquiring {res_tuple}")

            if idempotency_key:
                # Prune expired keys
                expired = [k for k, ts in self._recent_idempotency.items() if now - ts > dedup_window_s]
                for k in expired:
                    self._recent_idempotency.pop(k, None)
                last_ts = self._recent_idempotency.get(idempotency_key)
                if last_ts is not None and (now - last_ts) <= dedup_window_s:
                    raise DuplicateActionError(f"Duplicate action suppressed for key '{idempotency_key}'")
                self._recent_idempotency[idempotency_key] = now

            self._seq += 1
            entry = _WaitEntry(
                effective_priority=float(priority),
                seq=self._seq,
                task_id=task_id,
                base_priority=priority,
                enqueued_at=now,
                resources=res_tuple,
            )
            heapq.heappush(self._wait_heap, entry)

            deadline = now + max(0.05, timeout)
            acquired = False
            try:
                while True:
                    if task_id in self._cancelled_tasks:
                        raise TaskCancelledError(f"Task '{task_id}' cancelled while waiting for resources")

                    # Anti-starvation: boost priority by 1 level per second waited
                    cur_t = time.monotonic()
                    self._prune_and_age_heap(cur_t)

                    # Only head-of-line (or compatible reentrant owner) acquires next
                    if self._can_acquire_all(task_id, res_tuple) and self._is_next_eligible(entry):
                        for res in res_tuple:
                            self._owners[res] = task_id
                            self._owner_depth[(res, task_id)] = self._owner_depth.get((res, task_id), 0) + 1
                            self._acquired_at.setdefault(res, cur_t)
                        acquired = True
                        break

                    remaining = deadline - cur_t
                    if remaining <= 0:
                        holders = {r.value: self._owners.get(r) for r in res_tuple if r in self._owners}
                        raise ResourceAcquisitionError(
                            f"Timed out after {timeout:.2f}s waiting for {[r.value for r in res_tuple]} (held by {holders})"
                        )
                    self._cond.wait(timeout=min(0.1, remaining))
            finally:
                with contextlib.suppress(ValueError):
                    self._wait_heap.remove(entry)
                    heapq.heapify(self._wait_heap)
                self._cond.notify_all()

        # Acquire DesktopInteractionLock if physical UI coordination is involved;
        # wrapped inside try/finally so any failure or timeout ALWAYS releases _owners.
        ui_lock = None
        try:
            if any(r in _PHYSICAL_UI_RESOURCES for r in res_tuple):
                from core.application_intelligence.desktop_lock import DesktopInteractionLock
                ui_lock = DesktopInteractionLock(owner=f"scheduler:{task_id}", timeout=max(1.0, timeout))
                ui_lock.acquire()

            yield {
                "task_id": task_id,
                "resources": [r.value for r in res_tuple],
                "acquired_at": time.time(),
            }
        finally:
            if ui_lock is not None:
                with contextlib.suppress(Exception):
                    ui_lock.release()
            with self._cond:
                for res in res_tuple:
                    key = (res, task_id)
                    depth = self._owner_depth.get(key, 1) - 1
                    if depth <= 0:
                        self._owner_depth.pop(key, None)
                        if self._owners.get(res) == task_id:
                            self._owners.pop(res, None)
                            self._acquired_at.pop(res, None)
                    else:
                        self._owner_depth[key] = depth
                self._cond.notify_all()

    def _prune_and_age_heap(self, now: float) -> None:
        changed = False
        for item in self._wait_heap:
            waited = max(0.0, now - item.enqueued_at)
            new_prio = float(item.base_priority) - waited * 1.0
            if abs(new_prio - item.effective_priority) > 0.05:
                item.effective_priority = new_prio
                changed = True
        if changed:
            heapq.heapify(self._wait_heap)

    def _is_next_eligible(self, entry: _WaitEntry) -> bool:
        """Return True if caller already owns all requested resources (reentrant) or no higher-priority waiter conflicts."""
        if all(self._owners.get(r) == entry.task_id for r in entry.resources):
            return True
        entry_set = set(entry.resources)
        for other in self._wait_heap:
            if other is entry or other.task_id == entry.task_id:
                continue
            # Do not let a waiter blocked on a resource we already hold block our upgrade/reentry (prevents cycle deadlock)
            if any(self._owners.get(r) == entry.task_id for r in other.resources):
                continue
            if (other.effective_priority, other.seq) < (entry.effective_priority, entry.seq):
                if entry_set.intersection(other.resources):
                    return False
        return True

    def run_coordinated(
        self,
        resources: ResourceType | list[ResourceType],
        fn: Callable[[], Any],
        *,
        task_id: str = "orchestrator",
        priority: int = 5,
        timeout: float = 15.0,
        idempotency_key: str | None = None,
    ) -> Any:
        """Execute a callable while holding the requested shared resources."""
        with self.acquire(
            resources,
            task_id=task_id,
            priority=priority,
            timeout=timeout,
            idempotency_key=idempotency_key,
        ):
            return fn()
