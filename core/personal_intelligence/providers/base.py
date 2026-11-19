"""core/personal_intelligence/providers/base.py — Abstract Base Provider Adapter & OAuth Security.

Defines the common lifecycle contract for all external communication and calendar providers:
- Authentication & Connection Status
- Incremental and Initial Synchronization
- Rate Limiting and Inter-Process Token Refresh Locking
- PKCE (RFC 7636) and CSRF State Generation
- Honest Revocation Semantics (Remote Verified vs Disconnected Local Only)
- Health Checks & Error Normalization
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.personal_intelligence.types import (
    Account,
    Message,
    PersonalEvent,
    ProviderType,
)

logger = logging.getLogger("optimus.personal.providers")


def safe_parse_json(resp: Any) -> dict[str, Any]:
    """Robustly parse response dict from requests/httpx or mock objects."""
    if resp is None:
        return {}
    if hasattr(resp, "json") and callable(resp.json):
        try:
            res = resp.json()
            if isinstance(res, dict):
                return res
        except Exception:
            pass
    if hasattr(resp, "text"):
        try:
            res = json.loads(str(resp.text))
            if isinstance(res, dict):
                return res
        except Exception:
            pass
    return {}


def generate_pkce_pair() -> tuple[str, str]:
    """Generate RFC 7636 PKCE code_verifier and code_challenge (S256).

    Returns:
        (code_verifier, code_challenge)
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def generate_state_token() -> str:
    """Generate high-entropy random CSRF state token for OAuth redirect verification."""
    return secrets.token_urlsafe(32)


@contextmanager
def oauth_refresh_lock(
    account_id: str,
    locks_dir: Path | str | None = None,
    timeout: float = 10.0,
) -> Generator[None, None, None]:
    """Acquire an exclusive OS-level inter-process lock on OAuth token refresh for an account.

    Prevents Refresh Token Rotation (RTR) race conditions / stampedes when multiple
    processes detect an expired token simultaneously.
    """
    if locks_dir:
        ldir = Path(locks_dir)
    else:
        ldir = Path.cwd() / "memory" / "personal" / ".locks"

    ldir.mkdir(parents=True, exist_ok=True)
    lock_path = ldir / f"oauth_{account_id}.lock"

    with open(lock_path, "a+b") as f:
        start = time.perf_counter()
        acquired = False
        try:
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (OSError, PermissionError):
                    if (time.perf_counter() - start) >= timeout:
                        raise TimeoutError(
                            f"Timed out acquiring OAuth refresh lock for account '{account_id}' after {timeout:.1f}s"
                        ) from None
                    time.sleep(0.01 + (secrets.randbelow(15) / 1000.0))
            yield
        finally:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


class BaseProviderAdapter(ABC):
    """Abstract base class defining the standardized provider adapter interface."""

    def __init__(self, account: Account) -> None:
        self.account = account

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """The specific provider type handled by this adapter."""

    @abstractmethod
    def authenticate(self, auth_payload: dict[str, Any]) -> bool:
        """Authenticate account and store tokens in secure CredentialVault."""

    @abstractmethod
    def refresh_token_if_needed(self) -> bool:
        """Verify token expiration and refresh if necessary under oauth_refresh_lock."""

    @abstractmethod
    def check_health(self) -> dict[str, Any]:
        """Perform lightweight diagnostic health check against provider API."""

    @abstractmethod
    def initial_sync(self, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Perform initial sync returning (messages, events, cursor_token)."""

    @abstractmethod
    def incremental_sync(self, cursor: str, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Perform incremental sync from cursor returning (messages, events, new_cursor)."""

    @abstractmethod
    def revoke(self) -> bool:
        """Revoke active tokens remotely if possible, purge credentials, and update AuthStatus."""
