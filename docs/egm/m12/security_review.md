# OPTIMUS M12: Personal Life Intelligence — Security & Credential Review
**Document ID:** EGM-M12-STAGE6-SEC-001  
**Milestone:** M12 — Personal Life Intelligence  
**Author:** OPTIMUS Security Architecture & Cryptography Review  
**Date:** 2026-11-19  
**Status:** APPROVED | VERIFIED  

---

## 1. Threat Model & Security Invariants

Personal communication data (emails, chats, calendar schedules, contacts) is highly sensitive. The OPTIMUS Personal Life Intelligence subsystem operates under strict security boundaries:

1. **Local-First Storage:** All personal data is stored exclusively on the user's local disk under `memory/personal/`. No telemetry, external cloud sync, or third-party vector databases are utilized.
2. **Mandatory Approval for Mutating Actions:** Under no circumstances may an autonomous agent send an email, create or delete a calendar event, dispatch a WhatsApp message, or purge local data without human-in-the-loop authorization via an `ApprovalToken`.
3. **Least-Privilege Scopes:** External OAuth integrations default to read-only scopes. Write permissions are requested progressively only when explicit user workflows require them.
4. **Hardware Keyring Credential Vault:** OAuth refresh tokens, API keys, and client secrets are stored in the OS credential manager (`core/security/credentials.py`) using Windows DPAPI / macOS Keychain / Linux SecretService, never in plaintext configuration files.
5. **No Session Hijacking or Unofficial Scraping:** Consumer WhatsApp integrations strictly disallow browser automation (Puppeteer/Playwright session theft) or reverse-engineered protocols that violate terms of service.

---

## 2. Authorization & PolicyEngine Boundary Enforcement

Mutating tools are gated via `PRIVILEGED_APPROVAL_PATTERNS` in `core/security/policy_engine.py`:

```python
PRIVILEGED_APPROVAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    # ...
    ("personal_mutating_actions", re.compile(
        r"^(send_email|delete_email|delete_message|create_calendar_event|"
        r"update_calendar_event|delete_calendar_event|send_whatsapp_message|"
        r"purge_personal_store)$",
        re.IGNORECASE,
    )),
]
```

### ApprovalToken Verification Pipeline:
1. **Cryptographic Binding:** Tokens are bound to `tool_name`, `canonical_arguments_hash`, `mission_id`, and `subtask_id`.
2. **Operator Identity Verification:** Automated LLM agents cannot issue or approve tokens (`ValueError: SECURITY_VIOLATION: Automated LLM output cannot provide human approval`).
3. **Single-Use Nonce Consumption:** Nonces are recorded in an in-memory set upon execution; replaying a used token immediately triggers `APPROVAL_TOKEN_REPLAY_DETECTED`.
4. **TTL Expiration:** Tokens carry an explicit epoch expiration timestamp (default: 300 seconds); expired tokens are rejected.

---

## 3. Provider Security Specifications

| Provider | Authentication Standard | Stored Secrets | Default Scopes | Mutating Scopes |
| :--- | :--- | :--- | :--- | :--- |
| **Gmail** | OAuth 2.0 PKCE | Access Token, Refresh Token | `https://www.googleapis.com/auth/gmail.readonly` | `https://www.googleapis.com/auth/gmail.send` |
| **Google Calendar** | OAuth 2.0 PKCE | Access Token, Refresh Token | `https://www.googleapis.com/auth/calendar.readonly` | `https://www.googleapis.com/auth/calendar.events` |
| **Outlook** | Microsoft Graph Delegated OAuth | Access Token, Refresh Token | `Mail.Read`, `Calendars.Read` | `Mail.Send`, `Calendars.ReadWrite` |
| **WhatsApp Business** | Meta Graph API Access Token | System User Token, App Secret, Verify Token | `whatsapp_business_messaging` | Cloud API REST outbound |
| **WhatsApp Personal** | Local Chat Export / Web Deep Link | None (No credentials required) | Local file read | `https://wa.me/` one-click URLs |

---

## 4. Webhook Security (Meta WhatsApp Business)

Inbound webhooks from Meta are subjected to two layers of cryptographic verification:
1. **GET Subscription Challenge:** Verifies that `hub.verify_token` matches the stored verify token before echoing `hub.challenge`.
2. **POST Payload Signature:** Verifies the `X-Hub-Signature-256` header using HMAC-SHA256 with the app's secret key (`hmac.compare_digest`). Payloads failing signature verification are rejected with HTTP 403.
3. **Idempotent Ingestion:** Each webhook message ID is checked against `PersonalStore.has_message(message_id)` before persisting, preventing replay attacks or duplicate processing.

---

## 5. Local Data Wipe & Cryptographic Shredding

The `PersonalStore.wipe_all_data()` routine and `purge_personal_store` tool:
1. Clear all in-memory registries (`_accounts`, `_messages`, `_tasks`, `_events`, `_commitments`, `_contacts`, `_sync_state`).
2. Iteratively delete all files and subdirectories under `memory/personal/`.
3. Clear OS Keyring entries for all connected provider accounts.
4. Gated behind mandatory human confirmation.
