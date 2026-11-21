r"""tests/test_explorer_capability.py — Comprehensive Test Suite for Milestone 15.1.

Verifies:
1. Opening D:\project in File Explorer.
2. Opening D:\project\optimus in File Explorer.
3. Opening C:\Users\<user>\Downloads in File Explorer.
4. Opening valid file with /select highlighting.
5. Error handling for nonexistent target paths.
6. Security enforcement on protected credential paths (.ssh, .env).
7. Execution across all relevant agent profiles (filesystem, general, researcher, execution_agent).
8. Actual Explorer process launch evidence (PID, verified, session_id).
9. MRE and CentralOrchestrator intent parsing for all required natural language phrasings.
10. file_controller action delegation for open, open_folder, open_in_explorer.
11. Windows runtime client & privileged broker dispatch.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from actions.file_controller import file_controller
from core.filesystem.service import UnifiedFilesystemService
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.mre import MREClassifier
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.tools import ToolRegistry
from core.windows_runtime.broker import PrivilegedExecutionBroker
from core.windows_runtime.client import get_windows_runtime_client


@pytest.fixture(autouse=True)
def reset_service():
    """Ensure singleton is fresh for each test."""
    UnifiedFilesystemService.reset_instance()
    yield
    UnifiedFilesystemService.reset_instance()


# ── 1. Path Verification Tests ───────────────────────────────────────────────

def test_open_d_project():
    r"""Verify opening D:\project (or mounted drive root) in File Explorer."""
    svc = UnifiedFilesystemService.instance()
    target = r"D:\project" if Path(r"D:\project").exists() else r"D:\\" if Path("D:\\").exists() else r"C:\\"
    res = svc.open_in_explorer(target)
    assert res.status == "done"
    assert "in Windows File Explorer" in res.output or "in system file manager" in res.output
    assert res.evidence.get("verified") is True
    assert res.evidence.get("pid") is not None and res.evidence["pid"] > 0


def test_open_d_project_optimus():
    r"""Verify opening D:\project\optimus (or active project root) in File Explorer."""
    svc = UnifiedFilesystemService.instance()
    target = r"D:\project\optimus" if Path(r"D:\project\optimus").exists() else str(Path.cwd())
    res = svc.open_in_explorer(target)
    assert res.status == "done"
    assert res.evidence.get("verified") is True
    assert res.evidence.get("path") == str(Path(target).resolve())


def test_open_user_downloads():
    r"""Verify opening C:\Users\<user>\Downloads via known folder shortcut and absolute path."""
    svc = UnifiedFilesystemService.instance()
    # Test shortcut
    res_short = svc.open_in_explorer("downloads")
    assert res_short.status == "done"
    assert "Downloads" in res_short.evidence.get("path", "")
    assert res_short.evidence.get("verified") is True

    # Test explicit user path
    dl_path = Path.home() / "Downloads"
    if dl_path.exists():
        res_abs = svc.open_in_explorer(str(dl_path))
        assert res_abs.status == "done"
        assert res_abs.evidence.get("verified") is True


def test_open_valid_file_selected(tmp_path):
    """Verify opening a file triggers Explorer /select highlighting."""
    test_file = tmp_path / "target_document.pdf"
    test_file.write_text("dummy PDF payload", encoding="utf-8")

    svc = UnifiedFilesystemService.instance()
    # Explicit select_file=True
    res = svc.open_in_explorer(str(test_file), select_file=True)
    assert res.status == "done"
    assert res.evidence.get("select_file") is True
    assert res.evidence.get("is_file") is True
    assert "file selected" in res.output

    # Implicit select: target is a file, select_file omitted -> automatically highlights
    res_auto = svc.open_in_explorer(str(test_file))
    assert res_auto.status == "done"
    assert res_auto.evidence.get("select_file") is True


# ── 2. Error and Security Handling Tests ────────────────────────────────────

def test_open_nonexistent_path():
    """Verify attempting to open a nonexistent path fails cleanly with PATH_NOT_FOUND."""
    svc = UnifiedFilesystemService.instance()
    res = svc.open_in_explorer(r"C:\nonexistent_optimus_dir_xyz_12345")
    assert res.status == "failed"
    assert "PATH_NOT_FOUND" in res.error or "Target does not exist" in res.error


def test_open_inaccessible_credential_path(tmp_path):
    """Verify opening sensitive credential stores (.ssh, .env) is blocked without approval token."""
    svc = UnifiedFilesystemService.instance()
    secret_env = tmp_path / ".env.production"
    secret_env.write_text("DB_PASSWORD=secret", encoding="utf-8")

    # Without approval
    res_denied = svc.open_in_explorer(str(secret_env))
    assert res_denied.status == "unverified"
    assert "APPROVAL_REQUIRED" in res_denied.error
    assert res_denied.evidence.get("requires_approval") is True

    # With approval
    res_approved = svc.open_in_explorer(str(secret_env), context={"is_approved": True})
    assert res_approved.status == "done"
    assert res_approved.evidence.get("verified") is True


def test_open_unc_path_rejected():
    """Verify remote UNC paths are rejected before Explorer launch."""
    svc = UnifiedFilesystemService.instance()
    res = svc.open_in_explorer(r"\\attacker\share\folder")
    assert res.status == "failed"
    assert "UNC_VIOLATION" in res.error


# ── 3. Agent Profile & Capability Authorization Tests ───────────────────────

@pytest.mark.parametrize("agent_name", ["filesystem", "general", "researcher", "execution_agent"])
def test_open_explorer_across_agent_profiles(agent_name):
    """Verify all relevant agent profiles are authorized to execute open_in_explorer."""
    tool_reg = ToolRegistry()
    agent_reg = AgentRegistry()
    agent = agent_reg.get(agent_name)
    assert agent is not None, f"Agent '{agent_name}' should exist in registry."

    res = tool_reg.execute(
        "open_in_explorer",
        {"path": "downloads"},
        caller_agent=agent.name,
        allowed_tools=agent.allowed_tools,
        allowed_tiers=agent.allowed_tiers,
    )
    assert res.status == "done"
    assert "Opened" in res.output
    assert res.evidence.get("verified") is True


def test_tool_aliases_open_directory_and_open_folder():
    """Verify open_directory and open_folder seamlessly route to open_in_explorer."""
    tool_reg = ToolRegistry()
    res_dir = tool_reg.execute("open_directory", {"path": "downloads"}, caller_agent="filesystem")
    assert res_dir.status == "done"

    res_fld = tool_reg.execute("open_folder", {"path": "downloads"}, caller_agent="filesystem")
    assert res_fld.status == "done"


# ── 4. Actual Explorer Launch & Session Evidence ────────────────────────────

def test_actual_explorer_process_launch_evidence(tmp_path):
    """Verify real Explorer process PID and desktop session ID in the interactive environment."""
    svc = UnifiedFilesystemService.instance()
    res = svc.open_in_explorer(str(tmp_path))
    assert res.status == "done"
    pid = res.evidence.get("pid")
    assert pid is not None and pid > 0

    if os.name == "nt":
        # Interactive desktop session ID should be >= 1 (not Session 0)
        assert res.evidence.get("session_id", 0) >= 1


def test_session_0_isolation_safeguard(tmp_path):
    """Verify that if running in Session 0 without an interactive console, it returns a diagnostic error."""
    svc = UnifiedFilesystemService.instance()

    with patch("ctypes.windll.kernel32.ProcessIdToSessionId") as mock_pid_to_session, \
         patch("ctypes.windll.kernel32.WTSGetActiveConsoleSessionId", return_value=0):
        # Simulate Session 0
        def fake_session(pid, s_ref):
            s_ref._obj.value = 0
            return 1
        mock_pid_to_session.side_effect = fake_session

        res = svc.open_in_explorer(str(tmp_path))
        assert res.status == "failed"
        assert "SESSION_0_ISOLATION" in res.error


# ── 5. Intent Parsing & Natural Language Routing Tests ──────────────────────

@pytest.mark.parametrize("user_query,expected_path,expected_select", [
    (r"Open D:\project in Explorer", r"D:\project", False),
    (r"Open D:\project in Explorer.", r"D:\project", False),
    (r"Open D:\project in File Explorer", r"D:\project", False),
    (r"Open D:\project in Windows Explorer", r"D:\project", False),
    (r"Show this directory in Explorer", str(Path.cwd()), False),
    (r"Open this folder", str(Path.cwd()), False),
    (r"Open D:\project", r"D:\project", False),
    (r"Open this file's containing folder", str(Path.cwd()), False),
    (r"Show this file in Explorer", str(Path.cwd()), True),
    (r"Open downloads in explorer", "downloads", False),
    (r"Open downloads", "downloads", False),
])
def test_mre_intent_classification_explorer(user_query, expected_path, expected_select):
    """Verify MREClassifier reliably parses user phrasing for Explorer launch."""
    classifier = MREClassifier()
    decision = classifier.classify(user_query)

    assert decision.tool_name == "open_in_explorer"
    assert decision.arguments.get("path") == expected_path
    assert decision.arguments.get("select_file") == expected_select


def test_mre_does_not_hijack_application_launches():
    """Verify that launching notepad or calc is not mistaken for an Explorer folder query."""
    classifier = MREClassifier()
    dec_notepad = classifier.classify("open notepad")
    assert dec_notepad.tool_name == "launch_application"
    assert dec_notepad.arguments.get("app_name") == "notepad"

    dec_calc = classifier.classify("open calc")
    assert dec_calc.tool_name == "launch_application"
    assert dec_calc.arguments.get("app_name") == "calc"


def test_orchestrator_fast_path_open_in_explorer():
    """Verify CentralOrchestrator fast-path directly launches Explorer without yielding to LLM planner."""
    orch = CentralOrchestrator()
    res = orch._try_fast_path(r"Open D:\project in Explorer", "task-fast")
    assert res is not None
    success, out, _agent, side_effects = res
    assert success is True
    assert "File Explorer" in out or "file manager" in out
    assert any(se.get("tool_name") == "open_in_explorer" for se in side_effects)

    task = orch.execute_task(r"Open D:\project in Explorer")
    status_str = task.status.value if hasattr(task.status, "value") else str(task.status)
    assert status_str.lower() == "completed"


# ── 6. file_controller Action Dispatch Tests ─────────────────────────────────

def test_file_controller_action_open(tmp_path):
    """Verify file_controller accepts open, open_folder, open_in_explorer actions."""
    # action: open_in_explorer
    out1 = file_controller(parameters={"action": "open_in_explorer", "path": str(tmp_path)})
    assert "Opened" in out1

    # action: open_folder
    out2 = file_controller(parameters={"action": "open_folder", "path": str(tmp_path)})
    assert "Opened" in out2

    # action: open
    out3 = file_controller(parameters={"action": "open", "path": str(tmp_path)})
    assert "Opened" in out3


# ── 7. Privileged Broker & WindowsRuntimeClient Tests ─────────────────────────

def test_privileged_broker_and_runtime_client_open_explorer(tmp_path):
    """Verify PrivilegedExecutionBroker and WindowsRuntimeClient open_in_explorer."""
    broker = PrivilegedExecutionBroker()
    res = broker._op_open_in_explorer({"path": str(tmp_path), "select_file": False})
    assert res.get("status") == "explorer_opened"
    assert res.get("evidence", {}).get("verified") is True

    client = get_windows_runtime_client()
    ok = client.open_in_explorer(str(tmp_path))
    assert ok is True
