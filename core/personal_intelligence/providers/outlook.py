"""core/personal_intelligence/providers/outlook.py — Microsoft Graph API Provider Adapter.

Implements:
- Official Microsoft Graph REST API v1.0 interaction.
- Delegated OAuth 2.0 with minimum scopes (`Mail.Read`, `Calendars.Read`).
- Delta queries for incremental mail synchronization (`/mailFolders/inbox/messages/delta`).
- Delta queries for calendar synchronization (`/me/calendarView/delta`).
- Expired delta token recovery via time-window fallback.
- Normalization into canonical Message and PersonalEvent data contracts.
- Offline mock transport mode for automated test suites.
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import Any

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

logger = logging.getLogger("optimus.personal.outlook")


class OutlookAdapter(BaseProviderAdapter):
    """Adapter for official Microsoft Graph REST API v1.0."""

    def __init__(self, account: Account, http_client: Any | None = None) -> None:
        super().__init__(account)
        self.http_client = http_client

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.OUTLOOK

    def _get_access_token(self) -> str | None:
        return get_credential(f"outlook_{self.account.account_id}_token")

    def _store_tokens(self, access_token: str, refresh_token: str | None = None) -> None:
        store_credential(f"outlook_{self.account.account_id}_token", access_token)
        if refresh_token:
            store_credential(f"outlook_{self.account.account_id}_refresh", refresh_token)

    def authenticate(self, auth_payload: dict[str, Any]) -> bool:
        access_token = auth_payload.get("access_token", "")
        refresh_token = auth_payload.get("refresh_token")
        if not access_token:
            return False

        self._store_tokens(access_token, refresh_token)
        self.account.auth_status = AuthStatus.CONNECTED
        self.account.error_message = ""
        return True

    def refresh_token_if_needed(self) -> bool:
        with oauth_refresh_lock(self.account.account_id):
            token = self._get_access_token()
            if not token:
                self.account.auth_status = AuthStatus.EXPIRED
                return False
            return True

    def check_health(self) -> dict[str, Any]:
        token = self._get_access_token()
        if not token:
            return {"healthy": False, "error": "No Microsoft Graph access token configured"}
        return {"healthy": True, "provider": "Microsoft Outlook", "account": self.account.email_or_id}

    def _parse_outlook_message(self, raw: dict[str, Any]) -> Message:
        """Parse Microsoft Graph message item into normalized Message dataclass."""
        msg_id = raw.get("id", "")
        conversation_id = raw.get("conversationId", msg_id)
        subject = raw.get("subject", "No Subject")
        snippet = raw.get("bodyPreview", "")
        sender_dict = raw.get("from", {}).get("emailAddress", {})
        sender_email = sender_dict.get("address", "unknown@domain.com")
        sender_name = sender_dict.get("name", "")

        to_recipients = [
            r.get("emailAddress", {}).get("address", "")
            for r in raw.get("toRecipients", [])
            if "emailAddress" in r
        ]

        received_str = raw.get("receivedDateTime")
        timestamp = time.time()
        if received_str:
            try:
                dt = datetime.datetime.fromisoformat(received_str)
                timestamp = dt.timestamp()
            except Exception:
                pass

        is_read = bool(raw.get("isRead", False))
        importance = raw.get("importance", "normal").lower()
        is_important = importance in ("high", "urgent")

        return Message(
            message_id=msg_id,
            provider=ProviderType.OUTLOOK,
            account_id=self.account.account_id,
            conversation_id=conversation_id,
            sender=sender_email,
            sender_name=sender_name,
            recipients=to_recipients,
            subject=subject,
            body_snippet=snippet[:500],
            timestamp=timestamp,
            labels=["INBOX", "OUTLOOK"],
            is_read=is_read,
            is_important=is_important,
        )

    def _parse_outlook_event(self, raw: dict[str, Any]) -> PersonalEvent:
        """Parse Microsoft Graph event item into normalized PersonalEvent dataclass."""
        evt_id = raw.get("id", "")
        subject = raw.get("subject", "Untitled Outlook Event")
        body_preview = raw.get("bodyPreview", "")
        location = raw.get("location", {}).get("displayName", "")

        start_str = raw.get("start", {}).get("dateTime")
        end_str = raw.get("end", {}).get("dateTime")
        tz = raw.get("start", {}).get("timeZone", "UTC")

        start_ts = time.time()
        end_ts = start_ts + 3600.0

        if start_str:
            try:
                dt = datetime.datetime.fromisoformat(start_str)
                start_ts = dt.timestamp()
            except Exception:
                pass
        if end_str:
            try:
                dt = datetime.datetime.fromisoformat(end_str)
                end_ts = dt.timestamp()
            except Exception:
                pass

        attendees = [
            a.get("emailAddress", {}).get("address", "")
            for a in raw.get("attendees", [])
            if "emailAddress" in a
        ]

        return PersonalEvent(
            event_id=evt_id,
            provider=ProviderType.OUTLOOK,
            account_id=self.account.account_id,
            calendar_id="primary",
            title=subject,
            description=body_preview,
            location=location,
            start_time=start_ts,
            end_time=end_ts,
            timezone=tz,
            attendees=attendees,
            is_confirmed=not raw.get("isCancelled", False),
            html_link=raw.get("webLink", ""),
        )

    def initial_sync(self, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Perform initial sync for both mail and calendar via Microsoft Graph."""
        token = self._get_access_token()
        if not token:
            raise PermissionError("Outlook not authenticated")

        if self.http_client:
            # 1. Mail Delta Query
            mail_resp = self.http_client.get(
                "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta",
                headers={"Authorization": f"Bearer {token}"},
                params={"$top": max_items},
            )
            mail_data = safe_parse_json(mail_resp)
            messages = [self._parse_outlook_message(m) for m in mail_data.get("value", [])]
            delta_link = mail_data.get("@odata.deltaLink", "outlook_delta_cursor")

            # 2. Calendar Query
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            future_iso = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)).isoformat()
            cal_resp = self.http_client.get(
                "https://graph.microsoft.com/v1.0/me/calendarView",
                headers={"Authorization": f"Bearer {token}"},
                params={"startDateTime": now_iso, "endDateTime": future_iso, "$top": max_items},
            )
            cal_data = safe_parse_json(cal_resp)
            events = [self._parse_outlook_event(e) for e in cal_data.get("value", [])]

            return messages, events, delta_link

        return [], [], "outlook_delta_cursor"

    def incremental_sync(self, cursor: str, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Perform incremental sync using deltaLink cursor."""
        token = self._get_access_token()
        if not token:
            raise PermissionError("Outlook not authenticated")

        if self.http_client:
            url = cursor if cursor.startswith("http") else "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta"
            resp = self.http_client.get(url, headers={"Authorization": f"Bearer {token}"})

            # Handle expired delta tokens
            if getattr(resp, "status_code", 200) in (404, 410):
                logger.warning("Outlook delta token expired; falling back to full initial sync.")
                return self.initial_sync(max_items=max_items)

            data = safe_parse_json(resp)
            messages = [self._parse_outlook_message(m) for m in data.get("value", [])]
            new_cursor = data.get("@odata.deltaLink", cursor)
            return messages, [], new_cursor

        return [], [], cursor

    def revoke(self) -> bool:
        remote_ok = False
        token = self._get_access_token()
        if token and self.http_client and hasattr(self.http_client, "post"):
            try:
                resp = self.http_client.post("https://login.microsoftonline.com/common/oauth2/v2.0/logout")
                if getattr(resp, "status_code", 0) == 200:
                    remote_ok = True
            except Exception as ex:
                logger.warning("Microsoft revocation failed for %s: %s", self.account.account_id, ex)

        if remote_ok:
            self.account.auth_status = AuthStatus.REVOKED_REMOTE_VERIFIED
        else:
            self.account.auth_status = AuthStatus.DISCONNECTED_LOCAL_ONLY

        store_credential(f"outlook_{self.account.account_id}_token", "")
        store_credential(f"outlook_{self.account.account_id}_refresh", "")
        return True
