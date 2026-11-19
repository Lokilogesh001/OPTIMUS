"""tests/test_policy_engine.py — Comprehensive Test Suite for Authoritative Policy Engine.

Verifies:
1. Deterministic canonical hash computation.
2. Immutable security deny invariants (cannot be overridden by human approval).
3. Agent capability boundary enforcement (anti-privilege escalation).
4. Operation-bound, expiring, single-use, atomically consumed ApprovalTokens.
5. Detection of token replay, token expiration, and argument tampering.
6. Integration with CommandGuard (resolves DEF-02).
7. Integration with MissionCoordinator (resolves DEF-03).
8. Integration with ApprovalManager.
"""

from __future__ import annotations

import pytest

from core.missions.coordinator import MissionCoordinator
from core.missions.memory import MissionMemory
from core.missions.types import MissionRecord, MissionSubtask
from core.sandbox.command_guard import check_command_safety
from core.security.policy_engine import (
    ApprovalToken,
    OperationRequest,
    PolicyEngine,
    compute_canonical_hash,
)
from core.workflows.approval import ApprovalManager
from core.workflows.types import ApprovalStatus


@pytest.fixture(autouse=True)
def reset_policy_engine():
    """Ensure a clean PolicyEngine singleton instance for every test."""
    PolicyEngine.reset_instance()
    yield
    PolicyEngine.reset_instance()


def test_canonical_hash_determinism():
    """Verify compute_canonical_hash produces identical digests regardless of key ordering."""
    args1 = {"path": "C:\\test\\file.txt", "mode": "write", "content": "hello"}
    args2 = {"content": "hello", "path": "C:\\test\\file.txt", "mode": "write"}

    h1 = compute_canonical_hash("write_file", args1)
    h2 = compute_canonical_hash("WRITE_FILE", args2)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256


def test_immutable_deny_rules_cannot_be_overridden_by_approval():
    """Verify destructive/immutable operations are rejected even with valid approval tokens."""
    engine = PolicyEngine.instance()

    # Pre-generate a token
    token = engine.create_approval_token(
        mission_id="m_1",
        subtask_id="st_1",
        operator_id="admin_user",
        tool_name="execute_command",
        arguments={"command": "format C: /fs:NTFS"},
    )

    req = OperationRequest(
        tool_name="execute_command",
        agent_name="admin_agent",
        arguments={"command": "format C: /fs:NTFS"},
        approval_token=token,
    )

    verdict = engine.evaluate_operation(req)
    assert not verdict.is_authorized
    assert not verdict.requires_approval
    assert "IMMUTABLE_SECURITY_VIOLATION" in verdict.reason

    # Test defender tampering
    req2 = OperationRequest(
        tool_name="execute_command",
        agent_name="admin_agent",
        arguments={"command": "Set-MpPreference -DisableRealtimeMonitoring $true"},
        approval_token=token,
    )
    verdict2 = engine.evaluate_operation(req2)
    assert not verdict2.is_authorized
    assert "IMMUTABLE_SECURITY_VIOLATION" in verdict2.reason


def test_agent_capability_boundary_enforcement():
    """Verify agents cannot invoke tools outside their declared allowed_tools scope."""
    engine = PolicyEngine.instance()

    req = OperationRequest(
        tool_name="write_file",
        agent_name="researcher",
        arguments={"path": "C:\\notes.txt", "content": "data"},
        allowed_tools=["read_file", "search_web"],
    )

    verdict = engine.evaluate_operation(req)
    assert not verdict.is_authorized
    assert not verdict.requires_approval
    assert "CAPABILITY_VIOLATION" in verdict.reason


