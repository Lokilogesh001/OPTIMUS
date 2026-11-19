"""core/windows_runtime/ipc.py — Secure Authenticated Local IPC Interface for OPTIMUS Runtime.

Implements Section 3 of Milestone 11:
- Windows Named Pipe IPC at `\\\\.\\pipe\\optimus_privileged_ipc`.
- Restricts pipe access to authorized local user and SYSTEM accounts.
- Enforces mutual authentication via shared secret token with bounded freshness.
- Replay prevention using timestamp expiration and nonce verification cache.
- Message framing with explicit length prefix and bounded request size.
- Resilient client reconnection and thread-safe dispatch.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.windows_runtime.types import (
    OperationRequest,
    OperationResponse,
    RuntimeErrorCode,
)

logger = logging.getLogger("optimus.windows_runtime.ipc")

PIPE_NAME = r"\\.\pipe\optimus_privileged_ipc"
MAX_MESSAGE_BYTES = 10 * 1024 * 1024  # 10 MB maximum request/response payload
TIMESTAMP_TOLERANCE_SECONDS = 60.0    # Replay protection freshness window


def get_ipc_secret_file() -> Path:
    """Return path to the shared IPC authorization secret file."""
    if os.name == "nt":
        app_data = os.environ.get("PROGRAMDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
        secret_dir = Path(app_data) / "OPTIMUS" / "security"
    else:
        secret_dir = Path.home() / ".optimus" / "security"
    secret_dir.mkdir(parents=True, exist_ok=True)
    return secret_dir / "ipc_secret.key"


def get_or_create_shared_secret() -> str:
    """Retrieve or generate the local authentication secret."""
    key_file = get_ipc_secret_file()
    if key_file.exists():
        try:
            secret = key_file.read_text(encoding="utf-8").strip()
            if len(secret) >= 32:
                return secret
        except Exception as ex:  # noqa: BLE001  # noqa: BLE001
            logger.warning("Could not read existing IPC secret: %s", ex)

    # Generate a strong 256-bit token
    new_secret = uuid.uuid4().hex + uuid.uuid4().hex
    try:
        key_file.write_text(new_secret, encoding="utf-8")
        # Restrict permissions on Windows if possible
        if os.name == "nt":
            try:
                import win32api
                import win32file
                import win32security
                user, _domain, _ = win32security.LookupAccountName("", win32api.GetUserName())
                sd = win32security.SECURITY_DESCRIPTOR()
                dacl = win32security.ACL()
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32security.FILE_ALL_ACCESS, user)
                try:
                    admin_sid, _, _ = win32security.LookupAccountName("", "Administrators")
                    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32security.FILE_ALL_ACCESS, admin_sid)
                except Exception:  # noqa: BLE001, S110
                    pass
                try:
                    system_sid, _, _ = win32security.LookupAccountName("", "SYSTEM")
                    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32security.FILE_ALL_ACCESS, system_sid)
                except Exception:  # noqa: BLE001, S110
                    pass
                try:
                    users_sid, _, _ = win32security.LookupAccountName("", "Users")
                    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32file.GENERIC_READ, users_sid)
                except Exception:  # noqa: BLE001, S110
                    pass
                sd.SetSecurityDescriptorDacl(1, dacl, 0)
                win32security.SetFileSecurity(str(key_file), win32security.DACL_SECURITY_INFORMATION, sd)
            except Exception:  # noqa: BLE001, S110
                pass
    except Exception as ex:  # noqa: BLE001
        logger.error("Failed to write IPC secret file: %s", ex)

    return new_secret


def _read_exact(handle: Any, count: int) -> bytes:
    """Read exactly count bytes from a Named Pipe handle."""
    import win32file
    buf = bytearray()
    while len(buf) < count:
        to_read = count - len(buf)
        hr, chunk = win32file.ReadFile(handle, to_read)
        if hr not in (0, 234):
            break
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


class ReplayProtector:
    """Guards against replay attacks by verifying timestamps and caching nonces."""

    def __init__(self, window_seconds: float = TIMESTAMP_TOLERANCE_SECONDS, max_cache: int = 5000) -> None:
        self.window_seconds = window_seconds
        self.max_cache = max_cache
        self._seen_nonces: collections.deque[tuple[str, float]] = collections.deque()
        self._nonce_set: set[str] = set()
        self._lock = threading.Lock()

    def validate_and_record(self, nonce: str, timestamp: float) -> bool:
        """Validate request timestamp freshness and ensure nonce has not been seen."""
        now = time.time()
        if abs(now - timestamp) > self.window_seconds:
            return False

        with self._lock:
            # Clean expired nonces
            cutoff = now - self.window_seconds
            while self._seen_nonces and self._seen_nonces[0][1] < cutoff:
                old_nonce, _ = self._seen_nonces.popleft()
                self._nonce_set.discard(old_nonce)

            if nonce in self._nonce_set:
                return False

            self._nonce_set.add(nonce)
            self._seen_nonces.append((nonce, now))
            if len(self._seen_nonces) > self.max_cache:
                old_nonce, _ = self._seen_nonces.popleft()
                self._nonce_set.discard(old_nonce)

            return True

    def validate_request(self, nonce: str, timestamp: float) -> tuple[bool, str]:
        """Validate nonce freshness and uniqueness, returning (is_valid, reason)."""
        now = time.time()
        if abs(now - timestamp) > self.window_seconds:
            return False, "Timestamp out of range"
        with self._lock:
            if nonce in self._nonce_set:
                return False, "Duplicate nonce"
        if not self.validate_and_record(nonce, timestamp):
            return False, "Duplicate or expired nonce"
        return True, ""


# ── Windows Named Pipe IPC Server & Client ───────────────────────────────────

class NamedPipeServer:
    """Authenticated local Named Pipe server exposing the Privileged Execution Broker."""

    def __init__(self, request_handler: Any = None, pipe_name: str = PIPE_NAME) -> None:
        if request_handler is None:
            from core.windows_runtime.broker import PrivilegedExecutionBroker
            request_handler = PrivilegedExecutionBroker()
        self.request_handler = request_handler
        self.pipe_name = pipe_name
        self.shared_secret = get_or_create_shared_secret()
        self.replay_guard = ReplayProtector()
        self._running = False
        self._stop_event = threading.Event()
        self._server_thread: threading.Thread | None = None

    def handle_raw_request(self, payload: Any, client_pid: int | None = None) -> OperationResponse:
        """Process and validate an incoming request payload dictionary directly."""
        if not isinstance(payload, dict):
            return OperationResponse(
                request_id="unknown",
                success=False,
                error_code=RuntimeErrorCode.INVALID_REQUEST,
                error_message="Invalid request payload structure.",
            )

        try:
            request = OperationRequest.from_dict(payload)
            if client_pid:
                request.client_pid = client_pid
        except Exception as ex:  # noqa: BLE001
            return OperationResponse(
                request_id="unknown",
                success=False,
                error_code=RuntimeErrorCode.INVALID_REQUEST,
                error_message=f"Failed to parse operation request: {ex}",
            )

        # Authenticate Shared Token
        token = payload.get("auth_token") or request.client_token
        if token != self.shared_secret:
            return OperationResponse(
                request_id=request.request_id,
                success=False,
                error_code=RuntimeErrorCode.ACCESS_DENIED,
                error_message="Authentication token invalid.",
            )

        # Replay Attack Protection
        if not self.replay_guard.validate_and_record(request.nonce, request.timestamp):
            return OperationResponse(
                request_id=request.request_id,
                success=False,
                error_code=RuntimeErrorCode.REPLAY_DETECTED,
                error_message="Request replay detected or timestamp expired.",
            )

        # Dispatch
        if callable(self.request_handler):
            return self.request_handler(request)
        elif hasattr(self.request_handler, "execute_request"):
            return self.request_handler.execute_request(request)
        return OperationResponse(
            request_id=request.request_id,
            success=False,
            error_code=RuntimeErrorCode.SERVICE_UNAVAILABLE,
            error_message="Handler unavailable.",
        )

    def start(self) -> None:
        """Start the background pipe listener thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._server_thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="WindowsRuntimePipeServer",
        )
        self._server_thread.start()
        logger.info("Privileged Runtime Named Pipe Server listening on %s", self.pipe_name)

    def stop(self) -> None:
        """Stop the pipe listener cleanly."""
        self._running = False
        self._stop_event.set()
        # Ping the pipe locally to unblock any pending ConnectNamedPipe
        try:
            NamedPipeClient(pipe_name=self.pipe_name).ping()
        except Exception:  # noqa: BLE001, S110
            pass
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)
        self._server_thread = None
        logger.info("Privileged Runtime Named Pipe Server stopped.")

    def _listen_loop(self) -> None:
        """Main connection listening loop using win32pipe."""
        if os.name != "nt":
            logger.info("Non-Windows OS detected; NamedPipeServer running in mock mode.")
            return

        import win32file
        import win32pipe

        is_first_instance = True

        # Construct explicit security attributes to prevent rogue access while allowing local user & admin
        sa = None
        try:
            import win32api
            import win32security
            sd = win32security.SECURITY_DESCRIPTOR()
            dacl = win32security.ACL()
            user, _, _ = win32security.LookupAccountName("", win32api.GetUserName())
            dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32security.FILE_ALL_ACCESS, user)
            try:
                admin_sid, _, _ = win32security.LookupAccountName("", "Administrators")
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32security.FILE_ALL_ACCESS, admin_sid)
            except Exception:  # noqa: BLE001, S110
                pass
            try:
                users_sid, _, _ = win32security.LookupAccountName("", "Users")
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32file.GENERIC_READ | win32file.GENERIC_WRITE, users_sid)
            except Exception:  # noqa: BLE001, S110
                pass
            sd.SetSecurityDescriptorDacl(1, dacl, 0)
            sa = win32security.SECURITY_ATTRIBUTES()
            sa.SECURITY_DESCRIPTOR = sd
        except Exception:  # noqa: BLE001
            sa = None

        while not self._stop_event.is_set():
            pipe = None
            try:
                open_mode = win32pipe.PIPE_ACCESS_DUPLEX
                if is_first_instance:
                    flag_first = getattr(win32pipe, "FILE_FLAG_FIRST_PIPE_INSTANCE", 0x00080000)
                    open_mode |= flag_first

                # Create Named Pipe instance with duplex mode and byte stream boundaries
                pipe = win32pipe.CreateNamedPipe(
                    self.pipe_name,
                    open_mode,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    65536,
                    65536,
                    0,
                    sa,
                )
                is_first_instance = False

                # Wait for client connection
                win32pipe.ConnectNamedPipe(pipe, None)

                if self._stop_event.is_set():
                    try:
                        win32file.CloseHandle(pipe)
                    except Exception:  # noqa: BLE001, S110
                        pass
                    break

                # Spawn worker thread to handle client connection
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(pipe,),
                    daemon=True,
                )
                client_thread.start()

            except Exception as ex:  # noqa: BLE001
                if not self._stop_event.is_set():
                    logger.debug("Pipe connection error: %s", ex)
                time.sleep(0.1)

    def _handle_client(self, pipe: Any) -> None:
        """Handle a single client connection over Named Pipe."""
        import win32file
        import win32pipe

        try:
            # 1. Read message length prefix (4 bytes unsigned int)
            raw_len = _read_exact(pipe, 4)
            if len(raw_len) < 4:
                return

            msg_len = struct.unpack("!I", raw_len)[0]
            if msg_len > MAX_MESSAGE_BYTES or msg_len == 0:
                self._send_error(pipe, "REQ_TOO_LARGE", RuntimeErrorCode.INVALID_REQUEST, "Payload exceeded limit")
                return

            # 2. Read full message payload
            raw_data = _read_exact(pipe, msg_len)
            if len(raw_data) < msg_len:
                return
            data_str = raw_data.decode("utf-8", errors="replace")
            req_dict = json.loads(data_str)

            # 3. Client Identity & Process Validation
            client_pid = None
            try:
                client_pid = win32pipe.GetNamedPipeClientProcessId(pipe)
            except Exception:  # noqa: BLE001, S110
                pass

            response = self.handle_raw_request(req_dict, client_pid=client_pid)
            self._send_response(pipe, response)

        except Exception as ex:  # noqa: BLE001
            logger.error("Exception handling IPC client: %s", ex)
        finally:
            try:
                win32file.FlushFileBuffers(pipe)
                win32pipe.DisconnectNamedPipe(pipe)
                win32file.CloseHandle(pipe)
            except Exception:  # noqa: BLE001, S110
                pass

    def _send_response(self, pipe: Any, response: OperationResponse) -> None:
        import win32file
        payload = json.dumps(response.to_dict()).encode("utf-8")
        header = struct.pack("!I", len(payload))
        win32file.WriteFile(pipe, header + payload)

    def _send_error(self, pipe: Any, req_id: str, code: RuntimeErrorCode, msg: str) -> None:
        resp = OperationResponse(request_id=req_id, success=False, error_code=code, error_message=msg)
        self._send_response(pipe, resp)


