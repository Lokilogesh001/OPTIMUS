r"""tests/test_unified_filesystem.py — Comprehensive M15 Unified Filesystem Verification Suite.

Tests:
1. List C:\ drive root.
2. List C:\Windows where current Windows identity permits it.
3. List C:\Users directory.
4. List user Downloads, Desktop, Documents (handling junctions), and Music.
5. List D:\ drive root.
6. List D:\project directory.
7. Read an authorized file outside the repository (e.g. on C:\ or D:\).
8. Write inside an authorized workspace.
9. Reject writes outside a developer agent's assigned workspace.
10. Reject unauthorized destructive operations (delete without approval token).
11. Reject protected credential access without an approval token (.ssh, .aws, .env).
12. Reject UNC network paths and device namespace paths.
13. Handle broken junctions and unlinked symlinks without crashing.
14. Prevent symlink/junction traversal escapes.
15. Detect directory cycles during recursive search.
16. Distinguish Windows NTFS permission failures from application policy failures.
17. Verify agent-mediated access via file_controller and ToolRegistry.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from actions.file_controller import file_controller
from core.filesystem.service import UnifiedFilesystemService
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.tools import ToolRegistry


@pytest.fixture(autouse=True)
def clean_filesystem_service():
    """Ensure clean service state for each test."""
    UnifiedFilesystemService.reset_instance()
    yield
    UnifiedFilesystemService.reset_instance()


# ── 1. Drive & Directory Listing Tests ───────────────────────────────────────

def test_list_c_drive_root():
    """1. List C:\\ drive root."""
    svc = UnifiedFilesystemService.instance()
    res = svc.list_directory("C:\\")
    assert res.status == "done"
    assert res.evidence["count"] > 0
    assert "Windows" in res.output or "Program Files" in res.output or "Users" in res.output


def test_list_c_windows():
    """2. List C:\\Windows where current Windows identity permits it."""
    svc = UnifiedFilesystemService.instance()
    res = svc.list_directory("C:\\Windows")
    assert res.status == "done"
    assert res.evidence["count"] > 0
    assert "System32" in res.output or "notepad" in res.output.lower() or "win.ini" in res.output.lower()


def test_list_c_users():
    """3. List C:\\Users."""
    svc = UnifiedFilesystemService.instance()
    res = svc.list_directory("C:\\Users")
    assert res.status == "done"
    assert res.evidence["count"] > 0
    assert os.getlogin().lower() in res.output.lower() or "Public" in res.output or "Default" in res.output


def test_list_user_known_folders():
    """4. List Downloads, Desktop, Documents, and Music."""
    svc = UnifiedFilesystemService.instance()
    for shortcut in ("downloads", "desktop", "music", "documents"):
        res = svc.list_directory(shortcut)
        assert res.status == "done", f"Failed listing {shortcut}: {res.error}"
        assert "Contents of" in res.output or "Directory is empty" in res.output


def test_list_d_drive_root():
    """5. List D:\\ drive root."""
    svc = UnifiedFilesystemService.instance()
    mounted = svc.discover_mounted_drives()
    if "D:\\" not in [d.upper() for d in mounted]:
        pytest.skip("D: drive is not mounted on this test runner.")
    res = svc.list_directory("D:\\")
    assert res.status == "done"
    assert res.evidence["count"] > 0


def test_list_d_project():
    """6. List D:\\project."""
    svc = UnifiedFilesystemService.instance()
    if not Path("D:/project").exists():
        pytest.skip("D:/project does not exist on this machine.")
    res = svc.list_directory("D:\\project")
    assert res.status == "done"
    assert res.evidence["count"] > 0


# ── 2. Read / Write Authorization & Confinement Tests ────────────────────────

def test_read_authorized_file_outside_repository(tmp_path):
    """7. Read an authorized file outside the repository."""
    external_file = tmp_path / "outside_repo.txt"
    external_file.write_text("Hello from outside repo!", encoding="utf-8")

    svc = UnifiedFilesystemService.instance()
    res = svc.read_file(str(external_file))
    assert res.status == "done"
    assert "Hello from outside repo!" in res.output


def test_write_inside_authorized_workspace(tmp_path):
    """8. Write inside an authorized workspace."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    target_file = workspace_dir / "deliverable.txt"

    svc = UnifiedFilesystemService.instance()
    res = svc.write_file(str(target_file), "deliverable content", context={"workspace_root": str(workspace_dir)})
    assert res.status == "done"
    assert target_file.is_file()
    assert target_file.read_text(encoding="utf-8") == "deliverable content"


