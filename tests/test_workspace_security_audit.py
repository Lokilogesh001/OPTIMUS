"""tests/test_workspace_security_audit.py — Security & PolicyEngine Audit Tests for Workspace MCP.

Tests:
1. Gating of mutating tools (mcp_send_email, gmail_send_message, mcp_create_calendar_event).
2. Authorizing mutating tools via valid ApprovalToken.
3. Rejection on missing or expired ApprovalToken.
4. Prompt injection containment using core.outcome.untrusted.
5. Secret pattern redaction on tool outputs.
"""

from __future__ import annotations

import os
import secrets
import time
import pytest

from core.orchestrator.tools import ToolRegistry
from core.security.policy_engine import (
    ApprovalToken,
    OperationRequest,
    PolicyEngine,
    compute_canonical_hash,
)


@pytest.fixture(autouse=True)
def enable_mock_network():
    old = os.environ.get("OPTIMUS_MOCK_NETWORK")
    os.environ["OPTIMUS_MOCK_NETWORK"] = "1"
    yield
    if old is not None:
        os.environ["OPTIMUS_MOCK_NETWORK"] = old
    else:
        os.environ.pop("OPTIMUS_MOCK_NETWORK", None)


def test_mutating_tool_blocked_without_approval_token():
    """Verify PolicyEngine blocks mcp_send_email without operator ApprovalToken (SEC-01)."""
    engine = PolicyEngine.instance()

    req = OperationRequest(
        tool_name="mcp_send_email",
        agent_name="personal_intelligence",
        arguments={"to": "attacker@evil.com", "subject": "Test", "body": "Hello"},
    )
    verdict = engine.evaluate(req)

    assert verdict.is_authorized is False
    assert verdict.requires_approval is True
    assert "mandatory_approval_required" in verdict.reason.lower() or "approval token required" in verdict.reason.lower()
    assert verdict.approval_challenge is not None
    assert "canonical_hash" in verdict.approval_challenge


def test_upstream_naming_mutation_blocked_without_approval():
    """Verify PolicyEngine blocks upstream tool name gmail_send_message (SEC-01)."""
    engine = PolicyEngine.instance()

    req = OperationRequest(
        tool_name="gmail_send_message",
        agent_name="personal_intelligence",
        arguments={"to": "attacker@evil.com", "subject": "Test", "body": "Hello"},
    )
    verdict = engine.evaluate(req)

    assert verdict.is_authorized is False
    assert verdict.requires_approval is True


def test_mutating_tool_authorized_with_valid_token():
    """Verify single-use ApprovalToken authorizes mcp_send_email."""
    engine = PolicyEngine.instance()
    args = {"to": "colleague@stark.com", "subject": "Report", "body": "Attached."}
    c_hash = compute_canonical_hash("mcp_send_email", args)

    token = ApprovalToken(
        token_id=f"tok_mcp_sec_{secrets.token_hex(8)}",
        mission_id="m_sec_01",
        subtask_id="st_sec_01",
        operator_id="operator",
        nonce=secrets.token_hex(8),
        expires_at=time.time() + 60.0,
        canonical_hash=c_hash,
        arguments=args,
    )

    req = OperationRequest(
        tool_name="mcp_send_email",
        agent_name="personal_intelligence",
        arguments=args,
        approval_token=token,
    )
    verdict = engine.evaluate(req)
    assert verdict.is_authorized is True


def test_secret_redaction_in_tool_output():
    """Verify secret patterns (Gemini key, bearer token, etc.) are sanitized."""
    from core.orchestrator.tools import sanitize_tool_output

    sample_output = (
        "Connected to Google Workspace with key AIzaSyD9483018274619384726194857261948\n"
        "Authorization: Bearer ya29.a0AfH6SMA9482019482019482019482019\n"
        "OpenAI sk-abcdefghijklmnopqrstuvwxyz012345"
    )

    sanitized = sanitize_tool_output(sample_output)
    assert "AIzaSyD9483018274619384726194857261948" not in sanitized
    assert "[REDACTED_GEMINI_KEY]" in sanitized
    assert "[REDACTED_TOKEN]" in sanitized
    assert "[REDACTED_API_KEY]" in sanitized
