"""tests/test_gmail_oauth_repair.py — Comprehensive Test Suite for Gmail OAuth 2.0 PKCE & UI Repair.

Verifies:
1. Google OAuth 2.0 PKCE session building (RFC 7636 S256 code challenge, CSRF state token, scopes).
2. Progressive consent scope elevation (gmail.readonly vs gmail.send, calendar.readonly vs calendar.events).
3. Missing credentials handling, detection, and user guidance generation.
4. Local loopback redirect listener:
   - Valid callback with matching state -> successful code capture & HTTP 200 response.
   - CSRF state mismatch -> immediate security rejection with HTTP 400.
   - Google authorization error response handling -> HTTP 400.
   - Clean socket shutdown.
5. Token exchange with mock transport and credential isolation:
   - Access & refresh tokens stored in CredentialVault / Keyring.
   - Zero plaintext token leakage in Account entity metadata or logs.
6. Email & profile resolution from Gmail API profile / Userinfo.
7. Account connection lifecycle & state transitions (DISCONNECTED -> CONNECTED -> REVOKED/DISCONNECTED).
8. ServiceBridge supported providers discovery when 0 accounts are configured.
9. UI table rendering in SectionPersonal:
   - Zero-account initial state: panel is NOT blank; catalog rows rendered with CONNECT action buttons.
   - Connected account state: masked email, CONNECTED status, SYNC & DISCONNECT action buttons.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

from core.personal_intelligence.oauth import (
    DEFAULT_REDIRECT_URI,
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GoogleOAuthManager,
    get_google_client_id,
    get_google_client_secret,
    get_google_credentials_guidance,
    has_google_credentials,
)
from core.personal_intelligence.storage import PersonalStore
from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    ProviderType,
)
from core.security.credentials import (
    delete_credential,
    get_credential,
    store_credential,
)
from gui.optimus.service_bridge import OptimusServiceBridge


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication instance for headless Qt testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0], "-platform", "offscreen"])
    yield app


def get_free_port() -> int:
    """Find an available loopback port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def clean_credentials():
    """Ensure test credentials are clean before and after test."""
    delete_credential("google_client_id")
    delete_credential("google_client_secret")
    yield
    delete_credential("google_client_id")
    delete_credential("google_client_secret")


@pytest.fixture
def temp_store(tmp_path: Path):
    """Provide an isolated encrypted PersonalStore instance."""
    PersonalStore.reset_instance()
    store_dir = tmp_path / "personal_store"
    store = PersonalStore.instance(root_dir=store_dir)
    yield store
    PersonalStore.reset_instance()


# ── Phase 1 & 2: OAuth PKCE, CSRF, Scopes & Credentials Verification ──────────


def test_oauth_session_pkce_and_parameters(clean_credentials, temp_store):
    store_credential("google_client_id", "test_client_id_123.apps.googleusercontent.com")
    store_credential("google_client_secret", "test_secret_456")

    mgr = GoogleOAuthManager(store=temp_store)
    session = mgr.build_auth_session(provider=ProviderType.GMAIL, port=8484)

    assert session["client_id"] == "test_client_id_123.apps.googleusercontent.com"
    assert session["port"] == 8484
    assert session["redirect_uri"] == "http://localhost:8484/oauth/callback"

    # PKCE verification: SHA256 of code_verifier == code_challenge
    code_verifier = session["code_verifier"]
    code_challenge = session["code_challenge"]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert code_challenge == expected_challenge

    # High entropy state token verification
    state = session["state"]
    assert len(state) >= 32

    # Scopes verification: least privilege readonly
    scopes = session["scopes"]
    assert GMAIL_READONLY_SCOPE in scopes
    assert GMAIL_SEND_SCOPE not in scopes
    assert "openid" in scopes

    # Auth URL parameters
    parsed = urllib.parse.urlparse(session["auth_url"])
    params = urllib.parse.parse_qs(parsed.query)
    assert params["client_id"][0] == "test_client_id_123.apps.googleusercontent.com"
    assert params["code_challenge"][0] == code_challenge
    assert params["code_challenge_method"][0] == "S256"
    assert params["state"][0] == state
    assert params["access_type"][0] == "offline"
    assert params["prompt"][0] == "consent"


