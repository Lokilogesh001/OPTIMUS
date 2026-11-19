"""
tests/test_personal_intelligence.py — Automated Test Suite for Milestone 12: Personal Life Intelligence.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

import pytest

from core.orchestrator.tools import ToolRegistry
from core.personal_intelligence.engine import PersonalIntelligenceEngine
from core.personal_intelligence.providers.gmail import GmailAdapter
from core.personal_intelligence.providers.google_calendar import GoogleCalendarAdapter
from core.personal_intelligence.providers.whatsapp_business import (
    WhatsAppBusinessAdapter,
)
from core.personal_intelligence.providers.whatsapp_personal import (
    WhatsAppPersonalAdapter,
)
from core.personal_intelligence.storage import PersonalStore, mask_pii_string
from core.personal_intelligence.tools import register_personal_tools
from core.personal_intelligence.types import (
    Account,
    Message,
    PersonalEvent,
    PersonalTask,
    ProviderType,
    SourceReference,
    TaskPriority,
    TaskStatus,
)
from core.security.policy_engine import (
    OperationRequest,
    PolicyEngine,
)

# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def temp_personal_dir(tmp_path):
    store_dir = tmp_path / "personal_memory"
    return store_dir


@pytest.fixture
def personal_store(temp_personal_dir):
    return PersonalStore(root_dir=temp_personal_dir)


@pytest.fixture
def personal_engine(personal_store):
    return PersonalIntelligenceEngine(store=personal_store)


# ==============================================================================
# 1. Canonical Types & Data Contracts
# ==============================================================================

class TestCanonicalTypes:
    def test_account_serialization_and_safe_masking(self):
        account = Account(
            account_id="acc_gmail_123",
            provider=ProviderType.GMAIL,
            email_or_id="owner@example.com",
            display_name="Owner Gmail",
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            metadata={"access_token": "secret_abc", "refresh_token": "refresh_xyz"},
        )
        data_full = account.to_dict(safe=False)
        assert data_full["metadata"]["access_token"] == "secret_abc"

        data_safe = account.to_dict(safe=True)
        assert data_safe["metadata"]["access_token"] == "***MASKED***"
        assert data_safe["metadata"]["refresh_token"] == "***MASKED***"

        # Roundtrip
        restored = Account.from_dict(data_full)
        assert restored.account_id == account.account_id
        assert restored.provider == ProviderType.GMAIL
        assert restored.metadata["access_token"] == "secret_abc"

    def test_message_roundtrip(self):
        msg = Message(
            message_id="msg_001",
            provider=ProviderType.OUTLOOK,
            account_id="acc_1",
            conversation_id="conv_1",
            sender="alice@example.com",
            recipients=["bob@example.com"],
            subject="Urgent: Invoice review",
            body_snippet="Please pay invoice #100 before tomorrow.",
            importance_score=0.85,
            is_important=True,
        )
        d = msg.to_dict()
        restored = Message.from_dict(d)
        assert restored.message_id == msg.message_id
        assert restored.provider == ProviderType.OUTLOOK
        assert restored.importance_score == 0.85
        assert restored.is_important is True

    def test_personal_task_roundtrip(self):
        task = PersonalTask(
            task_id="task_001",
            title="Review client contract",
            source_provider=ProviderType.GMAIL,
            source_ref=SourceReference(
                provider=ProviderType.GMAIL,
                account_id="acc_1",
                record_id="msg_999",
                uri="gmail://msg_999",
                snippet="Please review contract",
            ),
            priority=TaskPriority.HIGH,
            status=TaskStatus.PENDING,
            due_date=1732000000.0,
        )
        d = task.to_dict()
        restored = PersonalTask.from_dict(d)
        assert restored.task_id == "task_001"
        assert restored.source_ref.record_id == "msg_999"
        assert restored.status == TaskStatus.PENDING


# ==============================================================================
# 2. Storage & Multi-Tenant Cursors & Safe Wipe
# ==============================================================================

class TestPersonalStore:
    def test_multi_tenant_stream_cursors(self, personal_store):
        personal_store.set_sync_cursor("acc_google_1", "mail", "hist_1001")
        personal_store.set_sync_cursor("acc_google_1", "calendar:primary", "sync_tok_555")
        personal_store.set_sync_cursor("acc_ms_2", "mail", "delta_link_xyz")

        assert personal_store.get_sync_cursor("acc_google_1", "mail") == "hist_1001"
        assert personal_store.get_sync_cursor("acc_google_1", "calendar:primary") == "sync_tok_555"
        assert personal_store.get_sync_cursor("acc_ms_2", "mail") == "delta_link_xyz"
        assert personal_store.get_sync_cursor("acc_unknown", "mail") is None

    def test_message_deduplication_and_lookup(self, personal_store):
        msg1 = Message(
            message_id="msg_dup_1",
            provider=ProviderType.GMAIL,
            account_id="acc_1",
            conversation_id="conv_1",
            sender="carol@example.com",
            subject="Hello",
            body_snippet="First version",
        )
        msg2 = Message(
            message_id="msg_dup_1",
            provider=ProviderType.GMAIL,
            account_id="acc_1",
            conversation_id="conv_1",
            sender="carol@example.com",
            subject="Hello",
            body_snippet="Updated version",
        )

        assert not personal_store.has_message("msg_dup_1")
        personal_store.save_messages([msg1])
        assert personal_store.has_message("msg_dup_1")

        # Saving second with same id updates in place without duplicating count
        personal_store.save_messages([msg2])
        all_msgs = personal_store.get_messages()
        assert len(all_msgs) == 1
        assert all_msgs[0].body_snippet == "Updated version"

    def test_wipe_all_data(self, personal_store):
        acc = Account(account_id="acc_wipe", provider=ProviderType.GMAIL, email_or_id="wipe@example.com")
        msg = Message(message_id="msg_wipe", provider=ProviderType.GMAIL, account_id="acc_wipe", conversation_id="c_1", sender="wipe@example.com")
        task = PersonalTask(task_id="task_wipe", title="Wipe me")
        event = PersonalEvent(event_id="evt_wipe", provider=ProviderType.GOOGLE_CALENDAR, account_id="acc_wipe", title="Meeting", start_time=1732000000.0, end_time=1732003600.0)

        personal_store.save_account(acc)
        personal_store.save_messages([msg])
        personal_store.save_tasks([task])
        personal_store.save_events([event])
        personal_store.set_sync_cursor("acc_wipe", "mail", "curs_1")

        assert len(personal_store.get_accounts()) == 1
        assert len(personal_store.get_messages()) == 1

        personal_store.wipe_all_data()

        assert len(personal_store.get_accounts()) == 0
        assert len(personal_store.get_messages()) == 0
        assert len(personal_store.get_tasks()) == 0
        assert len(personal_store.get_events()) == 0
        assert personal_store.get_sync_cursor("acc_wipe", "mail") is None

    def test_pii_masking_utility(self):
        masked = mask_pii_string("Contact john.doe@example.com for further questions")
        assert "john.doe@example.com" not in masked
        assert "j***@example.com" in masked


# ==============================================================================
# 3. Gmail Adapter (Incremental Sync & 410 Recovery)
# ==============================================================================

class TestGmailAdapter:
    def test_gmail_initial_sync(self, personal_store):
        acc = Account(account_id="acc_gm", provider=ProviderType.GMAIL, email_or_id="user@gmail.com")
        personal_store.save_account(acc)

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({
            "historyId": "hist_100",
            "messages": [
                {
                    "id": "g_msg_1",
                    "threadId": "th_1",
                    "snippet": "Meeting at 3pm",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Project Sync"},
                            {"name": "From", "value": "Alice <alice@work.com>"},
                            {"name": "To", "value": "user@gmail.com"},
                            {"name": "Date", "value": "Tue, 19 Nov 2026 10:00:00 +0000"},
                        ]
                    },
                }
            ],
        })
        mock_client.get.return_value = mock_response

        adapter = GmailAdapter(acc, http_client=mock_client)
        adapter._store_tokens("mock_access_token_gmail")

        msgs, evts, new_cursor = adapter.initial_sync(max_items=10)
        assert len(msgs) == 1
        assert msgs[0].subject == "Project Sync"
        assert msgs[0].sender == "Alice <alice@work.com>"
        assert new_cursor == "hist_100"

    def test_gmail_incremental_sync_and_410_recovery(self, personal_store):
        acc = Account(account_id="acc_gm_inc", provider=ProviderType.GMAIL, email_or_id="user@gmail.com")
        personal_store.save_account(acc)

        mock_client = MagicMock()

        # First call to history endpoint returns 410 Gone (cursor expired)
        mock_resp_410 = MagicMock()
        mock_resp_410.status_code = 410

        # Fallback call to initial_sync (messages list endpoint) returns fresh state
        mock_resp_initial = MagicMock()
        mock_resp_initial.status_code = 200
        mock_resp_initial.text = json.dumps({
            "historyId": "hist_fresh_200",
            "messages": [],
        })

        mock_client.get.side_effect = [mock_resp_410, mock_resp_initial]

        adapter = GmailAdapter(acc, http_client=mock_client)
        adapter._store_tokens("mock_access_token_gmail")

        msgs, evts, new_cursor = adapter.incremental_sync("expired_hist_999")
        assert new_cursor == "hist_fresh_200"


# ==============================================================================
# 4. Google Calendar Adapter & Conflict Detection
# ==============================================================================

class TestGoogleCalendarAdapter:
    def test_google_calendar_sync_and_conflict_detection(self, personal_store, personal_engine):
        acc = Account(account_id="acc_gcal", provider=ProviderType.GOOGLE_CALENDAR, email_or_id="user@gmail.com")
        personal_store.save_account(acc)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({
            "nextSyncToken": "sync_token_v3_abc",
            "items": [
                {
                    "id": "cal_evt_1",
                    "summary": "Team Architecture Review",
                    "description": "Quarterly system design review",
                    "start": {"dateTime": "2026-11-20T14:00:00Z"},
                    "end": {"dateTime": "2026-11-20T15:30:00Z"},
                    "attendees": [{"email": "lead@company.com"}],
                }
            ],
        })
        mock_client.get.return_value = mock_resp

        adapter = GoogleCalendarAdapter(acc, http_client=mock_client)
        adapter._store_tokens("mock_token_gcal")

        msgs, evts, new_token = adapter.initial_sync()
        assert len(evts) == 1
        assert evts[0].title == "Team Architecture Review"
        assert new_token == "sync_token_v3_abc"

        # Save event to store
        personal_store.save_events(evts)

        # Test conflict detection via PersonalIntelligenceEngine
        overlapping_event = PersonalEvent(
            event_id="evt_new",
            provider=ProviderType.GOOGLE_CALENDAR,
            account_id="acc_gcal",
            title="Dentist Appointment",
            start_time=evts[0].start_time + 300,  # 5 mins into the meeting
            end_time=evts[0].end_time - 300,
        )
        conflicts = personal_engine.detect_schedule_conflicts(overlapping_event)
        assert len(conflicts) == 1
        assert conflicts[0].title == "Team Architecture Review"

        # Non-overlapping event
        safe_event = PersonalEvent(
            event_id="evt_safe",
            provider=ProviderType.GOOGLE_CALENDAR,
            account_id="acc_gcal",
            title="Evening Run",
            start_time=evts[0].end_time + 3600,
            end_time=evts[0].end_time + 7200,
        )
        assert len(personal_engine.detect_schedule_conflicts(safe_event)) == 0


# ==============================================================================
# 5. WhatsApp Business (HMAC Signature & Webhook Verification)
# ==============================================================================

class TestWhatsAppBusinessAdapter:
    def test_webhook_get_challenge_verification(self, personal_store):
        acc = Account(account_id="acc_wa_biz", provider=ProviderType.WHATSAPP_BUSINESS)
        personal_store.save_account(acc)
        adapter = WhatsAppBusinessAdapter(acc)

        from core.security.credentials import store_credential
        store_credential(f"wabiz_{acc.account_id}_verify_token", "jarvis_secret_token")

        # Valid challenge
        resp = adapter.verify_hub_challenge(
            mode="subscribe",
            token="jarvis_secret_token",
            challenge="11559988",
        )
        assert resp == "11559988"

        # Invalid token challenge
        bad_resp = adapter.verify_hub_challenge(
            mode="subscribe",
            token="wrong_token",
            challenge="11559988",
        )
        assert bad_resp is None

    def test_webhook_post_hmac_and_parsing(self, personal_store):
        acc = Account(
            account_id="acc_wa_biz",
            provider=ProviderType.WHATSAPP_BUSINESS,
            metadata={"phone_number_id": "15551234567"},
        )
        personal_store.save_account(acc)
        adapter = WhatsAppBusinessAdapter(acc)

        from core.security.credentials import store_credential
        app_secret = "meta_app_secret_12345"
        store_credential(f"wabiz_{acc.account_id}_secret", app_secret)

        payload_dict = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "id": "wamid.HBgLMTU1NTEyMzQ1NjcVAgASGBQz",
                                        "from": "15559876543",
                                        "timestamp": "1732001000",
                                        "type": "text",
                                        "text": {"body": "Need confirmation for project deadline"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        signature = "sha256=" + hmac.new(app_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

        # Check signature verification
        assert adapter.verify_webhook_signature(payload_bytes, signature) is True
        assert adapter.verify_webhook_signature(payload_bytes, "sha256=invalidsig") is False

        # Parse messages
        messages, statuses = adapter.parse_webhook_payload(payload_dict)
        assert len(messages) == 1
        assert messages[0].body_snippet == "Need confirmation for project deadline"
        assert messages[0].sender == "15559876543"


# ==============================================================================
# 6. WhatsApp Personal (Local Chat Export & wa.me Handoff)
# ==============================================================================

class TestWhatsAppPersonalAdapter:
    def test_chat_export_parser(self, personal_store):
        acc = Account(account_id="acc_wa_pers", provider=ProviderType.WHATSAPP_PERSONAL)
        personal_store.save_account(acc)
        adapter = WhatsAppPersonalAdapter(acc)

        chat_content = (
            "[11/19/26, 2:05:22 PM] Alex: Hey, please send the quarterly report by Friday 5pm.\n"
            "[11/19/26, 2:06:10 PM] Me: Got it, I will finish it tomorrow morning.\n"
            "[11/19/26, 2:07:00 PM] Alex: Great, thanks!\n"
        )

        parsed = adapter.parse_chat_export(chat_content)
        assert len(parsed) == 3
        assert parsed[0].sender == "Alex"
        assert "quarterly report" in parsed[0].body_snippet

    def test_wame_deep_link_generation(self, personal_store):
        acc = Account(account_id="acc_wa_pers", provider=ProviderType.WHATSAPP_PERSONAL)
        adapter = WhatsAppPersonalAdapter(acc)

        url = adapter.generate_handoff_url(phone_number="+1 (555) 019-2834", message_text="Hello from Optimus")
        assert url == "https://wa.me/15550192834?text=Hello%20from%20Optimus"


# ==============================================================================
# 7. Personal Intelligence Engine (Task Extraction, Deduplication, Triage)
# ==============================================================================

class TestPersonalIntelligenceEngine:
    def test_task_extraction_and_importance_triage(self, personal_engine):
        msg = Message(
            message_id="msg_task_01",
            provider=ProviderType.GMAIL,
            account_id="acc_test",
            conversation_id="conv_task_01",
            sender="boss@enterprise.com",
            subject="Urgent: Please submit budget proposal before tomorrow",
            body_snippet="Hi, please submit the updated budget proposal before tomorrow afternoon. Thank you.",
            recipients=["me@work.com"],
        )
        score = personal_engine.score_message_importance(msg)
        assert score >= 0.70
        assert msg.is_important is True

        tasks, commitments = personal_engine.extract_tasks_and_commitments_from_message(msg)
        assert len(tasks) >= 1
        assert "submit" in tasks[0].title.lower() or "budget" in tasks[0].title.lower()
        assert tasks[0].source_ref.record_id == msg.message_id

    def test_conversation_anchored_deduplication_with_completed_exemption(self, personal_engine):
        store = personal_engine.store
        msg = Message(
            message_id="msg_repeat_1",
            provider=ProviderType.GMAIL,
            account_id="acc_test",
            conversation_id="conv_shared_99",
            sender="colleague@work.com",
            subject="Please review code by 5pm",
            body_snippet="Please review code by 5pm today.",
        )

        # 1st Extraction
        tasks_1, _ = personal_engine.extract_tasks_and_commitments_from_message(msg)
        assert len(tasks_1) == 1
        initial_task = tasks_1[0]

        # 2nd Extraction from same conversation -> duplicate detected, no new task added
        tasks_2, _ = personal_engine.extract_tasks_and_commitments_from_message(msg)
        assert len(tasks_2) == 0

        # Now mark initial task as COMPLETED
        initial_task.status = TaskStatus.COMPLETED
        store.save_task(initial_task)

        # 3rd Extraction with completed exemption -> should allow new task since old one is completed
        tasks_3, _ = personal_engine.extract_tasks_and_commitments_from_message(msg)
        assert len(tasks_3) == 1


# ==============================================================================
# 8. PolicyEngine Security & Gating
# ==============================================================================

class TestPolicyEngineEnforcement:
    def test_policy_engine_gates_mutating_personal_tools(self):
        pe = PolicyEngine.instance()

        mutating_tools = [
            ("send_email", {"to": "test@test.com", "subject": "Hi", "body": "Hello"}),
            ("create_calendar_event", {"title": "Meeting", "start_time": 1732000000.0}),
            ("send_whatsapp_message", {"to": "+15551234567", "text": "Hello"}),
            ("purge_personal_store", {}),
        ]

        for tool_name, params in mutating_tools:
            req = OperationRequest(
                tool_name=tool_name,
                agent_name="worker_agent",
                arguments=params,
            )
            verdict = pe.evaluate_operation(req)
            assert not verdict.is_authorized
            assert verdict.requires_approval

        read_tools = [
            ("get_personal_dashboard", {}),
            ("list_unified_inbox", {}),
            ("list_personal_tasks", {}),
            ("get_calendar_view", {}),
        ]
        for tool_name, params in read_tools:
            req = OperationRequest(
                tool_name=tool_name,
                agent_name="worker_agent",
                arguments=params,
            )
            verdict = pe.evaluate_operation(req)
            assert verdict.is_authorized
            assert not verdict.requires_approval

    def test_approval_token_authorizes_mutating_tool_and_prevents_replay(self):
        pe = PolicyEngine.instance()
        params = {"to": "partner@corp.com", "subject": "Contract", "body": "Signed"}
        token = pe.create_approval_token(
            mission_id="m_100",
            subtask_id="sub_1",
            operator_id="operator_human",
            tool_name="send_email",
            arguments=params,
        )

        req = OperationRequest(
            tool_name="send_email",
            agent_name="worker_agent",
            arguments=params,
            approval_token=token,
        )
        verdict = pe.evaluate_operation(req)
        assert verdict.is_authorized
        assert not verdict.requires_approval

        # Replay attempt with same token must be rejected
        req_replay = OperationRequest(
            tool_name="send_email",
            agent_name="worker_agent",
            arguments=params,
            approval_token=token,
        )
        verdict_replay = pe.evaluate_operation(req_replay)
        assert not verdict_replay.is_authorized
        assert "REPLAY" in verdict_replay.reason

    def test_tool_registry_invokes_tools_with_policy_engine(self, personal_store):
        reg = ToolRegistry()
        register_personal_tools(reg, personal_store)

        # Calling read tool works directly
        res = reg.execute("list_personal_tasks", {})
        assert res.status == "done"

        # Calling send_email without approval token returns unverified / APPROVAL_REQUIRED
        res_mut = reg.execute("send_email", {"to": "a@b.com", "subject": "Test", "body": "Body"})
        assert res_mut.status == "unverified"
        assert "APPROVAL_REQUIRED" in res_mut.error


# ==============================================================================
# 9. Dashboard Server Endpoints Integration
# ==============================================================================

class TestDashboardServerPersonalEndpoints:
    def test_dashboard_personal_endpoints(self, personal_store, personal_engine):
        from fastapi.testclient import TestClient

        from dashboard.server import DashboardServer

        server = DashboardServer()
        server.set_personal_engine(personal_engine)

        client = TestClient(server.app)

        # Unauthorized access returns 401
        res = client.get("/api/personal/dashboard")
        assert res.status_code == 401

        # Simulate authenticated session
        token = "test_auth_token_xyz"
        server._tokens[token] = time.time()
        headers = {"Authorization": f"Bearer {token}"}

        # Dashboard Summary
        res = client.get("/api/personal/dashboard", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["ok"]
        assert "summary" in data
        assert data["summary"]["unread_messages"] == 0

        # Inbox
        res_inbox = client.get("/api/personal/inbox", headers=headers)
        assert res_inbox.status_code == 200
        assert res_inbox.json()["ok"]

        # Tasks
        res_tasks = client.get("/api/personal/tasks", headers=headers)
        assert res_tasks.status_code == 200
        assert res_tasks.json()["ok"]

        # Calendar
        res_cal = client.get("/api/personal/calendar", headers=headers)
        assert res_cal.status_code == 200
        assert res_cal.json()["ok"]

        # Accounts
        res_acc = client.get("/api/personal/accounts", headers=headers)
        assert res_acc.status_code == 200
        assert res_acc.json()["ok"]

        # Sync trigger
        res_sync = client.post("/api/personal/sync", headers=headers, json={})
        assert res_sync.status_code == 200
        assert res_sync.json()["ok"]
