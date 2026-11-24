"""core/mcp_runtime/manager.py — Central Unified MCP Runtime Manager.

Features:
- Multi-server registration (stdio and streamable HTTP/SSE)
- Protocol handshake (initialize, initialized, tools/list, resources/list)
- Tool schema validation (jsonschema structures)
- Tool name isolation and namespace prefixing (mcp__<server_id>__<tool_name>)
- Core tool protection: prevents external servers from impersonating core native tools
- Output spillover: results > 64KB are written to data/artifacts/mcp_spill/
- Taint wrapping: untrusted output encapsulated in nonced XML tags
- Circuit-breaker status query and manual reset
"""
from __future__ import annotations

import html
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from core.mcp_runtime.supervisor import CircuitState, ServerSupervisor
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

logger = logging.getLogger("optimus.mcp_runtime.manager")

DEFAULT_SPILL_DIR = Path("data/artifacts/mcp_spill")
MAX_INLINE_OUTPUT_BYTES = 64 * 1024  # 64 KB

CORE_RESERVED_TOOL_NAMES = {
    "read_file", "write_file", "edit_file", "delete_file", "list_files",
    "run_command", "bash", "python", "git", "search_web", "inspect_desktop",
    "computer_interact", "terminal_command", "system_monitor", "memory_store"
}


class ToolSchemaValidationError(Exception):
    """Raised when an MCP tool declaration contains an invalid or prohibited parameter schema."""


class ToolImpersonationError(Exception):
    """Raised when an external MCP server attempts to declare a tool matching a reserved core tool."""


