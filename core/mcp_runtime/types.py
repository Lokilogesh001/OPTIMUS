"""core/mcp_runtime/types.py — Type definitions, protocol models, and states for Unified MCP Runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union


class MCPTransportType(str, Enum):
    STDIO = "stdio"
    HTTP_SSE = "http_sse"


class MCPServerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    TRIPPED = "tripped"  # Circuit breaker tripped
    FAILED = "failed"
    DISCONNECTED = "disconnected"


class MCPErrorCode(int, Enum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    SERVER_TIMEOUT = -32000
    SERVER_DISCONNECTED = -32001
    SECURITY_VIOLATION = -32002


@dataclass
class MCPToolParameterSchema:
    type: str = "object"
    properties: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)


@dataclass
class MCPToolDeclaration:
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    server_id: str = ""
    namespaced_name: str = ""
    is_sandboxed: bool = True
    capability_tier: str = "WORKSPACE_READ"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "server_id": self.server_id,
            "namespaced_name": self.namespaced_name or f"mcp__{self.server_id}__{self.name}",
            "is_sandboxed": self.is_sandboxed,
            "capability_tier": self.capability_tier,
        }


@dataclass
class MCPServerConfig:
    server_id: str
    name: str
    transport_type: MCPTransportType
    command: List[str] = field(default_factory=list)
    endpoint_url: str = ""
    environment_vars: Dict[str, str] = field(default_factory=dict)
    working_directory: Optional[str] = None
    timeout_seconds: float = 10.0
    max_restarts: int = 3
    enabled: bool = True
    allowed_roots: List[str] = field(default_factory=list)
    capability_tier: str = "WORKSPACE_READ"
    declared_secrets: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server_id": self.server_id,
            "name": self.name,
            "transport_type": self.transport_type.value,
            "command": self.command,
            "endpoint_url": self.endpoint_url,
            "environment_vars": self.environment_vars,
            "working_directory": self.working_directory,
            "timeout_seconds": self.timeout_seconds,
            "max_restarts": self.max_restarts,
            "enabled": self.enabled,
            "allowed_roots": self.allowed_roots,
            "capability_tier": self.capability_tier,
            "declared_secrets": self.declared_secrets,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MCPServerConfig:
        return cls(
            server_id=data["server_id"],
            name=data.get("name", data["server_id"]),
            transport_type=MCPTransportType(data.get("transport_type", MCPTransportType.STDIO.value)),
            command=data.get("command", []),
            endpoint_url=data.get("endpoint_url", ""),
            environment_vars=data.get("environment_vars", {}),
            working_directory=data.get("working_directory"),
            timeout_seconds=float(data.get("timeout_seconds", 10.0)),
            max_restarts=int(data.get("max_restarts", 3)),
            enabled=bool(data.get("enabled", True)),
            allowed_roots=data.get("allowed_roots", []),
            capability_tier=data.get("capability_tier", "WORKSPACE_READ"),
            declared_secrets=data.get("declared_secrets", []),
        )


@dataclass
class MCPRequest:
    id: Union[int, str]
    method: str
    params: Optional[Dict[str, Any]] = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id, "method": self.method}
        if self.params is not None:
            d["params"] = self.params
        return d


@dataclass
class MCPResponse:
    id: Optional[Union[int, str]]
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    jsonrpc: str = "2.0"

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d
