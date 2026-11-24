"""core/mcp_runtime/__init__.py — OPTIMUS Unified MCP Runtime Subsystem."""
from core.mcp_runtime.manager import (
    ToolImpersonationError,
    ToolSchemaValidationError,
    UnifiedMCPManager,
)
from core.mcp_runtime.supervisor import (
    CircuitBreaker,
    CircuitBreakerTripped,
    CircuitState,
    ServerHealthMetrics,
    ServerSupervisor,
)
from core.mcp_runtime.transports.base import MCPTransport
from core.mcp_runtime.transports.http_sse import StreamableHttpMCPTransport
from core.mcp_runtime.transports.stdio import StdioMCPTransport
from core.mcp_runtime.types import (
    MCPErrorCode,
    MCPRequest,
    MCPResponse,
    MCPServerConfig,
    MCPServerState,
    MCPToolDeclaration,
    MCPTransportType,
)

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerTripped",
    "CircuitState",
    "MCPErrorCode",
    "MCPRequest",
    "MCPResponse",
    "MCPServerConfig",
    "MCPServerState",
    "MCPToolDeclaration",
    "MCPTransport",
    "MCPTransportType",
    "ServerHealthMetrics",
    "ServerSupervisor",
    "StdioMCPTransport",
    "StreamableHttpMCPTransport",
    "ToolImpersonationError",
    "ToolSchemaValidationError",
    "UnifiedMCPManager",
]
