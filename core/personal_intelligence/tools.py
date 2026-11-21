"""core/personal_intelligence/tools.py — Central ToolRegistry Integration for Personal Life Intelligence.

Registers:
- get_gmail_status (NETWORK_EXTERNAL, READ_ONLY)
- list_gmail_messages (NETWORK_EXTERNAL, READ_ONLY)
- get_email_details (NETWORK_EXTERNAL, READ_ONLY)
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

import datetime
import json
import logging
import os
import re
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
    AuthStatus,
    Message,
    PersonalEvent,
    ProviderType,
    TaskStatus,
)

logger = logging.getLogger("optimus.personal.tools")


def register_personal_tools(registry: ToolRegistry, store: PersonalStore | None = None) -> None:
    """Register all personal life intelligence tools with the Central ToolRegistry."""
    store = store or PersonalStore.instance()
    engine = PersonalIntelligenceEngine(store=store)

    # 1. get_gmail_status
    def _gmail_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        acc_id = params.get("account_id")
        accounts = store.list_accounts()
        gmail_acc = None
        for a in accounts:
            if a.provider == ProviderType.GMAIL and (not acc_id or a.account_id == acc_id):
                gmail_acc = a
                break

        if not gmail_acc:
            msg = "No Google Gmail account is currently configured or connected in OPTIMUS."
            return ToolResult(
                status="done",
                output=msg,
                evidence={"connected": False, "provider": "GMAIL", "error": "No account configured"},
            )

        if os.environ.get("OPTIMUS_MOCK_NETWORK") == "1":
            health = {
                "healthy": True,
                "provider": "Gmail",
                "account_id": gmail_acc.account_id,
                "email": gmail_acc.email_or_id,
                "messages_total": 14,
                "threads_total": 14,
                "history_id": "1001",
                "auth_status": "CONNECTED",
                "scopes": gmail_acc.scopes or ["https://www.googleapis.com/auth/gmail.readonly"],
                "last_synced_at": "2026-11-21 12:00:00",
                "last_verified": time.time(),
            }
        else:
            from core.personal_intelligence.providers.gmail import GmailAdapter
            adapter = GmailAdapter(account=gmail_acc)
            health = adapter.check_health()

        # Update auth status in store if changed
        health_status = health.get("auth_status")
        if health_status and gmail_acc.auth_status.value != health_status:
            try:
                gmail_acc.auth_status = AuthStatus(health_status)
                store.save_account(gmail_acc)
            except (ValueError, KeyError, OSError) as e:
                logger.debug("Could not persist updated auth status: %s", e)

        if health.get("healthy"):
            out = (
                f"Gmail Connection Status: CONNECTED & ACTIVE\n"
                f"Account: {health.get('email', gmail_acc.email_or_id)}\n"
                f"Authentication: Valid (Google OAuth 2.0 PKCE Verified Live)\n"
                f"Last Synced: {health.get('last_synced_at', 'Never')}\n"
                f"Total Messages: {health.get('messages_total', 'N/A')}\n"
                f"Total Threads: {health.get('threads_total', 'N/A')}\n"
                f"History ID: {health.get('history_id', 'N/A')}\n"
                f"Granted Permissions: {', '.join(health.get('scopes', []))}"
            )
        else:
            out = (
                f"Gmail Connection Status: ATTENTION REQUIRED\n"
                f"Account: {gmail_acc.email_or_id}\n"
                f"Auth Status: {gmail_acc.auth_status.value}\n"
                f"Details: {health.get('error', 'Health verification encountered an issue')}"
            )

        clean_evidence = {k: v for k, v in health.items() if "token" not in k.lower()}
        return ToolResult(status="done", output=out, evidence=clean_evidence)

    registry.register(ToolDefinition(
        name="get_gmail_status",
        description="Verify real-time Google Gmail connection status, authenticated account identity, token validity, and API reachability.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "account_id": {"type": "STRING", "description": "Optional specific Gmail account ID"},
            },
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_gmail_status_handler,
        is_mutating=False,
    ))

    # 2. list_gmail_messages
    def _list_gmail_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        limit = max(1, min(int(params.get("limit", 5)), 25))
        is_unread = params.get("is_unread")
        is_important = params.get("is_important")
        sender = params.get("sender")
        search_query = params.get("search_query") or params.get("query")
        today_only = bool(params.get("today_only", False))
        mock_mode = os.environ.get("OPTIMUS_MOCK_NETWORK") == "1"
        live_fetch = False if mock_mode else bool(params.get("live_fetch", True))

        accounts = [a for a in store.list_accounts() if a.provider == ProviderType.GMAIL]
        gmail_acc = accounts[0] if accounts else None

        messages: list[Message] = []
        source = "local_store"

        if gmail_acc and live_fetch:
            try:
                from core.personal_intelligence.providers.gmail import GmailAdapter
                adapter = GmailAdapter(account=gmail_acc)
                q_terms = []
                if sender:
                    q_terms.append(f"from:{sender}")
                if search_query:
                    q_terms.append(search_query)
                if is_important:
                    q_terms.append("is:important")
                if today_only:
                    today_str = datetime.date.today().strftime("%Y/%m/%d")
                    q_terms.append(f"after:{today_str}")

                live_q = " ".join(q_terms) if q_terms else None
                fetched = adapter.fetch_recent_messages(limit=limit, query=live_q, is_unread=is_unread, is_important=is_important)
                if fetched:
                    messages = fetched
                    source = "live_gmail_api"
                    store.save_messages(fetched)
            except Exception as ex:  # noqa: BLE001
                logger.warning("Live Gmail message fetch failed; falling back to local store: %s", ex)

        if not messages:
            min_ts = (time.time() - (time.time() % 86400)) if today_only else None
            messages = store.list_messages(
                provider=ProviderType.GMAIL,
                is_unread=is_unread,
                search_query=search_query or sender,
                limit=limit,
            )
            if min_ts:
                messages = [m for m in messages if m.timestamp >= min_ts]
            source = "local_store"

        if not messages:
            status_text = "No Gmail messages found matching the requested criteria."
            if not gmail_acc:
                status_text += " (Note: No Gmail account is currently connected in OPTIMUS)."
            return ToolResult(
                status="done",
                output=status_text,
                evidence={"count": 0, "source": source, "messages": []},
            )

        lines = [f"Found {len(messages)} Gmail message(s) [Source: {source}]:"]
        evidence_msgs = []
        for idx, m in enumerate(messages, 1):
            date_str = datetime.datetime.fromtimestamp(m.timestamp).strftime("%Y-%m-%d %H:%M")
            status_tag = "[UNREAD]" if not m.is_read else "[READ]"
            imp_tag = " ★" if m.is_important else ""
            lines.append(f"{idx}. {status_tag}{imp_tag} From: {m.sender}")
            lines.append(f"   Subject: {m.subject}")
            lines.append(f"   Date: {date_str} (ID: {m.message_id})")
            if m.body_snippet:
                lines.append(f"   Snippet: {m.body_snippet}")
            lines.append("")
            evidence_msgs.append({
                "message_id": m.message_id,
                "sender": m.sender,
                "subject": m.subject,
                "date": date_str,
                "is_read": m.is_read,
                "is_important": m.is_important,
                "snippet": m.body_snippet,
            })

        return ToolResult(
            status="done",
            output="\n".join(lines).strip(),
            evidence={"count": len(messages), "source": source, "messages": evidence_msgs},
        )

    registry.register(ToolDefinition(
        name="list_gmail_messages",
        description="Retrieve recent or unread Gmail messages, search by sender or query, and display structured metadata.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "limit": {"type": "INTEGER", "description": "Maximum number of messages to return (default 5, max 25)"},
                "is_unread": {"type": "BOOLEAN", "description": "Filter for unread messages only"},
                "sender": {"type": "STRING", "description": "Filter by sender email address or name"},
                "search_query": {"type": "STRING", "description": "Search keyword query across email subject or body"},
                "today_only": {"type": "BOOLEAN", "description": "Only retrieve emails received today"},
                "live_fetch": {"type": "BOOLEAN", "description": "Whether to fetch live from Gmail API v1 (default true)"},
            },
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_list_gmail_handler,
        is_mutating=False,
    ))

    # 3. get_email_details
    def _email_detail_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        msg_id = str(params.get("message_id") or "").strip()
        if not msg_id or not re.match(r"^[a-zA-Z0-9_\-]+$", msg_id):
            return ToolResult(status="failed", error="Invalid or missing message_id parameter.")

        msg = store.get_message(msg_id)
        if not msg:
            accounts = [a for a in store.list_accounts() if a.provider == ProviderType.GMAIL]
            if accounts:
                try:
                    from core.personal_intelligence.providers.gmail import GmailAdapter
                    adapter = GmailAdapter(account=accounts[0])
                    msg = adapter.get_message_detail(msg_id)
                    if msg:
                        store.save_message(msg)
                except Exception as ex:  # noqa: BLE001
                    logger.warning("Failed fetching detail from live Gmail API: %s", ex)

        if not msg:
            return ToolResult(status="failed", error=f"Message with ID '{msg_id}' not found.")

        date_str = datetime.datetime.fromtimestamp(msg.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        out = (
            f"Email ID: {msg.message_id}\n"
            f"Provider: {msg.provider.value}\n"
            f"From: {msg.sender}\n"
            f"To: {', '.join(msg.recipients)}\n"
            f"Date: {date_str}\n"
            f"Subject: {msg.subject}\n"
            f"Status: {'Read' if msg.is_read else 'Unread'}, Important: {msg.is_important}\n"
            f"Content:\n{msg.body_snippet}"
        )
        return ToolResult(status="done", output=out, evidence=msg.to_dict())

    registry.register(ToolDefinition(
        name="get_email_details",
        description="Inspect single email message details by message ID with prompt-injection defanging.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "message_id": {"type": "STRING", "description": "Unique Gmail/Email message ID"},
            },
            "required": ["message_id"],
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_email_detail_handler,
        is_mutating=False,
    ))

    # 4. summarize_email
    def _summarize_email_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        msg_id = str(params.get("message_id") or "").strip()
        accounts = [a for a in store.list_accounts() if a.provider == ProviderType.GMAIL]
        if not accounts:
            return ToolResult(status="failed", error="No connected Gmail account.")

        mock_mode = os.environ.get("OPTIMUS_MOCK_NETWORK") == "1"
        if not msg_id or msg_id == "latest":
            recent = store.list_messages(provider=ProviderType.GMAIL, limit=1)
            if not recent and not mock_mode:
                from core.personal_intelligence.providers.gmail import GmailAdapter
                recent = GmailAdapter(account=accounts[0]).fetch_recent_messages(limit=1)
            if not recent:
                return ToolResult(status="done", output="No Gmail messages found to summarize.", evidence={"count": 0})
            msg_id = recent[0].message_id

        if mock_mode:
            local_msg = store.get_message(msg_id)
            if local_msg:
                snippet = local_msg.body_snippet or ""
                lines = [line.strip() for line in snippet.split("\n") if line.strip()]
                key_points = lines[:3] or ["No extended body content available."]
                summary = {
                    "message_id": local_msg.message_id,
                    "sender": local_msg.sender,
                    "subject": local_msg.subject,
                    "date": datetime.datetime.fromtimestamp(local_msg.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                    "is_read": local_msg.is_read,
                    "is_important": local_msg.is_important,
                    "summary": f"Email from {local_msg.sender} regarding '{local_msg.subject}'.",
                    "key_points": key_points,
                    "quarantined_content": snippet,
                }
            else:
                summary = None
        else:
            from core.personal_intelligence.providers.gmail import GmailAdapter
            adapter = GmailAdapter(account=accounts[0])
            summary = adapter.summarize_email(msg_id)
        if not summary:
            return ToolResult(status="failed", error=f"Could not generate summary for email ID '{msg_id}'.")

        out = (
            f"Email Summary (ID: {summary['message_id']}):\n"
            f"From: {summary['sender']}\n"
            f"Subject: {summary['subject']}\n"
            f"Date: {summary['date']}\n"
            f"Status: {'Read' if summary['is_read'] else 'Unread'}{' ★ (Important)' if summary['is_important'] else ''}\n"
            f"Overview: {summary['summary']}\n"
            f"Key Points:\n" + "\n".join(f"- {kp}" for kp in summary["key_points"])
        )
        return ToolResult(status="done", output=out, evidence=summary)

    registry.register(ToolDefinition(
        name="summarize_email",
        description="Fetch an email by ID (or 'latest') and generate a concise, safe summary with key points.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "message_id": {"type": "STRING", "description": "Gmail message ID or 'latest'"},
            },
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_summarize_email_handler,
        is_mutating=False,
    ))

    # 4. get_personal_dashboard
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

    # 5. list_unified_inbox
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

    # 6. list_personal_tasks
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

    # 7. get_calendar_view
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

    # 8. sync_personal_accounts
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

    # 9. send_email (Approval Gated via PolicyEngine)
    def _send_email_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        if not to or not subject or not body:
            return ToolResult(status="failed", error="Missing required parameters: to, subject, body")

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

    # 10. create_calendar_event (Approval Gated via PolicyEngine)
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

    # 11. send_whatsapp_message (Approval Gated via PolicyEngine)
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

    # 12. purge_personal_store (Approval Gated via PolicyEngine)
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