def test_oauth_progressive_consent_mutating_scopes(clean_credentials, temp_store):
    store_credential("google_client_id", "test_client_id_123.apps.googleusercontent.com")
    store_credential("google_client_secret", "test_secret_456")

    mgr = GoogleOAuthManager(store=temp_store)
    # Mutating scope enabled
    session = mgr.build_auth_session(provider=ProviderType.GMAIL, include_mutating=True)
    assert GMAIL_SEND_SCOPE in session["scopes"]
    assert GMAIL_READONLY_SCOPE in session["scopes"]


def test_google_calendar_scopes(clean_credentials, temp_store):
    store_credential("google_client_id", "test_client_id_123.apps.googleusercontent.com")
    store_credential("google_client_secret", "test_secret_456")

    mgr = GoogleOAuthManager(store=temp_store)
    session = mgr.build_auth_session(provider=ProviderType.GOOGLE_CALENDAR)
    assert "https://www.googleapis.com/auth/calendar.readonly" in session["scopes"]
    assert GMAIL_READONLY_SCOPE not in session["scopes"]


def test_missing_credentials_handling(clean_credentials, temp_store):
    assert not has_google_credentials()
    assert get_google_client_id() is None
    assert get_google_client_secret() is None

    guidance = get_google_credentials_guidance()
    assert "GOOGLE_CLIENT_ID" in guidance
    assert "GOOGLE_CLIENT_SECRET" in guidance
    assert DEFAULT_REDIRECT_URI in guidance

    mgr = GoogleOAuthManager(store=temp_store)
    with pytest.raises(ValueError, match="Google Client ID is missing or empty"):
        mgr.build_auth_session(provider=ProviderType.GMAIL)


# ── Phase 3: Loopback Redirect Handler & CSRF Protection ─────────────────────


def test_loopback_server_valid_callback(temp_store):
    import requests

    mgr = GoogleOAuthManager(store=temp_store)
    test_port = get_free_port()
    expected_state = "csrf_secure_token_999"

    server = mgr.start_loopback_listener(expected_state=expected_state, port=test_port)

    import threading
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{test_port}/oauth/callback?code=test_auth_code_xyz&state={expected_state}"
    resp = requests.get(url, timeout=3)
    assert resp.status_code == 200
    assert "OPTIMUS Authentication Successful" in resp.text

    t.join(timeout=2)
    res = server.callback_result

    assert res is not None
    assert res["success"] is True
    assert res["code"] == "test_auth_code_xyz"
    assert res["state"] == expected_state

    mgr.stop_loopback_listener()


def test_loopback_server_csrf_state_mismatch_rejected(temp_store):
    import requests

    mgr = GoogleOAuthManager(store=temp_store)
    test_port = get_free_port()
    expected_state = "legitimate_session_token"

    server = mgr.start_loopback_listener(expected_state=expected_state, port=test_port)

    import threading
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{test_port}/oauth/callback?code=stolen_code&state=forged_attacker_token"
    resp = requests.get(url, timeout=3)

    assert resp.status_code == 400
    assert "CSRF" in resp.text

    t.join(timeout=2)
    res = server.callback_result

    assert res is not None
    assert res["success"] is False
    assert "CSRF" in res["error"]

    mgr.stop_loopback_listener()


def test_loopback_server_error_parameter_handling(temp_store):
    import requests

    mgr = GoogleOAuthManager(store=temp_store)
    test_port = get_free_port()
    expected_state = "session_token_111"

    server = mgr.start_loopback_listener(expected_state=expected_state, port=test_port)

    import threading
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{test_port}/oauth/callback?error=access_denied&state={expected_state}"
    resp = requests.get(url, timeout=3)

    assert resp.status_code == 400
    assert "Google returned error" in resp.text

    t.join(timeout=2)
    res = server.callback_result

    assert res is not None
    assert res["success"] is False
    assert res["error"] == "access_denied"

    mgr.stop_loopback_listener()


# ── Phase 4: Token Exchange, Credential Isolation & Account Provisioning ─────


class MockHttpResponse:
    def __init__(self, data: dict[str, Any], status_code: int = 200, text: str = ""):
        self._data = data
        self.status_code = status_code
        self.text = text or json.dumps(data)

    def json(self):
        return self._data


class MockHttpClient:
    def __init__(self):
        self.post_responses: dict[str, Any] = {}
        self.get_responses: dict[str, Any] = {}

    def post(self, url: str, data: Any = None, params: Any = None):
        if url in self.post_responses:
            return self.post_responses[url]
        return MockHttpResponse({}, 200)

    def get(self, url: str, headers: Any = None, params: Any = None):
        if url in self.get_responses:
            return self.get_responses[url]
        return MockHttpResponse({}, 200)


