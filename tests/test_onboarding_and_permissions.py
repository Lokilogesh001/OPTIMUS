"""tests/test_onboarding_and_permissions.py — Targeted Tests for Milestone 1: First-Launch Onboarding & Permission Architecture.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.orchestrator.tools import CapabilityTier, ToolDefinition, ToolRegistry, ToolResult
from core.security.credentials import (
    delete_credential,
    get_credential,
    mask_credential,
    store_credential,
)
from core.security.onboarding import (
    get_onboarding_config,
    is_onboarded,
    save_onboarding_config,
    verify_provider_connection,
)
from core.security.permissions import (
    PermissionManager,
    PermissionProfile,
)


@pytest.fixture
def clean_permission_mgr(tmp_path):
    settings_file = tmp_path / "optimus_settings.json"
    mgr = PermissionManager(settings_file=settings_file)
    return mgr


# ── 1. Secure Credential Management Tests ────────────────────────────────────

def test_credential_store_and_masking(tmp_path):
    # Test storing and retrieval
    key_val = "sk-test-secret-key-1234567890abcdef"
    stored = store_credential("test_prov", key_val)
    assert stored is True

    retrieved = get_credential("test_prov")
    assert retrieved == key_val

    # Test masking
    masked = mask_credential(key_val)
    assert "1234567890" not in masked
    assert masked.startswith("sk-t")
    assert masked.endswith("cdef")

    # Test deletion
    deleted = delete_credential("test_prov")
    assert deleted is True
    assert get_credential("test_prov") is None


def test_credential_env_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback-token-999")
    val = get_credential("openai")
    assert val == "sk-env-fallback-token-999"


# ── 2. Permission Profile Evaluation Tests ───────────────────────────────────

def test_permission_profile_low(clean_permission_mgr):
    mgr = clean_permission_mgr
    mgr.set_profile(PermissionProfile.LOW)

    tool_read = ToolDefinition(
        name="read_file",
        description="Read file content",
        parameters={},
        capability_tier=CapabilityTier.READ_ONLY,
    )
    verdict = mgr.evaluate_tool(tool_read, "agent", {})
    assert verdict.allowed is True
    assert verdict.requires_approval is False

    tool_write = ToolDefinition(
        name="write_file",
        description="Write file content",
        parameters={"path": "test.txt"},
        capability_tier=CapabilityTier.WORKSPACE_WRITE,
    )
    verdict = mgr.evaluate_tool(tool_write, "agent", {"path": "test.txt"})
    assert verdict.allowed is False
    assert verdict.requires_approval is True

    tool_exec = ToolDefinition(
        name="run_cmd",
        description="Run command",
        parameters={},
        capability_tier=CapabilityTier.EXECUTION,
    )
    verdict = mgr.evaluate_tool(tool_exec, "agent", {})
    assert verdict.allowed is False
    assert verdict.requires_approval is True


def test_permission_profile_moderate(clean_permission_mgr):
    mgr = clean_permission_mgr
    mgr.set_profile(PermissionProfile.MODERATE)

    tool_write = ToolDefinition(
        name="write_file",
        description="Write file content",
        parameters={"path": "test.txt"},
        capability_tier=CapabilityTier.WORKSPACE_WRITE,
    )
    verdict = mgr.evaluate_tool(tool_write, "agent", {"path": "test.txt"})
    assert verdict.allowed is True

    tool_exec = ToolDefinition(
        name="run_cmd",
        description="Run command",
        parameters={"command": "npm test"},
        capability_tier=CapabilityTier.EXECUTION,
    )
    verdict = mgr.evaluate_tool(tool_exec, "agent", {"command": "npm test"})
    assert verdict.allowed is False
    assert verdict.requires_approval is True


def test_permission_profile_advanced(clean_permission_mgr):
    mgr = clean_permission_mgr
    mgr.set_profile(PermissionProfile.ADVANCED)

    tool_exec = ToolDefinition(
        name="run_cmd",
        description="Run command",
        parameters={"command": "npm test"},
        capability_tier=CapabilityTier.EXECUTION,
    )
    verdict = mgr.evaluate_tool(tool_exec, "agent", {"command": "npm test"})
    assert verdict.allowed is True
    assert verdict.requires_approval is False


def test_mandatory_security_invariants_across_all_profiles(clean_permission_mgr):
    """Even in ADVANCED profile, destructive actions must trigger mandatory approval."""
    mgr = clean_permission_mgr
    mgr.set_profile(PermissionProfile.ADVANCED)

    # 1. Destructive tool
    tool_destr = ToolDefinition(
        name="wipe_disk",
        description="Destructive disk wipe",
        parameters={},
        capability_tier=CapabilityTier.SYSTEM_DESTRUCTIVE,
    )
    verdict = mgr.evaluate_tool(tool_destr, "agent", {})
    assert verdict.allowed is False
    assert verdict.requires_approval is True
    assert "DESTRUCTIVE" in verdict.approval_reason

    # 2. Critical destructive keyword in arguments
    tool_exec = ToolDefinition(
        name="shell_exec",
        description="Execute shell command",
        parameters={"cmd": "STRING"},
        capability_tier=CapabilityTier.EXECUTION,
    )
    verdict = mgr.evaluate_tool(tool_exec, "agent", {"cmd": "rmdir /s /q c:\\"})
    assert verdict.allowed is False
    assert verdict.requires_approval is True
    assert "MANDATORY SECURITY GATE" in verdict.approval_reason


# ── 3. Central ToolRegistry Enforcement Integration ──────────────────────────

def test_tool_registry_enforces_central_permissions(tmp_path):
    settings_file = tmp_path / "optimus_settings.json"
    mgr = PermissionManager(settings_file=settings_file)
    mgr.set_profile(PermissionProfile.LOW)

    with patch("core.security.permissions.get_permission_manager", return_value=mgr):
        registry = ToolRegistry()

        # Workspace write should be blocked in LOW profile without pre-approval
        dummy_write = ToolDefinition(
            name="test_modifying_tool",
            description="Modifies file",
            parameters={},
            capability_tier=CapabilityTier.WORKSPACE_WRITE,
            handler=lambda p, **kw: ToolResult(status="done", output="written"),
        )
        registry.register(dummy_write)

        res = registry.execute("test_modifying_tool", {}, caller_agent="worker")
        assert res.status == "unverified"
        assert "APPROVAL_REQUIRED" in (res.error or "")

        # But pre-approved execution proceeds
        res_approved = registry.execute("test_modifying_tool", {}, caller_agent="worker", context={"is_approved": True})
        assert res_approved.status == "done"
        assert res_approved.output == "written"


# ── 4. Onboarding State & Verification Tests ─────────────────────────────────

def test_onboarding_state_persistence(tmp_path):
    settings_file = tmp_path / "test_settings.json"
    assert is_onboarded(settings_path=settings_file) is False

    save_onboarding_config({
        "primary_provider": "openai",
        "model_name": "gpt-4o",
        "permission_profile": "ADVANCED",
    }, settings_path=settings_file)

    assert is_onboarded(settings_path=settings_file) is True
    cfg = get_onboarding_config(settings_path=settings_file)
    assert cfg["primary_provider"] == "openai"
    assert cfg["model_name"] == "gpt-4o"
    assert cfg["permission_profile"] == "ADVANCED"


def test_verify_provider_connection_mock():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok, msg = verify_provider_connection("openai", api_key="sk-test-mock-key")
        assert ok is True
        assert "verified successfully" in msg


def test_onboarding_dialog_initialization():
    from PyQt6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication(sys.argv)
    from gui.optimus.onboarding_dialog import OptimusOnboardingDialog
    diag = OptimusOnboardingDialog()
    assert diag.stack.count() == 5
    assert diag.selected_provider == "gemini"
    assert diag.selected_profile == PermissionProfile.ADVANCED

