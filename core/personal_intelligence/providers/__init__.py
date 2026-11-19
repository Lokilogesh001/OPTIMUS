"""core/personal_intelligence/providers — Provider Adapters for Milestone 12."""

from __future__ import annotations

from core.personal_intelligence.providers.base import BaseProviderAdapter
from core.personal_intelligence.providers.gmail import GmailAdapter
from core.personal_intelligence.providers.google_calendar import GoogleCalendarAdapter
from core.personal_intelligence.providers.outlook import OutlookAdapter
from core.personal_intelligence.providers.whatsapp_business import (
    WhatsAppBusinessAdapter,
)
from core.personal_intelligence.providers.whatsapp_personal import (
    WhatsAppPersonalAdapter,
)

__all__ = [
    "BaseProviderAdapter",
    "GmailAdapter",
    "GoogleCalendarAdapter",
    "OutlookAdapter",
    "WhatsAppBusinessAdapter",
    "WhatsAppPersonalAdapter",
]