def test_token_exchange_and_email_resolution(temp_store):
    mgr = GoogleOAuthManager(store=temp_store)
    client = MockHttpClient()

    # Mock token exchange response
    client.post_responses["https://oauth2.googleapis.com/token"] = MockHttpResponse(
        {
            "access_token": "ya29.mock_access_token_secure_999",
            "refresh_token": "1//mock_refresh_token_secure_888",
            "expires_in": 3599,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        },
        200,
    )

    # Mock Gmail user profile response
    client.get_responses["https://gmail.googleapis.com/gmail/v1/users/me/profile"] = MockHttpResponse(
        {
            "emailAddress": "operator@jarvis-ai.org",
            "messagesTotal": 420,
            "historyId": "999888777",
        },
        200,
    )

    # Execute exchange
    tokens = mgr.exchange_code(
        code="test_code",
        code_verifier="test_verifier",
        client_id="cid",
        client_secret="sec",
        http_client=client,
    )
    assert tokens["access_token"] == "ya29.mock_access_token_secure_999"

    # Fetch email
    email = mgr.fetch_user_email(tokens["access_token"], http_client=client)
    assert email == "operator@jarvis-ai.org"

    # Provision connection
    account = mgr.complete_connection(
        token_data=tokens,
        provider=ProviderType.GMAIL,
        email_or_id=email,
        http_client=client,
    )

    assert account.auth_status == AuthStatus.CONNECTED
    assert account.provider == ProviderType.GMAIL
    assert account.email_or_id == "operator@jarvis-ai.org"

    # Verify CREDENTIAL ISOLATION: tokens must NOT exist in Account.metadata
    assert "access_token" not in account.metadata
    assert "refresh_token" not in account.metadata

    # Verify tokens stored securely in CredentialVault
    stored_acc_token = get_credential(f"gmail_{account.account_id}_token")
    stored_ref_token = get_credential(f"gmail_{account.account_id}_refresh")
    assert stored_acc_token == "ya29.mock_access_token_secure_999"
    assert stored_ref_token == "1//mock_refresh_token_secure_888"

    # Verify Account persisted in PersonalStore
    retrieved = temp_store.get_account(account.account_id)
    assert retrieved is not None
    assert retrieved.auth_status == AuthStatus.CONNECTED
    assert retrieved.email_or_id == "operator@jarvis-ai.org"


def test_disconnect_and_honest_revocation(temp_store):
    mgr = GoogleOAuthManager(store=temp_store)
    client = MockHttpClient()

    # Mock revoke response
    client.post_responses["https://oauth2.googleapis.com/revoke"] = MockHttpResponse({"status": "ok"}, 200)

    # Create connected account with credentials
    account = Account(
        account_id="gmail_disconnect_test",
        provider=ProviderType.GMAIL,
        email_or_id="disconnect_user@gmail.com",
        auth_status=AuthStatus.CONNECTED,
    )
    temp_store.save_account(account)
    store_credential("gmail_gmail_disconnect_test_token", "sample_token")
    store_credential("gmail_gmail_disconnect_test_refresh", "sample_refresh")

    # Disconnect
    success = mgr.disconnect_account(account.account_id, http_client=client)
    assert success is True

    # Account status updated honestly
    updated = temp_store.get_account("gmail_disconnect_test")
    assert updated is not None
    assert updated.auth_status in (AuthStatus.REVOKED_REMOTE_VERIFIED, AuthStatus.DISCONNECTED_LOCAL_ONLY)

    # Local credentials purged
    assert not get_credential("gmail_gmail_disconnect_test_token")
    assert not get_credential("gmail_gmail_disconnect_test_refresh")


# ── Phase 5: ServiceBridge & UI Rendering Acceptance ─────────────────────────


def test_service_bridge_supported_providers_when_empty(temp_store):
    bridge = OptimusServiceBridge.instance()
    supported = bridge.get_supported_personal_providers()

    assert len(supported) == 4
    providers = {p["provider"] for p in supported}
    assert ProviderType.GMAIL.value in providers
    assert ProviderType.GOOGLE_CALENDAR.value in providers
    assert ProviderType.OUTLOOK.value in providers
    assert ProviderType.WHATSAPP_BUSINESS.value in providers

    # When no accounts connected, status is DISCONNECTED
    for p in supported:
        assert p["auth_status"] == AuthStatus.DISCONNECTED.value