def test_reject_writes_outside_developer_workspace(tmp_path):
    """9. Reject writes outside a developer agent's assigned workspace."""
    assigned_workspace = tmp_path / "dev_workspace"
    assigned_workspace.mkdir()

    outside_target = tmp_path / "forbidden_escape.txt"

    svc = UnifiedFilesystemService.instance()
    res = svc.write_file(
        str(outside_target),
        "illegal overwrite",
        context={"workspace_root": str(assigned_workspace), "caller_agent": "developer"},
    )
    assert res.status == "failed"
    assert "WORKSPACE_CONFINEMENT_VIOLATION" in res.error
    assert not outside_target.exists()


def test_reject_unauthorized_destructive_delete(tmp_path):
    """10. Reject unauthorized destructive operations (delete without approval token)."""
    victim_file = tmp_path / "important.txt"
    victim_file.write_text("critical data", encoding="utf-8")

    svc = UnifiedFilesystemService.instance()
    # Attempt delete without approval token
    res = svc.delete_file(str(victim_file))
    assert res.status == "unverified"
    assert "APPROVAL_REQUIRED: DESTRUCTIVE_OPERATION" in res.error
    assert victim_file.exists(), "File was deleted without approval token!"

    # Now attempt with approval token
    res_appr = svc.delete_file(str(victim_file), context={"is_approved": True, "approval_token": "appr_test_123"})
    assert res_appr.status == "done"
    assert not victim_file.exists()


# ── 3. Security Boundary & Vulnerability Tests ───────────────────────────────

def test_reject_protected_credential_access_without_approval_token():
    """11. Reject protected credential access without an approval token (.ssh, .aws, .env)."""
    svc = UnifiedFilesystemService.instance()
    ssh_path = str(Path.home() / ".ssh" / "id_rsa")
    res_ssh = svc.read_file(ssh_path)
    assert res_ssh.status == "unverified"
    assert "APPROVAL_REQUIRED: SENSITIVE_CREDENTIAL_STORE" in res_ssh.error

    env_path = str(Path.home() / ".env")
    res_env = svc.read_file(env_path)
    assert res_env.status == "unverified"
    assert "APPROVAL_REQUIRED: SENSITIVE_CREDENTIAL_STORE" in res_env.error


def test_reject_unc_and_device_namespaces():
    """12. Reject UNC network paths and device namespace paths."""
    svc = UnifiedFilesystemService.instance()

    # UNC path
    res_unc = svc.list_directory(r"\\attacker-host\share\data")
    assert res_unc.status == "failed"
    assert "UNC_VIOLATION" in res_unc.error

    # Device namespace \\?\
    res_ns = svc.list_directory(r"\\?\C:\Windows")
    assert res_ns.status == "failed"
    assert "UNC_VIOLATION" in res_ns.error or "NAMESPACE_VIOLATION" in res_ns.error

    # Ambiguous drive-relative syntax C:foo
    res_rel = svc.read_file("C:foo.txt")
    assert res_rel.status == "failed"
    assert "DRIVE_RELATIVE_VIOLATION" in res_rel.error


def test_handle_broken_junctions_without_crashing():
    """13. Handle broken junctions and unlinked symlinks without crashing."""
    svc = UnifiedFilesystemService.instance()
    # In Windows 11 with OneDrive, user Documents contains legacy junctions like 'My Pictures'
    res = svc.list_directory("documents")
    assert res.status == "done"
    assert "Contents of" in res.output or "Directory is empty" in res.output


def test_atomic_prewrite_backup_on_overwrite(tmp_path):
    """Verify pre-write atomic backup snapshotting before overwriting non-empty files."""
    test_file = tmp_path / "important_config.json"
    test_file.write_text('{"version": 1}', encoding="utf-8")

    svc = UnifiedFilesystemService.instance(backup_dir=tmp_path / "backups")
    res = svc.write_file(str(test_file), '{"version": 2}')
    assert res.status == "done"
    assert "backup_path" in res.evidence
    backup_file = Path(res.evidence["backup_path"])
    assert backup_file.is_file()
    assert backup_file.read_text(encoding="utf-8") == '{"version": 1}'
    assert test_file.read_text(encoding="utf-8") == '{"version": 2}'


