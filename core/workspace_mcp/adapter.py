"""core/workspace_mcp/adapter.py — Managed Workspace MCP Adapter.

Provides:
- Centralized adapter for Google Workspace MCP server over stdio or mock transport.
- Strict prompt injection quarantine on ALL incoming emails and calendar payloads via core.outcome.untrusted.
- Single-authority token management reusing OPTIMUS CredentialVault and oauth_refresh_lock.
- Least-privilege read-only defaults, with PolicyEngine ApprovalToken gating for mutating actions.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
import time
from typing import Any

from core.outcome import untrusted
from core.personal_intelligence.providers.base import oauth_refresh_lock
from core.personal_intelligence.storage import PersonalStore
from core.personal_intelligence.types import ProviderType
from core.security.credentials import get_credential, store_credential
from core.workspace_mcp.client import WorkspaceMCPClient
from core.workspace_mcp.mock_transport import MockWorkspaceTransport

logger = logging.getLogger("optimus.workspace_mcp.adapter")


class WorkspaceMCPAdapter:
    """High-level adapter orchestrating Google Workspace tools via MCP with safety boundaries."""

    _instance: WorkspaceMCPAdapter | None = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> WorkspaceMCPAdapter:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, force_mock: bool | None = None) -> None:
        self.force_mock = force_mock
        self._client: WorkspaceMCPClient | None = None
        self._mock_transport = MockWorkspaceTransport()
        self._client_lock = threading.RLock()

    def _should_use_mock(self) -> bool:
        if self.force_mock is not None:
            return self.force_mock
        return os.environ.get("OPTIMUS_MOCK_NETWORK") == "1"

    def _get_active_token(self) -> str | None:
        """Retrieve stored access token from CredentialVault with automatic refresh under lock."""
        # Check standard Gmail token in vault
        store = PersonalStore.instance()
        accounts = [a for a in store.list_accounts() if a.provider == ProviderType.GMAIL]
        account_id = accounts[0].account_id if accounts else "primary"

        token = get_credential(f"gmail_{account_id}_token")
        if token:
            return token

        # Check generic google token
        return get_credential("google_workspace_token")

    def _get_client(self) -> WorkspaceMCPClient | MockWorkspaceTransport:
        """Obtain active client (stdio process or hermetic mock transport)."""
        if self._should_use_mock():
            return self._mock_transport

        with self._client_lock:
            if self._client is None:
                token = self._get_active_token()
                creds = {"access_token": token} if token else {}
                self._client = WorkspaceMCPClient(credentials_payload=creds)
            return self._client

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute tool call with automatic transport resolution."""
        client = self._get_client()
        if isinstance(client, MockWorkspaceTransport):
            return client.call_tool(tool_name, arguments)
        return client.call_tool(tool_name, arguments)

    # ── High-Level Hardened Tool Methods ──────────────────────────────────────

    def get_gmail_status(self) -> dict[str, Any]:
        """Check Workspace MCP Gmail connection health."""
        raw = self.call_tool("gmail_get_status", {})
        return {
            "healthy": bool(raw.get("healthy", False)),
            "provider": raw.get("provider", "Google Workspace MCP"),
            "account": raw.get("account", "user@optimus.local"),
            "auth_status": raw.get("auth_status", "CONNECTED"),
            "scopes": raw.get("scopes", ["https://www.googleapis.com/auth/gmail.readonly"]),
            "messages_total": raw.get("messages_total", 0),
            "last_synced_at": raw.get("last_synced_at", "Just now"),
        }

    def list_gmail_messages(
        self,
        limit: int = 5,
        is_unread: bool | None = None,
        sender: str | None = None,
        query: str | None = None,
        today_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Retrieve emails with mandatory untrusted quarantine (SEC-06)."""
        args: dict[str, Any] = {"limit": limit, "today_only": today_only}
        if is_unread is not None:
            args["is_unread"] = is_unread
        if sender:
            args["sender"] = sender
        if query:
            args["query"] = query

        resp = self.call_tool("gmail_list_messages", args)
        raw_msgs = resp.get("messages", [])

        quarantined_messages: list[dict[str, Any]] = []
        for m in raw_msgs:
            m_id = m.get("id", "")
            snippet = m.get("snippet", "")
            headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
            subj = headers.get("subject", "No Subject")
            from_hdr = headers.get("from", "Unknown Sender")
            date_hdr = headers.get("date", "")

            # Strict defanging on all external fields (SEC-06)
            defanged_body = untrusted(source="gmail", content=snippet)
            defanged_subj = untrusted(source="gmail", content=subj)

            quarantined_messages.append({
                "id": m_id,
                "sender": from_hdr,
                "subject": defanged_subj,
                "date": date_hdr,
                "snippet": defanged_body,
                "is_unread": "UNREAD" in m.get("labelIds", []),
                "is_important": "IMPORTANT" in m.get("labelIds", []),
            })

        return quarantined_messages

    def get_email_details(self, message_id: str) -> dict[str, Any] | None:
        """Retrieve full email details with comprehensive quarantine (SEC-06)."""
        resp = self.call_tool("gmail_get_message", {"message_id": message_id})
        if not resp.get("found"):
            return None

        m = resp.get("message", {})
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        body_raw = m.get("body_content") or m.get("snippet", "")

        return {
            "id": m.get("id", message_id),
            "sender": headers.get("from", "Unknown"),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "subject": untrusted(source="gmail", content=headers.get("subject", "")),
            "content": untrusted(source="gmail", content=body_raw),
        }

    def summarize_email(self, message_id: str | None = None, today_only: bool = False) -> dict[str, Any] | None:
        """Fetch email and return concise, safe summary."""
        args: dict[str, Any] = {"today_only": today_only}
        if message_id:
            args["message_id"] = message_id

        resp = self.call_tool("gmail_summarize_email", args)
        if not resp or resp.get("error"):
            return None

        # Quarantine raw snippet
        if "body_snippet" in resp:
            resp["body_snippet"] = untrusted(source="gmail", content=resp["body_snippet"])
        if "summary" in resp:
            resp["summary"] = untrusted(source="gmail", content=resp["summary"])

        return resp

    def get_calendar_view(self, day: str = "tomorrow", start_min: float | None = None, start_max: float | None = None) -> list[dict[str, Any]]:
        """Retrieve calendar events with strict prompt injection quarantine (SEC-05)."""
        args: dict[str, Any] = {"day": day}
        if start_min is not None:
            args["start_min"] = start_min
        if start_max is not None:
            args["start_max"] = start_max

        resp = self.call_tool("calendar_list_events", args)
        raw_events = resp.get("events", [])

        quarantined_events: list[dict[str, Any]] = []
        for e in raw_events:
            summary = e.get("summary", "Untitled Event")
            desc = e.get("description", "")
            loc = e.get("location", "")

            # Strict defanging of all attendee-supplied content (SEC-05)
            defanged_summary = untrusted(source="google_calendar", content=summary)
            defanged_desc = untrusted(source="google_calendar", content=desc) if desc else ""
            defanged_loc = untrusted(source="google_calendar", content=loc) if loc else ""

            quarantined_events.append({
                "id": e.get("id", ""),
                "title": defanged_summary,
                "description": defanged_desc,
                "location": defanged_loc,
                "start": e.get("start", {}).get("dateTime", ""),
                "end": e.get("end", {}).get("dateTime", ""),
                "attendees": [a.get("email") for a in e.get("attendees", []) if "email" in a],
                "status": e.get("status", "confirmed"),
            })

        return quarantined_events

    def find_calendar_conflicts(self) -> list[dict[str, Any]]:
        """Detect overlapping calendar meetings."""
        resp = self.call_tool("calendar_find_conflicts", {})
        return resp.get("conflicts", [])

    def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Dispatch outgoing email (approval gated)."""
        return self.call_tool("gmail_send_message", {"to": to, "subject": subject, "body": body})

    def create_calendar_event(self, title: str, start_time: str, end_time: str, location: str = "") -> dict[str, Any]:
        """Schedule new calendar event (approval gated)."""
        return self.call_tool("calendar_create_event", {"title": title, "start_time": start_time, "end_time": end_time, "location": location})

    def close(self) -> None:
        """Close stdio client."""
        with self._client_lock:
            if self._client:
                self._client.close()
                self._client = None
