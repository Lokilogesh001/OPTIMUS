"""core/workspace_mcp/client.py — Hardened Stdio JSON-RPC 2.0 Client for Local Workspace MCP Server.

Implements:
- Windows Job Object binding (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) to prevent zombie processes.
- Asynchronous stderr draining into a bounded circular deque (`maxlen=500`) to prevent pipe buffer deadlocks.
- Bounded 10.0s JSON-RPC execution timeout.
- Dynamic standard input credential injection (avoiding ambient environment variables).
- Thread-safe request/response correlation over line-delimited JSON-RPC 2.0.
"""

from __future__ import annotations

import atexit
import collections
import ctypes
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from typing import Any

logger = logging.getLogger("optimus.workspace_mcp.client")


class WorkspaceMCPClient:
    """Robust local stdio client communicating with the Workspace MCP server."""

    def __init__(
        self,
        server_command: list[str] | None = None,
        timeout_seconds: float = 10.0,
        credentials_payload: dict[str, Any] | None = None,
    ) -> None:
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.credentials_payload = credentials_payload or {}
        self._proc: subprocess.Popen | None = None
        self._job_handle: Any = None
        self._request_counter = 0
        self._lock = threading.RLock()
        self._pending_requests: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._stderr_buffer: collections.deque[str] = collections.deque(maxlen=500)
        self._is_closed = False

        if server_command is None:
            # Default to running our local server module using current Python executable
            py_exe = sys.executable
            server_command = [py_exe, "-m", "core.workspace_mcp.server", "--transport", "stdio"]

        self._server_command = server_command
        self._start_server()
        atexit.register(self.close)

    def _assign_to_windows_job(self, proc: subprocess.Popen) -> None:
        """Bind child process to a Windows Job Object configured to terminate when closed."""
        if platform.system() != "Windows":
            return

        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore

            # Create Job Object
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return

            # Set JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000)
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

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
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
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            res = kernel32.SetInformationJobObject(
                job,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if res:
                # Assign process handle to job
                proc_handle = int(proc._handle)  # type: ignore
                kernel32.AssignProcessToJobObject(job, proc_handle)
                self._job_handle = job
                logger.debug("Successfully bound Workspace MCP child PID %s to Windows Job Object", proc.pid)
        except Exception as e:
            logger.debug("Windows Job Object binding skipped: %s", e)

    def _start_server(self) -> None:
        """Spawn the MCP child process with piped standard I/O."""
        with self._lock:
            try:
                # Ensure child process starts cleanly without inheriting ambient secrets
                env = dict(os.environ)
                # Filter out sensitive tokens from ambient environment variables
                for k in list(env.keys()):
                    if any(s in k.lower() for s in ("token", "secret", "password", "api_key")):
                        if not k.startswith("OPTIMUS_"):
                            env.pop(k, None)

                self._proc = subprocess.Popen(
                    self._server_command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # Line buffered
                    env=env,
                )

                self._assign_to_windows_job(self._proc)

                # Start background stdout reader thread
                self._stdout_thread = threading.Thread(
                    target=self._stdout_loop,
                    name="WorkspaceMCP_StdoutReader",
                    daemon=True,
                )
                self._stdout_thread.start()

                # Start background stderr drainer thread (SEC-02 deadlock prevention)
                self._stderr_thread = threading.Thread(
                    target=self._stderr_drain_loop,
                    name="WorkspaceMCP_StderrDrainer",
                    daemon=True,
                )
                self._stderr_thread.start()

                # Send dynamic credential handshake over stdin (SEC-04)
                if self.credentials_payload:
                    handshake_req = {
                        "jsonrpc": "2.0",
                        "method": "initialize_session",
                        "params": {"credentials": self.credentials_payload},
                        "id": 0,
                    }
                    self._write_line(json.dumps(handshake_req))

                logger.info("Workspace MCP child process started (PID: %s)", self._proc.pid)
            except Exception as e:
                logger.error("Failed starting Workspace MCP process: %s", e)
                raise RuntimeError(f"Workspace MCP child process startup failed: {e}") from e

    def _stdout_loop(self) -> None:
        """Continuously read lines from child stdout and route responses to pending callers."""
        proc = self._proc
        if not proc or not proc.stdout:
            return

        for line in proc.stdout:
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue

            try:
                msg = json.loads(stripped)
                req_id = msg.get("id")
                if req_id is not None:
                    with self._lock:
                        if req_id in self._pending_requests:
                            event, box = self._pending_requests[req_id]
                            box.update(msg)
                            event.set()
            except Exception as ex:
                logger.debug("Failed parsing Workspace MCP JSON-RPC message: %s (raw: %s)", ex, stripped[:100])

    def _stderr_drain_loop(self) -> None:
        """Continuously drain child stderr to prevent pipe buffer deadlocks (SEC-02)."""
        proc = self._proc
        if not proc or not proc.stderr:
            return

        for line in proc.stderr:
            if not line:
                break
            self._stderr_buffer.append(line.rstrip())

    def _write_line(self, line: str) -> None:
        """Write line to child stdin and flush."""
        if not self._proc or not self._proc.stdin or self._proc.poll() is not None:
            raise ConnectionError("Workspace MCP process is not running.")
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ConnectionError(f"Failed writing to Workspace MCP stdin: {e}") from e

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool via JSON-RPC 2.0 with bounded timeout."""
        with self._lock:
            self._request_counter += 1
            req_id = self._request_counter
            event = threading.Event()
            response_box: dict[str, Any] = {}
            self._pending_requests[req_id] = (event, response_box)

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
            "id": req_id,
        }

        try:
            self._write_line(json.dumps(payload))
        except Exception as e:
            with self._lock:
                self._pending_requests.pop(req_id, None)
            raise

        # Wait with bounded timeout budget
        completed = event.wait(timeout=self.timeout_seconds)
        with self._lock:
            self._pending_requests.pop(req_id, None)

        if not completed:
            recent_errs = "\n".join(list(self._stderr_buffer)[-5:])
            raise TimeoutError(
                f"Workspace MCP tool '{tool_name}' timed out after {self.timeout_seconds}s. "
                f"Recent stderr: {recent_errs or 'None'}"
            )

        if "error" in response_box:
            err = response_box["error"]
            err_msg = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"Workspace MCP tool '{tool_name}' failed: {err_msg}")

        result = response_box.get("result", {})
        return result

    def close(self) -> None:
        """Terminate child process and cleanup resources."""
        with self._lock:
            if self._is_closed:
                return
            self._is_closed = True

            if self._proc:
                try:
                    if self._proc.stdin:
                        self._proc.stdin.close()
                except Exception:
                    pass

                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1.0)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None

            if self._job_handle and platform.system() == "Windows":
                try:
                    ctypes.windll.kernel32.CloseHandle(self._job_handle)  # type: ignore
                except Exception:
                    pass
                self._job_handle = None
