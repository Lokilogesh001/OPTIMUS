"""core/personal_intelligence/oauth.py — Official Google OAuth 2.0 PKCE Flow Manager.

Implements:
- RFC 7636 Proof Key for Code Exchange (PKCE) with S256 challenge.
- High-entropy cryptographic CSRF state token verification.
- Local loopback redirect listener on 127.0.0.1:8484 (/oauth/callback).
- Official Google authorization URL generation with least-privilege scopes.
- Token exchange against https://oauth2.googleapis.com/token.
- User profile & email resolution via Gmail Profile / Google Userinfo.
- Secure token persistence in OS Keyring / CredentialVault.
- Account entity registration in PersonalStore.
- Informative guidance and graceful error handling when credentials are missing.
"""

from __future__ import annotations

import html
import http.server
import logging
import threading
import time
import urllib.parse
from typing import Any

import requests

from core.personal_intelligence.providers.base import (
    generate_pkce_pair,
    generate_state_token,
    safe_parse_json,
)
from core.personal_intelligence.storage import PersonalStore, mask_pii_string
from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    ProviderType,
    SyncStatus,
)
from core.security.credentials import (
    get_credential,
    store_credential,
)

logger = logging.getLogger("optimus.personal.oauth")

DEFAULT_LOOPBACK_PORT = 8484
DEFAULT_REDIRECT_URI = f"http://localhost:{DEFAULT_LOOPBACK_PORT}/oauth/callback"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

# Scopes
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GCAL_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GCAL_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
USERINFO_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
OPENID_SCOPE = "openid"


def get_google_client_id() -> str | None:
    """Retrieve Google OAuth Client ID from CredentialVault or environment."""
    return get_credential("google_client_id")


def get_google_client_secret() -> str | None:
    """Retrieve Google OAuth Client Secret from CredentialVault or environment."""
    return get_credential("google_client_secret")


def has_google_credentials() -> bool:
    """Check if both Google Client ID and Secret are available."""
    cid = get_google_client_id()
    sec = get_google_client_secret()
    return bool(cid and sec)


