"""core/workspace_mcp/tools.py — Central ToolRegistry Tools for Google Workspace MCP.

Registers:
- mcp_get_gmail_status: Check Gmail connection health and scopes.
- mcp_list_gmail_messages: Retrieve recent or unread emails (with prompt quarantine).
- mcp_get_email_details: Retrieve full email content (with prompt quarantine).
- mcp_summarize_email: Generate concise summary with defanging.
- mcp_get_calendar_view: Query upcoming calendar events (with prompt quarantine).
- mcp_find_calendar_conflicts: Detect schedule collisions and overlaps.
- mcp_send_email: Outbound email dispatch (MANDATORY OPERATOR APPROVAL REQUIRED).
- mcp_create_calendar_event: Outbound calendar event creation (MANDATORY OPERATOR APPROVAL REQUIRED).
"""

from __future__ import annotations

import json
from typing import Any

from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    sanitize_tool_output,
)
from core.workspace_mcp.adapter import WorkspaceMCPAdapter


def _mcp_gmail_status_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    adapter = WorkspaceMCPAdapter.instance()
    status = adapter.get_gmail_status()

    lines = [
        "### Google Workspace MCP — Gmail Status",
        f"- **Connection State:** {'CONNECTED' if status['healthy'] else 'DISCONNECTED'}",
        f"- **Account:** {status['account']}",
        f"- **Granted Scopes:** {', '.join(status['scopes'])}",
        f"- **Total Messages:** {status['messages_total']}",
        f"- **Last Synced:** {status['last_synced_at']}",
    ]
    out_text = sanitize_tool_output("\n".join(lines))
    return ToolResult(status="done", output=out_text, evidence=status)


def _mcp_list_gmail_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    adapter = WorkspaceMCPAdapter.instance()
    limit = max(1, min(int(params.get("limit", 5)), 25))
    is_unread = params.get("is_unread")
    sender = params.get("sender")
    query = params.get("query") or params.get("search_query")
    today_only = bool(params.get("today_only", False))

    msgs = adapter.list_gmail_messages(
        limit=limit,
        is_unread=is_unread,
        sender=sender,
        query=query,
        today_only=today_only,
    )

    if not msgs:
        return ToolResult(
            status="done",
            output="No Gmail messages found matching the requested criteria.",
            evidence={"count": 0, "messages": []},
        )

    lines = [f"Found {len(msgs)} Gmail message(s) via Google Workspace MCP:"]
    for idx, m in enumerate(msgs, 1):
        status_tag = "[UNREAD]" if m.get("is_unread") else "[READ]"
        imp_tag = " ★" if m.get("is_important") else ""
        lines.append(f"{idx}. {status_tag}{imp_tag} From: {m['sender']}")
        lines.append(f"   Subject: {m['subject']}")
        lines.append(f"   Date: {m['date']} (ID: {m['id']})")
        if m.get("snippet"):
            lines.append(f"   Snippet: {m['snippet']}")
        lines.append("")

    out_text = sanitize_tool_output("\n".join(lines).strip())
    return ToolResult(status="done", output=out_text, evidence={"count": len(msgs), "messages": msgs})


def _mcp_email_details_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    msg_id = str(params.get("message_id") or "").strip()
    if not msg_id:
        return ToolResult(status="failed", error="Missing required parameter: message_id")

    adapter = WorkspaceMCPAdapter.instance()
    detail = adapter.get_email_details(msg_id)
    if not detail:
        return ToolResult(status="failed", error=f"Email with ID '{msg_id}' not found.")

    lines = [
        f"Email ID: {detail['id']}",
        f"From: {detail['sender']}",
        f"To: {detail['to']}",
        f"Date: {detail['date']}",
        f"Subject: {detail['subject']}",
        "Content:",
        detail["content"],
    ]
    out_text = sanitize_tool_output("\n".join(lines))
    return ToolResult(status="done", output=out_text, evidence=detail)


def _mcp_summarize_email_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    msg_id = params.get("message_id")
    today_only = bool(params.get("today_only", False))

    adapter = WorkspaceMCPAdapter.instance()
    summary = adapter.summarize_email(message_id=msg_id, today_only=today_only)
    if not summary:
        return ToolResult(status="done", output="No Gmail messages found to summarize.", evidence={"count": 0})

    lines = [
        f"Email Summary (ID: {summary.get('message_id', 'unknown')}):",
        f"From: {summary.get('sender', 'Unknown')}",
        f"Subject: {summary.get('subject', 'Untitled')}",
        f"Date: {summary.get('date', 'Unknown')}",
        f"Overview: {summary.get('summary', '')}",
        "Key Points:",
    ]
    for kp in summary.get("key_points", []):
        lines.append(f"- {kp}")

    out_text = sanitize_tool_output("\n".join(lines))
    return ToolResult(status="done", output=out_text, evidence=summary)


def _mcp_calendar_view_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    day = str(params.get("day", "tomorrow"))
    adapter = WorkspaceMCPAdapter.instance()
    events = adapter.get_calendar_view(day=day)

    if not events:
        return ToolResult(
            status="done",
            output=f"No calendar events scheduled for {day}.",
            evidence={"count": 0, "events": []},
        )

    lines = [f"Upcoming Schedule for {day.title()} ({len(events)} event(s)):"]
    for idx, e in enumerate(events, 1):
        lines.append(f"{idx}. {e['title']}")
        lines.append(f"   Time: {e['start']} to {e['end']}")
        if e.get("location"):
            lines.append(f"   Location: {e['location']}")
        if e.get("description"):
            lines.append(f"   Notes: {e['description']}")
        lines.append("")

    out_text = sanitize_tool_output("\n".join(lines).strip())
    return ToolResult(status="done", output=out_text, evidence={"count": len(events), "events": events})