def test_privileged_operation_requires_approval():
    """Verify privileged operations require approval when no token is supplied."""
    engine = PolicyEngine.instance()

    req = OperationRequest(
        tool_name="execute_command",
        agent_name="developer",
        arguments={"command": "runas /user:Administrator cmd.exe"},
        allowed_tools=["execute_command"],
    )

    verdict = engine.evaluate_operation(req)
    assert not verdict.is_authorized
    assert verdict.requires_approval
    assert "MANDATORY_APPROVAL_REQUIRED" in verdict.reason
    assert verdict.approval_challenge is not None
    assert verdict.approval_challenge["canonical_hash"] == compute_canonical_hash("execute_command", {"command": "runas /user:Administrator cmd.exe"})


def test_valid_approval_token_authorizes_privileged_operation():
    """Verify an authentic approval token permits a privileged operation."""
    engine = PolicyEngine.instance()
    args = {"command": "runas /user:Administrator netstat.exe"}

    token = engine.create_approval_token(
        mission_id="m_sec_1",
        subtask_id="st_sec_1",
        operator_id="security_officer",
        tool_name="execute_command",
        arguments=args,
        ttl_seconds=300.0,
    )

    req = OperationRequest(
        tool_name="execute_command",
        agent_name="developer",
        arguments=args,
        allowed_tools=["execute_command"],
        approval_token=token,
    )

    verdict = engine.evaluate_operation(req)
    assert verdict.is_authorized
    assert not verdict.requires_approval
    assert "Authorized via valid single-use approval token" in verdict.reason


def test_approval_token_single_use_atomicity():
    """Verify an approval token cannot be replayed after its initial consumption."""
    engine = PolicyEngine.instance()
    args = {"command": "runas /user:Administrator whoami.exe"}

    token = engine.create_approval_token(
        mission_id="m_replay",
        subtask_id="st_replay",
        operator_id="admin",
        tool_name="execute_command",
        arguments=args,
    )

    req = OperationRequest(
        tool_name="execute_command",
        agent_name="developer",
        arguments=args,
        allowed_tools=["execute_command"],
        approval_token=token,
    )

    # First consumption: Authorized
    verdict1 = engine.evaluate_operation(req)
    assert verdict1.is_authorized

    # Second consumption: Replay rejected!
    verdict2 = engine.evaluate_operation(req)
    assert not verdict2.is_authorized
    assert "APPROVAL_TOKEN_REPLAY_DETECTED" in verdict2.reason


def test_approval_token_expiration():
    """Verify an expired approval token is rejected."""
    engine = PolicyEngine.instance()
    args = {"command": "runas /user:Administrator ipconfig.exe"}

    token = engine.create_approval_token(
        mission_id="m_exp",
        subtask_id="st_exp",
        operator_id="admin",
        tool_name="execute_command",
        arguments=args,
        ttl_seconds=-1.0,  # Pre-expired
    )

    req = OperationRequest(
        tool_name="execute_command",
        agent_name="developer",
        arguments=args,
        allowed_tools=["execute_command"],
        approval_token=token,
    )

    verdict = engine.evaluate_operation(req)
    assert not verdict.is_authorized
    assert "APPROVAL_TOKEN_EXPIRED" in verdict.reason


def test_approval_token_tampering_hash_mismatch():
    """Verify that altering operation parameters invalidates the approval token."""
    engine = PolicyEngine.instance()
    original_args = {"command": "runas /user:Administrator tasklist.exe"}
    tampered_args = {"command": "runas /user:Administrator reg.exe add ..."}

    token = engine.create_approval_token(
        mission_id="m_tamper",
        subtask_id="st_tamper",
        operator_id="admin",
        tool_name="execute_command",
        arguments=original_args,
    )

    req = OperationRequest(
        tool_name="execute_command",
        agent_name="developer",
        arguments=tampered_args,
        allowed_tools=["execute_command"],
        approval_token=token,
    )

    verdict = engine.evaluate_operation(req)
    assert not verdict.is_authorized
    assert "APPROVAL_TOKEN_HASH_MISMATCH" in verdict.reason