def get_google_credentials_guidance() -> str:
    """Return user-facing instructions for setting up Google OAuth credentials."""
    return (
        "Google OAuth 2.0 Credentials Required\n\n"
        "To connect your Gmail or Google Calendar account, OPTIMUS requires a Google OAuth Desktop App Client:\n\n"
        "1. Open Google Cloud Console (https://console.cloud.google.com/).\n"
        "2. Create a project and enable 'Gmail API' and 'Google Calendar API'.\n"
        "3. Configure OAuth Consent Screen (User Type: External, add your email to Test Users).\n"
        "4. Create Credentials > OAuth client ID > Application type: Desktop app.\n"
        f"5. Add Authorized Redirect URI: {DEFAULT_REDIRECT_URI}\n"
        "6. Provide your credentials to OPTIMUS via:\n"
        "   - Environment variables: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET\n"
        "   - Or store in CredentialVault using the OPTIMUS Settings panel\n"
        "   - Or add to config/api_keys.json under 'google_client_id' and 'google_client_secret'."
    )


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler for processing the OAuth redirect callback."""

    server: _OAuthLoopbackServer  # type annotation for typing

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if not parsed_url.path.startswith("/oauth/callback"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        query_params = urllib.parse.parse_qs(parsed_url.query)
        error = query_params.get("error", [None])[0]
        code = query_params.get("code", [None])[0]
        state = query_params.get("state", [None])[0]

        if error:
            logger.warning("OAuth callback received error: %s", error)
            self.server.callback_result = {"success": False, "error": error}
            self._send_html_response(
                status=400,
                title="Authorization Failed",
                message=f"Google returned error: {html.escape(error)}.<br>Please try again in OPTIMUS.",
                is_error=True,
            )
            return

        if not state or state != self.server.expected_state:
            logger.error("OAuth state mismatch! Received: %s, Expected: %s", state, self.server.expected_state)
            self.server.callback_result = {
                "success": False,
                "error": "CSRF state verification failed. Request rejected for security.",
            }
            self._send_html_response(
                status=400,
                title="Security Alert: CSRF Verification Failed",
                message="The state parameter returned from Google does not match this session.<br>Authorization was rejected.",
                is_error=True,
            )
            return

        if not code:
            self.server.callback_result = {"success": False, "error": "No authorization code returned."}
            self._send_html_response(
                status=400,
                title="Missing Authorization Code",
                message="Google redirected without an authorization code.<br>Please try again.",
                is_error=True,
            )
            return

        self.server.callback_result = {
            "success": True,
            "code": code,
            "state": state,
        }
        self._send_html_response(
            status=200,
            title="OPTIMUS Authentication Successful",
            message="Your Google account was successfully authorized.<br>You may now close this browser tab and return to OPTIMUS.",
            is_error=False,
        )

    def _send_html_response(self, status: int, title: str, message: str, is_error: bool) -> None:
        color = "#ff5555" if is_error else "#00d4ff"
        bg = "#0d1117"
        card_bg = "#161b22"
        border = "#ff5555" if is_error else "rgba(0, 212, 255, 0.4)"

        body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{
            background: {bg};
            color: #c9d1d9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
        }}
        .card {{
            background: {card_bg};
            border: 1px solid {border};
            border-radius: 8px;
            padding: 36px 48px;
            max-width: 480px;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
        }}
        h2 {{
            color: {color};
            margin-top: 0;
            letter-spacing: 1px;
        }}
        p {{
            line-height: 1.6;
            font-size: 15px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h2>{title}</h2>
        <p>{message}</p>
    </div>
</body>
</html>"""
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stdout access logging to prevent credential leakage."""
        logger.debug("OAuth Loopback: " + format, *args)


class _OAuthLoopbackServer(http.server.HTTPServer):
    """Loopback server holding expected state and receiving callback result."""

    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: type[_OAuthCallbackHandler], expected_state: str):
        super().__init__(server_address, RequestHandlerClass)
        self.expected_state = expected_state
        self.callback_result: dict[str, Any] | None = None


class GoogleOAuthManager:
    """Manages the lifecycle of Google OAuth 2.0 PKCE authentication."""

    def __init__(self, store: PersonalStore | None = None) -> None:
        self.store = store or PersonalStore.instance()
        self._server: _OAuthLoopbackServer | None = None
        self._server_thread: threading.Thread | None = None

    def build_auth_session(
        self,
        provider: ProviderType = ProviderType.GMAIL,
        include_mutating: bool = False,
        port: int = DEFAULT_LOOPBACK_PORT,
        custom_client_id: str | None = None,
    ) -> dict[str, Any]:
        """Construct authorization URL, PKCE pair, and CSRF state token."""
        client_id = custom_client_id or get_google_client_id()
        if not client_id:
            raise ValueError("Google Client ID is not configured.")

        code_verifier, code_challenge = generate_pkce_pair()
        state = generate_state_token()

        scopes = [OPENID_SCOPE, USERINFO_EMAIL_SCOPE]
        if provider == ProviderType.GMAIL:
            scopes.append(GMAIL_READONLY_SCOPE)
            if include_mutating:
                scopes.append(GMAIL_SEND_SCOPE)
        elif provider == ProviderType.GOOGLE_CALENDAR:
            scopes.append(GCAL_READONLY_SCOPE)
            if include_mutating:
                scopes.append(GCAL_EVENTS_SCOPE)

        redirect_uri = f"http://localhost:{port}/oauth/callback"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

        return {
            "auth_url": auth_url,
            "code_verifier": code_verifier,
            "code_challenge": code_challenge,
            "state": state,
            "scopes": scopes,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "port": port,
            "provider": provider,
        }

    def start_loopback_listener(
        self,
        expected_state: str,
        port: int = DEFAULT_LOOPBACK_PORT,
    ) -> _OAuthLoopbackServer:
        """Start the loopback HTTP server bound to 127.0.0.1 on the specified port."""
        if self._server:
            self.stop_loopback_listener()

        server = _OAuthLoopbackServer(("127.0.0.1", port), _OAuthCallbackHandler, expected_state=expected_state)
        self._server = server
        return server

    def stop_loopback_listener(self) -> None:
        """Shut down and release the loopback listener socket."""
        if self._server:
            try:
                self._server.server_close()
            except OSError as e:
                logger.debug("Error stopping loopback server: %s", e)
            finally:
                self._server = None

    def wait_for_callback(
        self,
        server: _OAuthLoopbackServer,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Run the server loop until a callback is received or timeout occurs."""
        start_time = time.time()
        server.timeout = 1.0

        while (time.time() - start_time) < timeout:
            server.handle_request()
            if server.callback_result is not None:
                return server.callback_result

        return {"success": False, "error": f"OAuth callback timed out after {int(timeout)} seconds."}

    def exchange_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        client_id: str | None = None,
        client_secret: str | None = None,
        http_client: Any | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code and PKCE code_verifier for OAuth tokens."""
        cid = client_id or get_google_client_id()
        sec = client_secret or get_google_client_secret()
        if not cid or not sec:
            raise ValueError("Google Client ID and Client Secret are required for token exchange.")

        payload = {
            "client_id": cid,
            "client_secret": sec,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }

        if http_client and hasattr(http_client, "post"):
            resp = http_client.post(GOOGLE_TOKEN_URL, data=payload)
            data = safe_parse_json(resp)
        else:
            resp = requests.post(GOOGLE_TOKEN_URL, data=payload, timeout=20)
            if resp.status_code != 200:
                err_body = resp.text
                logger.error("Token exchange failed: HTTP %s - %s", resp.status_code, err_body)
                raise RuntimeError(f"Token exchange failed with HTTP {resp.status_code}: {err_body}")
            data = resp.json()

        if "access_token" not in data:
            err = data.get("error_description", data.get("error", "Unknown token exchange failure"))
            raise RuntimeError(f"Token exchange failed: {err}")

        return data

    def fetch_user_email(self, access_token: str, http_client: Any | None = None) -> str:
        """Resolve authenticated user's email address using Gmail Profile or Userinfo API."""
        headers = {"Authorization": f"Bearer {access_token}"}

        # 1. Try Gmail profile
        try:
            if http_client and hasattr(http_client, "get"):
                resp = http_client.get(GMAIL_PROFILE_URL, headers=headers)
                data = safe_parse_json(resp)
            else:
                resp = requests.get(GMAIL_PROFILE_URL, headers=headers, timeout=10)
                data = resp.json() if resp.status_code == 200 else {}
            email = data.get("emailAddress")
            if email:
                return str(email)
        except (requests.RequestException, OSError, ValueError) as ex:
            logger.debug("Failed resolving email via Gmail profile: %s", ex)

        # 2. Try Userinfo endpoint
        try:
            if http_client and hasattr(http_client, "get"):
                resp = http_client.get(GOOGLE_USERINFO_URL, headers=headers)
                data = safe_parse_json(resp)
            else:
                resp = requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=10)
                data = resp.json() if resp.status_code == 200 else {}
            email = data.get("email")
            if email:
                return str(email)
        except (requests.RequestException, OSError, ValueError) as ex:
            logger.debug("Failed resolving email via Userinfo: %s", ex)

        # Fallback identifier if email cannot be fetched
        return "connected_google_user"

    def complete_connection(
        self,
        token_data: dict[str, Any],
        provider: ProviderType = ProviderType.GMAIL,
        scopes: list[str] | None = None,
        email_or_id: str | None = None,
        http_client: Any | None = None,
    ) -> Account:
        """Register account, persist tokens securely in CredentialVault, and update PersonalStore."""
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token")
        if not access_token:
            raise ValueError("No access token present in token exchange response.")

        email = email_or_id or self.fetch_user_email(access_token, http_client=http_client)

        # Deterministic account ID or retrieve existing matching account
        safe_prefix = email.split("@")[0] if "@" in email else "user"
        prov_prefix = "gmail" if provider == ProviderType.GMAIL else "gcal"
        account_id = f"{prov_prefix}_{safe_prefix}"

        existing_accounts = self.store.list_accounts(provider=provider)
        matching = [a for a in existing_accounts if a.email_or_id == email]
        if matching:
            account = matching[0]
            account_id = account.account_id
        else:
            display_name = f"Gmail ({email})" if provider == ProviderType.GMAIL else f"Google Calendar ({email})"
            account = Account(
                account_id=account_id,
                provider=provider,
                email_or_id=email,
                display_name=display_name,
                scopes=scopes or [],
                auth_status=AuthStatus.CONNECTED,
                sync_status=SyncStatus.IDLE,
                last_synced_at=0.0,
            )

        account.auth_status = AuthStatus.CONNECTED
        account.error_message = ""
        account.email_or_id = email
        if scopes:
            account.scopes = scopes

        # Store tokens in secure CredentialVault
        prefix = "gmail" if provider == ProviderType.GMAIL else "gcal"
        store_credential(f"{prefix}_{account_id}_token", access_token)
        if refresh_token:
            store_credential(f"{prefix}_{account_id}_refresh", refresh_token)

        self.store.save_account(account)
        logger.info("Successfully connected %s account: %s", provider.value, mask_pii_string(email))
        return account

    def disconnect_account(self, account_id: str, http_client: Any | None = None) -> bool:
        """Disconnect and revoke an account, purging credentials honestly."""
        account = self.store.get_account(account_id)
        if not account:
            logger.warning("Account %s not found for disconnect.", account_id)
            return False

        from core.personal_intelligence.engine import PersonalIntelligenceEngine
        engine = PersonalIntelligenceEngine(store=self.store)
        try:
            adapter = engine.get_adapter_for_account(account, http_client=http_client)
            adapter.revoke()
        except (requests.RequestException, OSError, RuntimeError, ValueError) as ex:
            logger.warning("Error during adapter revoke for %s: %s", account_id, ex)
            account.auth_status = AuthStatus.DISCONNECTED_LOCAL_ONLY

        self.store.save_account(account)
        logger.info("Account %s disconnected with status: %s", account_id, account.auth_status.value)
        return True
