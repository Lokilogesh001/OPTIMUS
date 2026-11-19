"""core/personal_intelligence/providers/whatsapp_personal.py — Personal WhatsApp Safe Integration Adapter.

Implements:
- Architectural honesty: Strictly documents the absence of a public consumer WhatsApp API.
- Rejection of brittle/illegal browser scraping, session theft, and unofficial libraries.
- Supported safe integration paths:
  1. Manual or shared chat export file ingestion (`.txt` exports).
  2. Direct user message snippet submission.
  3. One-click outbound handoff links via official `https://wa.me/` URLs.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Any

from core.personal_intelligence.providers.base import BaseProviderAdapter
from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    Message,
    PersonalEvent,
    ProviderType,
)

logger = logging.getLogger("optimus.personal.whatsapp_personal")


class WhatsAppPersonalAdapter(BaseProviderAdapter):
    """Adapter providing honest, safe capabilities for personal consumer WhatsApp."""

    def __init__(self, account: Account) -> None:
        super().__init__(account)

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.WHATSAPP_PERSONAL

    def authenticate(self, auth_payload: dict[str, Any]) -> bool:
        """Personal WhatsApp operates in local mode without third-party OAuth."""
        phone_number = auth_payload.get("phone_number", "")
        self.account.metadata["phone_number"] = phone_number
        self.account.auth_status = AuthStatus.CONNECTED
        self.account.error_message = ""
        return True

    def refresh_token_if_needed(self) -> bool:
        return True

    def check_health(self) -> dict[str, Any]:
        return {
            "healthy": True,
            "provider": "WhatsApp Personal (Safe Local Integration)",
            "limitations": "No public consumer API; imports & official wa.me handoffs enabled.",
        }

    # ── Safe Integration Capabilities (Resolving MED-05) ─────────────────────

    def parse_chat_export(self, export_text: str, conversation_name: str = "WhatsApp Chat") -> list[Message]:
        """Parse standard exported WhatsApp chat history into normalized Message records."""
        messages: list[Message] = []
        # Pattern matching: "[11/19/26, 10:15:30 AM] Sender Name: Message body"
        # or "11/19/26, 10:15 AM - Sender Name: Message body"
        line_regex = re.compile(
            r"^(?:\[?(\d{1,2}/\d{1,2}/\d{2,4},\s+\d{1,2}:\d{2}(?::\d{2})?\s*[APMapm]{2})\]?|-?)\s*(?:-\s+)?([^:]+):\s*(.+)$"
        )

        lines = export_text.strip().splitlines()
        for idx, line in enumerate(lines):
            match = line_regex.match(line.strip())
            if match:
                date_str, sender, body = match.groups()
                # Skip system messages like encryption notices or security code changes
                if "Messages and calls are end-to-end encrypted" in body:
                    continue

                msg_id = f"wapers_{int(time.time())}_{idx}"
                messages.append(
                    Message(
                        message_id=msg_id,
                        provider=ProviderType.WHATSAPP_PERSONAL,
                        account_id=self.account.account_id,
                        conversation_id=f"wapers_conv_{sender.replace(' ', '_')}",
                        sender=sender.strip(),
                        sender_name=sender.strip(),
                        recipients=[self.account.email_or_id or "User"],
                        subject=f"WhatsApp with {sender.strip()}",
                        body_snippet=body.strip()[:500],
                        timestamp=time.time(),
                        labels=["WHATSAPP_PERSONAL", "CHAT_IMPORT"],
                        is_read=True,
                        is_important=False,
                    )
                )

        return messages

    def generate_handoff_url(self, phone_number: str, message_text: str = "") -> str:
        """Generate safe, official one-click deep link to WhatsApp desktop or web."""
        clean_phone = re.sub(r"[^0-9]", "", phone_number)
        encoded_msg = urllib.parse.quote(message_text)
        if clean_phone and encoded_msg:
            return f"https://wa.me/{clean_phone}?text={encoded_msg}"
        elif clean_phone:
            return f"https://wa.me/{clean_phone}"
        return f"https://wa.me/?text={encoded_msg}"

    def initial_sync(self, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        return [], [], None

    def incremental_sync(self, cursor: str, max_items: int = 50) -> tuple[list[Message], list[PersonalEvent], str | None]:
        return [], [], None

    def revoke(self) -> bool:
        self.account.auth_status = AuthStatus.DISCONNECTED
        return True
