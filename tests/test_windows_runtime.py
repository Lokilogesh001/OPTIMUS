"""tests/test_windows_runtime.py — Comprehensive Test Suite for Milestone 11.

Verifies:
- Test A: Cross-Drive Filesystem Operations (C:, D:, reading, writing, search outside workspace)
- Test B: Windows Application & Process Lifecycle (launch, list, inspect, terminate)
- Test C: Security Identity & Elevation Inspection (integrity level, admin status, serialization)
- Test D: 2-Tier Authorization Policy (routine auto-approval vs critical/destructive gating)
- Test E: Named Pipe IPC Security & Replay Protection (token validation, nonce tracking, framing)
- Test F: High-Availability Broker & ToolRegistry Integration (fallback, audit logging, registry tools)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from core.orchestrator.tools import ToolRegistry
from core.windows_runtime.broker import PrivilegedExecutionBroker
from core.windows_runtime.client import get_windows_runtime_client
from core.windows_runtime.ipc import NamedPipeClient, NamedPipeServer, ReplayProtector
from core.windows_runtime.policy import WindowsRuntimePolicy
from core.windows_runtime.types import (
    OperationRequest,
    OperationType,
    RuntimeErrorCode,
    RuntimeIdentity,
)


@pytest.fixture
def temp_workspace():
    """Create a temporary directory for safe filesystem testing."""
    d = Path(tempfile.mkdtemp(prefix="optimus_winrt_test_"))
    yield d
    try:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001, S110
        pass


@pytest.fixture
def runtime_client():
    """Retrieve isolated or singleton WindowsRuntimeClient."""
    return get_windows_runtime_client()


# ============================================================================
# Test A: Cross-Drive Filesystem Operations
# ============================================================================

def test_discover_mounted_drives(runtime_client):
    """Verify dynamic discovery of all local mounted drives (e.g. C:, D:)."""
    drives = runtime_client.discover_drives()
    assert isinstance(drives, list)
    assert len(drives) >= 1
    # At least C:\ must be present on standard Windows testbeds
    assert any("C:" in d.upper() for d in drives)


def test_cross_drive_read_write_outside_workspace(runtime_client, temp_workspace):
    """Verify write, read, and delete outside the repository root without confinement errors."""
    test_file = temp_workspace / "system_cross_drive_test.txt"
    payload = "OPTIMUS Windows Runtime Cross-Drive Verification Payload 2026"

    # 1. Write file outside repository
    bytes_written = runtime_client.write_file(str(test_file), payload)
    assert bytes_written == len(payload.encode("utf-8"))
    assert test_file.exists()

    # 2. Read file back
    read_content = runtime_client.read_file(str(test_file))
    assert read_content == payload

    # 3. File metadata
    meta = runtime_client.file_metadata(str(test_file))
    assert meta.get("size_bytes") == len(payload.encode("utf-8"))
    assert meta.get("is_file") is True

    # 4. Copy file
    copied_file = temp_workspace / "system_cross_drive_copy.txt"
    ok_copy = runtime_client.copy_file(str(test_file), str(copied_file))
    assert ok_copy is True
    assert copied_file.exists()
    assert copied_file.read_text(encoding="utf-8") == payload

    # 5. Move / Rename file
    moved_file = temp_workspace / "system_cross_drive_moved.txt"
    ok_move = runtime_client.move_file(str(copied_file), str(moved_file))
    assert ok_move is True
    assert not copied_file.exists()
    assert moved_file.exists()


def test_cross_drive_search_files(runtime_client, temp_workspace):
    """Verify file search capability across directories."""
    sub = temp_workspace / "sub_search"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "target_a.log").write_text("log A", encoding="utf-8")
    (sub / "target_b.log").write_text("log B", encoding="utf-8")
    (sub / "other.txt").write_text("text", encoding="utf-8")

    results = runtime_client.search_files(str(temp_workspace), pattern="*.log", recursive=True)
    assert len(results) == 2
    paths = [r["path"] for r in results]
    assert any("target_a.log" in p for p in paths)
    assert any("target_b.log" in p for p in paths)


def test_open_in_explorer(runtime_client, temp_workspace):
    """Verify open in explorer executes safely without blocking or raising error."""
    ok = runtime_client.open_in_explorer(str(temp_workspace))
    assert ok is True


# ============================================================================
# Test B: Windows Application & Process Lifecycle
# ============================================================================

def test_list_and_inspect_processes(runtime_client):
    """Verify enumerating active processes and inspecting process metadata."""
    current_pid = os.getpid()

    # 1. List processes
    procs = runtime_client.list_processes(limit=50)
    assert isinstance(procs, list)
    assert len(procs) > 0

    # 2. Inspect current test runner process
    details = runtime_client.inspect_process(current_pid)
    assert details.get("pid") == current_pid
    assert "name" in details
    assert "memory_percent" in details
    assert "create_time" in details


def test_launch_and_stop_application(runtime_client):
    """Verify launching a benign Windows application process and stopping it."""
    import sys
    app_to_launch = sys.executable
    args = ["-c", "import time; time.sleep(10)"]
    res = runtime_client.launch_application(app_to_launch, args=args)
    pid = res.get("pid")
    assert pid is not None and pid > 0

    try:
        time.sleep(0.3)
        # Verify process exists in inspection
        p_info = runtime_client.inspect_process(pid)
        assert p_info.get("pid") == pid

        # Stop process with approval
        stopped = runtime_client.stop_process(pid=pid, is_pre_approved=True)
        assert stopped is True
    finally:
        # Cleanup failsafe
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
        except Exception:  # noqa: BLE001, S110
            pass


# ============================================================================
# Test C: Security Identity & Elevation Inspection
# ============================================================================

def test_execution_identity_query(runtime_client):
    """Verify accurate extraction of Windows execution identity."""
    ident = runtime_client.get_execution_identity()
    assert isinstance(ident, RuntimeIdentity)
    assert len(ident.username) > 0
    assert ident.process_id > 0
    assert isinstance(ident.is_admin, bool)
    assert isinstance(ident.is_system, bool)
    assert ident.integrity_level in ("Low", "Medium", "High", "System", "Unknown")

    # Verify serialization roundtrip
    data = ident.to_dict()
    reconstructed = RuntimeIdentity.from_dict(data)
    assert reconstructed.username == ident.username
    assert reconstructed.domain == ident.domain
    assert reconstructed.is_admin == ident.is_admin
    assert reconstructed.integrity_level == ident.integrity_level


# ============================================================================
# Test D: 2-Tier Authorization Policy
# ============================================================================

def test_policy_routine_operations_auto_approved():
    """Verify routine operations execute without nagging operator approval."""
    policy = WindowsRuntimePolicy()

    # Routine file operations
    req_read = OperationRequest(operation=OperationType.READ_FILE, params={"path": "C:\\temp\\data.txt"})
    ok_read, req_app_read, _reason_read = policy.evaluate_authorization(req_read)
    assert ok_read is True
    assert req_app_read is False

    # Routine listing
    req_list = OperationRequest(operation=OperationType.LIST_DIRECTORY, params={"path": "D:\\"})
    ok_list, req_app_list, _ = policy.evaluate_authorization(req_list)
    assert ok_list is True
    assert req_app_list is False

    # Routine app launch
    req_launch = OperationRequest(operation=OperationType.LAUNCH_APPLICATION, params={"app_name": "calc.exe"})
    ok_launch, req_app_launch, _ = policy.evaluate_authorization(req_launch)
    assert ok_launch is True
    assert req_app_launch is False


def test_policy_destructive_operations_require_approval(runtime_client, temp_workspace):
    """Verify permanent file deletion requires explicit confirmation unless pre-approved."""
    target_file = temp_workspace / "delete_target.txt"
    target_file.write_text("To be deleted", encoding="utf-8")

    # 1. Unapproved delete request fails with APPROVAL_REQUIRED
    with pytest.raises(PermissionError) as exc_info:
        runtime_client.delete_file(str(target_file), is_pre_approved=False)
    assert "APPROVAL_REQUIRED" in str(exc_info.value)
    assert target_file.exists()

    # 2. Pre-approved delete request succeeds
    ok = runtime_client.delete_file(str(target_file), is_pre_approved=True)
    assert ok is True
    assert not target_file.exists()


def test_policy_critical_system_protection():
    """Verify protection against mutating system directories and stopping critical processes."""
    policy = WindowsRuntimePolicy()

    # Modifying Windows system directory
    req_sys_mod = OperationRequest(
        operation=OperationType.DELETE_FILE,
        params={"path": "C:\\Windows\\System32\\drivers\\etc\\hosts"},
        is_pre_approved=False,
    )
    _ok_mod, req_app, reason_mod = policy.evaluate_authorization(req_sys_mod)
    assert req_app is True
    assert "System Directory" in reason_mod or "irreversible action" in reason_mod or "approval" in reason_mod.lower()

    # Terminating critical Windows core process (csrss.exe)
    req_kill_sys = OperationRequest(
        operation=OperationType.STOP_PROCESS,
        params={"pid": 4, "process_name": "csrss.exe"},
    )
    ok_kill, req_app_kill, reason_kill = policy.evaluate_authorization(req_kill_sys)
    assert ok_kill is False
    assert req_app_kill is True
    assert "critical" in reason_kill.lower() or "system process" in reason_kill.lower()


def test_policy_naked_preapproval_without_token_rejected():
    """Verify that claiming is_pre_approved=True without an approval_token fails authorization."""
    broker = PrivilegedExecutionBroker()
    req = OperationRequest(
        operation=OperationType.DELETE_FILE,
        params={"path": "C:\\test\\target.txt"},
        is_pre_approved=True,
        approval_token=None,
    )
    resp = broker.execute_request(req)
    assert resp.success is False
    assert resp.error_code == RuntimeErrorCode.APPROVAL_REQUIRED


# ============================================================================
# Test E: Named Pipe IPC Security & Replay Protection
# ============================================================================

def test_replay_protector():
    """Verify nonce tracking and rejection of replayed IPC requests."""
    protector = ReplayProtector(window_seconds=10.0)

    # 1. Fresh request succeeds
    now = time.time()
    valid, _ = protector.validate_request("nonce-101", now)
    assert valid is True

    # 2. Duplicate nonce within window is rejected
    valid_dup, reason_dup = protector.validate_request("nonce-101", now)
    assert valid_dup is False
    assert "Duplicate nonce" in reason_dup

    # 3. Expired timestamp is rejected
    valid_old, reason_old = protector.validate_request("nonce-102", now - 60.0)
    assert valid_old is False
    assert "Timestamp out of range" in reason_old


def test_ipc_token_validation_and_rejection():
    """Verify server rejects requests with invalid security tokens."""
    server = NamedPipeServer(pipe_name=r"\\.\pipe\test_optimus_ipc_sec")
    # Generate invalid request with corrupted token
    req = OperationRequest(operation=OperationType.DISCOVER_DRIVES, params={})
    data = req.to_dict()
    data["auth_token"] = "CORRUPTED_OR_TAMPERED_TOKEN_999"

    resp = server.handle_raw_request(data)
    assert resp.success is False
    assert resp.error_code == RuntimeErrorCode.ACCESS_DENIED
    assert "Authentication token invalid" in resp.error_message


def test_ipc_malformed_payload_handling():
    """Verify server handles malformed or non-dictionary payloads gracefully."""
    server = NamedPipeServer(pipe_name=r"\\.\pipe\test_optimus_ipc_malform")
    resp = server.handle_raw_request("INVALID_STRING_NOT_A_DICT")
    assert resp.success is False
    assert resp.error_code == RuntimeErrorCode.INVALID_REQUEST


# ============================================================================
# Test F: High-Availability Broker & ToolRegistry Integration
# ============================================================================

def test_broker_audit_trail_logging(temp_workspace):
    """Verify operations generate tamper-evident audit records in JSONL."""
    audit_file = temp_workspace / "audit.jsonl"
    broker = PrivilegedExecutionBroker(audit_file=audit_file)

    req = OperationRequest(operation=OperationType.DISCOVER_DRIVES, params={})
    resp = broker.execute_request(req)
    assert resp.success is True

    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    assert "DISCOVER_DRIVES" in lines[-1]
    assert "audit_id" in lines[-1]


def test_tool_registry_windows_runtime_tools(temp_workspace):
    """Verify all 12 Windows runtime tools are registered and executable via Central ToolRegistry."""
    registry = ToolRegistry()

    expected_tools = [
        "discover_drives",
        "search_files",
        "create_directory",
        "copy_file",
        "move_file",
        "delete_file",
        "launch_application",
        "open_in_explorer",
        "list_processes",
        "inspect_process",
        "inspect_service",
        "get_execution_identity",
    ]
    for tool_name in expected_tools:
        assert registry.is_registered(tool_name), f"Tool {tool_name} was not registered in ToolRegistry"

    # Test discover_drives via ToolRegistry
    res_drives = registry.execute("discover_drives", {})
    assert res_drives.status == "done"
    assert "drives" in res_drives.evidence

    # Test get_execution_identity via ToolRegistry
    res_ident = registry.execute("get_execution_identity", {})
    assert res_ident.status == "done"
    assert "username" in res_ident.evidence

    # Test search_files via ToolRegistry
    res_search = registry.execute("search_files", {"root_path": str(temp_workspace), "pattern": "*"})
    assert res_search.status == "done"

    # Test list_processes via ToolRegistry
    res_proc = registry.execute("list_processes", {"limit": 5})
    assert res_proc.status == "done"
    assert "processes" in res_proc.evidence


def test_live_named_pipe_ipc_roundtrip():
    """Verify live end-to-end client/server communication over a real Windows Named Pipe."""
    test_pipe = rf"\\.\pipe\optimus_test_ipc_live_{int(time.time() * 1000)}"
    server = NamedPipeServer(pipe_name=test_pipe)
    server.start()
    try:
        time.sleep(0.3)
        client = NamedPipeClient(pipe_name=test_pipe)
        assert client.is_available(timeout_ms=1000) is True

        req = OperationRequest(operation=OperationType.DISCOVER_DRIVES, params={})
        resp = client.send_request(req, timeout_seconds=5.0)
        assert resp.success is True
        assert "drives" in resp.data
        assert len(resp.data["drives"]) >= 1
    finally:
        server.stop()
