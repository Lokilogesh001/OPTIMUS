"""core/mcp_runtime/transports/http_sse.py — Streamable HTTP/SSE transport client.

Supports:
- Server-Sent Events (SSE) streaming for server responses and notifications
- HTTP POST for client messages
- 30.0s keep-alive watchdog timer: auto-reconnects with exponential backoff if stream is silent > 30s
- Reconnection backoff: 1s, 2s, 4s up to 16s
- Clean session teardown
"""
from __future__ import annotations

import collections
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from core.mcp_runtime.transports.base import MCPTransport
from core.mcp_runtime.types import MCPServerConfig, MCPResponse, MCPErrorCode

logger = logging.getLogger("optimus.mcp_runtime.http_sse")


class StreamableHttpMCPTransport(MCPTransport):
    """Production streamable HTTP/SSE client for remote or containerized MCP servers."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.endpoint_url = config.endpoint_url
        self.post_url = config.endpoint_url  # Will be updated if server advertises an endpoint event
        self._req_id = 0
        self._req_lock = threading.Lock()
        self._pending_requests: Dict[int, threading.Event] = {}
        self._responses: Dict[int, MCPResponse] = {}
        self._stderr_buffer: collections.deque[str] = collections.deque(maxlen=500)
        self._sse_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._connected = False
        self._closing = False
        self._last_event_time = time.monotonic()
        self._backoff = 1.0

    def connect(self) -> bool:
        if self._connected:
            return True

        self._closing = False
        self._last_event_time = time.monotonic()

        # Launch background SSE listener
        self._sse_thread = threading.Thread(
            target=self._run_sse_listener,
            name=f"mcp-sse-{self.config.server_id}",
            daemon=True,
        )
        self._sse_thread.start()

        # Launch keepalive watchdog (30s)
        self._watchdog_thread = threading.Thread(
            target=self._run_watchdog,
            name=f"mcp-watchdog-{self.config.server_id}",
            daemon=True,
        )
        self._watchdog_thread.start()

        # Wait briefly for initial connection
        start = time.monotonic()
        while time.monotonic() - start < 3.0:
            if self._connected:
                return True
            time.sleep(0.05)

        return self._connected

    def _run_watchdog(self) -> None:
        """30s Keep-alive watchdog timer."""
        while not self._closing:
            time.sleep(5.0)
            if self._closing:
                break
            if self._connected:
                silence_duration = time.monotonic() - self._last_event_time
                if silence_duration > 30.0:
                    logger.warning(
                        "MCP server '%s' SSE stream silent for %.1fs (>30s), restarting transport",
                        self.config.server_id, silence_duration
                    )
                    self._last_event_time = time.monotonic()
                    # Trigger reconnect
                    self._connected = False

    def _run_sse_listener(self) -> None:
        """Main SSE event reading loop with backoff reconnection."""
        while not self._closing:
            try:
                req = urllib.request.Request(
                    self.endpoint_url,
                    headers={
                        "Accept": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "User-Agent": "Optimus-MCP-Client/2.0",
                    },
                    method="GET"
                )
                with urllib.request.urlopen(req, timeout=35.0) as resp:
                    self._connected = True
                    self._backoff = 1.0
                    self._last_event_time = time.monotonic()

                    event_type = "message"
                    data_lines: List[str] = []

                    for raw_line in resp:
                        if self._closing:
                            break
                        line = raw_line.decode("utf-8", errors="replace").strip("\r\n")
                        self._last_event_time = time.monotonic()

                        if not line:
                            # End of event
                            if data_lines:
                                full_data = "\n".join(data_lines)
                                self._handle_sse_event(event_type, full_data)
                                data_lines = []
                                event_type = "message"
                            continue

                        if line.startswith(":"):
                            # SSE comment / keepalive ping
                            continue
                        elif line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].strip())

            except Exception as e:
                self._connected = False
                if not self._closing:
                    self._stderr_buffer.append(f"SSE connection error: {e}")
                    logger.debug("SSE error for '%s': %s (reconnecting in %.1fs)", self.config.server_id, e, self._backoff)
                    time.sleep(self._backoff)
                    self._backoff = min(16.0, self._backoff * 2.0)

    def _handle_sse_event(self, event_type: str, data: str) -> None:
        """Processes received SSE events."""
        if event_type == "endpoint":
            # Server tells client where to POST messages
            new_post_url = urllib.parse.urljoin(self.endpoint_url, data.strip())
            self.post_url = new_post_url
            logger.debug("MCP server '%s' updated post URL to: %s", self.config.server_id, self.post_url)
            return

        try:
            msg = json.loads(data)
            if isinstance(msg, dict) and "id" in msg:
                req_id = msg["id"]
                response = MCPResponse(
                    id=req_id,
                    result=msg.get("result"),
                    error=msg.get("error"),
                    jsonrpc=msg.get("jsonrpc", "2.0"),
                )
                with self._req_lock:
                    self._responses[req_id] = response
                    evt = self._pending_requests.get(req_id)
                    if evt:
                        evt.set()
        except Exception as e:
            logger.debug("Failed parsing SSE JSON data from '%s': %s", self.config.server_id, e)

    def send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ) -> MCPResponse:
        if not self.is_connected():
            return MCPResponse(
                id=None,
                error={"code": MCPErrorCode.SERVER_DISCONNECTED.value, "message": "HTTP/SSE stream disconnected"}
            )

        with self._req_lock:
            self._req_id += 1
            req_id = self._req_id
            evt = threading.Event()
            self._pending_requests[req_id] = evt

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        effective_timeout = timeout or self.config.timeout_seconds

        try:
            raw_bytes = json.dumps(payload).encode("utf-8")
            post_req = urllib.request.Request(
                self.post_url,
                data=raw_bytes,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Optimus-MCP-Client/2.0",
                },
                method="POST"
            )
            urllib.request.urlopen(post_req, timeout=10.0)

            # Await response from SSE stream
            completed = evt.wait(timeout=effective_timeout)
            if not completed:
                self.send_notification("notifications/cancelled", {"requestId": req_id})
                return MCPResponse(
                    id=req_id,
                    error={"code": MCPErrorCode.SERVER_TIMEOUT.value, "message": f"Request timed out after {effective_timeout}s"}
                )

            with self._req_lock:
                return self._responses.pop(req_id, MCPResponse(id=req_id, result=None))

        except Exception as e:
            logger.error("Failed sending HTTP POST to '%s': %s", self.config.server_id, e)
            return MCPResponse(
                id=req_id,
                error={"code": MCPErrorCode.INTERNAL_ERROR.value, "message": str(e)}
            )
        finally:
            with self._req_lock:
                self._pending_requests.pop(req_id, None)

    def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if not self.is_connected():
            return

        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params

        try:
            raw_bytes = json.dumps(payload).encode("utf-8")
            post_req = urllib.request.Request(
                self.post_url,
                data=raw_bytes,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(post_req, timeout=5.0)
        except Exception as e:
            logger.debug("Failed sending HTTP notification to '%s': %s", self.config.server_id, e)

    def is_connected(self) -> bool:
        return self._connected

    def get_stderr_tail(self, lines: int = 50) -> List[str]:
        return list(self._stderr_buffer)[-lines:]

    def disconnect(self) -> None:
        self._closing = True
        self._connected = False
        with self._req_lock:
            for evt in self._pending_requests.values():
                evt.set()
            self._pending_requests.clear()
