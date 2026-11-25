"""core/application_intelligence/adapters/__init__.py — Application Adapters Registry & Factory."""

from __future__ import annotations

import logging
from typing import Any

from core.application_intelligence.adapters.base import BaseApplicationAdapter
from core.application_intelligence.adapters.browser import BrowserAdapter
from core.application_intelligence.adapters.devtools import DevToolsAdapter
from core.application_intelligence.adapters.document import DocumentAdapter
from core.application_intelligence.adapters.media import MediaAdapter
from core.application_intelligence.adapters.whatsapp import WhatsAppAdapter
from core.application_intelligence.registry import ApplicationRegistry

logger = logging.getLogger("optimus.application_intelligence.adapters")

_ADAPTERS: dict[str, BaseApplicationAdapter] = {}


def get_adapter(target: str) -> BaseApplicationAdapter | None:
    """Retrieve or instantiate an application adapter for a target name or category."""
    clean = target.strip().lower()

    if clean in _ADAPTERS:
        return _ADAPTERS[clean]

    reg = ApplicationRegistry.instance()
    app = reg.find_app(clean)

    # 1. WhatsApp
    if clean in ("whatsapp", "wa") or (app and app.category.value == "communication" and "whatsapp" in app.name.lower()):
        adapter = WhatsAppAdapter(app)
        _ADAPTERS["whatsapp"] = adapter
        _ADAPTERS["wa"] = adapter
        return adapter

    # 2. Browser & Web Search
    if clean in ("browser", "web", "search", "chrome", "msedge", "edge", "brave", "firefox") or (app and app.category.value == "browser"):
        adapter = BrowserAdapter(app)
        _ADAPTERS["browser"] = adapter
        _ADAPTERS[clean] = adapter
        return adapter

    # 3. Documents
    if clean in ("document", "document_viewer", "pdf", "docx", "xlsx", "pptx", "file", "report"):
        adapter = DocumentAdapter(app)
        _ADAPTERS["document"] = adapter
        _ADAPTERS[clean] = adapter
        return adapter

    # 4. DevTools
    if clean in ("devtools", "code", "vscode", "terminal", "powershell", "git", "tests") or (app and app.category.value == "development"):
        adapter = DevToolsAdapter(app)
        _ADAPTERS["devtools"] = adapter
        _ADAPTERS[clean] = adapter
        return adapter

    # 5. Media / Spotify
    if clean in ("spotify", "spotify web", "media", "music") or (app and "spotify" in app.name.lower()):
        adapter = MediaAdapter(app)
        _ADAPTERS["spotify"] = adapter
        _ADAPTERS[clean] = adapter
        return adapter

    return None


__all__ = [
    "BaseApplicationAdapter",
    "WhatsAppAdapter",
    "BrowserAdapter",
    "DocumentAdapter",
    "DevToolsAdapter",
    "MediaAdapter",
    "get_adapter",
]