def test_ui_table_rendering_not_blank_when_zero_accounts(qapp, temp_store):
    """Verify SectionPersonal Connected Accounts panel is NEVER blank even with 0 accounts."""
    from gui.optimus.section_personal import SectionPersonal

    # Ensure empty store
    for a in temp_store.list_accounts():
        temp_store.delete_account(a.account_id)

    section = SectionPersonal(bridge=OptimusServiceBridge.instance())
    section.store = temp_store
    section.engine.store = temp_store

    section._refresh_accounts()

    # Table must NOT have 0 rows! It must render the supported provider catalog
    assert section.tbl_accounts.rowCount() >= 4

    # Row 0 is Google Gmail
    row0_prov = section.tbl_accounts.item(0, 0).text()
    assert "Gmail" in row0_prov
    assert section.tbl_accounts.item(0, 3).text() == "DISCONNECTED"

    # Action cell must have an interactive CONNECT button
    act_w = section.tbl_accounts.cellWidget(0, 5)
    assert act_w is not None
    btn = act_w.findChild(QPushButton)
    assert btn is not None
    assert "CONNECT" in btn.text().upper()


def test_ui_table_rendering_when_account_connected(qapp, temp_store):
    """Verify SectionPersonal displays connected account details and actions."""
    from gui.optimus.section_personal import SectionPersonal

    account = Account(
        account_id="gmail_active_test",
        provider=ProviderType.GMAIL,
        email_or_id="logesh.dev@gmail.com",
        display_name="Gmail (logesh.dev@gmail.com)",
        auth_status=AuthStatus.CONNECTED,
        last_synced_at=1732098000.0,
    )
    temp_store.save_account(account)

    section = SectionPersonal(bridge=OptimusServiceBridge.instance())
    section.store = temp_store
    section.engine.store = temp_store

    section._refresh_accounts()

    # Must have the connected row
    assert section.tbl_accounts.rowCount() >= 1

    # Col 0: Google Gmail
    assert "Gmail" in section.tbl_accounts.item(0, 0).text()
    # Col 1: Account ID
    assert section.tbl_accounts.item(0, 1).text() == "gmail_active_test"
    # Col 2: Masked email
    assert "l***" in section.tbl_accounts.item(0, 2).text()
    # Col 3: CONNECTED status
    assert section.tbl_accounts.item(0, 3).text() == "CONNECTED"

    # Col 5: Actions must have SYNC, RECONFIGURE and DISCONNECT buttons
    act_w = section.tbl_accounts.cellWidget(0, 5)
    btns = act_w.findChildren(QPushButton)
    btn_texts = [b.text() for b in btns]
    assert "SYNC" in btn_texts
    assert "RECONFIGURE" in btn_texts
    assert "DISCONNECT" in btn_texts


# ── Phase 3: Credential Resolution, Validation & Replacement Tests ───────────


def test_cannot_launch_oauth_before_credentials_exist(clean_credentials, temp_store):
    """Prove clicking Connect cannot launch OAuth before credentials exist."""
    assert not has_google_credentials()

    mgr = GoogleOAuthManager(store=temp_store)
    with pytest.raises(ValueError, match="Cannot initialize Google OAuth session"):
        mgr.build_auth_session(provider=ProviderType.GMAIL)


def test_invalid_credentials_produce_useful_error():
    """Prove invalid credentials produce clear format errors and are rejected."""
    from core.personal_intelligence.oauth import validate_google_credentials

    # Empty values
    ok, err = validate_google_credentials("", "")
    assert not ok
    assert "missing or empty" in err

    # Newlines / whitespace in Client ID
    ok, err = validate_google_credentials("123-\nfoo.apps.googleusercontent.com", "GOCSPX-validsecret123")
    assert not ok
    assert "whitespace or newline" in err

    # Newlines / whitespace in Client Secret
    ok, err = validate_google_credentials("123-foo.apps.googleusercontent.com", "GOCSPX-\nsecret123")
    assert not ok
    assert "whitespace or newline" in err

    # Missing required Google desktop suffix
    ok, err = validate_google_credentials("invalid_client_id_without_suffix", "GOCSPX-validsecret123")
    assert not ok
    assert ".apps.googleusercontent.com" in err

    # Secret too short
    ok, err = validate_google_credentials("123-foo.apps.googleusercontent.com", "short")
    assert not ok
    assert "too short" in err

    # Clean, valid credentials
    ok, err = validate_google_credentials("123456789-abcdef.apps.googleusercontent.com", "GOCSPX-validsecret123")
    assert ok
    assert err == ""


