"""core/mcp_runtime/transports/base.py — Abstract transport interface for MCP."""
from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional

from core.mcp_runtime.types import MCPRequest, MCPResponse


class MCPTransport(abc.ABC):
    """Abstract base class for all MCP communication transports."""

    @abc.abstractmethod
    def connect(self) -> bool:
        """Establishes connection to the MCP server."""
        raise NotImplementedError

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Terminates connection and cleans up underlying processes/sockets."""
        raise NotImplementedError

    @abc.abstractmethod
    def send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ) -> MCPResponse:
        """Sends a JSON-RPC request and synchronously awaits the correlated response."""
        raise NotImplementedError

    @abc.abstractmethod
    def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Sends a one-way notification without expecting a response."""
        raise NotImplementedError

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Returns True if the transport is actively connected and healthy."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_stderr_tail(self, lines: int = 50) -> List[str]:
        """Returns recent log lines captured from the server stderr stream."""
        raise NotImplementedError
