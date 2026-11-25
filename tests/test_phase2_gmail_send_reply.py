"""Phase 2 Stage 1: Gmail Send & Reply Hardening Tests.

Tests:
1. Send new email + Gmail API post-send GET verification.
2. Reply to existing email preserving threadId, Subject (Re:), In-Reply-To, and References.
3. Safe attachments via UnifiedFilesystemService (authorized vs rejected symlink/outside path).
4. Mandatory human approval gate (ToolRegistry PolicyEngine token + confirm_gate.request).
5. Expired credentials (401), insufficient scope / 403, network failures, and duplicate-send dedup.
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.personal_intelligence.providers.gmail import GmailAdapter
from core.personal_intelligence.types import Account, AuthStatus, ProviderType


def _make_account(scopes: list[str] | None = None, acc_id: str = "acc_p2_gmail") -> Account:
    return Account(
        account_id=acc_id,
        provider=ProviderType.GMAIL,
        email_or_id="user@example.com",
        display_name="Test User",
        auth_status=AuthStatus.CONNECTED,
        scopes=scopes if scopes is not None else [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ],
    )


def _fake_resp(status_code: int, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, json=lambda: payload, text=str(payload))


class TestGmailSendAndReply:
    def setup_method(self):
        with GmailAdapter._SEND_DEDUP_LOCK:
            GmailAdapter._RECENT_SENDS.clear()

    def test_send_new_email_verifies_via_gmail_api_and_blocks_duplicate(self):
        acc = _make_account()
        http = MagicMock()
        http.post.return_value = _fake_resp(200, {"id": "msg_sent_1", "threadId": "thr_1", "labelIds": ["SENT"]})
        http.get.return_value = _fake_resp(200, {"id": "msg_sent_1", "threadId": "thr_1", "labelIds": ["SENT"]})

        adapter = GmailAdapter(account=acc, http_client=http)
        with patch.object(adapter, "_get_access_token", return_value="secret_tok_123"):
            res = adapter.send_message(to="alice@example.com", subject="Phase 2 Test", body="Hello Alice")
            dup = adapter.send_message(to="alice@example.com", subject="Phase 2 Test", body="Hello Alice")

        assert res["success"] is True
        assert res["message_id"] == "msg_sent_1"
        assert res["thread_id"] == "thr_1"
        assert res["verified"] is True
        assert "SENT" in res["labels"]
        assert "secret_tok_123" not in str(res)
        http.get.assert_called_once()
        assert dup["success"] is False
        assert dup["error_code"] == "duplicate_send"

    def test_reply_preserves_thread_and_rfc2822_headers(self):
        acc = _make_account()
        http = MagicMock()

        def _get(url, **kwargs):
            if kwargs.get("params", {}).get("format") == "metadata":
                return _fake_resp(200, {
                    "id": "msg_orig_42",
                    "threadId": "thr_orig_99",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Quarterly Review"},
                            {"name": "From", "value": "Bob <bob@example.com>"},
                            {"name": "Message-ID", "value": "<orig-42@mail.example.com>"},
                            {"name": "References", "value": "<root-1@mail.example.com>"},
                        ]
                    },
                })
            return _fake_resp(200, {"id": "msg_reply_43", "threadId": "thr_orig_99", "labelIds": ["SENT"]})

        http.get.side_effect = _get
        http.post.return_value = _fake_resp(200, {"id": "msg_reply_43", "threadId": "thr_orig_99", "labelIds": ["SENT"]})

        adapter = GmailAdapter(account=acc, http_client=http)
        with patch.object(adapter, "_get_access_token", return_value="tok_reply"):
            res = adapter.reply_to_message("msg_orig_42", "Thanks Bob, received.")

        assert res["success"] is True
        assert res["thread_id"] == "thr_orig_99"
        assert res["verified"] is True
        sent_json = http.post.call_args.kwargs["json"]
        assert sent_json["threadId"] == "thr_orig_99"
        raw_bytes = base64.urlsafe_b64decode(sent_json["raw"] + "==")
        em = message_from_bytes(raw_bytes)
        assert em["To"] == "Bob <bob@example.com>"
        assert em["Subject"] == "Re: Quarterly Review"
        assert em["In-Reply-To"] == "<orig-42@mail.example.com>"
        assert em["References"] == "<root-1@mail.example.com> <orig-42@mail.example.com>"

    def test_error_handling_expired_scope_permission_and_network_release_dedup(self):
        # 1. Insufficient scope
        acc_ro = _make_account(scopes=["https://www.googleapis.com/auth/gmail.readonly"])
        adapter_ro = GmailAdapter(account=acc_ro, http_client=MagicMock())
        with patch.object(adapter_ro, "_get_access_token", return_value="tok"):
            res_scope = adapter_ro.send_message("a@b.com", "Sub", "Body")
        assert res_scope["success"] is False
        assert res_scope["error_code"] == "insufficient_scope"

        # 2. Expired 401
        acc = _make_account()
        http_401 = MagicMock()
        http_401.post.return_value = _fake_resp(401, {"error": {"message": "Invalid Credentials"}})
        adapter_401 = GmailAdapter(account=acc, http_client=http_401)
        with patch.object(adapter_401, "_get_access_token", return_value="tok"):
            res_401 = adapter_401.send_message("a@b.com", "Sub", "Body")
        assert res_401["success"] is False
        assert res_401["error_code"] == "auth_expired"
        assert acc.auth_status == AuthStatus.EXPIRED

        # 3. Network failure releases dedup slot so retry is allowed
        acc_net = _make_account(acc_id="acc_net")
        http_net = MagicMock()
        http_net.post.side_effect = [
            requests.ConnectionError("DNS timeout"),
            _fake_resp(200, {"id": "msg_ok", "threadId": "thr_ok", "labelIds": ["SENT"]}),
        ]
        http_net.get.return_value = _fake_resp(200, {"id": "msg_ok", "threadId": "thr_ok", "labelIds": ["SENT"]})
        adapter_net = GmailAdapter(account=acc_net, http_client=http_net)
        with patch.object(adapter_net, "_get_access_token", return_value="tok"):
            r1 = adapter_net.send_message("a@b.com", "Sub", "Body")
            r2 = adapter_net.send_message("a@b.com", "Sub", "Body")
        assert r1["success"] is False
        assert r1["error_code"] == "network_error"
        assert r2["success"] is True

    def test_approval_gates_and_safe_attachments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from actions.gmail_assistant import gmail_assistant
        from core import confirm as confirm_gate
        from core.orchestrator.tools import CapabilityTier, ToolRegistry
        from core.personal_intelligence.storage import PersonalStore
        from core.personal_intelligence.tools import register_personal_tools

        store = PersonalStore(root_dir=tmp_path / "pi_store")
        store.save_account(_make_account())
        reg = ToolRegistry.instance()
        register_personal_tools(reg, store=store)

        # ToolRegistry blocks reply_email & send_email without PolicyEngine ApprovalToken (even under MODERATE profile)
        from core.security.policy_engine import PolicyEngine
        from core.security.permissions import PermissionProfile
        pe = PolicyEngine.instance()
        prev_profile = pe.permission_manager.get_profile()
        try:
            pe.permission_manager._profile = PermissionProfile.MODERATE
            r_no_tok = reg.execute(
                "reply_email",
                {"message_id": "m1", "body": "hi"},
                caller_agent="orchestrator",
                allowed_tiers=[CapabilityTier.NETWORK_EXTERNAL],
            )
            assert not r_no_tok.is_success
        finally:
            pe.permission_manager._profile = prev_profile

        # Outside/disallowed attachment is rejected fail-closed (3-tuple check_access return)
        monkeypatch.setenv("OPTIMUS_MOCK_NETWORK", "1")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        args_bad = {"message_id": "m1", "body": "hi", "attachments": [str(outside)]}
        tok_bad = pe.create_approval_token(
            mission_id="m_test",
            subtask_id="st_1",
            operator_id="human_hud_operator",
            tool_name="reply_email",
            arguments=args_bad,
        )
        with patch("core.filesystem.service.UnifiedFilesystemService.check_access", return_value=(False, True, "Protected path")):
            r_bad_att = reg.execute(
                "reply_email",
                args_bad,
                caller_agent="orchestrator",
                allowed_tiers=[CapabilityTier.NETWORK_EXTERNAL],
                context={"approval_token": tok_bad},
            )
        assert not r_bad_att.is_success
        assert r_bad_att.evidence.get("error_code") == "attachment_rejected"

        # gmail_assistant(action="reply") parks behind confirm_gate.request() and does not send until confirmed
        captured: dict = {}

        def _fake_request(key: str, title: str, detail: str, run):
            captured["key"] = key
            captured["run"] = run
            return "[CONFIRMATION_PENDING]"

        with patch.object(confirm_gate, "request", side_effect=_fake_request):
            out = gmail_assistant({"action": "reply", "message_id": "m1", "body": "Sounds good"})
        assert "status=ask" in out
        assert captured["key"] == "reply_email"
        confirmed_out = captured["run"]()
        assert "status=done" in confirmed_out
