"""actions/gmail_assistant.py — Live Voice Interface Tool for Real Gmail Operations.

Connects the Gemini Live voice assistant directly to OPTIMUS's verified GmailAdapter,
PersonalIntelligenceEngine, and PolicyEngine/ToolRegistry gates — preventing the
assistant from relying on stored memory notes or unsupported browser automation for Gmail.
"""

from __future__ import annotations

import logging
from typing import Any

from core import confirm as confirm_gate
from core import outcome
from core.orchestrator.tools import CapabilityTier, ToolRegistry

logger = logging.getLogger("optimus.actions.gmail_assistant")


def gmail_assistant(
    parameters: dict[str, Any] | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Execute deterministic read-only or policy-gated Gmail operations."""
    params = dict(parameters or {})
    action = str(params.get("action") or "morning_important").strip().lower()
    reg = ToolRegistry.instance()
    tiers = [CapabilityTier.READ_ONLY, CapabilityTier.NETWORK_EXTERNAL]

    if action == "status":
        res = reg.execute(
            "get_gmail_status",
            {"account_id": params.get("account_id")},
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        return outcome.done("gmail_assistant", action="status", details=res.output) if res.is_success else outcome.failed("gmail_assistant", res.error or res.output)

    if action in ("morning_important", "important_morning", "important"):
        res = reg.execute(
            "get_important_morning_emails",
            {
                "timezone": params.get("timezone"),
                "limit": int(params.get("limit") or 10),
                "live_fetch": bool(params.get("live_fetch", True)),
            },
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        if not res.is_success:
            return outcome.failed("gmail_assistant", res.error or res.output, action="morning_important")
        return res.output if "<<<UNTRUSTED" in (res.output or "") else outcome.untrusted("gmail", res.output)

    if action in ("list", "search"):
        res = reg.execute(
            "list_gmail_messages",
            {
                "limit": int(params.get("limit") or 8),
                "is_unread": params.get("is_unread"),
                "is_important": params.get("is_important"),
                "sender": params.get("sender"),
                "search_query": params.get("query") or params.get("search_query"),
                "today_only": bool(params.get("today_only", False)),
                "live_fetch": bool(params.get("live_fetch", True)),
            },
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        if not res.is_success:
            return outcome.failed("gmail_assistant", res.error or res.output, action=action)
        return res.output if "<<<UNTRUSTED" in (res.output or "") else outcome.untrusted("gmail", res.output)

    if action in ("read", "details"):
        msg_id = str(params.get("message_id") or "").strip()
        res = reg.execute(
            "get_email_details",
            {"message_id": msg_id},
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        if not res.is_success:
            return outcome.failed("gmail_assistant", res.error or "Failed reading email", action="read")
        return res.output if "<<<UNTRUSTED" in (res.output or "") else outcome.untrusted("gmail", res.output)

    if action == "summarize":
        msg_id = str(params.get("message_id") or "latest").strip()
        res = reg.execute(
            "summarize_email",
            {"message_id": msg_id},
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        if not res.is_success:
            return outcome.failed("gmail_assistant", res.error or "Failed summarizing email", action="summarize")
        return res.output if "<<<UNTRUSTED" in (res.output or "") else outcome.untrusted("gmail", res.output)

    if action == "attachments":
        msg_id = str(params.get("message_id") or "").strip()
        res = reg.execute(
            "list_email_attachments",
            {"message_id": msg_id},
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        if not res.is_success:
            return outcome.failed("gmail_assistant", res.error or "Failed listing attachments", action="attachments")
        return outcome.done("gmail_assistant", action="attachments", details=res.output)

    if action == "get_attachment":
        res = reg.execute(
            "get_email_attachment",
            {
                "message_id": str(params.get("message_id") or "").strip(),
                "attachment_id": str(params.get("attachment_id") or "").strip(),
                "save_path": params.get("save_path"),
            },
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        if not res.is_success:
            return outcome.failed("gmail_assistant", res.error or "Failed retrieving attachment", action="get_attachment")
        return outcome.done("gmail_assistant", action="get_attachment", details=res.output)

    if action == "draft":
        res = reg.execute(
            "prepare_email_draft",
            {
                "to": str(params.get("to") or "").strip(),
                "subject": str(params.get("subject") or "").strip(),
                "body": str(params.get("body") or ""),
            },
            caller_agent="personal_intelligence",
            allowed_tiers=tiers,
        )
        if not res.is_success:
            return outcome.failed("gmail_assistant", res.error or "Failed creating draft", action="draft")
        return outcome.done("gmail_assistant", action="draft", details=res.output)

    if action == "send":
        to = str(params.get("to") or "").strip()
        subject = str(params.get("subject") or "").strip()
        body = str(params.get("body") or "")
        if not to or not subject or not body:
            return outcome.failed("gmail_assistant", "Sending an email requires to, subject, and body.")
        def _do_send() -> str:
            from core.security.policy_engine import PolicyEngine
            send_args = {"to": to, "subject": subject, "body": body}
            tok = PolicyEngine.instance().create_approval_token(
                mission_id="voice_gmail_send",
                subtask_id="send_email",
                operator_id="human_hud_operator",
                tool_name="send_email",
                arguments=send_args,
            )
            res = reg.execute(
                "send_email",
                send_args,
                caller_agent="orchestrator",
                allowed_tiers=tiers,
                context={"approval_token": tok},
            )
            if not res.is_success:
                return outcome.failed("gmail_assistant", res.error or "Email send failed", action="send")
            return outcome.done("gmail_assistant", action="send", details=res.output)

        gate_msg = confirm_gate.request(
            key="send_email",
            title=f"Send email to {to}",
            detail=f"Subject: {subject}",
            run=_do_send,
        )
        return outcome.ask("gmail_assistant", gate_msg, action="send", to=to, subject=subject)

    return outcome.failed("gmail_assistant", f"Unknown action '{action}'")


TOOL = {
    "name": "gmail_assistant",
    "description": (
        "Direct live Gmail integration. ALWAYS use this for ANY question about Gmail connection, "
        "important morning emails, searching/reading/summarizing emails, listing or downloading "
        "attachments, preparing drafts, or sending emails (confirmation-gated). "
        "NEVER answer Gmail questions from memory notes or open a browser search instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status | morning_important | search | list | read | summarize | attachments | get_attachment | draft | send",
            },
            "query": {"type": "STRING", "description": "Gmail search query"},
            "sender": {"type": "STRING", "description": "Sender filter"},
            "limit": {"type": "INTEGER", "description": "Max messages to inspect"},
            "today_only": {"type": "BOOLEAN", "description": "Filter for emails received today"},
            "is_unread": {"type": "BOOLEAN", "description": "Filter unread emails"},
            "is_important": {"type": "BOOLEAN", "description": "Filter important emails"},
            "message_id": {"type": "STRING", "description": "Gmail message ID"},
            "attachment_id": {"type": "STRING", "description": "Attachment ID"},
            "save_path": {"type": "STRING", "description": "Optional authorized save path for attachment"},
            "timezone": {"type": "STRING", "description": "Optional IANA timezone name (e.g. 'Asia/Kolkata')"},
            "to": {"type": "STRING", "description": "Recipient email for draft/send"},
            "subject": {"type": "STRING", "description": "Subject for draft/send"},
            "body": {"type": "STRING", "description": "Body for draft/send"},
        },
        "required": ["action"],
    },
    "handler": gmail_assistant,
}
