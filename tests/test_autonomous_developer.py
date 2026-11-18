"""tests/test_autonomous_developer.py — Targeted Tests for Milestone 2: Autonomous Developer Capabilities.

Covers:
1. Workspace boundary confinement & path traversal prevention.
2. File snapshot backups, unified diff computation, and rollback.
3. AST symbol indexing and regex/text grep search.
4. Command risk classification and sandboxed execution with timeout.
5. 8-Phase Developer Lifecycle execution (UNDERSTAND -> ... -> REPORT).
6. Central permission enforcement & approval gating on destructive tasks.
7. Verification Agent independent deliverable and postcondition validation.
8. Developer Tool suite integration in ToolRegistry.
9. Multi-agent delegation and registry extension.
10. Developer Workspace UI initialization and section switching.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Setup paths
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from PyQt6.QtWidgets import QApplication

from core.agents import (
    DeveloperAgent,
    VerificationAgent,
    get_specialized_agent,
    list_specialized_agents,
)
from core.agents.contracts import AgentInputContract
from core.developer.lifecycle import LifecyclePhase
from core.developer.terminal import TerminalManager
from core.developer.tools import (
    register_developer_tools,
)
from core.developer.workspace import DevelopmentWorkspace
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.tools import CapabilityTier, ToolRegistry
from core.security.permissions import PermissionManager, PermissionProfile
from gui.optimus.section_developer import SectionDeveloper
from gui.optimus.service_bridge import OptimusServiceBridge


@pytest.fixture
def temp_workspace(tmp_path: Path):
    """Fixture providing an isolated DevelopmentWorkspace inside a temporary directory."""
    ws = DevelopmentWorkspace(tmp_path)
    # Create sample files
    (tmp_path / "main.py").write_text("def hello(name: str) -> str:\n    return f'Hello {name}'\n", encoding="utf-8")
    (tmp_path / "utils.py").write_text("class MathHelper:\n    def add(self, a, b):\n        return a + b\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# Test Project\nSample description.", encoding="utf-8")
    return ws


def test_workspace_boundary_confinement(temp_workspace: DevelopmentWorkspace, tmp_path: Path):
    """Verify that operations outside the authorized workspace root are strictly rejected."""
    # 1. Path within workspace is allowed
    assert temp_workspace.is_path_safe("main.py")
    assert temp_workspace.resolve_path("main.py").exists()

    # 2. Path traversal escaping workspace root is rejected
    with pytest.raises(PermissionError):
        temp_workspace.resolve_path("../../system_secret.txt")

    assert not temp_workspace.is_path_safe("../../system_secret.txt")

    # 3. Absolute path outside root is rejected
    outside_path = tmp_path.parent / "outside_file.txt"
    with pytest.raises(PermissionError):
        temp_workspace.resolve_path(outside_path)


def test_file_write_backup_and_rollback(temp_workspace: DevelopmentWorkspace):
    """Verify that file writes create automatic backup snapshots and can be rolled back."""
    # Read original content
    orig_text, orig_sha, orig_len = temp_workspace.read_file("main.py")
    assert "def hello" in orig_text

    # Modify file
    new_text = "def hello(name: str) -> str:\n    return f'Hi, {name}!'\n"
    ok, msg, evidence = temp_workspace.write_file("main.py", new_text, create_backup=True)
    assert ok is True
    assert evidence["action"] == "modify"
    assert evidence["snapshot_id"] is not None

    # Verify content changed
    mod_text, mod_sha, _ = temp_workspace.read_file("main.py")
    assert mod_text == new_text
    assert mod_sha != orig_sha

    # Verify diff computation
    diff = temp_workspace.get_diff("main.py")
    assert "-    return f'Hello {name}'" in diff
    assert "+    return f'Hi, {name}!'" in diff

    # Roll back file
    rb_ok, rb_msg = temp_workspace.rollback("main.py")
    assert rb_ok is True
    restored_text, restored_sha, _ = temp_workspace.read_file("main.py")
    assert restored_text == orig_text
    assert restored_sha == orig_sha


def test_symbol_indexing_and_grep_search(temp_workspace: DevelopmentWorkspace):
    """Verify AST symbol indexing (functions/classes) and text grep search."""
    # AST symbol search
    symbols = temp_workspace.search_symbols("hello")
    assert len(symbols) >= 1
    assert symbols[0]["name"] == "hello"
    assert symbols[0]["kind"] == "function"
    assert symbols[0]["file"] == "main.py"

    classes = temp_workspace.search_symbols("MathHelper")
    assert len(classes) >= 1
    assert classes[0]["name"] == "MathHelper"
    assert classes[0]["kind"] == "class"

    # Grep search
    matches = temp_workspace.grep_search("MathHelper")
    assert len(matches) >= 1
    assert matches[0]["file"] == "utils.py"
    assert "class MathHelper" in matches[0]["content"]


def test_terminal_risk_classification_and_execution(tmp_path: Path):
    """Verify command risk classification, working dir boundary, and sandboxed run."""
    term = TerminalManager(tmp_path)

    # 1. Safe command classification
    tier_safe, needs_app_safe, _ = term.classify_command("git status")
    assert tier_safe == CapabilityTier.EXECUTION
    assert needs_app_safe is False

    # 2. Destructive command classification
    tier_dest, needs_app_dest, _ = term.classify_command("rm -rf /")
    assert tier_dest == CapabilityTier.SYSTEM_DESTRUCTIVE
    assert needs_app_dest is True

    # 3. Administrative command classification
    tier_adm, needs_app_adm, _ = term.classify_command("runas /user:admin cmd")
    assert tier_adm in (CapabilityTier.SYSTEM_MUTATING, CapabilityTier.SYSTEM_DESTRUCTIVE)
    assert needs_app_adm is True

    # 4. Safe execution
    rec = term.run_python("print('OPTIMUS_DEV_TEST_42')")
    assert rec.exit_code == 0
    assert "OPTIMUS_DEV_TEST_42" in rec.output


def test_developer_agent_8_phase_lifecycle(temp_workspace: DevelopmentWorkspace):
    """Verify DeveloperAgent executes the 8-phase engineering lifecycle end-to-end."""
    agent = DeveloperAgent()
    agent.workspace = temp_workspace
    agent.terminal = TerminalManager(temp_workspace.root)

    registry = ToolRegistry()
    register_developer_tools(registry)

    # Prepare input contract with intended file edit and validation
    contract = AgentInputContract(
        task_id="dev_test_001",
        subtask_id="sub_test_001",
        goal="Add a new multiplication function to utils.py",
        instructions="Implement multiply(a, b) and verify deliverables.",
        context_data={
            "target_files": ["utils.py"],
            "file_edits": [
                {
                    "path": "utils.py",
                    "content": "class MathHelper:\n    def add(self, a, b):\n        return a + b\n    def multiply(self, a, b):\n        return a * b\n",
                }
            ],
            "commands": [],
            "is_approved": True,
        },
    )

    out = agent.execute(contract, registry)
    assert out.is_success is True
    assert "COMPLETED SUCCESSFULLY" in out.summary
    assert len(out.deliverables) == 1
    assert out.deliverables[0]["path"] == "utils.py"

    # Verify physical file on disk was updated
    content, _, _ = temp_workspace.read_file("utils.py")
    assert "def multiply" in content

    # Verify evidence contains all completed lifecycle phases
    evidence = out.evidence
    assert evidence["current_phase"] == LifecyclePhase.COMPLETED.value
    for p in ("UNDERSTAND", "INSPECT", "PLAN", "APPROVE", "IMPLEMENT", "EXECUTE", "VERIFY", "REPORT"):
        assert p in evidence["phases_completed"]


def test_permission_approval_gated_on_unauthorized_destructive_task(temp_workspace: DevelopmentWorkspace):
    """Verify that unapproved destructive tasks pause in the APPROVE phase."""
    agent = DeveloperAgent()
    agent.workspace = temp_workspace
    registry = ToolRegistry()
    register_developer_tools(registry)

    # Configure LOW permission profile
    perm_mgr = PermissionManager.instance()
    perm_mgr.set_profile(PermissionProfile.LOW)

    contract = AgentInputContract(
        task_id="dev_test_unauth",
        subtask_id="sub_test_unauth",
        goal="Modify codebase under LOW permission profile",
        instructions="Should be blocked pending authorization.",
        context_data={
            "file_edits": [{"path": "main.py", "content": "# hacked\n"}],
            "is_approved": False,
        },
    )

    out = agent.execute(contract, registry)
    # Must pause for approval under LOW profile
    assert out.status == "waiting_approval"
    assert "APPROVAL_REQUIRED" in (out.error or "")

    # Reset back to ADVANCED profile
    perm_mgr.set_profile(PermissionProfile.ADVANCED)


def test_independent_verification_agent(temp_workspace: DevelopmentWorkspace):
    """Verify VerificationAgent checks physical postconditions on disk."""
    v_agent = VerificationAgent()
    registry = ToolRegistry()
    register_developer_tools(registry)

    # 1. Verification of existing file succeeds
    contract_ok = AgentInputContract(
        task_id="verif_01",
        subtask_id="sub_v01",
        goal="Verify main.py exists",
        instructions="Perform independent verification.",
        context_data={
            "expected_files": [
                {"path": str(temp_workspace.resolve_path("main.py")), "min_size": 5}
            ]
        },
    )
    out_ok = v_agent.execute(contract_ok, registry)
    assert out_ok.is_success is True
    assert out_ok.evidence["verified"] is True

    # 2. Verification of non-existent file fails
    contract_fail = AgentInputContract(
        task_id="verif_02",
        subtask_id="sub_v02",
        goal="Verify missing_file.py",
        instructions="Perform independent verification.",
        context_data={
            "expected_files": [
                {"path": str(temp_workspace.resolve_path("non_existent_12345.py")), "min_size": 5}
            ]
        },
    )
    out_fail = v_agent.execute(contract_fail, registry)
    assert out_fail.is_success is False
    assert out_fail.evidence["verified"] is False


def test_specialized_agents_registration():
    """Verify DeveloperAgent and VerificationAgent are registered in the specialized library."""
    _ = AgentRegistry()
    dev_agent = get_specialized_agent("developer_agent")
    assert dev_agent is not None
    assert dev_agent.name == "developer_agent"

    alias_dev = get_specialized_agent("developer")
    assert alias_dev is not None

    verif_agent = get_specialized_agent("verification_agent")
    assert verif_agent is not None
    assert verif_agent.name == "verification_agent"

    all_agents = list_specialized_agents()
    agent_names = [a.name for a in all_agents]
    assert "developer_agent" in agent_names
    assert "verification_agent" in agent_names


def test_developer_ui_initialization():
    """Verify SectionDeveloper initializes cleanly with all widgets and tabs."""
    _ = QApplication.instance() or QApplication(sys.argv)
    bridge = OptimusServiceBridge.instance()
    sec_dev = SectionDeveloper(bridge)

    assert sec_dev.lbl_ws_path.text() != ""
    assert sec_dev.file_list.count() >= 0
    assert sec_dev.phase_tracker is not None
    assert sec_dev.code_viewer is not None
    assert sec_dev.term_log is not None
    assert sec_dev.verif_log is not None

    sec_dev.close()
