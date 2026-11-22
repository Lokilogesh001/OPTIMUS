"""core/workspace_mcp — Local Managed Google Workspace MCP Subsystem for OPTIMUS."""

from core.workspace_mcp.adapter import WorkspaceMCPAdapter
from core.workspace_mcp.client import WorkspaceMCPClient
from core.workspace_mcp.mock_transport import MockWorkspaceTransport
from core.workspace_mcp.server import WorkspaceMCPServer
from core.workspace_mcp.tools import register_workspace_mcp_tools

__all__ = [
    "MockWorkspaceTransport",
    "WorkspaceMCPAdapter",
    "WorkspaceMCPClient",
    "WorkspaceMCPServer",
    "register_workspace_mcp_tools",
]