def _mcp_calendar_conflicts_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    adapter = WorkspaceMCPAdapter.instance()
    conflicts = adapter.find_calendar_conflicts()

    if not conflicts:
        return ToolResult(
            status="done",
            output="No scheduling conflicts found in your calendar.",
            evidence={"conflict_count": 0, "conflicts": []},
        )

    lines = [f"Found {len(conflicts)} scheduling conflict(s):"]
    for idx, c in enumerate(conflicts, 1):
        lines.append(f"{idx}. Overlap detected:")
        lines.append(f"   - Event 1: {c['event_a']}")
        lines.append(f"   - Event 2: {c['event_b']}")
        lines.append(f"   - Collision Window: {c['overlap_window']} ({c['overlap_minutes']} mins)")
        lines.append("")

    out_text = sanitize_tool_output("\n".join(lines).strip())
    return ToolResult(status="done", output=out_text, evidence={"conflict_count": len(conflicts), "conflicts": conflicts})


def _mcp_send_email_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    to = params.get("to", "")
    subject = params.get("subject", "")
    body = params.get("body", "")
    if not to or not subject or not body:
        return ToolResult(status="failed", error="Missing required parameters: to, subject, body")

    adapter = WorkspaceMCPAdapter.instance()
    resp = adapter.send_email(to=to, subject=subject, body=body)
    msg = f"Email dispatched via Workspace MCP to '{to}' with subject '{subject}'."
    return ToolResult(status="done", output=msg, evidence=resp)


def _mcp_create_calendar_event_handler(params: dict[str, Any], context: dict[str, Any] | None = None) -> ToolResult:
    title = params.get("title", "")
    start_time = str(params.get("start_time", ""))
    end_time = str(params.get("end_time", ""))
    location = params.get("location", "")
    if not title or not start_time:
        return ToolResult(status="failed", error="Missing required parameters: title, start_time")

    adapter = WorkspaceMCPAdapter.instance()
    resp = adapter.create_calendar_event(title=title, start_time=start_time, end_time=end_time, location=location)
    msg = f"Calendar event '{title}' scheduled via Workspace MCP."
    return ToolResult(status="done", output=msg, evidence=resp)


def register_workspace_mcp_tools(registry: ToolRegistry) -> None:
    """Register all Google Workspace MCP tools with the Central ToolRegistry."""
    registry.register(ToolDefinition(
        name="mcp_get_gmail_status",
        description="Verify real-time Google Workspace MCP Gmail connection status, account identity, and scopes.",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_mcp_gmail_status_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="mcp_list_gmail_messages",
        description="Retrieve recent or unread emails via Workspace MCP with prompt-injection defanging.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "limit": {"type": "INTEGER", "description": "Maximum number of messages (default 5, max 25)"},
                "is_unread": {"type": "BOOLEAN", "description": "Filter for unread messages only"},
                "sender": {"type": "STRING", "description": "Filter by sender email or name"},
                "query": {"type": "STRING", "description": "Search keyword query"},
                "today_only": {"type": "BOOLEAN", "description": "Only retrieve emails received today"},
            },
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_mcp_list_gmail_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="mcp_get_email_details",
        description="Inspect single email message details by message ID with strict defanging.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "message_id": {"type": "STRING", "description": "Unique email message ID"},
            },
            "required": ["message_id"],
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_mcp_email_details_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="mcp_summarize_email",
        description="Fetch an email by ID or 'latest'/'today' and generate a concise summary with key points.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "message_id": {"type": "STRING", "description": "Email ID, 'latest', or leave empty for latest"},
                "today_only": {"type": "BOOLEAN", "description": "Whether to summarize emails from today"},
            },
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_mcp_summarize_email_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="mcp_get_calendar_view",
        description="Query calendar events for a specific timeframe ('tomorrow', 'today', etc.) via Workspace MCP.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "day": {"type": "STRING", "description": "Day descriptor, e.g. 'tomorrow', 'today' (default: tomorrow)"},
            },
        },
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_mcp_calendar_view_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="mcp_find_calendar_conflicts",
        description="Analyze schedule to identify overlapping or conflicting meetings.",
        parameters={"type": "OBJECT", "properties": {}},
        capability_tier=CapabilityTier.NETWORK_EXTERNAL,
        handler=_mcp_calendar_conflicts_handler,
        is_mutating=False,
    ))

    registry.register(ToolDefinition(
        name="mcp_send_email",
        description="Dispatch an email message via Google Workspace MCP (MANDATORY APPROVAL REQUIRED).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "to": {"type": "STRING", "description": "Recipient email address"},
                "subject": {"type": "STRING", "description": "Email subject"},
                "body": {"type": "STRING", "description": "Email body text"},
            },
            "required": ["to", "subject", "body"],
        },
        capability_tier=CapabilityTier.SYSTEM_MUTATING,
        handler=_mcp_send_email_handler,
        is_mutating=True,
    ))

    registry.register(ToolDefinition(
        name="mcp_create_calendar_event",
        description="Create a new calendar event via Google Workspace MCP (MANDATORY APPROVAL REQUIRED).",
        parameters={
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "Event title"},
                "start_time": {"type": "STRING", "description": "Start timestamp or ISO time string"},
                "end_time": {"type": "STRING", "description": "End timestamp or ISO time string"},
                "location": {"type": "STRING", "description": "Location or meeting link"},
            },
            "required": ["title", "start_time"],
        },
        capability_tier=CapabilityTier.SYSTEM_MUTATING,
        handler=_mcp_create_calendar_event_handler,
        is_mutating=True,
    ))