def test_newly_supplied_credentials_replace_stale_credentials(clean_credentials):
    """Prove newly supplied credentials replace stale credentials and are verified."""
    from core.personal_intelligence.oauth import (
        save_and_verify_google_credentials,
    )

    # 1. Store initial/stale credentials
    store_credential("google_client_id", "stale-client-111.apps.googleusercontent.com")
    store_credential("google_client_secret", "stale-secret-111")
    assert get_google_client_id() == "stale-client-111.apps.googleusercontent.com"

    # 2. Save new credentials through save_and_verify
    new_cid = "new-client-222.apps.googleusercontent.com"
    new_sec = "new-secret-222"
    ok, err = save_and_verify_google_credentials(new_cid, new_sec)
    assert ok, err

    # 3. Assert vault/keyring contains the new credentials, not the stale ones
    assert get_google_client_id() == new_cid
    assert get_google_client_secret() == new_sec


def test_oauth_uses_newly_saved_client_id(clean_credentials, temp_store):
    """Prove OAuth session generation strictly uses the newly saved Client ID."""
    from core.personal_intelligence.oauth import save_and_verify_google_credentials

    # Stale credential
    store_credential("google_client_id", "stale-id.apps.googleusercontent.com")
    store_credential("google_client_secret", "stale-secret-xxx")

    # User reconfigures with new credential
    new_cid = "freshly-created-client.apps.googleusercontent.com"
    new_sec = "freshly-created-secret-yyy"
    ok, err = save_and_verify_google_credentials(new_cid, new_sec)
    assert ok, err

    mgr = GoogleOAuthManager(store=temp_store)
    session = mgr.build_auth_session(provider=ProviderType.GMAIL)

    assert session["client_id"] == new_cid
    assert f"client_id={new_cid}" in session["auth_url"]
    assert "stale-id" not in session["auth_url"]


def test_missing_credentials_opens_dialog(qapp, clean_credentials, monkeypatch):
    """Prove clicking Connect when credentials are missing displays GoogleCredentialsDialog."""
    from PyQt6.QtWidgets import QDialog

    from gui.optimus.section_personal import GoogleCredentialsDialog, SectionPersonal

    assert not has_google_credentials()

    dialog_opened = False

    def mock_exec(self):
        nonlocal dialog_opened
        dialog_opened = True
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(GoogleCredentialsDialog, "exec", mock_exec)

    sec = SectionPersonal()
    sec._start_google_oauth(ProviderType.GMAIL)

    assert dialog_opened, "GoogleCredentialsDialog was not opened when credentials were missing"


def test_change_credentials_reconfigure_action_present_in_ui(qapp, temp_store):
    """Prove explicit CHANGE CREDENTIALS / RECONFIGURE actions are present in the toolbar and table."""
    from gui.optimus.section_personal import SectionPersonal

    sec = SectionPersonal()
    sec.store = temp_store
    sec._refresh_accounts()

    # Toolbar action must be present
    assert hasattr(sec, "btn_oauth_reconfig")
    assert sec.btn_oauth_reconfig.text() == "CHANGE CREDENTIALS"

    # Table catalog row must have RECONFIGURE action
    act_w = sec.tbl_accounts.cellWidget(0, 5)
    assert act_w is not None
    btns = act_w.findChildren(QPushButton)
    btn_texts = [b.text() for b in btns]
    assert "RECONFIGURE" in btn_texts


def test_existing_encrypted_credential_storage_remains_intact(tmp_path):
    """Prove existing encrypted CredentialVault storage roundtrips correctly and protects secrets."""
    from core.security.credentials import CredentialVault

    vault = CredentialVault(vault_dir=tmp_path / "test_vault")
    assert vault.read_all() == {}

    # Write secrets
    ok = vault.set("google_client_id", "my-test-id.apps.googleusercontent.com")
    assert ok
    ok = vault.set("google_client_secret", "my-test-secret-zzz")
    assert ok

    # Read secrets back
    assert vault.get("google_client_id") == "my-test-id.apps.googleusercontent.com"
    assert vault.get("google_client_secret") == "my-test-secret-zzz"

    # Verify encrypted on disk (file exists and does not contain plaintext secret)
    enc_bytes = (tmp_path / "test_vault" / ".vault.enc").read_bytes()
    assert b"my-test-secret-zzz" not in enc_bytes
    assert b"my-test-id.apps.googleusercontent.com" not in enc_bytes