class UnifiedMCPManager:
    """Manages all registered MCP servers, transports, tools, and security boundaries."""

    def __init__(self, spill_dir: Optional[Path] = None, reserved_tools: Optional[Set[str]] = None):
        self.spill_dir = spill_dir or DEFAULT_SPILL_DIR
        self.spill_dir.mkdir(parents=True, exist_ok=True)
        self.reserved_tools = reserved_tools or CORE_RESERVED_TOOL_NAMES
        self.servers: Dict[str, MCPServerConfig] = {}
        self.supervisors: Dict[str, ServerSupervisor] = {}
        self.tools: Dict[str, MCPToolDeclaration] = {}  # Key: namespaced_name
        self.server_tools: Dict[str, List[str]] = {}   # Key: server_id -> List[namespaced_name]
        self.server_info: Dict[str, Dict[str, Any]] = {}

    def register_server(self, config: MCPServerConfig) -> None:
        """Registers a server configuration without immediately starting it."""
        self.servers[config.server_id] = config

    def create_transport(self, config: MCPServerConfig) -> MCPTransport:
        """Factory for server transport."""
        if config.transport_type == MCPTransportType.STDIO:
            return StdioMCPTransport(config)
        elif config.transport_type == MCPTransportType.HTTP_SSE:
            return StreamableHttpMCPTransport(config)
        else:
            raise ValueError(f"Unsupported transport type: {config.transport_type}")

    def start_server(self, server_id: str, custom_transport: Optional[MCPTransport] = None) -> bool:
        """Starts server, performs MCP initialization handshake, and discovers tools."""
        config = self.servers.get(server_id)
        if not config:
            raise ValueError(f"Server '{server_id}' is not registered")

        if server_id in self.supervisors and self.supervisors[server_id].state == MCPServerState.RUNNING:
            return True

        transport = custom_transport or self.create_transport(config)
        supervisor = ServerSupervisor(config, transport)
        self.supervisors[server_id] = supervisor

        success = supervisor.start()
        if not success:
            logger.error("Failed to start server '%s'", server_id)
            return False

        # Step 1: Initialize handshake
        init_req = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
            },
            "clientInfo": {
                "name": "Optimus",
                "version": "2.0.0",
            }
        }
        resp = supervisor.execute_request("initialize", init_req, timeout=10.0)
        if resp.is_error:
            logger.error("MCP initialize failed for '%s': %s", server_id, resp.error)
            supervisor.stop()
            return False

        self.server_info[server_id] = resp.result or {}

        # Step 2: Send notifications/initialized
        supervisor.transport.send_notification("notifications/initialized", {})

        # Step 3: Discover tools
        self.refresh_tools(server_id)
        return True

    def stop_server(self, server_id: str) -> None:
        """Stops and unregisters tools for a server."""
        supervisor = self.supervisors.get(server_id)
        if supervisor:
            supervisor.stop()
        # Clean registered tools
        for tool_name in self.server_tools.pop(server_id, []):
            self.tools.pop(tool_name, None)

    def refresh_tools(self, server_id: str) -> List[MCPToolDeclaration]:
        """Queries tools/list and registers valid tools into the catalog."""
        supervisor = self.supervisors.get(server_id)
        if not supervisor:
            return []

        resp = supervisor.execute_request("tools/list", {}, timeout=10.0)
        if resp.is_error or not isinstance(resp.result, dict):
            logger.warning("tools/list failed for '%s': %s", server_id, resp.error)
            return []

        raw_tools = resp.result.get("tools", [])
        registered_tools: List[MCPToolDeclaration] = []

        # Remove previous tools from this server
        for prev in self.server_tools.get(server_id, []):
            self.tools.pop(prev, None)
        self.server_tools[server_id] = []

        for item in raw_tools:
            name = item.get("name", "")
            desc = item.get("description", "")
            schema = item.get("inputSchema", {})

            # 1. Impersonation check
            if name in self.reserved_tools:
                logger.error("Server '%s' attempted to impersonate reserved core tool '%s'", server_id, name)
                raise ToolImpersonationError(f"Server '{server_id}' cannot declare reserved tool name '{name}'")

            # 2. Schema structure validation
            if not isinstance(schema, dict) or schema.get("type", "object") != "object":
                logger.warning("Tool '%s' on server '%s' has invalid inputSchema type", name, server_id)
                raise ToolSchemaValidationError(f"Invalid inputSchema for tool '{name}'")

            namespaced_name = f"mcp__{server_id}__{name}"
            tool_decl = MCPToolDeclaration(
                name=name,
                description=desc,
                parameters=schema,
                server_id=server_id,
                namespaced_name=namespaced_name,
                capability_tier=supervisor.config.capability_tier,
            )
            self.tools[namespaced_name] = tool_decl
            self.server_tools[server_id].append(namespaced_name)
            registered_tools.append(tool_decl)

        logger.info("Registered %d tools from server '%s'", len(registered_tools), server_id)
        return registered_tools

    def call_tool(
        self,
        namespaced_tool_name: str,
        arguments: Dict[str, Any],
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """Calls an MCP tool by its namespaced name with security boundaries and output spillover."""
        tool_decl = self.tools.get(namespaced_tool_name)
        if not tool_decl:
            return {
                "error": f"Tool '{namespaced_tool_name}' not found",
                "is_error": True,
            }

        server_id = tool_decl.server_id
        supervisor = self.supervisors.get(server_id)
        if not supervisor:
            return {
                "error": f"Server supervisor for '{server_id}' is not running",
                "is_error": True,
            }

        # Format JSON-RPC tools/call request
        call_params = {
            "name": tool_decl.name,
            "arguments": arguments,
        }

        resp = supervisor.execute_request("tools/call", call_params, timeout=timeout)
        if resp.is_error:
            return {
                "error": resp.error.get("message", "Unknown error"),
                "code": resp.error.get("code"),
                "is_error": True,
            }

        result = resp.result or {}

        # Content extraction
        raw_output_str = ""
        if isinstance(result, dict) and "content" in result:
            parts = []
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            raw_output_str = "\n".join(parts)
        else:
            raw_output_str = json.dumps(result, indent=2)

        # Output bounding (> 64KB)
        output_bytes = len(raw_output_str.encode("utf-8", errors="replace"))
        is_spilled = False
        spill_path: Optional[str] = None

        if output_bytes > MAX_INLINE_OUTPUT_BYTES:
            spill_filename = f"mcp_{server_id}_{tool_decl.name}_{secrets.token_hex(4)}.txt"
            target_spill = self.spill_dir / spill_filename
            with open(target_spill, "w", encoding="utf-8") as f:
                f.write(raw_output_str)
            is_spilled = True
            spill_path = str(target_spill)
            # Truncate inline representation
            raw_output_str = (
                f"[OUTPUT SPILLOVER: Content ({output_bytes} bytes) exceeded 64KB threshold. "
                f"Full output preserved at: {spill_path}]\n\n"
                f"{raw_output_str[:2048]}...\n[TRUNCATED]"
            )

        # Nonce tagging and taint encapsulation
        nonce = secrets.token_hex(8)
        escaped_output = html.escape(raw_output_str)
        demarcated_output = (
            f'<untrusted_data source="mcp:{server_id}:{tool_decl.name}" nonce="{nonce}">\n'
            f'{escaped_output}\n'
            f'</untrusted_data>'
        )

        return {
            "content": demarcated_output,
            "raw_text": raw_output_str,
            "nonce": nonce,
            "is_error": result.get("isError", False) if isinstance(result, dict) else False,
            "is_spilled": is_spilled,
            "spill_path": spill_path,
        }

    def reset_circuit_breaker(self, server_id: str) -> bool:
        """Resets circuit breaker for a server."""
        supervisor = self.supervisors.get(server_id)
        if supervisor:
            supervisor.circuit_breaker.reset()
            return True
        return False

    def list_server_statuses(self) -> List[Dict[str, Any]]:
        """Returns diagnostic status list for UI and telemetry."""
        statuses = []
        for sid, cfg in self.servers.items():
            sup = self.supervisors.get(sid)
            st = sup.state.value if sup else MCPServerState.STOPPED.value
            circuit_st = sup.circuit_breaker.state.value if sup else CircuitState.CLOSED.value
            metrics = sup.metrics if sup else None
            statuses.append({
                "server_id": sid,
                "name": cfg.name,
                "transport": cfg.transport_type.value,
                "state": st,
                "circuit_state": circuit_st,
                "tool_count": len(self.server_tools.get(sid, [])),
                "total_requests": metrics.total_requests if metrics else 0,
                "failed_requests": metrics.failed_requests if metrics else 0,
                "restart_count": metrics.restart_count if metrics else 0,
                "last_response_time_ms": metrics.last_response_time_ms if metrics else 0.0,
            })
        return statuses
