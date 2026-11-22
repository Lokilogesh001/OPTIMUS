"""core/application_intelligence/frameworks/ufo — Microsoft UFO² Desktop Integration."""

from core.application_intelligence.frameworks.ufo.app_agent_schemas import UFOAppAgentSchemas
from core.application_intelligence.frameworks.ufo.ufo_backend import UFODesktopBackend
from core.application_intelligence.frameworks.ufo.wincom_bridge import WinCOMBridge

__all__ = ["UFODesktopBackend", "WinCOMBridge", "UFOAppAgentSchemas"]
