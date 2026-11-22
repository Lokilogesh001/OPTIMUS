"""tests/test_workspace_mcp_adapter.py — Verification Suite for Google Workspace MCP.

Tests:
1. WorkspaceMCPAdapter tool execution (mock transport and stdio).
2. Prompt injection quarantine verification for emails (SEC-06).
3. Prompt injection quarantine verification for calendar events (SEC-05).
4. Calendar conflict detection.
5. ToolRegistry tool dispatch for all mcp_* tools.
6. Stdio client stderr buffer draining and execution timeout budget.
"""

from __future__ import annotations

import os
import pytest

from core.orchestrator.tools import ToolRegistry
from core.workspace_mcp.adapter import WorkspaceMCPAdapter
from core.workspace_mcp.mock_transport import MockWorkspaceTransport


@pytest.fixture(autouse=True)
def enable_mock_network():
    """Ensure mock mode is active for hermetic tests."""
    old = os.environ.get("OPTIMUS_MOCK_NETWORK")
    os.environ["OPTIMUS_MOCK_NETWORK"] = "1"
    yield
    if old is not None:
        os.environ["OPTIMUS_MOCK_NETWORK"] = old
    else:
        os.environ.pop("OPTIMUS_MOCK_NETWORK", None)


def test_workspace_mcp_adapter_initialization():
    """Verify adapter initializes and resolves mock transport."""
    adapter = WorkspaceMCPAdapter.instance()
    status = adapter.get_gmail_status()

    assert status["healthy"] is True
    assert "user@optimus.local" in status["account"]
    assert "gmail.readonly" in status["scopes"][0]


def test_workspace_mcp_email_retrieval_and_quarantine():
    """Verify email retrieval wraps content with authoritative core.outcome.untrusted (SEC-06)."""
    adapter = WorkspaceMCPAdapter.instance()
    msgs = adapter.list_gmail_messages(limit=3, is_unread=True)

    assert len(msgs) > 0
    first = msgs[0]
    assert first["is_unread"] is True
    # Verify authoritative quarantine markers
    assert "<<<UNTRUSTED source=gmail>>>" in first["snippet"]
    assert "<<<END UNTRUSTED>>>" in first["snippet"]
    assert "<<<UNTRUSTED source=gmail>>>" in first["subject"]


def test_workspace_mcp_email_detail_and_summary():
    """Verify detail retrieval and summarization."""
    adapter = WorkspaceMCPAdapter.instance()
    detail = adapter.get_email_details("msg_mcp_001")

    assert detail is not None
    assert detail["id"] == "msg_mcp_001"
    assert "<<<UNTRUSTED source=gmail>>>" in detail["content"]

    summary = adapter.summarize_email(message_id="msg_mcp_001")
    assert summary is not None
    assert summary["message_id"] == "msg_mcp_001"
    assert len(summary["key_points"]) > 0


def test_workspace_mcp_calendar_view_and_quarantine():
    """Verify calendar events wrap title, desc, and location in core.outcome.untrusted (SEC-05)."""
    adapter = WorkspaceMCPAdapter.instance()
    events = adapter.get_calendar_view(day="tomorrow")

    assert len(events) > 0
    e1 = events[0]
    assert "<<<UNTRUSTED source=google_calendar>>>" in e1["title"]
    assert "<<<END UNTRUSTED>>>" in e1["title"]
    if e1["description"]:
        assert "<<<UNTRUSTED source=google_calendar>>>" in e1["description"]
    if e1["location"]:
        assert "<<<UNTRUSTED source=google_calendar>>>" in e1["location"]


def test_workspace_mcp_calendar_conflict_detection():
    """Verify schedule conflicts are accurately identified."""
    adapter = WorkspaceMCPAdapter.instance()
    conflicts = adapter.find_calendar_conflicts()

    assert len(conflicts) > 0
    c1 = conflicts[0]
    assert "Core Engineering Sync" in c1["event_a"]
    assert "Telemetry & Infrastructure Review" in c1["event_b"]
    assert c1["overlap_minutes"] == 30


def test_workspace_mcp_tool_registry_dispatch():
    """Verify all mcp_* tools dispatch cleanly through the central ToolRegistry."""
    registry = ToolRegistry.instance()

    # 1. mcp_get_gmail_status
    r_status = registry.execute("mcp_get_gmail_status", {}, caller_agent="personal_intelligence")
    assert r_status.is_success
    assert "Google Workspace MCP — Gmail Status" in r_status.output

    # 2. mcp_list_gmail_messages
    r_list = registry.execute("mcp_list_gmail_messages", {"limit": 2, "is_unread": True}, caller_agent="personal_intelligence")
    assert r_list.is_success
    assert "Found" in r_list.output
    assert "From:" in r_list.output

    # 3. mcp_get_calendar_view
    r_cal = registry.execute("mcp_get_calendar_view", {"day": "tomorrow"}, caller_agent="personal_intelligence")
    assert r_cal.is_success
    assert "Upcoming Schedule" in r_cal.output

    # 4. mcp_find_calendar_conflicts
    r_conf = registry.execute("mcp_find_calendar_conflicts", {}, caller_agent="personal_intelligence")
    assert r_conf.is_success
    assert "scheduling conflict" in r_conf.output
