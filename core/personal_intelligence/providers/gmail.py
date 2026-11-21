"""core/personal_intelligence/providers/gmail.py — Official Gmail API Provider Adapter.

Implements:
- Official Google Gmail API v1 interaction.
- Least-privilege OAuth 2.0 (`gmail.readonly` default, progressive consent for `gmail.send`).
- Secure token storage in OS Keyring / CredentialVault.
- History ID-based incremental synchronization.
- Real-time live profile diagnostics & token health checks.
- Two-stage message retrieval (message list + full payload metadata).
- Token refresh with client_id and client_secret under exclusive lock.
- Prompt injection quarantine using core.outcome.untrusted.
- Backward-compatible offline mock transport mode for automated unit tests.
"""

from __future__ import annotations

import datetime
import email.utils
import logging
import re
import time
from typing import Any

import requests

from core.outcome import untrusted
from core.personal_intelligence.oauth import (
    get_google_client_id,
    get_google_client_secret,
)
from core.personal_intelligence.providers.base import (
    BaseProviderAdapter,
    oauth_refresh_lock,
    safe_parse_json,
)
from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    Message,
    PersonalEvent,
    ProviderType,
)
from core.security.credentials import get_credential, store_credential

logger = logging.getLogger("optimus.personal.gmail")


class GmailAdapter(BaseProviderAdapter):
    """Adapter for official Google Gmail REST API v1."""

    def __init__(self, account: Account, http_client: Any | None = None) -> None:
        super().__init__(account)
        self.http_client = http_client  # Allows injecting mock client for testing

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GMAIL

    def _get_access_token(self) -> str | None:
        """Retrieve stored access token from CredentialVault."""
        return get_credential(f"gmail_{self.account.account_id}_token")

    def _store_tokens(self, access_token: str, refresh_token: str | None = None) -> None:
        store_credential(f"gmail_{self.account.account_id}_token", access_token)
        if refresh_token:
            store_credential(f"gmail_{self.account.account_id}_refresh", refresh_token)

    def authenticate(self, auth_payload: dict[str, Any]) -> bool:
        """Store OAuth tokens and verify profile."""
        access_token = auth_payload.get("access_token", "")
        refresh_token = auth_payload.get("refresh_token")
        if not access_token:
            return False

        self._store_tokens(access_token, refresh_token)
        self.account.auth_status = AuthStatus.CONNECTED
        self.account.error_message = ""
        return True

    def _execute_request(self, method: str, url: str, **kwargs) -> Any:
        """Execute HTTP request using injected client or requests with automatic 401 retry."""
        token = self._get_access_token()
        headers = dict(kwargs.pop("headers", {}))
        if token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {token}"
        kwargs["headers"] = headers

        if "timeout" not in kwargs:
            kwargs["timeout"] = 15.0

        if self.http_client:
            func = getattr(self.http_client, method.lower())
            return func(url, **kwargs)

        # Live network request
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 401:
            logger.info("Encountered 401 for %s; attempting token refresh...", self.account.account_id)
            if self.refresh_token_if_needed():
                new_token = self._get_access_token()
                if new_token:
                    headers["Authorization"] = f"Bearer {new_token}"
                    kwargs["headers"] = headers
                    resp = requests.request(method, url, **kwargs)
        return resp

    def refresh_token_if_needed(self) -> bool:
        """Execute OAuth refresh token exchange with client_id & client_secret under exclusive lock."""
        with oauth_refresh_lock(self.account.account_id):
            refresh_token = get_credential(f"gmail_{self.account.account_id}_refresh")
            if not refresh_token:
                logger.warning("No refresh token available for %s", self.account.account_id)
                self.account.auth_status = AuthStatus.EXPIRED
                return False

            client_id = get_google_client_id() or self.account.metadata.get("oauth_client_id", "")
            client_secret = get_google_client_secret()
            if not client_id or not client_secret:
                logger.warning("Google OAuth client credentials missing for token refresh")
                self.account.auth_status = AuthStatus.EXPIRED
                return False

            payload = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }

            try:
                if self.http_client and hasattr(self.http_client, "post"):
                    resp = self.http_client.post("https://oauth2.googleapis.com/token", data=payload)
                    data = safe_parse_json(resp)
                else:
                    resp = requests.post("https://oauth2.googleapis.com/token", data=payload, timeout=15.0)
                    data = resp.json() if resp.status_code == 200 else {}

                new_access_token = data.get("access_token")
                if new_access_token:
                    new_refresh = data.get("refresh_token", refresh_token)
                    self._store_tokens(new_access_token, new_refresh)
                    self.account.auth_status = AuthStatus.CONNECTED
                    self.account.error_message = ""
                    logger.info("Successfully refreshed access token for %s", self.account.account_id)
                    return True

                err_msg = data.get("error_description", data.get("error", "Refresh token rejected"))
                logger.warning("Token refresh rejected for %s: %s", self.account.account_id, err_msg)
                self.account.auth_status = AuthStatus.EXPIRED
                self.account.error_message = str(err_msg)
                return False
            except Exception as ex:  # noqa: BLE001
                logger.warning("Network or unexpected error refreshing token for %s: %s", self.account.account_id, ex)
                self.account.auth_status = AuthStatus.EXPIRED
                self.account.error_message = str(ex)
                return False

    def check_health(self) -> dict[str, Any]:
        """Perform live diagnostic health check against official Gmail API v1."""
        token = self._get_access_token()
        if not token:
            return {
                "healthy": False,
                "provider": "Gmail",
                "error": "No access token configured in CredentialVault",
                "auth_status": self.account.auth_status.value,
            }

        try:
            resp = self._execute_request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
            status_code = getattr(resp, "status_code", 200)
            if status_code == 200:
                profile = safe_parse_json(resp)
                email_addr = profile.get("emailAddress", self.account.email_or_id)
                self.account.auth_status = AuthStatus.CONNECTED
                self.account.error_message = ""
                sync_str = (
                    datetime.datetime.fromtimestamp(self.account.last_synced_at).strftime("%Y-%m-%d %H:%M:%S")
                    if self.account.last_synced_at > 0
                    else "Never"
                )
                return {
                    "healthy": True,
                    "provider": "Gmail",
                    "account_id": self.account.account_id,
                    "email": email_addr,
                    "messages_total": profile.get("messagesTotal", 0),
                    "threads_total": profile.get("threadsTotal", 0),
                    "history_id": profile.get("historyId", ""),
                    "auth_status": "CONNECTED",
                    "scopes": self.account.scopes or ["https://www.googleapis.com/auth/gmail.readonly"],
                    "last_synced_at": sync_str,
                    "last_synced_timestamp": self.account.last_synced_at,
                    "last_verified": time.time(),
                }
            if status_code == 401:
                self.account.auth_status = AuthStatus.EXPIRED
                return {
                    "healthy": False,
                    "provider": "Gmail",
                    "error": "OAuth access token expired and refresh failed",
                    "auth_status": "EXPIRED",
                }
            if status_code == 403:
                return {
                    "healthy": False,
                    "provider": "Gmail",
                    "error": "Gmail API access forbidden or rate limit exceeded",
                    "auth_status": self.account.auth_status.value,
                }
            return {
                "healthy": False,
                "provider": "Gmail",
                "error": f"Gmail API returned HTTP {status_code}",
                "auth_status": self.account.auth_status.value,
            }
        except Exception as ex:  # noqa: BLE001
            return {
                "healthy": False,
                "provider": "Gmail",
                "error": f"Gmail service unreachable: {ex}",
                "auth_status": self.account.auth_status.value,
            }

    def _parse_raw_gmail_message(self, raw_item: dict[str, Any], defang: bool = True) -> Message:
        """Parse raw Gmail message resource into normalized Message dataclass with injection defense."""
        msg_id = raw_item.get("id", "")
        thread_id = raw_item.get("threadId", msg_id)
        snippet = raw_item.get("snippet", "")
        labels = raw_item.get("labelIds", [])

        # Extract headers
        headers_list = raw_item.get("payload", {}).get("headers", [])
        headers = {h.get("name", "").lower(): h.get("value", "") for h in headers_list if "name" in h}

        subject = headers.get("subject", "No Subject")
        sender = headers.get("from", "Unknown Sender")
        to_header = headers.get("to", "")
        recipients = [r.strip() for r in to_header.split(",") if r.strip()]

        # Parse date
        date_header = headers.get("date")
        timestamp = time.time()
        if date_header:
            try:
                parsed_tuple = email.utils.parsedate_tz(date_header)
                if parsed_tuple:
                    timestamp = email.utils.mktime_tz(parsed_tuple)
            except (ValueError, TypeError, OverflowError) as e:
                logger.debug("Failed parsing email date header '%s': %s", date_header, e)

        is_unread = "UNREAD" in labels
        is_important = "IMPORTANT" in labels or "STARRED" in labels

        # Quarantined body snippet using core.outcome.untrusted
        safe_snippet = untrusted("gmail", snippet[:500]) if (defang and snippet) else snippet[:500]

        return Message(
            message_id=msg_id,
            provider=ProviderType.GMAIL,
            account_id=self.account.account_id,
            conversation_id=thread_id,
            sender=sender,
            recipients=recipients,
            subject=subject,
            body_snippet=safe_snippet,
            timestamp=timestamp,
            labels=labels,
            is_read=not is_unread,
            is_important=is_important,
            raw_reference={"historyId": raw_item.get("historyId", "")},
        )

    def initial_sync(self, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Execute initial synchronization fetching recent messages and latest historyId."""
        token = self._get_access_token()
        if not token:
            raise PermissionError("Gmail account not authenticated")

        try:
            resp = self._execute_request(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"maxResults": max_items},
            )
            data = safe_parse_json(resp)
            messages_raw = data.get("messages", [])
            parsed_messages: list[Message] = []
            for m in messages_raw:
                if "payload" in m:
                    # Injected test stub with full payload already present (preserves unit tests)
                    parsed_messages.append(self._parse_raw_gmail_message(m))
                else:
                    # Live Gmail API stub {"id": "...", "threadId": "..."} - fetch full payload
                    msg_id = m.get("id")
                    if msg_id:
                        try:
                            msg_resp = self._execute_request(
                                "GET",
                                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                                params={"format": "full"},
                            )
                            if getattr(msg_resp, "status_code", 200) == 200:
                                raw_detail = safe_parse_json(msg_resp)
                                parsed_messages.append(self._parse_raw_gmail_message(raw_detail))
                        except Exception as m_ex:  # noqa: BLE001
                            logger.warning("Failed fetching detail for Gmail msg %s: %s", msg_id, m_ex)

            new_cursor = str(data.get("historyId", "1000000"))
            return parsed_messages, [], new_cursor
        except Exception as ex:  # noqa: BLE001
            logger.warning("Gmail initial_sync error: %s", ex)
            return [], [], "1000000"

    def incremental_sync(self, cursor: str, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Execute incremental sync from historyId."""
        token = self._get_access_token()
        if not token:
            raise PermissionError("Gmail account not authenticated")

        try:
            resp = self._execute_request(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/history",
                params={"startHistoryId": cursor, "maxResults": max_items},
            )
            # Handle HTTP 404/410 cursor expired
            if getattr(resp, "status_code", 200) in (404, 410):
                logger.warning("Gmail historyId %s expired; falling back to full initial sync.", cursor)
                return self.initial_sync(max_items=max_items)

            data = safe_parse_json(resp)
            new_cursor = str(data.get("historyId", cursor))
            history_records = data.get("history", [])
            messages: list[Message] = []
            for h in history_records:
                for added in h.get("messagesAdded", []):
                    m = added.get("message")
                    if m:
                        if "payload" in m:
                            messages.append(self._parse_raw_gmail_message(m))
                        else:
                            msg_id = m.get("id")
                            if msg_id:
                                try:
                                    msg_resp = self._execute_request(
                                        "GET",
                                        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                                        params={"format": "full"},
                                    )
                                    if getattr(msg_resp, "status_code", 200) == 200:
                                        messages.append(self._parse_raw_gmail_message(safe_parse_json(msg_resp)))
                                except Exception as m_ex:  # noqa: BLE001
                                    logger.warning("Failed fetching detail for incremental msg %s: %s", msg_id, m_ex)
            return messages, [], new_cursor
        except Exception as ex:  # noqa: BLE001
            logger.warning("Gmail incremental_sync error: %s", ex)
            return [], [], cursor

    def fetch_recent_messages(
        self,
        limit: int = 5,
        query: str | None = None,
        is_unread: bool | None = None,
        is_important: bool | None = None,
    ) -> list[Message]:
        """Fetch recent messages directly from Gmail API v1 with search and filter parameters."""
        token = self._get_access_token()
        if not token:
            raise PermissionError("Gmail account not authenticated")

        limit = max(1, min(int(limit), 25))
        q_parts = []
        if query:
            q_parts.append(query)
        if is_unread:
            q_parts.append("is:unread")
        if is_important:
            q_parts.append("is:important")
        q = " ".join(q_parts) if q_parts else None

        params: dict[str, Any] = {"maxResults": limit}
        if q:
            params["q"] = q

        resp = self._execute_request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params=params,
        )
        data = safe_parse_json(resp)
        messages_raw = data.get("messages", [])
        results: list[Message] = []
        for m in messages_raw:
            if "payload" in m:
                results.append(self._parse_raw_gmail_message(m))
            else:
                msg_id = m.get("id")
                if msg_id:
                    try:
                        msg_resp = self._execute_request(
                            "GET",
                            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                            params={"format": "full"},
                        )
                        if getattr(msg_resp, "status_code", 200) == 200:
                            raw_detail = safe_parse_json(msg_resp)
                            results.append(self._parse_raw_gmail_message(raw_detail))
                    except Exception as m_ex:  # noqa: BLE001
                        logger.warning("Failed fetching detail for msg %s: %s", msg_id, m_ex)
        return results

    def get_message_detail(self, message_id: str) -> Message | None:
        """Fetch single full message by ID with strict input validation."""
        if not re.match(r"^[a-zA-Z0-9_\-]+$", message_id):
            raise ValueError(f"Invalid message ID format: {message_id}")
        resp = self._execute_request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            params={"format": "full"},
        )
        if getattr(resp, "status_code", 200) == 200:
            raw_detail = safe_parse_json(resp)
            return self._parse_raw_gmail_message(raw_detail)
        return None

    def summarize_email(self, message_id: str) -> dict[str, Any] | None:
        """Fetch and generate a structured summary of an email with injection defanging."""
        msg = self.get_message_detail(message_id)
        if not msg:
            return None

        snippet = msg.body_snippet or ""
        lines = [line.strip() for line in snippet.split("\n") if line.strip()]
        key_points = lines[:3] or ["No extended body content available."]

        return {
            "message_id": msg.message_id,
            "sender": msg.sender,
            "subject": msg.subject,
            "date": datetime.datetime.fromtimestamp(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
            "is_read": msg.is_read,
            "is_important": msg.is_important,
            "summary": f"Email from {msg.sender} regarding '{msg.subject}'.",
            "key_points": key_points,
            "quarantined_content": snippet,
        }

    def revoke(self) -> bool:
        """Revoke tokens remotely if possible, purge credentials, and update AuthStatus honestly."""
        remote_ok = False
        token = self._get_access_token()

        if token:
            try:
                if self.http_client and hasattr(self.http_client, "post"):
                    resp = self.http_client.post(
                        "https://oauth2.googleapis.com/revoke",
                        params={"token": token},
                    )
                else:
                    resp = requests.post(
                        "https://oauth2.googleapis.com/revoke",
                        params={"token": token},
                        timeout=15.0,
                    )
                if getattr(resp, "status_code", 0) == 200:
                    remote_ok = True
            except Exception as ex:  # noqa: BLE001
                logger.warning("Remote revocation network error for %s: %s", self.account.account_id, ex)

        if remote_ok:
            self.account.auth_status = AuthStatus.REVOKED_REMOTE_VERIFIED
        else:
            self.account.auth_status = AuthStatus.DISCONNECTED_LOCAL_ONLY

        store_credential(f"gmail_{self.account.account_id}_token", "")
        store_credential(f"gmail_{self.account.account_id}_refresh", "")
        return True
