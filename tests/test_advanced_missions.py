"""tests/test_advanced_missions.py — Comprehensive Test Suite for OPTIMUS Autonomous Missions.

Tests:
1. State Machine & Types: Lifecycle transitions and state enum contracts.
2. Mission Planner: DAG decomposition, cycle detection, dependency validation, capability detection.
3. Mission Archetype A: Research and Comparison Mission (real Python research + local env).
4. Mission Archetype B: System Intelligence Mission (CPU/RAM, disk, Ollama, zero system mutation).
5. Mission Archetype C: Developer Mission (project inspection, tests, findings report).
6. Mission Archetype D: Adaptive Failure Recovery (failure diagnosis, bounded retry, downstream replanning).
7. Mission Archetype E: Persistence & Resumption (save to disk, simulate app restart, resume without re-running completed steps).
8. Multi-Agent Concurrency: Parallelizable subtask execution with ThreadPool.
9. Approval Gate: Elevated clearance requirement, approval and rejection handling.
10. UI Integration: SectionMissions widget, nav bar, and bridge signal dispatch.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import pytest

from core.missions.coordinator import MissionCoordinator
from core.missions.engine import MissionExecutionEngine
from core.missions.memory import MissionMemory
from core.missions.planner import MissionPlanner
from core.missions.recovery import MissionRecoveryEngine
from core.missions.storage import MissionStore
from core.missions.types import (
    MissionPlan,
    MissionRecord,
    MissionState,
    MissionSubtask,
    SubtaskStatus,
)
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.tools import ToolRegistry
from core.planning.types import FailureType


@pytest.fixture
def temp_mission_env():
    """Create isolated sandbox directory for test mission persistence and deliverables."""
    temp_dir = tempfile.mkdtemp(prefix="optimus_missions_test_")
    store_dir = Path(temp_dir) / "storage"
    store_dir.mkdir(parents=True, exist_ok=True)
    
    agent_reg = AgentRegistry()
    from core.agents.registry_ext import register_specialized_agents
    register_specialized_agents(agent_reg)
    tool_reg = ToolRegistry()
    
    store = MissionStore(storage_dir=store_dir)
    planner = MissionPlanner(agent_reg, tool_reg)
    coord = MissionCoordinator(agent_reg, tool_reg, allowed_roots=[str(Path.cwd()), temp_dir])
    recovery = MissionRecoveryEngine(max_retries_per_step=3)
    engine = MissionExecutionEngine(
        store=store,
        planner=planner,
        coordinator=coord,
        recovery_engine=recovery,
    )
    
    yield {
        "temp_dir": temp_dir,
        "store": store,
        "engine": engine,
        "planner": planner,
        "coordinator": coord,
        "agent_reg": agent_reg,
        "tool_reg": tool_reg,
    }

    try:
        engine.shutdown(timeout=2.0)
    except Exception:
        pass
    shutil.rmtree(temp_dir, ignore_errors=True)


# ── Test 1: State Machine & Data Contracts ───────────────────────────────────

def test_mission_types_and_state_machine():
    """Verify MissionState, MissionSubtask, MissionPlan, and MissionRecord contracts."""
    st = MissionSubtask(
        subtask_id="st_1",
        title="Sample Step",
        description="Inspect system",
        assigned_agent="system_agent",
        required_tools=["cpu_status"],
        parallelizable=True,
    )
    assert st.status == SubtaskStatus.PENDING
    d = st.to_dict()
    assert d["subtask_id"] == "st_1"
    assert d["parallelizable"] is True

    recovered = MissionSubtask.from_dict(d)
    assert recovered.title == "Sample Step"

    plan = MissionPlan(objective="Test plan", subtasks=[st])
    plan_dict = plan.to_dict()
    assert len(plan_dict["subtasks"]) == 1

    rec = MissionRecord(objective="Run audit", state=MissionState.UNDERSTAND, plan=plan)
    rec.add_timeline_event("init", "Initialized test mission")
    assert len(rec.timeline) == 1
    rec_dict = rec.to_dict()
    assert rec_dict["state"] == MissionState.UNDERSTAND.value


# ── Test 2: DAG Validation & Cycle Detection ─────────────────────────────────

def test_mission_planner_dag_validation_and_cycle_detection(temp_mission_env):
    """Verify DAG cycle detection, missing dependencies, and capability detection."""
    planner = temp_mission_env["planner"]

    # 1. Valid linear plan
    s1 = MissionSubtask("s1", "Step 1", depends_on=[])
    s2 = MissionSubtask("s2", "Step 2", depends_on=["s1"])
    plan_valid = MissionPlan(objective="Valid Plan", subtasks=[s1, s2])
    assert planner.validate_plan(plan_valid) is True
    assert len(plan_valid.validation_errors) == 0

    # 2. Cycle detection: s1 depends on s2, s2 depends on s1
    s1_cycle = MissionSubtask("s1", "Step 1", depends_on=["s2"])
    s2_cycle = MissionSubtask("s2", "Step 2", depends_on=["s1"])
    plan_cycle = MissionPlan(objective="Cyclic Plan", subtasks=[s1_cycle, s2_cycle])
    assert planner.validate_plan(plan_cycle) is False
    assert any("Cyclic dependency detected" in err for err in plan_cycle.validation_errors)

    # 3. Missing dependency reference
    s_missing = MissionSubtask("s_bad", "Bad Step", depends_on=["ghost_id"])
    plan_missing = MissionPlan(objective="Missing Dep Plan", subtasks=[s_missing])
    assert planner.validate_plan(plan_missing) is False
    assert any("ghost_id" in err for err in plan_missing.validation_errors)

    # 4. Unavailable tool detection
    s_bad_tool = MissionSubtask("s_tool", "Bad Tool Step", required_tools=["totally_fictional_unregistered_tool"])
    plan_bad_tool = MissionPlan(objective="Bad Tool Plan", subtasks=[s_bad_tool])
    assert planner.validate_plan(plan_bad_tool) is False
    assert any("unavailable tool" in err for err in plan_bad_tool.validation_errors)


# ── Test 3: Mission Archetype A (Research and Report) ─────────────────────────

def test_mission_archetype_a_research_and_report(temp_mission_env):
    """Execute complete end-to-end Python research & local version comparison mission."""
    engine = temp_mission_env["engine"]
    rec = engine.create_mission("Research latest Python release, compare with local version, and produce markdown report")
    assert rec.state == MissionState.UNDERSTAND

    engine.start_mission(rec.mission_id)

    # Wait for completion (allow sufficient time for network timeout/fallback ladder)
    for _ in range(60):
        time.sleep(0.5)
        updated = engine.store.get(rec.mission_id)
        if updated and updated.state in (MissionState.COMPLETE, MissionState.FAILED, MissionState.PARTIALLY_COMPLETED):
            break

    final = engine.store.get(rec.mission_id)
    assert final is not None
    assert final.state == MissionState.COMPLETE
    assert len(final.deliverables) >= 1
    # Check that report was physically written
    report_path = Path(final.deliverables[0])
    assert report_path.exists()
    assert report_path.stat().st_size > 0
    content = report_path.read_text(encoding="utf-8")
    assert "OPTIMUS Mission Report" in content
    assert "Executive Summary" in content


# ── Test 4: Mission Archetype B (System Intelligence & Audit) ─────────────────

def test_mission_archetype_b_system_intelligence(temp_mission_env):
    """Execute complete system intelligence mission (CPU, RAM, disk, Ollama) with zero modifications."""
    engine = temp_mission_env["engine"]
    rec = engine.create_mission("Execute complete system intelligence audit including CPU, RAM, disk space, and Ollama inspection")
    
    engine.start_mission(rec.mission_id)

    for _ in range(20):
        time.sleep(0.5)
        updated = engine.store.get(rec.mission_id)
        if updated and updated.state in (MissionState.COMPLETE, MissionState.FAILED):
            break

    final = engine.store.get(rec.mission_id)
    assert final is not None
    assert final.state == MissionState.COMPLETE
    assert len(final.deliverables) >= 1
    audit_report = Path(final.deliverables[0])
    assert audit_report.exists()
    assert audit_report.stat().st_size > 0


# ── Test 5: Mission Archetype C (Developer Mission) ───────────────────────────

def test_mission_archetype_c_developer_inspection(temp_mission_env):
    """Execute developer mission to inspect project structure, git status, and generate assessment."""
    engine = temp_mission_env["engine"]
    rec = engine.create_mission("Developer mission to inspect project structure, git status, and test suite")
    
    engine.start_mission(rec.mission_id)

    for _ in range(20):
        time.sleep(0.5)
        updated = engine.store.get(rec.mission_id)
        if updated and updated.state in (MissionState.COMPLETE, MissionState.FAILED):
            break

    final = engine.store.get(rec.mission_id)
    assert final is not None
    assert final.state == MissionState.COMPLETE
    assert len(final.deliverables) >= 1
    dev_report = Path(final.deliverables[0])
    assert dev_report.exists()


# ── Test 6: Mission Archetype D (Adaptive Failure Recovery) ───────────────────

def test_mission_archetype_d_failure_recovery_and_tool_substitution(temp_mission_env):
    """Test failure diagnosis, bounded retry, tool substitution, and downstream replanning."""
    recovery = temp_mission_env["engine"].recovery_engine

    # 1. Failure diagnosis
    subtask = MissionSubtask("st_test", "Failing step", retry_count=0, max_retries=3)
    diag_transient = recovery.diagnose_failure(subtask, "Connection timeout to remote service after 30000ms")
    assert diag_transient == FailureType.TRANSIENT

    diag_env = recovery.diagnose_failure(subtask, "FileNotFoundError: [Errno 2] No such file or directory: 'notes.txt'")
    assert diag_env == FailureType.ENVIRONMENTAL

    diag_perm = recovery.diagnose_failure(subtask, "PermissionError: Access denied: path outside permitted directories")
    assert diag_perm == FailureType.PERMISSIONS_DENIED

    # 2. Tool and agent fallback substitution
    alt_tool = recovery.select_alternative_tool("web_search", ["duckduckgo_search", "read_file"])
    assert alt_tool == "duckduckgo_search"

    alt_agent = recovery.select_alternative_agent("researcher", ["research_agent", "general"])
    assert alt_agent == "research_agent"

    # 3. Adapt subtask for retry
    can_retry = recovery.can_retry(subtask)
    assert can_retry is True
    adapted = recovery.adapt_subtask_for_recovery(
        subtask,
        FailureType.TRANSIENT,
        available_tools=["search_web"],
        available_agents=["researcher"],
    )
    assert adapted is True
    assert subtask.retry_count == 1
    assert subtask.status == SubtaskStatus.RETRYING

    # 4. Downstream replanning preserves completed work
    s1 = MissionSubtask("s1", "Step 1", status=SubtaskStatus.COMPLETED)
    s2 = MissionSubtask("s2", "Step 2", status=SubtaskStatus.FAILED, depends_on=["s1"])
    s3 = MissionSubtask("s3", "Step 3", status=SubtaskStatus.PENDING, depends_on=["s2"])
    plan = MissionPlan("plan_test", subtasks=[s1, s2, s3])

    recovery.replan_downstream(plan, failed_subtask_id="s2")
    # Verify s1 remains COMPLETED
    assert plan.subtasks[0].status == SubtaskStatus.COMPLETED
    assert plan.version == 2


# ── Test 7: Mission Archetype E (Persistence & Resumption) ────────────────────

def test_mission_archetype_e_persistence_and_resumption_after_restart(temp_mission_env):
    """Test that interrupted missions can be resumed after restart without re-executing completed work."""
    store = temp_mission_env["store"]

    # 1. Create a mission with pre-completed step 1 and pending step 2
    s1 = MissionSubtask(
        "step_1",
        "Step 1 Already Done",
        status=SubtaskStatus.COMPLETED,
        result_output="Step 1 previously computed finding.",
    )
    s2 = MissionSubtask(
        "step_2",
        "Step 2 Pending",
        status=SubtaskStatus.PENDING,
        assigned_agent="developer",
        required_tools=["write_file"],
        expected_deliverables=["reports/resumed_mission_output.md"],
        depends_on=["step_1"],
    )
    plan = MissionPlan(objective="Resumption Test", subtasks=[s1, s2])
    rec = MissionRecord(
        mission_id="mission_resumed_99",
        objective="Resumption Test",
        state=MissionState.PAUSED,
        plan=plan,
    )
    store.save(rec)

    # Verify found as resumable
    resumables = store.find_resumable()
    assert any(m.mission_id == "mission_resumed_99" for m in resumables)

    # 2. Simulate application restart: instantiate a new engine from the same store
    new_engine = MissionExecutionEngine(
        store=store,
        planner=temp_mission_env["planner"],
        coordinator=temp_mission_env["coordinator"],
        recovery_engine=temp_mission_env["engine"].recovery_engine,
    )

    # Resume the mission
    resumed_rec = new_engine.resume_mission("mission_resumed_99")
    assert resumed_rec is not None

    # Wait for completion
    for _ in range(15):
        time.sleep(0.5)
        check = store.get("mission_resumed_99")
        if check and check.state in (MissionState.COMPLETE, MissionState.FAILED):
            break

    final = store.get("mission_resumed_99")
    assert final.state == MissionState.COMPLETE
    # Step 1 was preserved as COMPLETED
    assert final.plan.subtasks[0].status == SubtaskStatus.COMPLETED
    assert final.plan.subtasks[1].status == SubtaskStatus.COMPLETED
    assert len(final.deliverables) >= 1


# ── Test 8: Multi-Agent Concurrency ──────────────────────────────────────────

def test_mission_concurrency_and_parallel_execution(temp_mission_env):
    """Test parallel execution of independent subtasks using ThreadPool."""
    coord = temp_mission_env["coordinator"]
    record = MissionRecord(objective="Concurrent Batch")
    memory = MissionMemory(record.mission_id)

    st_a = MissionSubtask("st_a", "Parallel Task A", parallelizable=True, required_tools=["cpu_status"])
    st_b = MissionSubtask("st_b", "Parallel Task B", parallelizable=True, required_tools=["memory_status"])

    batch_results = coord.execute_concurrent_group([st_a, st_b], record, memory)
    assert len(batch_results) == 2
    assert batch_results["st_a"][0] is True
    assert batch_results["st_b"][0] is True
    assert st_a.status == SubtaskStatus.COMPLETED
    assert st_b.status == SubtaskStatus.COMPLETED


# ── Test 9: Human Approval Gate ──────────────────────────────────────────────

def test_mission_approval_gate_workflow(temp_mission_env):
    """Verify that missions flagged with elevated risk wait for approval and can be approved/rejected."""
    engine = temp_mission_env["engine"]
    
    # 1. High-risk objective triggers approval
    rec = engine.create_mission("Terminate process and format disk")
    engine.start_mission(rec.mission_id)

    # Wait for WAITING_FOR_APPROVAL state
    for _ in range(10):
        time.sleep(0.2)
        check = engine.store.get(rec.mission_id)
        if check and check.state == MissionState.WAITING_FOR_APPROVAL:
            break

    waiting_check = engine.store.get(rec.mission_id)
    assert waiting_check.state == MissionState.WAITING_FOR_APPROVAL

    # 2. Reject mission
    rejected = engine.reject_mission(rec.mission_id)
    assert rejected is True

    time.sleep(0.5)
    final = engine.store.get(rec.mission_id)
    assert final.state == MissionState.CANCELLED


# ── Test 10: Mission Control Center UI Integration ───────────────────────────

def test_mission_control_center_ui_integration(temp_mission_env):
    """Verify SectionMissions widget rendering and service bridge signals."""
    import sys

    from PyQt6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication(sys.argv)

    from gui.optimus.control_center import OptimusControlCenterWidget
    from gui.optimus.nav_bar import OptimusNavBar
    from gui.optimus.section_missions import SectionMissions
    from gui.optimus.service_bridge import OptimusServiceBridge

    bridge = OptimusServiceBridge.instance()
    sec = SectionMissions(bridge)
    assert sec is not None
    assert sec.table_missions is not None
    assert sec.table_subtasks is not None

    # Check nav bar integration
    nav = OptimusNavBar()
    section_titles = [title for title, _ in nav.SECTIONS]
    assert "MISSIONS" in section_titles

    # Check Control Center Widget integration
    cc = OptimusControlCenterWidget(bridge)
    assert cc.missions_view is not None
    assert cc.stack.count() == 14
    # Switch to missions section
    cc.navigate_to("MISSIONS")
    assert cc.stack.currentWidget() == cc.missions_view