def test_command_guard_integration_resolves_def02(tmp_path):
    """Verify CommandGuard permits pre-approved runas command with token (resolving DEF-02)."""
    engine = PolicyEngine.instance()
    cmd = "runas /user:Administrator echo test"

    # 1. Without token: Blocked
    is_safe_no_token, reason_no_token = check_command_safety(cmd)
    assert not is_safe_no_token
    assert "Privilege escalation" in reason_no_token

    # 2. With valid ApprovalToken: Permitted!
    token = engine.create_approval_token(
        mission_id="m_cg",
        subtask_id="st_cg",
        operator_id="admin",
        tool_name="execute_command",
        arguments={"command": cmd},
    )
    is_safe_with_token, reason_with_token = check_command_safety(cmd, approval_token=token)
    assert is_safe_with_token
    assert reason_with_token == ""

    # 3. But immutable deny command is blocked even with token
    immutable_cmd = "format D: /fs:NTFS"
    token_imm = engine.create_approval_token(
        mission_id="m_cg",
        subtask_id="st_cg",
        operator_id="admin",
        tool_name="execute_command",
        arguments={"command": immutable_cmd},
    )
    is_safe_imm, reason_imm = check_command_safety(immutable_cmd, approval_token=token_imm)
    assert not is_safe_imm
    assert "Disk partition formatting" in reason_imm


def test_mission_coordinator_prevents_privilege_escalation_resolves_def03():
    """Verify MissionCoordinator does NOT append missing tools and rejects execution (resolving DEF-03)."""
    coordinator = MissionCoordinator()

    # Researcher only has read-only tools
    researcher_prof = coordinator.agent_registry.get("researcher")
    assert "write_file" not in researcher_prof.allowed_tools
    original_allowed_tools = list(researcher_prof.allowed_tools)

    subtask = MissionSubtask(
        subtask_id="st_escalate",
        title="Unauthorized Write Subtask",
        assigned_agent="researcher",
        required_tools=["write_file"],
    )
    record = MissionRecord(mission_id="m_esc", objective="Test Escalation")
    memory = MissionMemory("m_esc")

    success, output, _evidence = coordinator.execute_subtask(subtask, record, memory)

    # Must fail authorization
    assert not success
    assert "POLICY_VIOLATION" in output or "CAPABILITY_VIOLATION" in output

    # Agent profile MUST REMAIN UNMODIFIED
    after_prof = coordinator.agent_registry.get("researcher")
    assert "write_file" not in after_prof.allowed_tools
    assert list(after_prof.allowed_tools) == original_allowed_tools


def test_approval_manager_integration():
    """Verify ApprovalManager.create_approval_token generates valid tokens for approved gates."""
    mgr = ApprovalManager()
    gate = mgr.request_approval(
        workflow_id="wf_appr_1",
        execution_id="exec_appr_1",
        step_id="step_appr_1",
        reason="Elevated system execution required",
    )

    args = {"command": "runas /user:Admin net.exe"}

    # 1. Cannot create token while PENDING
    with pytest.raises(ValueError, match="not in APPROVED status"):
        mgr.create_approval_token(gate.approval_id, "execute_command", args)

    # 2. Cannot approve with automated agent name
    with pytest.raises(ValueError, match="Automated LLM output cannot provide human approval"):
        mgr.approve(gate.approval_id, approver="ai_agent")

    # 3. Approve with human identity
    decision = mgr.approve(gate.approval_id, approver="alice_admin", reason="Authorized for diagnostics")
    assert decision.status == ApprovalStatus.APPROVED

    # 4. Create token
    token = mgr.create_approval_token(gate.approval_id, "execute_command", args)
    assert isinstance(token, ApprovalToken)
    assert token.operator_id == "alice_admin"
    assert token.canonical_hash == compute_canonical_hash("execute_command", args)

    # 5. Verify token authorizes through PolicyEngine
    engine = PolicyEngine.instance()
    req = OperationRequest(
        tool_name="execute_command",
        agent_name="developer",
        arguments=args,
        allowed_tools=["execute_command"],
        approval_token=token,
    )
    verdict = engine.evaluate_operation(req)
    assert verdict.is_authorized
