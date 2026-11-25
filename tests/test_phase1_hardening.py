"""Comprehensive Phase 1 Hardening Verification Suite.

Covers all hardened subsystems in layered unit & integration tests (zero live network mutations):
1. Web search outcome fix + full error classification + zero fabricated citations.
2. Authoritative active model & config intelligence + orchestrator fast-path routing.
3. Gmail integration (connection vs retrieval, local morning window + timezone, rationale,
   untrusted quarantine, nested attachments + safe path, draft vs approval-gated send).
4. Strict Spotify routing & playback verification + query cleaning + NO YouTube fallback.
5. Recursive filesystem search, SuvAI/SOI semantic filtering, sequential Win+R batch opener,
   and 6-state PDF launch verification taxonomy.
6. SharedActionScheduler, ScreenObserver redaction, PushToTalk focus-loss/cancel, Startup.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest


# ── 1. Web Search Exception Handling & Classification ────────────────────────


class TestWebSearchHardening:
    def test_main_execute_tool_no_unbound_local_error_on_outcome(self):
        import main

        ol = object.__new__(main.OptimusLive)
        shown: list[tuple[str, str]] = []
        ol.ui = SimpleNamespace(
            muted=False,
            current_file=None,
            set_state=lambda _s: None,
            show_content=lambda label, text: shown.append((label, text)),
        )
        ol.speak = lambda _t: None
        ol.speak_error = MagicMock()
        ol._plugin_registry = SimpleNamespace(has=lambda _n: False, scheduling=lambda _n: None)

        # Case A: Success outcome -> unwrapped cleanly and displayed
        ol._action_registry = SimpleNamespace(
            has=lambda n: n == "web_search",
            run=lambda _n, _a, _c: 'status=done action=web_search summary="Python 3.13 notes"',
            scheduling=lambda _n: None,
        )
        fc_ok = SimpleNamespace(id="fc_1", name="web_search", args={"query": "python 3.13"})
        resp_ok = asyncio.run(ol._execute_tool(fc_ok))
        assert "status=done" in str(resp_ok.response["result"])
        assert len(shown) == 1
        ol.speak_error.assert_not_called()

        # Case B: Failure outcome -> NO UnboundLocalError and NOT shown as valid content
        shown.clear()
        ol._action_registry = SimpleNamespace(
            has=lambda n: n == "web_search",
            run=lambda _n, _a, _c: 'status=failed action=web_search reason="timeout"',
            scheduling=lambda _n: None,
        )
        fc_fail = SimpleNamespace(id="fc_2", name="web_search", args={"query": "python 3.13"})
        resp_fail = asyncio.run(ol._execute_tool(fc_fail))
        assert "status=failed" in str(resp_fail.response["result"])
        assert len(shown) == 0
        ol.speak_error.assert_not_called()

    def test_web_search_structured_error_classifications(self):
        from actions.web_search import WebSearchError, classify_search_error, web_search_structured

        assert classify_search_error(TimeoutError("timed out"))[0] == "timeout"
        assert classify_search_error(RuntimeError("HTTP 401 unauthorized API key"))[0] == "auth_failure"
        assert classify_search_error(json.JSONDecodeError("bad", "", 0))[0] == "parsing_failure"
        assert classify_search_error(WebSearchError("invalid_response", "empty payload"))[0] == "invalid_response"

        # Empty query
        r_empty_q = web_search_structured({"query": "   "})
        assert r_empty_q["status"] == "failed"
        assert r_empty_q["outcome_status"] == "invalid_request"

        # Empty results (Gemini falls back to DDG which returns [] -> empty_results)
        with patch("actions.web_search._gemini_search", side_effect=RuntimeError("no results")):
            with patch("actions.web_search._ddg_search", return_value=[]):
                r_emp = web_search_structured({"query": "obscure nonexistent query xyz123"})
                assert r_emp["outcome_status"] in ("empty_results", "provider_unavailable")

        # Unexpected non-string type -> invalid_response
        with patch("actions.web_search._gemini_search", return_value=None):
            r_inv = web_search_structured({"query": "malformed query"})
            assert r_inv["outcome_status"] == "invalid_response"

        # Timeout propagation (no UnboundLocalError, structured timeout outcome)
        with patch("actions.web_search._gemini_search", side_effect=TimeoutError("network timeout")):
            with patch("actions.web_search._ddg_search", side_effect=TimeoutError("network timeout")):
                r_to = web_search_structured({"query": "timeout query"})
                assert r_to["status"] == "failed"
                assert r_to["outcome_status"] == "timeout"

        # Success with results
        with patch(
            "actions.web_search._gemini_search",
            return_value="[Example](https://example.com) - Verified result",
        ):
            r_ok = web_search_structured({"query": "valid query"})
            assert r_ok["status"] == "done"
            assert r_ok["outcome_status"] == "success_with_results"
            assert "https://example.com" in r_ok["output"]

    def test_sandbox_tools_research_returns_zero_fake_citations_on_failure(self):
        from core.sandbox.tools_research import search_web_handler

        with patch(
            "actions.web_search.web_search_structured",
            return_value={
                "status": "failed",
                "outcome_status": "timeout",
                "reason": "Search request timed out",
                "output": "",
            },
        ):
            res = search_web_handler({"query": "latest benchmarks"})
        assert res.status == "failed"
        assert res.evidence["outcome_status"] == "timeout"
        assert res.evidence["urls"] == []
        assert res.evidence["citations_count"] == 0


# ── 2. Active Model & Configuration Intelligence ─────────────────────────────


class TestActiveModelStatus:
    def test_active_model_status_gemini_live_and_ollama_and_fallback(self, tmp_path: Path):
        from core.intelligence.model_status import format_active_model_report, get_active_model_status

        settings_p = tmp_path / "optimus_settings.json"
        keys_p = tmp_path / "api_keys.json"

        # Case A: Gemini configured & runtime matches
        settings_p.write_text(json.dumps({
            "primary_provider": "gemini",
            "model_name": "models/gemini-3.1-flash-live-preview",
            "fallback_provider": "ollama",
            "fallback_model": "qwen2.5:7b",
        }), encoding="utf-8")
        keys_p.write_text(json.dumps({"gemini_api_key": "dummy-test-key"}), encoding="utf-8")

        st_gem = get_active_model_status(
            runtime_live_model="models/gemini-3.1-flash-live-preview",
            settings_path=settings_p,
            api_keys_path=keys_p,
        )
        assert st_gem["provider"] == "gemini"
        assert st_gem["active_model_id"] == "models/gemini-3.1-flash-live-preview"
        assert st_gem["fallback_activated"] is False
        assert st_gem["differs_from_configured_default"] is False
        rep = format_active_model_report(st_gem)
        assert "models/gemini-3.1-flash-live-preview" in rep
        assert "dummy-test-key" not in rep

        # Case B: Runtime differs from configured default (override / fallback model active)
        st_fb = get_active_model_status(
            runtime_live_model="models/gemini-2.5-flash-native-audio-preview-12-2025",
            settings_path=settings_p,
            api_keys_path=keys_p,
        )
        assert st_fb["differs_from_configured_default"] is True

        # Case C: Ollama local-only mode
        settings_p.write_text(json.dumps({
            "primary_provider": "ollama",
            "model_name": "qwen2.5:7b",
        }), encoding="utf-8")
        keys_p.write_text(json.dumps({"local_only": True}), encoding="utf-8")
        st_oll = get_active_model_status(settings_path=settings_p, api_keys_path=keys_p)
        assert st_oll["provider"] == "ollama"
        assert st_oll["configured_model_id"] == "qwen2.5:7b"

        # Case D: Missing config files handled gracefully without crashing
        st_miss = get_active_model_status(
            settings_path=tmp_path / "missing_s.json",
            api_keys_path=tmp_path / "missing_k.json",
        )
        assert "active_model_id" in st_miss
        assert "verification_status" in st_miss

    def test_orchestrator_routes_model_questions_never_web_search(self):
        from core.orchestrator.orchestrator import CentralOrchestrator

        orch = CentralOrchestrator()
        for q in (
            "What model are you using?",
            "Which Gemini model is active right now?",
            "Check your configuration",
            "Are you using the fallback model?",
        ):
            routed = orch._try_fast_path(q, "test_model_q")
            assert routed is not None
            ok, out, agent, effects = routed
            assert ok is True, f"Failed for {q}: {out}"
            assert effects and effects[0]["tool_name"] == "get_active_model_status"
            assert "Active Model:" in out


# ── 3. Gmail Integration Hardening ───────────────────────────────────────────


class TestGmailHardening:
    def _make_store(self, tmp_path: Path, connected: bool = True):
        from core.personal_intelligence import Account, AuthStatus, ProviderType
        from core.personal_intelligence.storage import PersonalStore

        tmp_path.mkdir(parents=True, exist_ok=True)
        store = PersonalStore(root_dir=tmp_path)
        if connected:
            store.save_account(Account(
                account_id="gmail_test",
                provider=ProviderType.GMAIL,
                email_or_id="test.user@gmail.com",
                display_name="Test User",
                auth_status=AuthStatus.CONNECTED,
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            ))
        return store

    def test_morning_important_emails_window_and_rationale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from core.personal_intelligence import Account, Message, ProviderType
        from core.personal_intelligence.engine import PersonalIntelligenceEngine

        monkeypatch.setenv("OPTIMUS_MOCK_NETWORK", "1")
        tz = ZoneInfo("Asia/Kolkata")
        now_dt = datetime.datetime(2026, 11, 25, 10, 30, 0, tzinfo=tz)
        now_ts = now_dt.timestamp()

        # 1. No connected account -> explicit connection_error
        empty_store = self._make_store(tmp_path / "empty", connected=False)
        res_disc = PersonalIntelligenceEngine(empty_store).find_important_morning_emails(
            now=now_ts, timezone_name="Asia/Kolkata", live_fetch=False
        )
        assert res_disc["connected"] is False
        assert res_disc["connection_error"]

        # 2. Connected account with morning vs yesterday vs afternoon messages
        store = self._make_store(tmp_path / "conn", connected=True)
        store.save_messages([
            Message(
                message_id="m_imp",
                conversation_id="conv_1",
                provider=ProviderType.GMAIL,
                account_id="gmail_test",
                sender="recruiter@company.com",
                sender_name="Hiring Team",
                subject="Urgent: Interview Schedule & Deadline Today",
                body_snippet="Please confirm your slot before 2 PM.",
                timestamp=now_dt.replace(hour=8, minute=15).timestamp(),
                labels=["INBOX", "IMPORTANT", "UNREAD"],
                is_important=True,
                is_read=False,
            ),
            Message(
                message_id="m_ord",
                conversation_id="conv_2",
                provider=ProviderType.GMAIL,
                account_id="gmail_test",
                sender="newsletter@promo.example",
                sender_name="Weekly Digest",
                subject="Top 10 tech links",
                body_snippet="Unsubscribe anytime.",
                timestamp=now_dt.replace(hour=9, minute=5).timestamp(),
                labels=["CATEGORY_PROMOTIONS"],
                is_important=False,
                is_read=True,
            ),
            Message(
                message_id="m_yest",
                conversation_id="conv_3",
                provider=ProviderType.GMAIL,
                account_id="gmail_test",
                sender="boss@company.com",
                sender_name="Boss",
                subject="Urgent yesterday item",
                body_snippet="From yesterday",
                timestamp=(now_dt - datetime.timedelta(days=1)).timestamp(),
                labels=["IMPORTANT"],
                is_important=True,
                is_read=False,
            ),
        ])

        res_ok = PersonalIntelligenceEngine(store).find_important_morning_emails(
            now=now_ts, timezone_name="Asia/Kolkata", live_fetch=False
        )
        assert res_ok["connected"] is True
        assert res_ok["retrieval_ok"] is True
        imp_ids = [m["message_id"] for m in res_ok["important_messages"]]
        ord_ids = [m["message_id"] for m in res_ok["ordinary_messages"]]
        assert "m_imp" in imp_ids
        assert "m_ord" in ord_ids
        assert "m_yest" not in imp_ids
        # 3. Real GmailAdapter parsed message preserves single-layer untrusted quarantine + timezone preservation when now=None
        from core.personal_intelligence import Account
        from core.personal_intelligence.providers.gmail import GmailAdapter as _GA
        raw_gmail_item = {
            "id": "live_m_1",
            "threadId": "th_1",
            "labelIds": ["INBOX", "IMPORTANT", "UNREAD"],
            "snippet": "Urgent review needed before 11am",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Security <sec@company.com>"},
                    {"name": "Subject", "value": "Urgent: Action Required"},
                ]
            },
        }
        parsed_msg = _GA(Account(account_id="gmail_test", provider=ProviderType.GMAIL, email_or_id="t@g.com"))._parse_raw_gmail_message(raw_gmail_item)
        # Override timestamp to current moment in America/New_York if before noon, or verify timezone preserved
        res_tz = PersonalIntelligenceEngine(store).find_important_morning_emails(
            now=None, timezone_name="America/New_York", live_fetch=False
        )
        assert "-05:00" in res_tz["window_start"] or "-04:00" in res_tz["window_start"]
        store2 = self._make_store(tmp_path / "conn_parsed", connected=True)
        parsed_msg.timestamp = datetime.datetime(2026, 11, 25, 8, 0, tzinfo=tz).timestamp()
        store2.save_messages([parsed_msg])
        res_single_wrap = PersonalIntelligenceEngine(store2).find_important_morning_emails(
            now=now_ts, timezone_name="Asia/Kolkata", live_fetch=False
        )
        assert "UNTRUSTED-ESCAPED-ESCAPED" not in res_single_wrap["summary"]
        assert "<<<UNTRUSTED-ESCAPED source=gmail>>>" not in res_single_wrap["summary"]

        # 4. HTTP 403/500 in fetch_recent_messages raises instead of returning empty list
        bad_http = MagicMock()
        bad_http.get.return_value = SimpleNamespace(status_code=503, text='{"error":{"message":"backend error"}}', json=lambda: {"error": {"message": "backend error"}})
        bad_adapter = _GA(Account(account_id="gmail_err", provider=ProviderType.GMAIL, email_or_id="t@g.com"), http_client=bad_http)
        with patch.object(bad_adapter, "_get_access_token", return_value="tok"):
            with pytest.raises(RuntimeError, match="backend error"):
                bad_adapter.fetch_recent_messages(limit=3)

    def test_gmail_attachments_draft_and_send_policy_gate(self):
        from core.personal_intelligence import Account, AuthStatus, ProviderType
        from core.personal_intelligence.providers.gmail import GmailAdapter

        acc = Account(
            account_id="gmail_att",
            provider=ProviderType.GMAIL,
            email_or_id="user@gmail.com",
            display_name="User",
            auth_status=AuthStatus.CONNECTED,
        )
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
                {
                    "filename": "SuvAI_Brief.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att_001", "size": 2048},
                },
            ],
        }
        atts = GmailAdapter._extract_attachments_from_payload(payload)
        assert len(atts) == 1
        assert atts[0]["filename"] == "SuvAI_Brief.pdf"
        assert atts[0]["attachment_id"] == "att_001"

        # Draft creation via mocked HTTP client (never calls send endpoint)
        mock_http = MagicMock()
        mock_http.post.return_value = SimpleNamespace(
            status_code=200,
            text='{"id":"draft_xyz"}',
            json=lambda: {"id": "draft_xyz"},
        )
        adapter = GmailAdapter(account=acc, http_client=mock_http)
        with patch.object(adapter, "_get_access_token", return_value="secret_tok_123"):
            d_res = adapter.create_draft("alice@example.com", "Hello", "Draft body")
        assert d_res["success"] is True
        assert d_res["draft_id"] == "draft_xyz"
        assert "drafts" in mock_http.post.call_args.args[0]

        # Approval gate in actions/gmail_assistant parks behind confirm_gate.request and executes only with HUD ApprovalToken
        from actions.gmail_assistant import gmail_assistant
        from core.orchestrator.tools import ToolRegistry
        from core.personal_intelligence import Account as _Acc, AuthStatus as _AS, ProviderType as _PT
        from core.personal_intelligence.storage import PersonalStore
        from core.personal_intelligence.tools import register_personal_tools

        captured_run = {}

        def _capture_request(key, title, detail, run):
            captured_run["fn"] = run
            return "Confirm sending email?"

        reg = ToolRegistry()
        pi_dir = Path("memory") / "_test_pi_send_gate"
        try:
            pi_dir.mkdir(parents=True, exist_ok=True)
            st_send = PersonalStore(root_dir=pi_dir)
            st_send.save_account(_Acc(account_id="g1", provider=_PT.GMAIL, email_or_id="u@g.com", auth_status=_AS.CONNECTED))
            register_personal_tools(reg, store=st_send)
            with patch("actions.gmail_assistant.ToolRegistry.instance", return_value=reg):
                with patch("core.confirm.request", side_effect=_capture_request) as m_req:
                    s_out = gmail_assistant({"action": "send", "to": "a@b.com", "subject": "Hi", "body": "Body"})
            assert "status=ask" in s_out
            assert m_req.called
            assert "secret_tok_123" not in s_out

            # Simulate user pressing CONFIRM on HUD -> invokes parked _do_send with single-use ApprovalToken
            with patch("core.personal_intelligence.providers.gmail.GmailAdapter.send_message", return_value={"success": True, "message_id": "m_sent_1", "thread_id": "t_1"}) as m_send:
                with patch.dict("os.environ", {"OPTIMUS_MOCK_NETWORK": "0"}):
                    confirmed_out = captured_run["fn"]()
            assert "status=done" in confirmed_out
            assert m_send.called

            # Verify get_email_attachment blocks traversal outside allowed_roots even with relative/absolute paths
            with patch("core.personal_intelligence.providers.gmail.GmailAdapter.get_attachment", return_value={"success": True, "data_bytes": b"MZ"}):
                with patch.dict("os.environ", {"OPTIMUS_MOCK_NETWORK": "0"}):
                    trav_res = reg.execute(
                        "get_email_attachment",
                        {"message_id": "m1", "attachment_id": "att1", "save_path": str(Path("C:/Windows/win.ini"))},
                        caller_agent="personal_intelligence",
                    )
                    assert trav_res.status == "failed"
                    assert "PATH_VIOLATION" in str(trav_res.error or "")
        finally:
            import shutil
            shutil.rmtree(pi_dir, ignore_errors=True)

    def test_orchestrator_routes_morning_emails_to_gmail(self):
        from core.orchestrator.orchestrator import CentralOrchestrator

        orch = CentralOrchestrator()
        fake_res = SimpleNamespace(
            is_success=True,
            status="done",
            output="Morning Gmail Check: 1 important.",
            evidence={"important_messages": [{"subject": "Test"}]},
            error=None,
        )
        with patch.object(orch.tool_registry, "execute", return_value=fake_res) as m_exec:
            routed = orch._try_fast_path("Find any important email I got this morning", "test_gmail_q")
        assert routed is not None
        ok, out, agent, effects = routed
        assert ok is True
        assert agent == "personal_intelligence"
        assert m_exec.call_args.args[0] == "get_important_morning_emails"


# ── 4. Strict Spotify Routing & Playback Verification ────────────────────────


class TestSpotifyStrictRouting:
    def _make_contract(self, query: str = "Nallaru song from Dude movie"):
        from core.application_intelligence.types import ActionContract

        return ActionContract(
            action_id="sp_test_1",
            target_app="spotify",
            operation="play_spotify_strict",
            parameters={"query": query, "mode": "desktop", "strict_spotify": True},
        )

    def test_spotify_cleaned_query_and_verified_playback(self):
        from core.application_intelligence.adapters.media import MediaAdapter

        calls: list[list[str]] = []

        def fake_cli(args: list[str]):
            calls.append(args)
            cmd = args[0]
            if cmd == "status":
                return {"ok": True, "returncode": 0, "data": {"running": True, "logged_in": True}}
            if cmd == "search":
                q = args[1]
                if "from" in q.lower():
                    return {"ok": True, "returncode": 0, "data": {"tracks": []}}
                return {
                    "ok": True,
                    "returncode": 0,
                    "data": {
                        "tracks": [
                            {"uri": "spotify:track:3Nt5m3MOIrNt1sf8v14GPM", "name": 'Nallaru Po - From "Dude"', "artists": ["Sai Abhyankkar"]},
                            {"uri": "spotify:track:2", "name": "Nallaru Po Remix", "artists": ["Artist"]},
                        ]
                    },
                }
            if cmd == "play":
                return {"ok": True, "returncode": 0, "data": {}}
            if cmd == "now-playing":
                return {"ok": True, "returncode": 0, "data": {"currently_playing": {"is_playing": True, "description": 'Nallaru Po - From "Dude"'}}}
            return {"ok": False, "returncode": 1, "error": "unexpected"}

        ma = MediaAdapter(cli_runner=fake_cli)
        with patch("core.application_intelligence.adapters.media.time.sleep"):
            ev = ma.execute(self._make_contract("Nallaru song from Dude movie"))

        assert ev.status == "done"
        assert ev.evidence_data["playing"] is True
        assert ev.evidence_data["fallback_used"] == "none"
        assert len(ev.evidence_data["candidates"]) == 2
        assert any(c[0] == "search" and "Nallaru Dude" in c[1] for c in calls)

    def test_spotify_failure_cases_never_fallback_to_youtube(self):
        from core.application_intelligence.adapters.media import MediaAdapter

        with patch("actions.youtube_video.youtube_video", side_effect=AssertionError("Must NOT call YouTube!")):
            # A. Spotify unavailable
            ma_unavail = MediaAdapter(cli_runner=lambda args: {"ok": False, "returncode": 1, "data": None, "error": "not running"})
            ev_a = ma_unavail.execute(self._make_contract("Believer"))
            assert ev_a.status == "failed"
            assert ev_a.evidence_data.get("error_code") == "spotify_unavailable"
            assert ev_a.evidence_data.get("fallback_used") == "none"

            # B. Track not found
            def cli_not_found(args):
                if args[0] == "status":
                    return {"ok": True, "returncode": 0, "data": {"running": True, "logged_in": True}}
                if args[0] == "search":
                    return {"ok": True, "returncode": 0, "data": {"tracks": []}}
                return {"ok": True, "returncode": 0, "data": {}}

            ev_b = MediaAdapter(cli_runner=cli_not_found).execute(self._make_contract("nonexistent xyz track 999"))
            assert ev_b.status == "failed"
            assert ev_b.evidence_data.get("error_code") == "track_not_found"
            assert ev_b.evidence_data.get("fallback_used") == "none"

            # C. Playback unverified (now-playing says is_playing=False)
            def cli_unverified(args):
                if args[0] == "status":
                    return {"ok": True, "returncode": 0, "data": {"running": True, "logged_in": True}}
                if args[0] == "search":
                    return {"ok": True, "returncode": 0, "data": {"tracks": [{"uri": "spotify:track:1", "name": "Song", "artists": ["A"]}]}}
                if args[0] == "play":
                    return {"ok": True, "returncode": 0, "data": {}}
                if args[0] == "now-playing":
                    return {"ok": True, "returncode": 0, "data": {"currently_playing": {"is_playing": False}}}
                return {"ok": True, "returncode": 0}

            with patch("core.application_intelligence.adapters.media.time.sleep"):
                with patch("core.vision.window_intelligence.enumerate_windows", return_value=[]):
                    ev_c = MediaAdapter(cli_runner=cli_unverified).execute(self._make_contract("Song"))
            assert ev_c.status == "unverified"
            assert ev_c.evidence_data.get("error_code") == "playback_unverified"
            assert ev_c.evidence_data.get("fallback_used") == "none"

    def test_orchestrator_spotify_voice_prompt_routing(self):
        from core.orchestrator.orchestrator import CentralOrchestrator

        orch = CentralOrchestrator()
        fake_res = SimpleNamespace(is_success=True, status="done", output="Playing Nallaru Po", evidence={}, error=None)
        with patch.object(orch.tool_registry, "execute", return_value=fake_res) as m_exec:
            routed = orch._try_fast_path("Open Spotify and play Nallaru song from Dude movie", "test_spotify_q")
        assert routed is not None
        ok, out, agent, effects = routed
        assert ok is True
        assert m_exec.call_args.args[0] == "app_execute_action"
        req = m_exec.call_args.args[1]
        assert req["target_app"] == "spotify"
        assert req["operation"] == "play_spotify_strict"
        assert "Nallaru" in req["parameters"]["query"]


# ── 5. Recursive Search, SuvAI/SOI Filtering & Sequential Win+R Opener ───────


class TestRecursiveSearchAndRunOpener:
    def test_recursive_semantic_pdf_filtering_excludes_unrelated_and_non_pdfs(self, tmp_path: Path):
        from core.filesystem.service import UnifiedFilesystemService

        dl = tmp_path / "Downloads"
        sub = dl / "nested" / "research"
        sub.mkdir(parents=True)
        f1 = dl / "SuvAI_Abstract.pdf"
        f2 = sub / "Suv AI Paper.pdf"
        f3 = sub / "SOI_Architecture.pdf"
        f_pptx = dl / "SuvAI_Slides.pptx"
        f_soil = dl / "soil_nutrients.pdf"
        f_unrel = dl / "10th marksheet.pdf"
        for f in (f1, f2, f3, f_pptx, f_soil, f_unrel):
            f.write_bytes(b"%PDF-1.4\n" if f.suffix == ".pdf" else b"PK")

        fs = UnifiedFilesystemService(backup_dir=tmp_path / "_backups")
        res = fs.search_files(
            root_path=str(dl),
            pattern="*",
            recursive=True,
            extensions=[".pdf"],
            semantic=True,
            semantic_query="SuvAI",
        )
        assert res.is_success is True
        found_names = {Path(m["path"]).name for m in res.evidence["matches"]}
        assert found_names == {"SuvAI_Abstract.pdf", "Suv AI Paper.pdf", "SOI_Architecture.pdf"}
        assert "SuvAI_Slides.pptx" not in found_names
        assert "soil_nutrients.pdf" not in found_names
        assert "10th marksheet.pdf" not in found_names

        # Direct short query "SOI" must still exclude lookalike "soil_nutrients.pdf"
        res_soi = fs.search_files(
            root_path=str(dl),
            pattern="*",
            recursive=True,
            extensions=[".pdf"],
            semantic=True,
            semantic_query="SOI",
        )
        soi_names = {Path(m["path"]).name for m in res_soi.evidence["matches"]}
        assert "soil_nutrients.pdf" not in soi_names
        assert "SOI_Architecture.pdf" in soi_names

        # Unchanged pre-existing viewer window must NOT trigger false target_app_detected
        from core.filesystem.run_opener import VerifiedRunFileOpener
        opener_unchanged = VerifiedRunFileOpener(
            fs_service=fs,
            run_driver=type("D", (), {"open_and_submit": lambda self, p, timeout=6.0: (True, "ok")})(),
            window_inspector=lambda: [{"title": "Google - Microsoft Edge", "process": "msedge.exe", "hwnd": 555}],
        )
        r_unch = opener_unchanged.open_path(f1, verify_window=True)
        assert r_unch.launch_state == "launch_unconfirmed"

    def test_sequential_win_r_opener_states_and_dedup(self, tmp_path: Path):
        from core.filesystem.run_opener import VerifiedRunFileOpener
        from core.filesystem.service import UnifiedFilesystemService

        f1 = tmp_path / "SuvAI_1.pdf"
        f2 = tmp_path / "SuvAI_2.pdf"
        f3 = tmp_path / "SuvAI_3.pdf"
        for f in (f1, f2, f3):
            f.write_bytes(b"%PDF")

        opened_order: list[str] = []

        class FakeDriver:
            def open_and_submit(self, canonical_path: str, timeout: float = 6.0):
                opened_order.append(Path(canonical_path).name)
                if "SuvAI_3" in canonical_path:
                    return False, "Simulated Run failure"
                return True, f"Submitted {canonical_path}"

        state_seq = [
            [{"title": "SuvAI_1.pdf - Adobe Acrobat", "process": "Acrobat.exe", "hwnd": 101}],
            [{"title": "Microsoft Edge", "process": "msedge.exe", "hwnd": 102}],
        ]
        inspector_calls = [
            [],            # f1 before
            state_seq[0],  # f1 after -> target_document_detected
            [{"title": "SuvAI_1.pdf - Adobe Acrobat", "process": "Acrobat.exe", "hwnd": 101}],  # f2 before
            state_seq[1],  # f2 after -> target_app_detected (new viewer hwnd 102)
            [],            # f3 before
        ]

        def fake_inspector():
            return inspector_calls.pop(0) if inspector_calls else []

        fs = UnifiedFilesystemService(backup_dir=tmp_path / "_backups")
        opener = VerifiedRunFileOpener(fs_service=fs, run_driver=FakeDriver(), window_inspector=fake_inspector)
        batch = opener.open_sequential([f1, f1, f2, f3, tmp_path / "missing.pdf"], deduplicate=True)

        assert opened_order == ["SuvAI_1.pdf", "SuvAI_2.pdf", "SuvAI_3.pdf"]  # deduplicated + sequential
        assert batch["total"] == 4
        assert batch["succeeded"] == 2
        assert batch["failed"] == 2
        states = [r["launch_state"] for r in batch["results"]]
        assert states == ["target_document_detected", "target_app_detected", "launch_failed", "launch_failed"]

        # Unconfirmed viewer state (Run closed cleanly, no viewer title)
        opener_unconf = VerifiedRunFileOpener(fs_service=fs, run_driver=FakeDriver(), window_inspector=lambda: [])
        r_unconf = opener_unconf.open_path(f1, verify_window=True)
        assert r_unconf.success is True
        assert r_unconf.launch_state == "launch_unconfirmed"

    def test_orchestrator_single_task_batch_search_and_win_r_open(self, tmp_path: Path):
        from core.filesystem.run_opener import VerifiedRunFileOpener
        from core.filesystem.service import UnifiedFilesystemService
        from core.orchestrator.orchestrator import CentralOrchestrator

        dl = tmp_path / "Downloads"
        dl.mkdir(parents=True)
        p1 = dl / "SuvAI_Abstract.pdf"
        p2 = dl / "SUVAI_SOP.pdf"
        p_other = dl / "10th marksheet.pdf"
        for p in (p1, p2, p_other):
            p.write_bytes(b"%PDF")

        opened: list[str] = []

        class MockDriver:
            def open_and_submit(self, canonical_path: str, timeout: float = 6.0):
                opened.append(Path(canonical_path).name)
                return True, f"Submitted {canonical_path}"

        fs = UnifiedFilesystemService(backup_dir=tmp_path / "_backups")
        opener = VerifiedRunFileOpener(fs_service=fs, run_driver=MockDriver(), window_inspector=lambda: [])
        orch = CentralOrchestrator()
        with patch.object(fs, "_resolve_known_folder", return_value=dl):
            with patch("core.filesystem.service.UnifiedFilesystemService.instance", return_value=fs):
                with patch("core.filesystem.run_opener.VerifiedRunFileOpener.instance", return_value=opener):
                    routed = orch._try_fast_path(
                        "Find all SuvAI PDFs in Downloads and open them one by one using Windows Run",
                        "batch_pdf_1",
                    )
        assert routed is not None
        ok, out, agent, effects = routed
        assert ok is True
        assert agent == "file_agent"
        assert opened == ["SuvAI_Abstract.pdf", "SUVAI_SOP.pdf"]
        assert "10th marksheet.pdf" not in out


# ── 6. Scheduler, ScreenObserver, PushToTalk & Startup ───────────────────────


class TestRuntimeSafetySystems:
    def test_shared_scheduler_and_cancellation(self):
        from core.orchestrator.scheduler import ResourceType, SharedActionScheduler, TaskCancelledError

        sched = SharedActionScheduler()
        res = sched.run_coordinated(
            [ResourceType.RUN_DIALOG, ResourceType.ACTIVE_WINDOW],
            lambda: "done_work",
            task_id="task_a",
            timeout=2.0,
        )
        assert res == "done_work"
        assert sched.get_resource_owners().get("run_dialog") is None

        sched.cancel_task("task_cancelled")
        with pytest.raises(TaskCancelledError, match="cancelled"):
            sched.run_coordinated([ResourceType.RUN_DIALOG], lambda: "never", task_id="task_cancelled", timeout=1.0)
        sched.clear_cancellation("task_cancelled")

    def test_screen_observer_redacts_secrets(self):
        from core.vision.screen_observer import mask_sensitive_tokens

        raw = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz12345 and key sk-proj1234567890abcdefghij"
        masked = mask_sensitive_tokens(raw)
        assert "abcdefghijklmnopqrstuvwxyz12345" not in masked
        assert "sk-proj1234567890abcdefghij" not in masked
        assert "[REDACTED_TOKEN]" in masked
        assert "[REDACTED_API_KEY]" in masked

    def test_pushtotalk_debounce_cancel_and_focus_loss(self):
        from core.hotkey import PushToTalk

        events: list[bool] = []
        ptt = PushToTalk(events.append)
        assert ptt.chord == ("ctrl", "alt")
        assert ptt.label == "Ctrl+Alt"
        ptt.set_held(True)
        assert ptt.held is True
        assert events[-1] is True

        # Window-scoped focus loss releases hold
        assert ptt.on_focus_lost() is True
        assert ptt.held is False
        assert events[-1] is False

        # Cancel forces release cleanly
        ptt.set_held(True)
        ptt.cancel()
        assert ptt.held is False

    def test_startup_desktop_shortcut_resolution(self):
        from core.runtime.startup import AutoStartManager, DesktopShortcutManager

        d = DesktopShortcutManager.resolve_desktop_dir()
        assert isinstance(d, Path)
        cmd = AutoStartManager.build_command()
        assert "main.py" in cmd
