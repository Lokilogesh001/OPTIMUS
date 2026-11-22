"""core/application_intelligence/frameworks/ufo — Microsoft UFO² Desktop Integration (M18 & M19)."""

from core.application_intelligence.frameworks.ufo.app_agent_schemas import UFOAppAgentSchemas
from core.application_intelligence.frameworks.ufo.compat import ensure_ufo_environment, patch_fastmcp
from core.application_intelligence.frameworks.ufo.mission_runner import UFODesktopMissionRunner
from core.application_intelligence.frameworks.ufo.security_gateway import (
    GatewayInvocationResult,
    GatewaySecurityViolation,
    UFOMCPSecurityGateway,
)
from core.application_intelligence.frameworks.ufo.ufo_backend import UFODesktopBackend
from core.application_intelligence.frameworks.ufo.ufo_desktop_provider import UFODesktopAgentProvider
from core.application_intelligence.frameworks.ufo.wincom_bridge import WinCOMBridge
from core.application_intelligence.frameworks.ufo.workflow_integrator import UFOWorkflowIntegrator

__all__ = [
    "UFODesktopBackend",
    "WinCOMBridge",
    "UFOAppAgentSchemas",
    "UFODesktopAgentProvider",
    "UFODesktopMissionRunner",
    "UFOMCPSecurityGateway",
    "UFOWorkflowIntegrator",
    "GatewaySecurityViolation",
    "GatewayInvocationResult",
    "ensure_ufo_environment",
    "patch_fastmcp",
]
