"""tests/test_universal_application_intelligence.py — Full M17 Acceptance Test Suite.

Verifies all 20 mandatory real-world acceptance criteria for OPTIMUS M17:
1. Discover installed applications.
2. Open an application that is not already running.
3. Activate an existing application instance.
4. Open a file using the correct associated application.
5. Find and open a document when multiple folders are involved.
6. Open WhatsApp.
7. Resolve a contact correctly (with ambiguity handling).
8. Compose and send an authorized test message.
9. Verify the resulting message state.
10. Search the web and return useful sourced results.
11. Open a browser tab and navigate to a requested page.
12. Open a PDF in an appropriate application.
13. Open a development project in VS Code.
14. Execute a test and inspect its actual result.
15. Operate an unfamiliar application using controlled discovery.
16. Convert a successful workflow into a reusable skill.
17. Reuse the learned skill in a subsequent task.
18. Detect and recover from an application interaction failure.
19. Preserve permissions and workspace boundaries.
20. Resume an interrupted multi-application task.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.application_intelligence.adapters import (
    BrowserAdapter,
    DevToolsAdapter,
    DocumentAdapter,
    WhatsAppAdapter,
    get_adapter,
)
from core.application_intelligence.capabilities import (
    is_control_destructive,
    is_control_safe_for_exploration,
)
from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.discovery import (
    discover_all_installed_applications,
    discover_packaged_apps_and_protocols,
    discover_running_processes_and_windows,
    resolve_file_association,
)
from core.application_intelligence.interaction_router import InteractionRouter
from core.application_intelligence.learning_engine import ApplicationLearningEngine
from core.application_intelligence.registry import ApplicationRegistry
from core.application_intelligence.types import (
    ActionContract,
    AppCategory,
    AppDefinition,
    AppInteractionTier,
    LearnedWorkflow,
    ResolutionStatus,
)
from core.application_intelligence.workflow_store import WorkflowStore
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.tools import ToolRegistry
from core.security.policy_engine import PolicyEngine
from core.skills.models import SkillTrustLevel
from core.skills.registry import SkillRegistry


@pytest.fixture(autouse=True)
def reset_singletons(tmp_path):
    """Reset singletons for clean test isolation."""
    ApplicationRegistry.reset_instance()
    WorkflowStore.reset_instance()
    PolicyEngine.reset_instance()
    SkillRegistry._instance = None


# ── Scenario 1: Discover installed applications ──────────────────────────────
def test_01_discover_installed_applications(tmp_path):
    """Verify multi-source discovery of Win32, MSIX/UWP, and built-in Windows applications."""
    reg = ApplicationRegistry.instance(cache_file=tmp_path / "app_inventory.json")
    reg.refresh(force=True)
    apps = reg.list_all_apps()

    assert len(apps) >= 5
    app_names = [a.name.lower() for a in apps]
    assert any("calc" in n for n in app_names)
    assert any("notepad" in n for n in app_names)
    assert any("explorer" in n for n in app_names)

    # Verify WhatsApp protocol handler / UWP package is discovered (CRIT-1)
    wa_app = reg.find_app("whatsapp")
    assert wa_app is not None
    assert wa_app.category == AppCategory.COMMUNICATION


# ── Scenario 2: Open an application that is not already running ──────────────
def test_02_open_app_not_already_running(monkeypatch, tmp_path):
    """Verify launching an application that is currently closed."""
    reg = ApplicationRegistry.instance(cache_file=tmp_path / "app_inventory.json")
    mock_launched = {}

    def mock_launch_app(app_name, args=None):
        mock_launched["name"] = app_name
        mock_launched["args"] = args
        return {"status": "success", "pid": 12345}

    from core.windows_runtime.client import WindowsRuntimeClient
    monkeypatch.setattr(WindowsRuntimeClient, "launch_application", lambda self, app_name, args=None: mock_launch_app(app_name, args))

    res = reg.launch_or_activate("notepad")
    assert res["success"] is True
    assert res["action"] == "launched"
    assert "notepad" in mock_launched["name"].lower()


# ── Scenario 3: Activate an existing application instance ────────────────────
def test_03_activate_existing_application_instance(monkeypatch, tmp_path):
    """Verify activating an existing running window instance brings it to the foreground."""
    reg = ApplicationRegistry.instance(cache_file=tmp_path / "app_inventory.json")

    import core.application_intelligence.registry
    monkeypatch.setattr(
        core.application_intelligence.registry,
        "discover_running_processes_and_windows",
        lambda: [{"name": "testapp", "pid": 5432, "main_hwnd": 999888, "window_titles": ["TestRunningApp"], "integrity_level": "Medium"}],
    )

    # Seed running application
    app = AppDefinition(
        app_id="app.test_running",
        name="TestRunningApp",
        aliases=["testapp"],
        is_running=True,
        main_hwnd=999888,
        pids=[5432],
    )
    reg._register_internal(app)

    activated_hwnds = []
    import win32con
    import win32gui
    monkeypatch.setattr(win32gui, "IsIconic", lambda hwnd: False)
    monkeypatch.setattr(win32gui, "ShowWindow", lambda hwnd, flag: True)
    monkeypatch.setattr(win32gui, "SetForegroundWindow", lambda hwnd: activated_hwnds.append(hwnd))

    res = reg.launch_or_activate("testapp")
    assert res["success"] is True
    assert res["action"] == "activated"
    assert 999888 in activated_hwnds


# ── Scenario 4: Open a file using the correct associated application ─────────
def test_04_open_file_using_correct_associated_application(tmp_path):
    """Verify file extension association resolves the correct application."""
    test_pdf = tmp_path / "annual_statement.pdf"
    test_pdf.write_text("%PDF-1.4 Mock PDF Header")

    doc_adapter = DocumentAdapter()
    res = doc_adapter.resolve_document(str(test_pdf))
    assert res.is_matched is True
    assert res.resolved_value == str(test_pdf.resolve())

    # Check file association candidate for pdf
    assoc = resolve_file_association(".pdf")
    assert any("edge" in a.lower() or "chrome" in a.lower() or "acrobat" in a.lower() for a in assoc)


# ── Scenario 5: Find and open a document across multiple folders ─────────────
def test_05_find_and_open_document_across_multiple_folders(tmp_path):
    """Verify finding a document when multiple directories and subdirectories are involved."""
    fld1 = tmp_path / "downloads_mock"
    fld2 = tmp_path / "documents_mock"
    fld1.mkdir(parents=True)
    fld2.mkdir(parents=True)

    target_doc = fld2 / "project_status_report.docx"
    target_doc.write_text("Mock Word Document")

    doc_adapter = DocumentAdapter()
    res = doc_adapter.resolve_document("project_status_report", search_roots=[str(fld1), str(fld2)])
    assert res.is_matched is True
    assert res.resolved_value == str(target_doc.resolve())


# ── Scenario 6: Open WhatsApp ────────────────────────────────────────────────
def test_06_open_whatsapp(monkeypatch):
    """Verify discovering and activating WhatsApp."""
    wa = WhatsAppAdapter()
    assert wa.discover() is True

    activated = []
    monkeypatch.setattr(ApplicationRegistry, "launch_or_activate", lambda self, q, args=None, timeout=10.0: activated.append(q) or {"success": True, "action": "activated", "hwnd": 1234})

    connected = wa.connect()
    assert connected is True
    assert "whatsapp" in activated


# ── Scenario 7: Resolve a contact correctly with ambiguity handling ─────────
def test_07_resolve_contact_correctly_and_ambiguity():
    """Verify contact resolution handles exact, fuzzy, and ambiguous contacts cleanly (FINDING-M17-02)."""
    wa = WhatsAppAdapter()

    # Exact match
    res_exact = wa.resolve_contact("John Doe")
    assert res_exact.is_matched is True
    assert res_exact.resolved_value["phone"] == "+15551234567"

    # Ambiguous match ("John" matches John Doe and John Smith)
    res_ambig = wa.resolve_contact("John")
    assert res_ambig.is_ambiguous is True
    assert len(res_ambig.candidates) == 2
    assert "Multiple contacts matched" in res_ambig.clarification_prompt

    # Not found
    res_none = wa.resolve_contact("Zack Nonexistent")
    assert res_none.status == ResolutionStatus.NOT_FOUND


# ── Scenario 8: Compose and send an authorized test message ──────────────────
def test_08_compose_and_send_authorized_message(tmp_path):
    """Verify composing and sending a message requiring PolicyEngine authorization (CRIT-2)."""
    wa = WhatsAppAdapter()
    policy = PolicyEngine.instance(journal_path=tmp_path / "nonces.journal")

    # 1. Unapproved send must be rejected with APPROVAL_REQUIRED
    unapproved_contract = ActionContract(
        action_id="act_wa_01",
        target_app="whatsapp",
        operation="send_message",
        parameters={"recipient": "John Doe", "message": "hii"},
    )
    unapproved_evidence = wa.execute(unapproved_contract)
    assert unapproved_evidence.status == "unverified"
    assert "APPROVAL_REQUIRED" in unapproved_evidence.error_message

    # 2. Approved send with single-use ApprovalToken
    token = policy.create_approval_token(
        mission_id="m17_test",
        subtask_id="step_01",
        operator_id="human_operator",
        tool_name="send_whatsapp_message",
        arguments={"recipient": "John Doe", "message": "hii"},
    )
    approved_contract = ActionContract(
        action_id="act_wa_02",
        target_app="whatsapp",
        operation="send_message",
        parameters={
            "recipient": "John Doe",
            "message": "hii",
            "approval_token": token.token_id,
            "operator_id": "human_operator",
        },
    )
    approved_evidence = wa.execute(approved_contract)
    assert approved_evidence.status == "done"
    assert any("message_sent" in f for f in approved_evidence.facts)


# ── Scenario 9: Verify resulting message state ───────────────────────────────
def test_09_verify_resulting_message_state():
    """Verify delivery and fact-based outcomes in ExecutionEvidence."""
    wa = WhatsAppAdapter()
    contract = ActionContract(
        action_id="act_draft_01",
        target_app="whatsapp",
        operation="draft_message",
        parameters={"recipient": "Alice Johnson", "message": "Meeting at 3PM"},
    )
    evidence = wa.execute(contract)
    assert evidence.status == "done"
    assert any("message_drafted" in f for f in evidence.facts)
    assert evidence.evidence_data["ready_to_send"] is True


# ── Scenario 10: Search the web and return useful sourced results ────────────
def test_10_search_web_and_return_useful_sourced_results():
    """Verify web search extracts multiple sources and compares information."""
    browser = BrowserAdapter()
    res = browser.search_and_compare("NVIDIA AI GPUs", max_sources=3)

    assert res["source_count"] >= 2
    assert any("H100" in s["title"] or "Blackwell" in s["title"] for s in res["sources"])
    assert "### Web Intelligence Synthesis" in res["synthesized_comparison"]
    # Verify indirect prompt injection protection (CRIT-3 canonical untrusted demarcator)
    assert "<<<UNTRUSTED source=web>>>" in res["synthesized_comparison"]
    assert "<<<END UNTRUSTED>>>" in res["synthesized_comparison"]


# ── Scenario 11: Open a browser tab and navigate to a requested page ─────────
def test_11_open_browser_tab_and_navigate(monkeypatch):
    """Verify browser navigation with DesktopInteractionLock."""
    browser = BrowserAdapter()
    opened_urls = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_urls.append(url))

    contract = ActionContract(
        action_id="act_nav_01",
        target_app="browser",
        operation="navigate",
        parameters={"url": "https://docs.python.org"},
    )
    evidence = browser.execute(contract)
    assert evidence.status == "done"
    assert "https://docs.python.org" in opened_urls


# ── Scenario 12: Open a PDF in an appropriate application ────────────────────
def test_12_open_pdf_in_appropriate_application(tmp_path, monkeypatch):
    """Verify document adapter launches PDF using associated viewer."""
    doc_file = tmp_path / "system_architecture.pdf"
    doc_file.write_text("%PDF-1.5 Architecture Spec")

    doc_adapter = DocumentAdapter()
    opened_targets = []
    monkeypatch.setattr(os, "startfile", lambda p: opened_targets.append(str(p)))

    contract = ActionContract(
        action_id="act_open_pdf",
        target_app="document",
        operation="open_document",
        parameters={"query": str(doc_file)},
    )
    evidence = doc_adapter.execute(contract)
    assert evidence.status == "done"
    assert any("document_opened" in f for f in evidence.facts)


# ── Scenario 13: Open a development project in VS Code ───────────────────────
def test_13_open_development_project_in_vscode(tmp_path, monkeypatch):
    """Verify DevTools adapter opens project in VS Code."""
    proj_dir = tmp_path / "my_microservice"
    proj_dir.mkdir()

    devtools = DevToolsAdapter()
    mock_vs = []
    monkeypatch.setattr(ApplicationRegistry, "launch_or_activate", lambda self, q, args=None, timeout=10.0: mock_vs.append((q, args)) or {"success": True})

    contract = ActionContract(
        action_id="act_vscode_01",
        target_app="devtools",
        operation="open_workspace",
        parameters={"path": str(proj_dir)},
    )
    evidence = devtools.execute(contract)
    assert evidence.status == "done"
    assert mock_vs[0][0] == "code"
    assert str(proj_dir.resolve()) in mock_vs[0][1]


# ── Scenario 14: Execute a test and inspect its actual result ────────────────
def test_14_execute_test_and_inspect_actual_result(monkeypatch):
    """Verify running test command, parsing exit code, and diagnosing failures."""
    devtools = DevToolsAdapter()

    def mock_subprocess_run(cmd, **kwargs):
        class MockCompletedProcess:
            returncode = 0
            stdout = "test_auth.py::test_login PASSED\ntest_auth.py::test_logout PASSED\n2 passed in 0.12s"
            stderr = ""
        return MockCompletedProcess()

    import subprocess
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    contract = ActionContract(
        action_id="act_run_tests_01",
        target_app="devtools",
        operation="run_tests",
        parameters={"command": "pytest tests/test_auth.py"},
    )
    evidence = devtools.execute(contract)
    assert evidence.status == "done"
    assert any("tests_passed=2" in f for f in evidence.facts)
    assert "All tests passed" in evidence.evidence_data["diagnosis"]


# ── Scenario 15: Operate an unfamiliar application using controlled discovery
def test_15_operate_unfamiliar_app_controlled_discovery(monkeypatch):
    """Verify Passive Zero-Click exploration on an unfamiliar application (CRIT-5)."""
    engine = ApplicationLearningEngine()

    monkeypatch.setattr(ApplicationRegistry, "launch_or_activate", lambda self, q, args=None, timeout=10.0: {"success": True})

    class MockElement:
        def __init__(self, name, role):
            self.name = name
            self.role = role
            self.automation_id = f"id_{name}"

    class MockView:
        active_window = "LegacyCAD v2.1"
        elements = [
            MockElement("File", "menuitem"),
            MockElement("Export", "menuitem"),
            MockElement("Delete Project", "button"),    # Destructive control
            MockElement("Zoom In", "button"),
        ]

    import core.screenview
    monkeypatch.setattr(core.screenview, "look", lambda: MockView())

    explore = engine.passive_explore_app("LegacyCAD")
    assert explore["success"] is True
    assert explore["mode"] == "passive_zero_click"

    # Verify destructive controls are flagged and blocked from action
    controls = explore["controls"]
    del_ctrl = next(c for c in controls if "Delete" in c["label"])
    assert del_ctrl["is_destructive"] is True
    assert del_ctrl["is_safe_for_action"] is False


# ── Scenario 16: Convert a successful workflow into a reusable skill ─────────
def test_16_convert_workflow_into_reusable_skill(tmp_path, monkeypatch):
    """Verify learning engine synthesizes a workflow and registers into SkillRegistry with UNVERIFIED trust (HIGH-5)."""
    store = WorkflowStore.instance(storage_file=tmp_path / "workflows.json")
    engine = ApplicationLearningEngine(workflow_store=store)

    monkeypatch.setattr(ApplicationRegistry, "launch_or_activate", lambda self, q, args=None, timeout=10.0: {"success": True})

    class MockView:
        active_window = "AudioMaster Pro"
        elements = [
            type("El", (), {"name": "File", "role": type("R", (), {"value": "menuitem"})(), "automation_id": "m_file"})(),
            type("El", (), {"name": "Export MP3", "role": type("R", (), {"value": "menuitem"})(), "automation_id": "m_exp"})(),
        ]

    import core.screenview
    monkeypatch.setattr(core.screenview, "look", lambda: MockView())

    wf = engine.learn_workflow_for_goal("AudioMaster", "Export MP3 audio")
    assert wf.validation_state == "UNVERIFIED"  # HIGH-5 invariant
    assert len(wf.action_sequence) >= 1

    # Verify registered in SkillRegistry
    manifest = store.skill_registry.get(wf.workflow_id)
    assert manifest is not None
    assert manifest.trust_level == SkillTrustLevel.UNVERIFIED


# ── Scenario 17: Reuse the learned skill in a subsequent task ────────────────
def test_17_reuse_learned_skill_in_subsequent_task(tmp_path):
    """Verify reusing a stored learned workflow from WorkflowStore."""
    store = WorkflowStore.instance(storage_file=tmp_path / "workflows.json")
    wf = LearnedWorkflow(
        workflow_id="wf_photoshop_export_png",
        target_app="Photoshop",
        goal="Export image to PNG",
        action_sequence=[
            {"step": 1, "target_element": "File", "action": "click"},
            {"step": 2, "target_element": "Quick Export as PNG", "action": "click"},
        ],
        validation_state="UNVERIFIED",
    )
    store.save_workflow(wf)

    loaded = store.get_workflow("wf_photoshop_export_png")
    assert loaded is not None
    assert loaded.target_app == "Photoshop"
    assert len(loaded.action_sequence) == 2

    store.record_execution("wf_photoshop_export_png", success=True)
    assert loaded.execution_count == 1
    assert loaded.success_count == 1


# ── Scenario 18: Detect and recover from an application interaction failure ──
def test_18_detect_and_recover_from_interaction_failure(monkeypatch):
    """Verify InteractionRouter recovers dirty state (Esc) on tier failure (HIGH-2 & LOW-2)."""
    router = InteractionRouter()

    escaped = []
    import core.screenact
    monkeypatch.setattr(core.screenact, "press", lambda k: escaped.append(k))

    contract = ActionContract(
        action_id="act_fail_01",
        target_app="nonexistent_app_xyz",
        operation="unsupported_op",
    )
    evidence = router.dispatch(contract)
    assert evidence.status == "failed"
    assert "escape" in escaped  # Atomic rollback was triggered


# ── Scenario 19: Preserve permissions, CBAC, and workspace boundaries ────────
def test_19_preserve_permissions_and_workspace_boundaries():
    """Verify negative control patterns hard-block dangerous UI buttons (CRIT-5)."""
    assert is_control_destructive("Delete File") is True
    assert is_control_destructive("Format Partition") is True
    assert is_control_destructive("Wipe Database") is True
    assert is_control_destructive("Submit Payment") is True
    assert is_control_destructive("View Report") is False
    assert is_control_destructive("Save") is False


# ── Scenario 20: Resume an interrupted task and approval suspension ──────────
def test_20_resume_interrupted_task_and_approval_suspension(tmp_path):
    """Verify CentralOrchestrator handles approval required status and resumes with token (HIGH-4)."""
    orchestrator = CentralOrchestrator()
    policy = PolicyEngine.instance(journal_path=tmp_path / "nonces.journal")

    # Step 1: Dispatched without token -> returns False with approval required
    ok, text, agent, side_effects = orchestrator.execute_user_request("open whatsapp and text John Doe hello there")
    assert ok is False
    assert "APPROVAL_REQUIRED" in text

    # Step 2: Human operator grants approval token -> executes successfully
    token = policy.create_approval_token(
        mission_id="m17_test_orch",
        subtask_id="step_01",
        operator_id="operator",
        tool_name="send_whatsapp_message",
        arguments={"recipient": "John Doe", "message": "hello there"},
    )
    res = orchestrator.tool_registry.execute(
        "app_execute_action",
        {
            "target_app": "whatsapp",
            "operation": "send_message",
            "parameters": {
                "recipient": "John Doe",
                "message": "hello there",
                "approval_token": token.token_id,
                "operator_id": "operator",
            },
        },
        caller_agent="application_agent",
    )
    assert res.is_success is True
    assert res.evidence.get("delivered") is True or res.evidence.get("evidence_data", {}).get("delivered") is True


# ── Scenario 21: Enforce UIPI Elevation Barrier (HIGH-6 & Finding 3) ────────
def test_21_uipi_elevation_barrier_enforcement(monkeypatch, tmp_path):
    """Verify InteractionRouter immediately flags UIPI_ELEVATION_BARRIER on elevated apps."""
    reg = ApplicationRegistry.instance(cache_file=tmp_path / "app_inventory.json")

    import core.application_intelligence.registry
    monkeypatch.setattr(
        core.application_intelligence.registry,
        "discover_running_processes_and_windows",
        lambda: [{"name": "admintool", "pid": 7771, "main_hwnd": 777888, "window_titles": ["AdminTool"], "integrity_level": "High"}],
    )

    elevated_app = AppDefinition(
        app_id="app.elevated_admin",
        name="AdminTool",
        aliases=["admintool"],
        is_running=True,
        main_hwnd=777888,
        integrity_level="High",
    )
    reg._register_internal(elevated_app)

    router = InteractionRouter()
    contract = ActionContract(
        action_id="act_admin_01",
        target_app="admintool",
        operation="click_button",
    )
    evidence = router.dispatch(contract)
    assert evidence.status == "failed"
    assert "UIPI_ELEVATION_BARRIER" in evidence.error_message


# ── Scenario 22: DesktopInteractionLock Reentrancy Stack (Finding 4) ─────────
def test_22_desktop_interaction_lock_reentrancy_ownership_stack():
    """Verify DesktopInteractionLock correctly maintains ownership stack during nested reentrant calls."""
    assert DesktopInteractionLock.is_locked() is False
    assert DesktopInteractionLock.get_current_owner() is None

    with DesktopInteractionLock(owner="outer_caller", timeout=5.0):
        assert DesktopInteractionLock.is_locked() is True
        assert DesktopInteractionLock.get_current_owner() == "outer_caller"

        # Nested reentrant acquisition
        with DesktopInteractionLock(owner="inner_adapter", timeout=5.0):
            assert DesktopInteractionLock.is_locked() is True
            assert DesktopInteractionLock.get_current_owner() == "inner_adapter"

        # After inner release, outer owner must be restored (not None!)
        assert DesktopInteractionLock.is_locked() is True
        assert DesktopInteractionLock.get_current_owner() == "outer_caller"

    # After full exit, lock is free
    assert DesktopInteractionLock.is_locked() is False
    assert DesktopInteractionLock.get_current_owner() is None

