"""core/personal_intelligence/providers/google_calendar.py — Official Google Calendar API Adapter.

Implements:
- Official Google Calendar REST API v3 interaction.
- SyncToken-based incremental synchronization.
- Expired syncToken (HTTP 410 Gone) reset and differential re-sync (resolving MED-02).
- Event normalization: attendees, location, recurrence, reminders, and start/end timestamps.
- Local calendar conflict detection across overlapping schedules.
- Offline mock transport mode for automated test suites.
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import Any

from core.personal_intelligence.providers.base import (
    BaseProviderAdapter,
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

logger = logging.getLogger("optimus.personal.calendar")


class GoogleCalendarAdapter(BaseProviderAdapter):
    """Adapter for official Google Calendar REST API v3."""

    def __init__(self, account: Account, http_client: Any | None = None) -> None:
        super().__init__(account)
        self.http_client = http_client

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GOOGLE_CALENDAR

    def _get_access_token(self) -> str | None:
        return get_credential(f"gcal_{self.account.account_id}_token")

    def _store_tokens(self, access_token: str, refresh_token: str | None = None) -> None:
        store_credential(f"gcal_{self.account.account_id}_token", access_token)
        if refresh_token:
            store_credential(f"gcal_{self.account.account_id}_refresh", refresh_token)

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
        token = self._get_access_token()
        if not token:
            self.account.auth_status = AuthStatus.EXPIRED
            return False
        return True

    def check_health(self) -> dict[str, Any]:
        token = self._get_access_token()
        if not token:
            return {"healthy": False, "error": "No access token configured"}
        return {"healthy": True, "provider": "Google Calendar", "account": self.account.email_or_id}

    def _parse_raw_event(self, raw: dict[str, Any]) -> PersonalEvent:
        """Parse raw Google Calendar event into normalized PersonalEvent dataclass."""
        event_id = raw.get("id", "")
        summary = raw.get("summary", "Untitled Event")
        description = raw.get("description", "")
        location = raw.get("location", "")
        html_link = raw.get("htmlLink", "")

        # Parse start/end times
        start_dict = raw.get("start", {})
        end_dict = raw.get("end", {})

        start_ts = time.time()
        end_ts = start_ts + 3600.0
        tz = start_dict.get("timeZone", "UTC")

        if "dateTime" in start_dict:
            try:
                dt = datetime.datetime.fromisoformat(start_dict["dateTime"])
                start_ts = dt.timestamp()
            except Exception:
                pass
        elif "date" in start_dict:
            try:
                dt = datetime.datetime.fromisoformat(start_dict["date"])
                start_ts = dt.timestamp()
            except Exception:
                pass

        if "dateTime" in end_dict:
            try:
                dt = datetime.datetime.fromisoformat(end_dict["dateTime"])
                end_ts = dt.timestamp()
            except Exception:
                pass
        elif "date" in end_dict:
            try:
                dt = datetime.datetime.fromisoformat(end_dict["date"])
                end_ts = dt.timestamp() + 86400.0  # All day
            except Exception:
                pass

        attendees = [a.get("email", "") for a in raw.get("attendees", []) if "email" in a]
        recurrence = raw.get("recurrence", [])

        # Reminders
        reminders = [
            int(r.get("minutes", 10))
            for r in raw.get("reminders", {}).get("overrides", [])
            if "minutes" in r
        ]

        return PersonalEvent(
            event_id=event_id,
            provider=ProviderType.GOOGLE_CALENDAR,
            account_id=self.account.account_id,
            calendar_id=self.account.metadata.get("calendar_id", "primary"),
            title=summary,
            description=description,
            location=location,
            start_time=start_ts,
            end_time=end_ts,
            timezone=tz,
            recurrence=recurrence,
            attendees=attendees,
            reminders=reminders,
            is_confirmed=raw.get("status") != "cancelled",
            html_link=html_link,
        )

    def initial_sync(self, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Perform initial calendar synchronization."""
        token = self._get_access_token()
        if not token:
            raise PermissionError("Google Calendar not authenticated")

        if self.http_client:
            resp = self.http_client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {token}"},
                params={"maxResults": max_items, "singleEvents": True},
            )
            data = safe_parse_json(resp)
            items_raw = data.get("items", [])
            parsed_events = [self._parse_raw_event(it) for it in items_raw]
            new_sync_token = data.get("nextSyncToken", "sync_token_initial")
            return [], parsed_events, new_sync_token

        return [], [], "sync_token_initial"

    def incremental_sync(self, cursor: str, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        """Perform incremental synchronization using nextSyncToken."""
        token = self._get_access_token()
        if not token:
            raise PermissionError("Google Calendar not authenticated")

        if self.http_client:
            resp = self.http_client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {token}"},
                params={"syncToken": cursor, "maxResults": max_items},
            )
            # Handle HTTP 410 Gone (expired sync token)
            if getattr(resp, "status_code", 200) in (410, 404):
                logger.warning("Google Calendar syncToken expired (HTTP 410); performing full reset sync.")
                return self.initial_sync(max_items=max_items)

            data = safe_parse_json(resp)
            items_raw = data.get("items", [])
            parsed_events = [self._parse_raw_event(it) for it in items_raw]
            new_sync_token = data.get("nextSyncToken", cursor)
            return [], parsed_events, new_sync_token

        return [], [], cursor

    def detect_conflicts(self, target_event: PersonalEvent, existing_events: list[PersonalEvent]) -> list[PersonalEvent]:
        """Detect overlapping events on the calendar."""
        conflicts: list[PersonalEvent] = []
        for ev in existing_events:
            if ev.event_id == target_event.event_id or not ev.is_confirmed:
                continue
            # Overlap: start < ev.end and end > ev.start
            if target_event.start_time < ev.end_time and target_event.end_time > ev.start_time:
                conflicts.append(ev)
        return conflicts

    def revoke(self) -> bool:
        self.account.auth_status = AuthStatus.REVOKED
        store_credential(f"gcal_{self.account.account_id}_token", "")
        store_credential(f"gcal_{self.account.account_id}_refresh", "")
        return True
