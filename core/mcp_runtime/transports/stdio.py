"""core/mcp_runtime/transports/stdio.py — Line-delimited JSON-RPC stdio transport with Windows Job Objects.

Features:
- Windows Job Object binding: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000) + JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK (0x10)
- Fallback psutil process-tree cleanup
- Dedicated background reader threads for stdout and stderr (bounded 500-line circular buffer)
- Strict scoped environment variable injection: ambient secrets (GEMINI_API_KEY, etc.) scrubbed
- Request correlation, timeout enforcement, and cancellation notifications
"""
from __future__ import annotations

import collections
import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import psutil

from core.mcp_runtime.transports.base import MCPTransport
from core.mcp_runtime.types import MCPServerConfig, MCPResponse, MCPErrorCode

logger = logging.getLogger("optimus.mcp_runtime.stdio")

# Windows Job Object constants
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x10
JobObjectExtendedLimitInformation = 9

# Ambient secrets that must never leak into MCP child processes
AMBIENT_SECRET_KEYS = {
    "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NVIDIA_API_KEY",
    "GITHUB_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "SLACK_BOT_TOKEN",
    "DISCORD_TOKEN", "OPTIMUS_MASTER_KEY", "OPTIMUS_VAULT_KEY",
}


def create_windows_job_object() -> Optional[int]:
    """Creates a Windows Job Object configured to terminate all child processes when closed."""
    if sys.platform != "win32":
        return None

    try:
        kernel32 = ctypes.windll.kernel32
        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            logger.debug("CreateJobObjectW failed with error: %s", kernel32.GetLastError())
            return None

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryLimit", ctypes.c_size_t),
                ("PeakJobMemoryLimit", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        )

        success = kernel32.SetInformationJobObject(
            h_job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not success:
            logger.debug("SetInformationJobObject failed: %s", kernel32.GetLastError())
            kernel32.CloseHandle(h_job)
            return None

        return h_job
    except Exception as e:
        logger.debug("Failed configuring Job Object: %s", e)
        return None


def assign_process_to_job(h_job: int, process_handle: int) -> bool:
    """Assigns a spawned process handle to an active Windows Job Object."""
    if sys.platform != "win32" or not h_job:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        res = kernel32.AssignProcessToJobObject(h_job, process_handle)
        return bool(res)
    except Exception as e:
        logger.debug("AssignProcessToJobObject failed: %s", e)
        return False


class StdioMCPTransport(MCPTransport):
    """Production stdio JSON-RPC transport for local MCP processes."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.proc: Optional[subprocess.Popen] = None
        self._h_job: Optional[int] = None
        self._req_id = 0
        self._req_lock = threading.Lock()
        self._pending_requests: Dict[int, threading.Event] = {}
        self._responses: Dict[int, MCPResponse] = {}
        self._stderr_buffer: collections.deque[str] = collections.deque(maxlen=500)
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._closing = False

    def _build_scoped_environment(self) -> Dict[str, str]:
        """Builds a minimal, clean environment dictionary without ambient secrets."""
        base_env: Dict[str, str] = {}
        # Keep standard OS infrastructure paths
        for key in (
            "SYSTEMROOT", "PATH", "TEMP", "TMP", "SYSTEMDRIVE",
            "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA"
        ):
            if key in os.environ:
                base_env[key] = os.environ[key]

        # Apply declared config environment variables
        if self.config.environment_vars:
            base_env.update(self.config.environment_vars)

        # Explicitly scrub all ambient secret keys
        for key in AMBIENT_SECRET_KEYS:
            base_env.pop(key, None)

        return base_env

    def connect(self) -> bool:
        if self.proc and self.proc.poll() is None:
            return True

        if not self.config.command:
            logger.error("Cannot connect stdio server '%s': command is empty", self.config.server_id)
            return False

        self._closing = False
        scoped_env = self._build_scoped_environment()

        try:
            # Create Windows Job Object
            self._h_job = create_windows_job_object()

            logger.info("Spawning stdio MCP server '%s' with command %s", self.config.server_id, self.config.command)
            self.proc = subprocess.Popen(
                self.config.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.config.working_directory,
                env=scoped_env,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Bind to Windows Job Object
            if self._h_job and hasattr(self.proc, "_handle"):
                assigned = assign_process_to_job(self._h_job, self.proc._handle)
                if assigned:
                    logger.debug("Bound MCP server '%s' PID %d to Windows Job Object", self.config.server_id, self.proc.pid)

            # Start reader threads
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                name=f"mcp-stdout-{self.config.server_id}",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                name=f"mcp-stderr-{self.config.server_id}",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()

            return True
        except Exception as e:
            logger.error("Failed spawning MCP server '%s': %s", self.config.server_id, e)
            self.disconnect()
            return False

    def _read_stdout(self) -> None:
        """Reads line-delimited JSON-RPC messages from server stdout."""
        if not self.proc or not self.proc.stdout:
            return

        try:
            for line in self.proc.stdout:
                line_str = line.strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                    if isinstance(msg, dict):
                        if "id" in msg:
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
                        elif "method" in msg:
                            notif_method = msg.get("method", "")
                            notif_params = msg.get("params", {})
                            self._stderr_buffer.append(f"[MCP-NOTIFICATION:{notif_method}] {json.dumps(notif_params)}")
                except Exception as parse_err:
                    logger.debug("Failed parsing JSON-RPC line from '%s': %s", self.config.server_id, parse_err)
        except Exception as e:
            if not self._closing:
                logger.debug("Exception in stdout reader for '%s': %s", self.config.server_id, e)

    def _read_stderr(self) -> None:
        """Reads stderr lines into circular buffer preventing OS pipe deadlocks."""
        if not self.proc or not self.proc.stderr:
            return

        try:
            for line in self.proc.stderr:
                line_str = line.strip()
                if line_str:
                    self._stderr_buffer.append(line_str)
        except Exception as e:
            if not self._closing:
                logger.debug("Exception in stderr reader for '%s': %s", self.config.server_id, e)

    def send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ) -> MCPResponse:
        if not self.is_connected():
            return MCPResponse(
                id=None,
                error={"code": MCPErrorCode.SERVER_DISCONNECTED.value, "message": "Server process is not running"}
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
            line = json.dumps(payload) + "\n"
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            else:
                return MCPResponse(
                    id=req_id,
                    error={"code": MCPErrorCode.SERVER_DISCONNECTED.value, "message": "Server stdin unavailable"}
                )

            # Await response event
            completed = evt.wait(timeout=effective_timeout)
            if not completed:
                # Cancel request
                self.send_notification("notifications/cancelled", {"requestId": req_id})
                return MCPResponse(
                    id=req_id,
                    error={"code": MCPErrorCode.SERVER_TIMEOUT.value, "message": f"Request timed out after {effective_timeout}s"}
                )

            with self._req_lock:
                return self._responses.pop(req_id, MCPResponse(id=req_id, result=None))

        except Exception as e:
            logger.error("Error sending request to '%s': %s", self.config.server_id, e)
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
            line = json.dumps(payload) + "\n"
            if self.proc and self.proc.stdin:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
        except Exception as e:
            logger.debug("Failed sending notification to '%s': %s", self.config.server_id, e)

    def is_connected(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def get_stderr_tail(self, lines: int = 50) -> List[str]:
        return list(self._stderr_buffer)[-lines:]

    def disconnect(self) -> None:
        self._closing = True

        # Clean pending requests
        with self._req_lock:
            for evt in self._pending_requests.values():
                evt.set()
            self._pending_requests.clear()

        # Terminate process and tree
        if self.proc:
            try:
                pid = self.proc.pid
                # psutil process tree termination fallback
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        child.terminate()
                    parent.terminate()
                    _, alive = psutil.wait_procs(children + [parent], timeout=2.0)
                    for p in alive:
                        p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            finally:
                self.proc = None

        # Close Windows Job Object Handle
        if self._h_job:
            try:
                ctypes.windll.kernel32.CloseHandle(self._h_job)
            except Exception:
                pass
            self._h_job = None
