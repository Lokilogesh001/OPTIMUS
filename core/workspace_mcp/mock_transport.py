"""core/workspace_mcp/mock_transport.py — Deterministic Hermetic Mock Transport for Workspace MCP.

Provides zero-network offline test execution for Google Workspace MCP tools,
mirroring official Google API responses and matching M12 test assertions.
"""

from __future__ import annotations

import datetime
import time
from typing import Any


class MockWorkspaceTransport:
    """Hermetic offline JSON-RPC mock transport for Google Workspace MCP testing."""

    def __init__(self) -> None:
        self.messages = [
            {
                "id": "msg_mcp_001",
                "threadId": "th_mcp_001",
                "labelIds": ["INBOX", "UNREAD", "IMPORTANT"],
                "snippet": "Quarterly architecture review scheduled for tomorrow morning.",
                "internalDate": str(int(time.time() * 1000)),
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Alex River <alex.river@starkindustries.com>"},
                        {"name": "To", "value": "User <user@optimus.local>"},
                        {"name": "Subject", "value": "Q4 System Architecture Review"},
                        {"name": "Date", "value": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")},
                    ],
                    "body": {"size": 420},
                },
                "body_content": (
                    "Hello team,\n\n"
                    "Please find attached the Q4 system architecture review. "
                    "We need all engineering leads to sign off on the telemetry specifications.\n\n"
                    "Best regards,\nAlex"
                ),
            },
            {
                "id": "msg_mcp_002",
                "threadId": "th_mcp_002",
                "labelIds": ["INBOX", "UNREAD"],
                "snippet": "Your server telemetry threshold alert triggered on host WIN-LVII.",
                "internalDate": str(int((time.time() - 1800) * 1000)),
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Monitor Alerts <alerts@infra.internal>"},
                        {"name": "To", "value": "User <user@optimus.local>"},
                        {"name": "Subject", "value": "Telemetry Alert: High Memory Usage on Host"},
                        {"name": "Date", "value": datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")},
                    ],
                    "body": {"size": 250},
                },
                "body_content": (
                    "Warning: Host WIN-LVII memory utilization exceeded 85% for more than 5 minutes.\n"
                    "Recommended action: inspect top active processes via system telemetry."
                ),
            },
            {
                "id": "msg_mcp_003",
                "threadId": "th_mcp_003",
                "labelIds": ["INBOX"],
                "snippet": "Invoice #1094 for cloud GPU cluster hosting.",
                "internalDate": str(int((time.time() - 86400 * 2) * 1000)),
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Billing <billing@cloudgpu.io>"},
                        {"name": "To", "value": "User <user@optimus.local>"},
                        {"name": "Subject", "value": "Monthly Cloud GPU Invoice #1094"},
                        {"name": "Date", "value": "Thu, 20 Nov 2026 10:00:00 +0000"},
                    ],
                    "body": {"size": 180},
                },
                "body_content": "Your monthly invoice for NVIDIA GPU cluster compute is ready.",
            },
        ]

        now = time.time()
        start_of_day = now - (now % 86400)
        tomorrow_start = start_of_day + 86400

        self.events = [
            {
                "id": "evt_mcp_101",
                "summary": "Core Engineering Sync",
                "description": "Daily standup to review M20 security remediations and Workspace MCP.",
                "location": "Conference Room A / Google Meet",
                "start": {"dateTime": datetime.datetime.fromtimestamp(tomorrow_start + 36000).isoformat()},  # 10:00 AM
                "end": {"dateTime": datetime.datetime.fromtimestamp(tomorrow_start + 39600).isoformat()},    # 11:00 AM
                "status": "confirmed",
                "attendees": [{"email": "lead@stark.com"}, {"email": "user@optimus.local"}],
            },
            {
                "id": "evt_mcp_102",
                "summary": "Telemetry & Infrastructure Review",
                "description": "Cross-check psutil and pynvml telemetry pipelines with team.",
                "location": "Virtual",
                "start": {"dateTime": datetime.datetime.fromtimestamp(tomorrow_start + 37800).isoformat()},  # 10:30 AM (CONFLICT with evt_101!)
                "end": {"dateTime": datetime.datetime.fromtimestamp(tomorrow_start + 41400).isoformat()},    # 11:30 AM
                "status": "confirmed",
                "attendees": [{"email": "devops@stark.com"}],
            },
            {
                "id": "evt_mcp_103",
                "summary": "Sprint Retrospective",
                "description": "Review milestone delivery outcomes and lessons learned.",
                "location": "Main Auditorium",
                "start": {"dateTime": datetime.datetime.fromtimestamp(tomorrow_start + 50400).isoformat()},  # 2:00 PM
                "end": {"dateTime": datetime.datetime.fromtimestamp(tomorrow_start + 54000).isoformat()},    # 3:00 PM
                "status": "confirmed",
                "attendees": [{"email": "all@stark.com"}],
            },
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute mock tool call with deterministic responses."""
        if tool_name in ("gmail_get_status", "mcp_get_gmail_status"):
            return {
                "healthy": True,
                "provider": "Google Workspace MCP",
                "account": "user@optimus.local",
                "transport": "mock_stdio",
                "auth_status": "CONNECTED",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/calendar.readonly"],
                "messages_total": len(self.messages),
                "last_synced_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        elif tool_name in ("gmail_list_messages", "mcp_list_gmail_messages"):
            limit = int(arguments.get("limit", 5))
            is_unread = arguments.get("is_unread")
            sender = arguments.get("sender")
            search_q = arguments.get("query") or arguments.get("search_query")
            today_only = bool(arguments.get("today_only", False))

            filtered = self.messages
            if is_unread is True:
                filtered = [m for m in filtered if "UNREAD" in m.get("labelIds", [])]
            elif is_unread is False:
                filtered = [m for m in filtered if "UNREAD" not in m.get("labelIds", [])]

            if sender:
                s_low = sender.lower()
                filtered = [
                    m for m in filtered
                    if any(h["name"].lower() == "from" and s_low in h["value"].lower() for h in m["payload"]["headers"])
                ]

            if search_q:
                q_low = search_q.lower()
                filtered = [
                    m for m in filtered
                    if q_low in m.get("snippet", "").lower()
                    or any(q_low in h["value"].lower() for h in m["payload"]["headers"] if h["name"].lower() in ("subject", "from"))
                ]

            return {
                "count": len(filtered[:limit]),
                "messages": filtered[:limit],
            }

        elif tool_name in ("gmail_get_message", "mcp_get_email_details"):
            msg_id = arguments.get("message_id")
            for m in self.messages:
                if m["id"] == msg_id:
                    return {"found": True, "message": m}
            return {"found": False, "error": f"Message '{msg_id}' not found"}

        elif tool_name in ("gmail_summarize_email", "mcp_summarize_email"):
            msg_id = arguments.get("message_id")
            target = None
            if not msg_id or msg_id in ("latest", "today"):
                target = self.messages[0]
            else:
                for m in self.messages:
                    if m["id"] == msg_id:
                        target = m
                        break

            if not target:
                return {"found": False, "error": "Email not found"}

            headers = {h["name"]: h["value"] for h in target["payload"]["headers"]}
            return {
                "message_id": target["id"],
                "sender": headers.get("From", "Unknown"),
                "subject": headers.get("Subject", "Untitled"),
                "date": headers.get("Date", ""),
                "summary": f"Summary of email regarding '{headers.get('Subject')}'.",
                "key_points": [
                    "Discussed telemetry threshold and engineering review.",
                    "Review required tomorrow morning.",
                ],
                "body_snippet": target["snippet"],
            }

        elif tool_name in ("calendar_list_events", "mcp_get_calendar_view"):
            return {
                "count": len(self.events),
                "events": self.events,
            }

        elif tool_name in ("calendar_find_conflicts", "mcp_find_calendar_conflicts"):
            conflicts = [
                {
                    "event_a": "Core Engineering Sync (10:00 - 11:00)",
                    "event_b": "Telemetry & Infrastructure Review (10:30 - 11:30)",
                    "overlap_minutes": 30,
                    "overlap_window": "10:30 - 11:00",
                }
            ]
            return {
                "conflict_count": len(conflicts),
                "conflicts": conflicts,
            }

        elif tool_name in ("gmail_send_message", "mcp_send_email"):
            return {
                "sent": True,
                "message_id": f"msg_sent_{int(time.time())}",
                "to": arguments.get("to"),
                "subject": arguments.get("subject"),
            }

        elif tool_name in ("calendar_create_event", "mcp_create_calendar_event"):
            return {
                "created": True,
                "event_id": f"evt_created_{int(time.time())}",
                "title": arguments.get("title"),
            }

        raise ValueError(f"Unknown mock tool: {tool_name}")
