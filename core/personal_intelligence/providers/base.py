"""core/personal_intelligence/providers/base.py — Abstract Base Provider Adapter.

Defines the common lifecycle contract for all external communication and calendar providers:
- Authentication & Connection Status
- Incremental and Initial Synchronization
- Rate Limiting and Token Refresh
- Health Checks & Error Normalization
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from core.personal_intelligence.types import (
    Account,
    Message,
    PersonalEvent,
    ProviderType,
)


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
        """Verify token expiration and refresh if necessary."""

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
        """Revoke active tokens and disconnect from provider."""
