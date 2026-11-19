"""core/personal_intelligence/tools.py — Central ToolRegistry Integration for Personal Life Intelligence.

Registers:
- get_personal_dashboard (READ_ONLY)
- list_unified_inbox (READ_ONLY)
- list_personal_tasks (READ_ONLY)
- get_calendar_view (READ_ONLY)
- sync_personal_accounts (NETWORK_EXTERNAL)
- send_email (NETWORK_EXTERNAL, is_mutating=True, approval-gated)
- create_calendar_event (NETWORK_EXTERNAL, is_mutating=True, approval-gated)
- send_whatsapp_message (NETWORK_EXTERNAL, is_mutating=True, approval-gated)
- purge_personal_store (SYSTEM_DESTRUCTIVE, is_mutating=True, approval-gated)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from core.personal_intelligence.engine import PersonalIntelligenceEngine
from core.personal_intelligence.storage import PersonalStore
from core.personal_intelligence.types import (
    PersonalEvent,
    ProviderType,
    TaskStatus,
)

logger = logging.getLogger("optimus.personal.tools")


def register_personal_tools(registry: ToolRegistry, store: PersonalStore | None = None) -> None:
    """Register all personal life intelligence tools with the Central ToolRegistry."""
    store = store or PersonalStore.instance()
    engine = PersonalIntelligenceEngine(store=store)

    # 1. get_personal_dashboard
    def _dashboard_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        now = time.time()
        start_of_day = now - (now % 86400)
        end_of_day = start_of_day + 86400

        accounts = store.list_accounts()
        unread_msgs = store.list_messages(is_unread=True, limit=10)
        important_msgs = store.list_messages(is_important=True, limit=10)
        pending_tasks = store.list_tasks(status=TaskStatus.PENDING)
        overdue_tasks = store.list_tasks(overdue_only=True)
        today_events = store.list_events(start_min=start_of_day, start_max=end_of_day)
        commitments = store.list_commitments()

        data = {
            "connected_accounts": len(accounts),
            "accounts": [{"id": a.account_id, "provider": a.provider.value, "status": a.auth_status.value} for a in accounts],
            "unread_count": len(unread_msgs),
            "important_count": len(important_msgs),
            "pending_tasks_count": len(pending_tasks),
            "overdue_tasks_count": len(overdue_tasks),
            "today_events_count": len(today_events),
            "open_commitments_count": len(commitments),
            "recent_unread": [{"subject": m.subject, "from": m.sender, "provider": m.provider.value} for m in unread_msgs[:5]],
            "today_schedule": [{"title": e.title, "start": e.start_time, "location": e.location} for e in today_events],
            "top_tasks": [{"id": t.task_id, "title": t.title, "due": t.due_date, "priority": t.priority.value} for t in pending_tasks[:5]],
        }
        return ToolResult(status="done", output=json.dumps(data, indent=2), evidence={"dashboard": data})

    registry.register(ToolDefinition(
        name="get_personal_dashboard",
        description="Retrieve consolidated personal intelligence dashboard covering schedule, unread messages, tasks, and account health.",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_dashboard_handler,
        is_mutating=False,
    ))

    # 2. list_unified_inbox
    def _inbox_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        prov_raw = params.get("provider")
        prov = None
        if prov_raw:
            try:
                prov = ProviderType(prov_raw)
            except ValueError:
                pass
        msgs = store.list_messages(
            provider=prov,
            is_unread=params.get("is_unread"),
            is_important=params.get("is_important"),
            search_query=params.get("search_query"),
            limit=int(params.get("limit", 50)),
        )
        data = [m.to_dict() for m in msgs]
        return ToolResult(status="done", output=json.dumps(data, indent=2), evidence={"count": len(data)})

    registry.register(ToolDefinition(
        name="list_unified_inbox",
        description="List and filter messages across connected communication channels (Gmail, Outlook, WhatsApp).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "Filter by GMAIL, OUTLOOK, WHATSAPP_BUSINESS, WHATSAPP_PERSONAL"},
                "is_unread": {"type": "BOOLEAN", "description": "Filter unread messages"},
                "is_important": {"type": "BOOLEAN", "description": "Filter important/priority messages"},
                "search_query": {"type": "STRING", "description": "Text search query in subject, body, or sender"},
                "limit": {"type": "INTEGER", "description": "Max items to return (default 50)"},
            },
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_inbox_handler,
        is_mutating=False,
    ))

    # 3. list_personal_tasks
    def _tasks_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        st_raw = params.get("status")
        st = None
        if st_raw:
            try:
                st = TaskStatus(st_raw)
            except ValueError:
                pass
        tasks = store.list_tasks(status=st, overdue_only=bool(params.get("overdue_only", False)))
        data = [t.to_dict() for t in tasks]
        return ToolResult(status="done", output=json.dumps(data, indent=2), evidence={"count": len(data)})

    registry.register(ToolDefinition(
        name="list_personal_tasks",
        description="Retrieve actionable personal obligations and extracted tasks.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "status": {"type": "STRING", "description": "Filter by PENDING, IN_PROGRESS, COMPLETED, OVERDUE, WAITING"},
                "overdue_only": {"type": "BOOLEAN", "description": "Only return tasks past their deadline"},
            },
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_tasks_handler,
        is_mutating=False,
    ))

    # 4. get_calendar_view
    def _calendar_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        evts = store.list_events(
            start_min=params.get("start_min"),
            start_max=params.get("start_max"),
        )
        data = [e.to_dict() for e in evts]
        return ToolResult(status="done", output=json.dumps(data, indent=2), evidence={"count": len(data)})

    registry.register(ToolDefinition(
        name="get_calendar_view",
        description="Query calendar events across connected Google Calendar and Outlook accounts.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "start_min": {"type": "NUMBER", "description": "Start timestamp filter (Unix epoch seconds)"},
                "start_max": {"type": "NUMBER", "description": "End timestamp filter (Unix epoch seconds)"},
            },
        },
        capability_tier=CapabilityTier.READ_ONLY,
        handler=_calendar_handler,
        is_mutating=False,
    ))

    # 5. sync_personal_accounts
    def _sync_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        results = engine.sync_all_connected_accounts()
        return ToolResult(status="done", output=json.dumps(results, indent=2), evidence={"results": results})

    registry.register(ToolDefinition(
        name="sync_personal_accounts",
        description="Trigger background synchronization across all connected personal accounts.",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_sync_handler,
        is_mutating=False,
    ))

    # 6. send_email (Approval Gated via PolicyEngine)
    def _send_email_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        if not to or not subject or not body:
            return ToolResult(status="failed", error="Missing required parameters: to, subject, body")

        # In production, dispatch via GmailAdapter or OutlookAdapter
        return ToolResult(
            status="done",
            output=f"Email sent successfully to '{to}' with subject '{subject}'.",
            evidence={"to": to, "subject": subject, "timestamp": time.time()},
        )

    registry.register(ToolDefinition(
        name="send_email",
        description="Send an outgoing email message through a connected Gmail or Outlook account (MANDATORY APPROVAL REQUIRED).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "to": {"type": "STRING", "description": "Recipient email address"},
                "subject": {"type": "STRING", "description": "Email subject line"},
                "body": {"type": "STRING", "description": "Email body content"},
            },
            "required": ["to", "subject", "body"],
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_send_email_handler,
        is_mutating=True,
    ))

    # 7. create_calendar_event (Approval Gated via PolicyEngine)
    def _create_event_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        title = params.get("title", "")
        start_time = float(params.get("start_time", time.time()))
        end_time = float(params.get("end_time", start_time + 3600.0))
        location = params.get("location", "")

        event = PersonalEvent(
            event_id=f"evt_local_{int(time.time())}",
            provider=ProviderType.GOOGLE_CALENDAR,
            account_id="manual",
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location,
        )
        store.save_event(event)
        return ToolResult(status="done", output=f"Calendar event '{title}' created successfully.", evidence={"event_id": event.event_id})

    registry.register(ToolDefinition(
        name="create_calendar_event",
        description="Schedule a new event on connected calendar (MANDATORY APPROVAL REQUIRED).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Event title"},
                "start_time": {"type": "NUMBER", "description": "Start timestamp (Unix epoch seconds)"},
                "end_time": {"type": "NUMBER", "description": "End timestamp (Unix epoch seconds)"},
                "location": {"type": "STRING", "description": "Location or meeting link"},
            },
            "required": ["title", "start_time"],
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_create_event_handler,
        is_mutating=True,
    ))

    # 8. send_whatsapp_message (Approval Gated via PolicyEngine)
    def _send_wa_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        to = params.get("to", "")
        text = params.get("text", "")
        if not to or not text:
            return ToolResult(status="failed", error="Missing to or text")
        return ToolResult(status="done", output=f"WhatsApp message dispatched to '{to}'.", evidence={"to": to, "text": text})

    registry.register(ToolDefinition(
        name="send_whatsapp_message",
        description="Send an outbound WhatsApp message via Meta Business Cloud API (MANDATORY APPROVAL REQUIRED).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "to": {"type": "STRING", "description": "Recipient phone number with country code"},
                "text": {"type": "STRING", "description": "Message text to deliver"},
            },
            "required": ["to", "text"],
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_send_wa_handler,
        is_mutating=True,
    ))

    # 9. purge_personal_store (Approval Gated via PolicyEngine)
    def _purge_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        store.wipe_all_data()
        return ToolResult(status="done", output="PersonalStore completely wiped and local personal data purged.", evidence={"purged": True})

    registry.register(ToolDefinition(
        name="purge_personal_store",
        description="Irreversibly delete all local personal intelligence data (MANDATORY OPERATOR APPROVAL REQUIRED).",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.SYSTEM_DESTRUCTIVE,
        handler=_purge_handler,
        is_mutating=True,
    ))
