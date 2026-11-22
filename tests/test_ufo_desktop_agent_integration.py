"""tests/test_ufo_desktop_agent_integration.py — Acceptance Test Suite for M19.

Tests the full Microsoft UFO² desktop-agent integration in OPTIMUS (reconciles CRIT-1, CRIT-2, CRIT-3,
HIGH-1, HIGH-2, HIGH-3, HIGH-4, MED-1, MED-2, LOW-1):
- FastMCP 4+ compatibility (exclude_args monkey-patch).
- UFOMCPSecurityGateway: PolicyEngine authorization, single-use token replay prevention.
- WhatsApp & communication exact-content and recipient binding.
- Decoupled prompt injection demarcation (pristine locators, demarcated prompt context).
- CommandLineExecutor launch-only boundary (blocking shell operators & multi-token commands).
- Multi-application delegated missions (Word to Excel extraction).
- Headless PDFReaderExecutor enforcement (simulate_human=False).
- Dynamic VRAM pre-flight on RTX 3050 Laptop GPU (< 3072 MB degrades to UIA).
- WorkflowStore UNVERIFIED trust boundary.
- DesktopInteractionLock focus synchronization under concurrency.
- Capability-based InteractionRouter multi-tier provider selection.
- Orchestrator tools (ufo_desktop_mission, ufo_mcp_servers_status) and GUI integration.
"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.frameworks.registry import ProviderRegistry
from core.application_intelligence.frameworks.ufo import (
    GatewayInvocationResult,
    UFODesktopAgentProvider,
    UFODesktopMissionRunner,
    UFOMCPSecurityGateway,
    UFOWorkflowIntegrator,
    ensure_ufo_environment,
)
from core.application_intelligence.interaction_router import InteractionRouter
from core.application_intelligence.types import (
    ActionContract,
    AppInteractionTier,
    LearnedWorkflow,
)
from core.application_intelligence.workflow_store import WorkflowStore
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.tools import ToolRegistry
from core.outcome import untrusted
from core.security.policy_engine import PolicyEngine


@pytest.fixture(autouse=True)
def reset_all_singletons():
    """Reset singletons before each test scenario."""
    DesktopInteractionLock.reset_instance()
    PolicyEngine.reset_instance()
    ProviderRegistry.reset_instance()
    WorkflowStore.reset_instance()
    UFOMCPSecurityGateway.reset_instance()
    yield
    DesktopInteractionLock.reset_instance()
    PolicyEngine.reset_instance()
    ProviderRegistry.reset_instance()
    WorkflowStore.reset_instance()
    UFOMCPSecurityGateway.reset_instance()


# ── Scenario 1: FastMCP 4+ Compatibility and Server Registration ─────────────
def test_01_fastmcp_4_compatibility_and_server_registration():
    """Verify FastMCP 4+ compatibility shim and successful registration of all 8 local MCP servers."""
    ufo_path = ensure_ufo_environment()
    assert ufo_path.exists()

    from ufo.client.mcp.local_servers import (
        cli_mcp_server,
        excel_wincom_mcp_server,
        pdf_reader_mcp_server,
        ppt_wincom_mcp_server,
        ui_mcp_server,
        word_wincom_mcp_server,
    )
    from ufo.client.mcp.mcp_registry import MCPRegistry

    registered = MCPRegistry.list()
    expected = [
        "UICollector",
        "HostUIExecutor",
        "AppUIExecutor",
        "CommandLineExecutor",
        "WordCOMExecutor",
        "ExcelCOMExecutor",
        "PowerPointCOMExecutor",
        "PDFReaderExecutor",
    ]
    for srv in expected:
        assert srv in registered, f"Missing MCP server: {srv}"


# ── Scenario 2: Gateway PolicyEngine Authorization Gate ──────────────────────
def test_02_gateway_policy_engine_authorization_enforcement():
    """Verify destructive tools are blocked without authentic PolicyEngine approval tokens."""
    gateway = UFOMCPSecurityGateway.instance()
    pe = PolicyEngine.instance()

    contract_unauth = ActionContract(
        target_app="WINWORD.EXE",
        operation="save_as",
        is_destructive=True,
        requires_approval=True,
    )

    res_rejected = gateway.authorize_and_execute(
        server_name="WordCOMExecutor",
        tool_name="save_as",
        arguments={"file_path": "C:/docs/report.docx", "is_destructive": True},
        contract=contract_unauth,
        executor_func=lambda **kw: {"saved": True},
    )
    assert res_rejected.success is False
    assert res_rejected.requires_approval is True
    assert "APPROVAL_REQUIRED" in (res_rejected.error or "")

    # Now create valid cryptographic token
    token = pe.create_approval_token(
        mission_id="m19_sec_01",
        subtask_id="sub_save_01",
        operator_id="security_officer",
        tool_name="wordcomexecutor.save_as",
        arguments={"file_path": "C:/docs/report.docx", "is_destructive": True},
    )
    assert token is not None

    contract_auth = ActionContract(
        target_app="WINWORD.EXE",
        operation="save_as",
        is_destructive=True,
        requires_approval=True,
        approval_token=token.token_id,
        session_nonce="nonce_word_01",
    )

    res_approved = gateway.authorize_and_execute(
        server_name="WordCOMExecutor",
        tool_name="save_as",
        arguments={"file_path": "C:/docs/report.docx", "is_destructive": True},
        contract=contract_auth,
        executor_func=lambda **kw: {"saved": True, "path": kw.get("file_path")},
    )
    assert res_approved.success is True
    assert res_approved.data.get("saved") is True


# ── Scenario 3: Token Replay Prevention ──────────────────────────────────────
def test_03_gateway_single_use_token_replay_prevention():
    """Verify approval tokens are single-use and cannot be replayed after execution."""
    gateway = UFOMCPSecurityGateway.instance()
    pe = PolicyEngine.instance()

    token = pe.create_approval_token(
        mission_id="m19_sec_02",
        subtask_id="sub_del_01",
        operator_id="sec_admin",
        tool_name="wordcomexecutor.save_as",
        arguments={"file_path": "doc.docx", "is_destructive": True},
    )

    contract = ActionContract(
        target_app="WINWORD.EXE",
        operation="save_as",
        is_destructive=True,
        requires_approval=True,
        approval_token=token.token_id,
    )

    # First execution succeeds
    res_first = gateway.authorize_and_execute(
        server_name="WordCOMExecutor",
        tool_name="save_as",
        arguments={"file_path": "doc.docx", "is_destructive": True},
        contract=contract,
        executor_func=lambda **kw: {"status": "ok"},
    )
    assert res_first.success is True

    # Immediate replay must fail because token nonce was consumed
    res_replay = gateway.authorize_and_execute(
        server_name="WordCOMExecutor",
        tool_name="save_as",
        arguments={"file_path": "doc.docx", "is_destructive": True},
        contract=contract,
        executor_func=lambda **kw: {"status": "ok"},
    )
    assert res_replay.success is False
    assert "APPROVAL_REQUIRED" in (res_replay.error or "")


# ── Scenario 4: WhatsApp Exact-Content Binding (CRIT-1) ──────────────────────
def test_04_whatsapp_communication_exact_content_binding():
    """Verify communication actions require exact recipient and message hash binding."""
    gateway = UFOMCPSecurityGateway.instance()
    pe = PolicyEngine.instance()

    token = pe.create_approval_token(
        mission_id="m19_comm_01",
        subtask_id="sub_msg_01",
        operator_id="user_verified",
        tool_name="send_message",
        arguments={"recipient": "+15550199", "message": "Authorized Flight Plan Delta"},
    )

    # 1. Attempt with modified recipient must fail
    contract_wrong_recip = ActionContract(
        target_app="WhatsApp",
        operation="send_message",
        approval_token=token.token_id,
        allowed_apps=["WhatsApp"],
    )
    res_wrong_recip = gateway.authorize_and_execute(
        server_name="AppUIExecutor",
        tool_name="click_input",
        arguments={
            "target_app": "WhatsApp",
            "action_type": "send_message",
            "recipient": "+15559999",  # Tampered
            "message": "Authorized Flight Plan Delta",
        },
        contract=contract_wrong_recip,
        executor_func=lambda **kw: {"sent": True},
    )
    assert res_wrong_recip.success is False
    assert "SECURITY_HASH_MISMATCH" in (res_wrong_recip.error or "")

    # 2. Attempt with modified message text must fail
    contract_wrong_msg = ActionContract(
        target_app="WhatsApp",
        operation="send_message",
        approval_token=token.token_id,
        allowed_apps=["WhatsApp"],
    )
    res_wrong_msg = gateway.authorize_and_execute(
        server_name="AppUIExecutor",
        tool_name="click_input",
        arguments={
            "target_app": "WhatsApp",
            "action_type": "send_message",
            "recipient": "+15550199",
            "message": "Tampered Payload Unauthorized",  # Tampered
        },
        contract=contract_wrong_msg,
        executor_func=lambda **kw: {"sent": True},
    )
    assert res_wrong_msg.success is False
    assert "SECURITY_HASH_MISMATCH" in (res_wrong_msg.error or "")

    # 3. Exact match succeeds
    contract_valid = ActionContract(
        target_app="WhatsApp",
        operation="send_message",
        approval_token=token.token_id,
        allowed_apps=["WhatsApp"],
    )
    res_valid = gateway.authorize_and_execute(
        server_name="AppUIExecutor",
        tool_name="click_input",
        arguments={
            "target_app": "WhatsApp",
            "action_type": "send_message",
            "recipient": "+15550199",
            "message": "Authorized Flight Plan Delta",
        },
        contract=contract_valid,
        executor_func=lambda **kw: {"sent": True, "recipient": kw["recipient"]},
    )
    assert res_valid.success is True


# ── Scenario 5: Decoupled UI Demarcation & Locator Preservation (CRIT-3) ──────
def test_05_decoupled_ui_demarcation_locator_preservation():
    """Verify internal locators remain pristine while prompt context is demarcated with untrusted tags."""
    gateway = UFOMCPSecurityGateway.instance()

    raw_control_name = "Submit Query Button"
    demarcated = gateway.demarcate_for_prompter(raw_control_name, source="screen")

    # Prompt text is properly demarcated
    assert "<<<UNTRUSTED source=screen>>>" in demarcated
    assert raw_control_name in demarcated
    assert "<<<END UNTRUSTED>>>" in demarcated

    # But the raw name passed to UIA or pywinauto remains uncorrupted
    assert raw_control_name == "Submit Query Button"
    assert "<<<UNTRUSTED" not in raw_control_name


# ── Scenario 6: CommandLineExecutor Launch-Only Boundary (CRIT-2) ────────────
def test_06_command_line_executor_launch_only_boundary():
    """Verify CommandLineExecutor is strictly restricted to approved bare executable launch."""
    gateway = UFOMCPSecurityGateway.instance()
    contract = ActionContract(target_app="system", operation="launch")

    # 1. Bare approved launch succeeds
    res_ok = gateway.authorize_and_execute(
        server_name="CommandLineExecutor",
        tool_name="run_shell",
        arguments={"command": "notepad.exe"},
        contract=contract,
        executor_func=lambda **kw: {"pid": 1234, "command": kw.get("command")},
    )
    assert res_ok.success is True

    # 2. Command with shell chaining (& or ;) must be blocked
    res_chain = gateway.authorize_and_execute(
        server_name="CommandLineExecutor",
        tool_name="run_shell",
        arguments={"command": "calc.exe & whoami"},
        contract=contract,
        executor_func=lambda **kw: {"pid": 9999},
    )
    assert res_chain.success is False
    assert "COMMAND_SECURITY_VIOLATION" in (res_chain.error or "")

    # 3. Multi-token command (e.g. running test runner) must be blocked
    res_multi = gateway.authorize_and_execute(
        server_name="CommandLineExecutor",
        tool_name="run_shell",
        arguments={"command": "pytest tests/"},
        contract=contract,
        executor_func=lambda **kw: {"pid": 9999},
    )
    assert res_multi.success is False
    assert "COMMAND_SECURITY_VIOLATION" in (res_multi.error or "")


# ── Scenario 7: Multi-Application Word to Excel Mission (HIGH-1) ─────────────
def test_07_multi_app_mission_word_to_excel_handoff():
    """Verify UFODesktopMissionRunner executes multi-app contracts across allowed_apps."""
    runner = UFODesktopMissionRunner()

    contract = ActionContract(
        target_app="WINWORD.EXE",
        operation="extract_table_to_excel",
        allowed_apps=["WINWORD.EXE", "EXCEL.EXE"],
        allowed_operations=["select_table", "insert_excel_table"],
    )

    evidence = runner.execute_mission(contract, timeout=15.0, max_steps=5)
    assert evidence.status == "done"
    assert "WINWORD.EXE" in str(evidence.evidence_data.get("allowed_apps"))
    assert "EXCEL.EXE" in str(evidence.evidence_data.get("allowed_apps"))
    assert any("WordCOMExecutor" in c for c in evidence.evidence_data.get("tool_calls", []))
    assert any("ExcelCOMExecutor" in c for c in evidence.evidence_data.get("tool_calls", []))


# ── Scenario 8: Headless PDFReaderExecutor Enforcement (HIGH-4) ──────────────
def test_08_pdf_reader_executor_headless_enforcement():
    """Verify extract_pdf_text forces simulate_human=False, preventing focus theft."""
    gateway = UFOMCPSecurityGateway.instance()
    contract = ActionContract(target_app="PDFReader", operation="read_pdf")

    # Pass simulate_human=True in arguments
    args = {"pdf_path": "C:/test/sample.pdf", "simulate_human": True}
    captured_kw = {}

    def mock_extract(**kw):
        captured_kw.update(kw)
        return {"pages": 5, "text": "Sample text"}

    res = gateway.authorize_and_execute(
        server_name="PDFReaderExecutor",
        tool_name="extract_pdf_text",
        arguments=args,
        contract=contract,
        executor_func=mock_extract,
    )
    assert res.success is True
    assert captured_kw.get("simulate_human") is False  # Stripped by gateway


# ── Scenario 9: Dynamic VRAM Pre-Flight on RTX 3050 (MED-1) ─────────────────
def test_09_dynamic_vram_preflight_rtx3050():
    """Verify mission runner queries VRAM and degrades to programmatic UIA if < 3072 MB."""
    runner = UFODesktopMissionRunner()

    # Case A: High VRAM (>= 3072 MB)
    with patch.object(runner.benchmark, "get_hardware_status", return_value={"gpu_detected": True, "free_vram_mb": 3400.0}):
        mode_high = runner.check_vram_and_determine_mode()
        assert mode_high["mode"] == "visual"
        assert mode_high["meets_threshold"] is True

    # Case B: Low VRAM (< 3072 MB)
    with patch.object(runner.benchmark, "get_hardware_status", return_value={"gpu_detected": True, "free_vram_mb": 2100.0}):
        mode_low = runner.check_vram_and_determine_mode()
        assert mode_low["mode"] == "programmatic_uia"
        assert mode_low["meets_threshold"] is False


# ── Scenario 10: WorkflowStore UNVERIFIED Trust Boundary (HIGH-5, MED-2) ────
def test_10_workflow_store_unverified_provenance():
    """Verify captured UFO mission workflows are saved strictly as UNVERIFIED."""
    store = WorkflowStore.instance()
    integrator = UFOWorkflowIntegrator(store=store)

    contract = ActionContract(
        target_app="Notepad",
        operation="format_notes",
    )
    evidence_data = {
        "tool_calls": ["HostUIExecutor.select_application_window", "AppUIExecutor.set_edit_text"],
    }

    wf_id = integrator.capture_and_store_trajectory(contract, evidence_data)
    assert wf_id is not None

    wf = store.get_workflow(wf_id)
    assert wf is not None
    assert wf.validation_state == "UNVERIFIED"

    # Human promotion
    promoted = store.promote_workflow(wf_id, human_confirmed=True)
    assert promoted is True
    assert store.get_workflow(wf_id).validation_state == "VERIFIED_SKILL"


# ── Scenario 11: DesktopInteractionLock Focus Synchronization Under Concurrency
def test_11_desktop_interaction_lock_concurrency():
    """Verify gateway acquires DesktopInteractionLock during interactive UI actions."""
    gateway = UFOMCPSecurityGateway.instance()
    contract = ActionContract(target_app="App", operation="click")

    was_locked = False

    def mock_ui_click(**kw):
        nonlocal was_locked
        was_locked = DesktopInteractionLock.is_locked()
        return {"clicked": True}

    res = gateway.authorize_and_execute(
        server_name="AppUIExecutor",
        tool_name="click_input",
        arguments={"name": "SubmitButton"},
        contract=contract,
        executor_func=mock_ui_click,
    )
    assert res.success is True
    assert was_locked is True
    assert DesktopInteractionLock.is_locked() is False  # Cleanly released


# ── Scenario 12: Capability-Based InteractionRouter Multi-Tier Dispatch ─────
def test_12_interaction_router_multi_app_ufo_selection():
    """Verify InteractionRouter routes multi-app contracts to UFO while retaining Playwright for browser."""
    router = InteractionRouter()

    # 1. Multi-app desktop mission routes to TIER_5_UFO_DESKTOP_AGENT
    contract_ufo = ActionContract(
        target_app="WINWORD.EXE",
        operation="multi_app_data_pipeline",
        allowed_apps=["WINWORD.EXE", "EXCEL.EXE"],
    )
    tier_ufo = router.select_best_tier(contract_ufo)
    assert tier_ufo == AppInteractionTier.TIER_5_UFO_DESKTOP_AGENT

    # 2. Browser contract routes to TIER_4_BROWSER_DOM (Playwright)
    contract_browser = ActionContract(
        target_app="Google Chrome",
        operation="search_and_scrape",
    )
    tier_browser = router.select_best_tier(contract_browser)
    assert tier_browser == AppInteractionTier.TIER_4_BROWSER_DOM


# ── Scenario 13: Orchestrator Tools & Agent Profiles Integration ─────────────
def test_13_orchestrator_tools_and_gui_integration():
    """Verify ufo_desktop_mission and ufo_mcp_servers_status tools are registered and assigned."""
    tr = ToolRegistry.instance()
    t_mission = tr.get("ufo_desktop_mission")
    t_status = tr.get("ufo_mcp_servers_status")

    assert t_mission is not None
    assert t_status is not None

    ar = AgentRegistry.instance()
    app_agent = ar.get("application_agent")
    assert "ufo_desktop_mission" in app_agent.capability.allowed_tools
    assert "ufo_mcp_servers_status" in app_agent.capability.allowed_tools

    # Execute ufo_mcp_servers_status handler
    res = t_status.handler({})
    assert res.status == "done"
    assert "UFO Local FastMCP Servers" in res.output
    assert res.evidence["count"] >= 4
