"""core/application_intelligence/desktop_lock.py — Desktop Interaction Lock.

Implements a reentrant, thread-safe, timeout-guarded synchronization primitive
to prevent focus race conditions and keystroke/mouse interleaving across
concurrent orchestrator subtasks.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("optimus.application_intelligence.desktop_lock")


class DesktopInteractionLockTimeout(Exception):
    """Raised when acquiring the desktop interaction lock exceeds the timeout."""


class DesktopInteractionLock:
    """Process-wide reentrant lock for physical and UI-focused desktop operations."""

    _lock = threading.RLock()
    _owner_stack: list[str] = []
    _acquired_at: float = 0.0

    def __init__(self, owner: str = "orchestrator", timeout: float = 30.0) -> None:
        self.owner = owner
        self.timeout = timeout
        self._acquired = False

    def acquire(self) -> bool:
        """Acquire the desktop interaction lock, waiting up to timeout seconds."""
        deadline = time.time() + self.timeout
        acquired = False
        while time.time() < deadline:
            acquired = self._lock.acquire(blocking=True, timeout=0.1)
            if acquired:
                break

        if not acquired:
            current = self.get_current_owner()
            logger.warning(
                "DesktopInteractionLock acquisition timed out for %s after %.1fs (current owner: %s)",
                self.owner,
                self.timeout,
                current,
            )
            raise DesktopInteractionLockTimeout(
                f"Failed to acquire desktop interaction lock after {self.timeout}s (held by {current})"
            )

        self._acquired = True
        DesktopInteractionLock._owner_stack.append(self.owner)
        DesktopInteractionLock._acquired_at = time.time()
        logger.debug("DesktopInteractionLock acquired by %s (depth=%d)", self.owner, len(DesktopInteractionLock._owner_stack))
        return True

    def release(self) -> None:
        """Release the desktop interaction lock."""
        if self._acquired:
            try:
                self._lock.release()
                logger.debug("DesktopInteractionLock released by %s", self.owner)
            except RuntimeError:
                pass
            finally:
                self._acquired = False
                if DesktopInteractionLock._owner_stack:
                    DesktopInteractionLock._owner_stack.pop()

    def __enter__(self) -> DesktopInteractionLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()

    @classmethod
    def is_locked(cls) -> bool:
        """Check whether the desktop lock is currently held."""
        return len(cls._owner_stack) > 0

    @classmethod
    def get_current_owner(cls) -> str | None:
        """Return the current lock owner."""
        return cls._owner_stack[-1] if cls._owner_stack else None
