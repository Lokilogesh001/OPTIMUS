# OPTIMUS M12: Personal Life Intelligence — Provider Setup Guide
**Document ID:** EGM-M12-STAGE6-SETUP-001  
**Milestone:** M12 — Personal Life Intelligence  
**Author:** OPTIMUS Architecture & Integration Guide  
**Date:** 2026-11-19  
**Status:** COMPLETE  

---

## 1. Overview

This document provides setup instructions for all 5 personal life intelligence providers supported by OPTIMUS Milestone 12. It details Google Cloud Console setup, Microsoft Entra ID registration, Meta for Developers configuration, public tunnel setup for desktop webhook reception, and WhatsApp personal chat export handling.

---

## 2. Google Workspace (Gmail & Google Calendar)

### 2.1 Google Cloud Project Configuration
1. Navigate to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project (e.g. `optimus-personal-agent`).
3. Enable the following APIs under **APIs & Services > Library**:
   - **Gmail API**
   - **Google Calendar API**
4. Configure the **OAuth Consent Screen**:
   - User Type: **External** (or Internal for Google Workspace domains).
   - App Name: `OPTIMUS Agent`.
   - Add Test Users: Include your personal Gmail address.

### 2.2 Scopes Configuration
Configure the minimum required scopes:
- **Read-Only Scopes (Default):**
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/calendar.readonly`
- **Mutating Scopes (Progressive Approval Only):**
  - `https://www.googleapis.com/auth/gmail.send`
  - `https://www.googleapis.com/auth/calendar.events`

### 2.3 OAuth 2.0 Client Credentials
1. Go to **APIs & Services > Credentials > Create Credentials > OAuth client ID**.
2. Application type: **Desktop app**.
3. Download the client secret JSON or copy the Client ID and Client Secret.
4. In OPTIMUS, navigate to **Personal Intelligence > Connected Accounts > Connect Google**.
5. Complete the OAuth 2.0 PKCE browser authorization flow. Tokens are automatically stored in the secure local CredentialVault.

---

## 3. Microsoft Outlook & Calendar (Microsoft Graph)

### 3.1 Microsoft Entra ID (Azure Portal) Registration
1. Navigate to the [Microsoft Entra ID Portal](https://entra.microsoft.com/) or Azure Portal.
2. Go to **Identity > Applications > App registrations > New registration**.
3. Name: `OPTIMUS Personal Assistant`.
4. Supported account types: **Accounts in any organizational directory and personal Microsoft accounts** (e.g. `@outlook.com`, `@hotmail.com`, `@live.com`).
5. Redirect URI:
   - Platform: **Mobile and desktop applications**.
   - URI: `http://localhost:8484/oauth/callback` or `https://login.microsoftonline.com/common/oauth2/nativeclient`.

### 3.2 Microsoft Graph Scopes
Add Delegated permissions:
- **Read-Only (Default):**
  - `Mail.Read`
  - `Calendars.Read`
  - `offline_access` (enables refresh tokens)
- **Mutating (Progressive Approval Only):**
  - `Mail.Send`
  - `Calendars.ReadWrite`

### 3.3 Authorization Flow
In OPTIMUS Control Center:
1. Navigate to **Connected Accounts > Connect Outlook**.
2. Authenticate through the Microsoft login window.
3. Access tokens and refresh tokens are stored securely in the CredentialVault. Incremental sync utilizes `/me/mailFolders/inbox/messages/delta` and `/me/calendarView/delta`.

---

## 4. WhatsApp Business Cloud API (Meta)

### 4.1 Meta for Developers App Setup
1. Visit [Meta for Developers](https://developers.facebook.com/).
2. Create an App > Type: **Business**.
3. Under Add Products, select **WhatsApp**.
4. In the WhatsApp Getting Started panel:
   - Note the **Phone Number ID**.
   - Generate a temporary access token or configure a **System User** with a permanent access token.

### 4.2 Webhook Configuration (Inbound Desktop Tunnel)
Because desktop applications run on local machines behind NAT/firewalls, receiving inbound webhooks from Meta requires a secure reverse tunnel.

#### Recommended Tunnels:
- **Cloudflare Tunnel (Zero Trust):**
  ```bash
  cloudflared tunnel --url http://localhost:8000
  ```
- **ngrok:**
  ```bash
  ngrok http 8000
  ```

#### Meta Webhook Setup in App Dashboard:
1. In Meta App Dashboard, go to **WhatsApp > Configuration > Webhook**.
2. **Callback URL:** `https://<your-tunnel-domain>/api/personal/whatsapp/webhook`
3. **Verify Token:** Enter your secret verification token (configured in OPTIMUS).
4. Click **Verify and Save**. Meta sends a GET request with `hub.challenge`.
5. Under **Webhook fields**, subscribe to `messages`.

### 4.3 App Secret for HMAC Verification
Copy your **App Secret** from **App Settings > Basic**. Store it in OPTIMUS via Connected Accounts > WhatsApp Business. All incoming POST payloads are verified using `X-Hub-Signature-256`.

---

## 5. WhatsApp Personal (Safe Local Integration)

### 5.1 Architecture & Limitations
Meta does not provide a public API for consumer WhatsApp accounts. OPTIMUS explicitly rejects scraping, session theft, and headless automation.

### 5.2 Supported Integration Workflows:
1. **Export Chat History:**
   - Open WhatsApp on your phone or desktop.
   - Open the desired conversation > Options (three dots) > **More > Export chat**.
   - Choose **Without Media** to generate a `.txt` file.
2. **Ingest in OPTIMUS:**
   - In OPTIMUS Control Center, go to **Personal Intelligence > WhatsApp**.
   - Click **Import Chat Export File (.txt)** or paste raw snippets into the import box.
   - OPTIMUS parses timestamps, messages, senders, and automatically extracts tasks and obligations.
3. **One-Click Handoff Links (`wa.me`):**
   - When OPTIMUS drafts an outbound response for WhatsApp, click **Open in WhatsApp**.
   - This launches the official `https://wa.me/<phone>?text=<encoded_text>` deep link directly into your native WhatsApp desktop or web client.