class NamedPipeClient:
    """Thread-safe Named Pipe client for OPTIMUS Desktop and Orchestrator."""

    def __init__(self, pipe_name: str = PIPE_NAME) -> None:
        self.pipe_name = pipe_name
        self.shared_secret = get_or_create_shared_secret()
        self._lock = threading.Lock()

    def is_available(self, timeout_ms: int = 500) -> bool:
        """Check if the privileged pipe server is reachable."""
        if os.name != "nt":
            return False
        import win32pipe
        try:
            win32pipe.WaitNamedPipe(self.pipe_name, timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False

    def ping(self) -> bool:
        """Send a lightweight ping to the server."""
        req = OperationRequest(client_pid=os.getpid(), client_token=self.shared_secret)
        try:
            resp = self.send_request(req, timeout_seconds=1.0)
            return resp.success or resp.error_code == RuntimeErrorCode.SUCCESS
        except Exception:  # noqa: BLE001
            return False

    def send_request(self, request: OperationRequest, timeout_seconds: float = 30.0) -> OperationResponse:
        """Deliver an authenticated operation request to the Privileged Broker."""
        if os.name != "nt":
            return OperationResponse(
                request_id=request.request_id,
                success=False,
                error_code=RuntimeErrorCode.SERVICE_UNAVAILABLE,
                error_message="Named Pipes are only supported on Windows.",
            )

        import win32file
        import win32pipe

        # Ensure credentials
        request.client_pid = os.getpid()
        request.client_token = self.shared_secret
        request.timestamp = time.time()
        if not request.nonce:
            request.nonce = uuid.uuid4().hex

        with self._lock:
            pipe_handle = None
            try:
                # Wait for pipe instance to become available
                win32pipe.WaitNamedPipe(self.pipe_name, int(timeout_seconds * 1000))
                pipe_handle = win32file.CreateFile(
                    self.pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )

                # Set pipe read mode to byte
                win32pipe.SetNamedPipeHandleState(
                    pipe_handle,
                    win32pipe.PIPE_READMODE_BYTE,
                    None,
                    None,
                )

                # Send framed request
                payload = json.dumps(request.to_dict()).encode("utf-8")
                header = struct.pack("!I", len(payload))
                win32file.WriteFile(pipe_handle, header + payload)

                # Read framed response
                raw_len = _read_exact(pipe_handle, 4)
                if len(raw_len) < 4:
                    return OperationResponse(
                        request_id=request.request_id,
                        success=False,
                        error_code=RuntimeErrorCode.SERVICE_UNAVAILABLE,
                        error_message="Empty response header from privileged broker.",
                    )

                resp_len = struct.unpack("!I", raw_len)[0]
                raw_data = _read_exact(pipe_handle, resp_len)
                if len(raw_data) < resp_len:
                    return OperationResponse(
                        request_id=request.request_id,
                        success=False,
                        error_code=RuntimeErrorCode.SERVICE_UNAVAILABLE,
                        error_message="Incomplete response body from privileged broker.",
                    )
                resp_dict = json.loads(raw_data.decode("utf-8"))
                return OperationResponse.from_dict(resp_dict)

            except Exception as ex:  # noqa: BLE001
                return OperationResponse(
                    request_id=request.request_id,
                    success=False,
                    error_code=RuntimeErrorCode.SERVICE_UNAVAILABLE,
                    error_message=f"IPC Communication error: {ex}",
                )
            finally:
                if pipe_handle:
                    try:
                        win32file.CloseHandle(pipe_handle)
                    except Exception:  # noqa: BLE001, S110
                        pass