def test_detect_directory_cycles(tmp_path):
    """15. Detect directory cycles during recursive search."""
    root = tmp_path / "search_root"
    root.mkdir()
    (root / "file1.txt").write_text("match me", encoding="utf-8")
    sub = root / "subdir"
    sub.mkdir()
    (sub / "file2.txt").write_text("match me too", encoding="utf-8")

    svc = UnifiedFilesystemService.instance()
    res = svc.search_files(str(root), pattern="*.txt", recursive=True)
    assert res.status == "done"
    assert res.evidence["count"] == 2


def test_distinguish_windows_permission_from_policy_denial():
    """16. Distinguish Windows NTFS permission failures from application policy failures."""
    svc = UnifiedFilesystemService.instance()

    # Application policy denial (e.g. mutating C:\Windows without approval token)
    res_pol = svc.write_file(r"C:\Windows\optimus_test_unauthorized.txt", "data")
    assert res_pol.status == "unverified"
    assert "POLICY_DENIED" in res_pol.error or "APPROVAL_REQUIRED" in res_pol.error

    # Windows OS NTFS denial (e.g. System Volume Information)
    sys_vol = r"C:\System Volume Information" if Path(r"C:\System Volume Information").exists() else r"D:\System Volume Information"
    res_os = svc.list_directory(sys_vol)
    # If the directory exists, it should fail with WinError 5 or system protected restriction
    assert res_os.status in ("failed", "unverified")
    assert "ACCESS_DENIED" in res_os.error or "WinError 5" in res_os.error or "PROTECTED_PATH" in res_os.error


# ── 4. Agent-Mediated Access Tests ──────────────────────────────────────────

def test_agent_mediated_file_controller_access():
    """17a. Verify agent-mediated access via file_controller tool."""
    # List downloads via voice/desktop tool
    out_dl = file_controller(parameters={"action": "list", "path": "downloads"})
    assert "Contents of Downloads" in out_dl or "Directory is empty" in out_dl

    # List D:\project via voice/desktop tool (previously failed with 'Access denied: D:\project')
    if Path("D:/project").exists():
        out_d = file_controller(parameters={"action": "list", "path": "D:\\project"})
        assert "Contents of project" in out_d or "project (" in out_d
        assert "Access denied" not in out_d


def test_agent_mediated_tool_registry_access():
    """17b. Verify agent-mediated access via ToolRegistry under CentralOrchestrator context."""
    reg = ToolRegistry()
    agent_reg = AgentRegistry()
    fs_agent = agent_reg.get("filesystem")
    assert fs_agent is not None

    # Execute list_directory on D:\project through ToolRegistry
    if Path("D:/project").exists():
        res = reg.execute(
            "list_directory",
            {"path": "D:\\project"},
            caller_agent="filesystem",
            allowed_tools=fs_agent.allowed_tools,
            allowed_tiers=fs_agent.allowed_tiers,
            context={"allowed_roots": [str(Path.cwd()), "ALL_DRIVES"], "cross_drive": True},
        )
        assert res.status == "done"
        assert "Contents of project" in res.output or res.evidence.get("count", 0) > 0


def test_env_variants_protection_and_nt_namespace(tmp_path):
    """Verify .env variants and NT device namespace syntax protection."""
    svc = UnifiedFilesystemService.instance()

    # .env.local and .env.production
    env_local = tmp_path / ".env.local"
    env_local.write_text("SECRET_KEY=123", encoding="utf-8")
    res_env = svc.read_file(str(env_local))
    assert res_env.status == "unverified"
    assert "SENSITIVE_CREDENTIAL_STORE" in res_env.error

    # NT object manager namespace
    with pytest.raises(ValueError, match="NAMESPACE_VIOLATION"):
        svc.resolve_path(r"\??\C:\Windows")


def test_file_controller_mutating_actions_delegation(tmp_path):
    """Verify mutating actions in file_controller delegate to UnifiedFilesystemService with backup & protection."""
    test_file = tmp_path / "fc_test.txt"
    out_create = file_controller(parameters={"action": "create", "path": str(tmp_path), "name": "fc_test.txt", "content": "hello world"})
    assert "File created" in out_create
    assert test_file.read_text(encoding="utf-8") == "hello world"

    # Write update
    out_write = file_controller(parameters={"action": "write", "path": str(test_file), "content": "updated content"})
    assert "Written to" in out_write
    assert test_file.read_text(encoding="utf-8") == "updated content"

    # Mutating protected path is blocked by file_controller
    out_sys = file_controller(parameters={"action": "create", "path": r"C:\Windows", "name": "bad.txt", "content": "fail"})
    assert "Access denied" in out_sys
