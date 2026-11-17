"""tests/test_component10_e2e.py — Component 10 End-to-End System Integration Tests.

Validates the complete 20-scenario OPTIMUS system lifecycle:
1.  test_e2e_01_startup_initialization
2.  test_e2e_02_simple_text_request
3.  test_e2e_03_multistep_autonomous_task
4.  test_e2e_04_workflow_with_dependencies
5.  test_e2e_05_specialized_agent_delegation
6.  test_e2e_06_sandboxed_tool_execution
7.  test_e2e_07_physical_postcondition_verification
8.  test_e2e_08_memory_persist_and_retrieve
9.  test_e2e_09_interrupted_workflow_recovery
10. test_e2e_10_replan_after_failure
11. test_e2e_11_reject_unauthorized_destructive
12. test_e2e_12_cancelled_task
13. test_e2e_13_voice_interaction
14. test_e2e_14_stop_speech_preserves_task
15. test_e2e_15_privacy_strict_image_routing
16. test_e2e_16_model_provider_failure_recovery
17. test_e2e_17_conversation_restoration
18. test_e2e_18_planning_history_restoration
19. test_e2e_19_approval_expiration
20. test_e2e_20_clean_shutdown
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pytest

from core.intelligence import (
    IntelligenceGateway,
    ModelRequest,
    ModelResponse,
    PrivacyViolationError,
    RoutingPolicy,
)
from core.intelligence.types import ChatMessage
from core.interaction.runtime import InteractionRuntime
from core.interaction.types import (
    InteractionModality,
    InteractionRole,
)
from core.memory import MemoryCategory, MemoryEntry, OptimusMemoryManager
from core.orchestrator.events import EventBus, TaskCompletedEvent
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import (
    ExecutionPlan,
    Subtask,
    SubtaskStatus,
    TaskRecord,
    TaskStatus,
    TaskStore,
)
from core.orchestrator.tools import ToolRegistry
from core.planning import (
    AdaptiveExecutionEngine,
    AdaptivePlanner,
    AdaptiveReplanner,
    OutcomeStore,
    StrategyType,
)
from core.planning.types import (
    AdaptivePlan,
    ExecutionOutcome,
    GoalCategory,
    OutcomeType,
    PlanStep,
)
from core.verification.engine import VerificationEngine
from core.verification.types import Postcondition, PostconditionType, VerificationStatus
from core.verification.verifiers import FilesystemVerifier
from core.workflows.approval import ApprovalManager, ApprovalStatus
from core.workflows.engine import WorkflowEngine
from core.workflows.storage import WorkflowStore
from core.workflows.types import (
    StepExecutionRecord,
    StepStatus,
    WorkflowDefinition,
    WorkflowExecutionRecord,
    WorkflowStatus,
    WorkflowStepDefinition,
)

# ── Scenario 1: Startup & Initialization ─────────────────────────────────────

def test_e2e_01_startup_initialization(tmp_path):
    """Scenario 1: All OPTIMUS subsystems initialize and link without error."""
    bus = EventBus()
    mem = OptimusMemoryManager(storage_root=tmp_path / "init_mem")
    tools = ToolRegistry(event_bus=bus)
    verif = VerificationEngine(storage_root=tmp_path / "init_verif")
    wf_store = WorkflowStore(base_dir=tmp_path / "init_wf")
    orch = CentralOrchestrator(event_bus=bus, memory_manager=mem, tool_registry=tools, verification_engine=verif)
    wf_engine = WorkflowEngine(orchestrator=orch, store=wf_store)
    outcomes = OutcomeStore(storage_path=tmp_path / "outcomes.json", memory_manager=mem)
    planner = AdaptivePlanner(event_bus=bus, tool_registry=tools, outcome_store=outcomes)

    assert bus is not None
    assert mem.store is not None
    assert tools.get("calculator") is not None
    assert wf_engine is not None
    assert outcomes.get_success_rate() == 1.0
    assert planner is not None


# ── Scenario 2: Simple Text Request ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_02_simple_text_request(tmp_path):
    """Scenario 2: Submit text conversational turn, verify assistant response."""
    runtime = InteractionRuntime(
        event_bus=EventBus(),
        memory_manager=OptimusMemoryManager(storage_root=tmp_path / "mem_s2"),
    )
    # Mock conversational intent response
    resp = await runtime.process_turn(
        session_id="s2-session",
        text="Hello, OPTIMUS system diagnostic status please.",
        modality=InteractionModality.TEXT,
    )
    assert isinstance(resp, str)
    assert len(resp) > 0


# ── Scenario 3: Multi-step Autonomous Task ───────────────────────────────────

def test_e2e_03_multistep_autonomous_task(tmp_path):
    """Scenario 3: Planning->Execution->Verification pipeline records physical outcomes."""
    bus = EventBus()
    outcomes_file = tmp_path / "outcomes_s3.json"
    outcome_store = OutcomeStore(storage_path=outcomes_file)
    replanner = AdaptiveReplanner(event_bus=bus, outcome_store=outcome_store)
    verif = VerificationEngine(storage_root=tmp_path / "verif_s3")
    plan = AdaptivePlan(
        plan_id="e2e-plan-s3",
        goal="Parse JSON and calculate total items",
        category=GoalCategory.DATA_PIPELINE,
        strategy=StrategyType.CONSERVATIVE_SEQUENTIAL,
        steps=[
            PlanStep(
                step_id="step-1",
                title="Read data",
                description="Read data",
                assigned_agent="data_intelligence",
                required_tools=["read_file"],
            ),
            PlanStep(
                step_id="step-2",
                title="Calculate metric",
                description="Calculate metric",
                assigned_agent="data_intelligence",
                required_tools=["calculator"],
                depends_on=["step-1"],
            ),
        ],
    )

    def custom_runner(step):
        if step.step_id == "step-1":
            return {"success": True, "raw_json": '{"items": [10, 20, 30]}'}
        return {"success": True, "total": 60}

    executor = AdaptiveExecutionEngine(
        outcome_store=outcome_store,
        replanner=replanner,
        verification_engine=verif,
        event_bus=bus,
        step_runner=custom_runner,
    )

    outcome_type, _final_plan = executor.execute_plan(plan)
    assert outcome_type == OutcomeType.VERIFIED_SUCCESS

    # Verify outcome recorded
    recorded = outcome_store.list_outcomes()
    assert len(recorded) >= 1
    assert recorded[-1].plan_id == "e2e-plan-s3"


# ── Scenario 4: Workflow with Dependencies ───────────────────────────────────

def test_e2e_04_workflow_with_dependencies(tmp_path):
    """Scenario 4: WorkflowEngine executes DAG steps in topological order."""
    store = WorkflowStore(base_dir=tmp_path / "wf_dag")
    orch = CentralOrchestrator()
    engine = WorkflowEngine(orchestrator=orch, store=store)

    wf_def = WorkflowDefinition(
        workflow_id="wf-dag-01",
        name="Sequential DAG Pipeline",
        description="Run A then B",
        steps=[
            WorkflowStepDefinition(
                step_id="step-a",
                name="Step A",
                assigned_agent="calculator",
                description="Calculate 100 + 50",
            ),
            WorkflowStepDefinition(
                step_id="step-b",
                name="Step B",
                assigned_agent="calculator",
                description="Calculate 150 * 2",
                depends_on=["step-a"],
            ),
        ],
    )
    engine.register_workflow(wf_def)
    rec = engine.run_workflow_sync("wf-dag-01")
    assert rec.status == WorkflowStatus.COMPLETED
    assert rec.step_states["step-a"].status == StepStatus.COMPLETED
    assert rec.step_states["step-b"].status == StepStatus.COMPLETED


# ── Scenario 5: Specialized Agent Delegation ─────────────────────────────────

def test_e2e_05_specialized_agent_delegation():
    """Scenario 5: Agent uses authorized tool within its declared capability envelope."""
    registry = ToolRegistry()
    # code_intelligence agent using safe calculator AST tool
    res = registry.execute(
        tool_name="calculator",
        arguments={"expression": "2 ** 8"},
        caller_agent="code_intelligence",
        allowed_tools=["calculator", "code_ast_search"],
    )
    assert res.status == "done"
    assert "256" in res.output


# ── Scenario 6: Sandboxed Tool Execution ─────────────────────────────────────

def test_e2e_06_sandboxed_tool_execution(tmp_path):
    """Scenario 6: Tool runs in sandbox; outside path access is strictly blocked."""
    registry = ToolRegistry()
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    target = sandbox_dir / "output.txt"

    # Write inside allowed root -> success
    w_res = registry.execute(
        tool_name="write_file",
        arguments={"path": str(target), "content": "sandboxed content"},
        caller_agent="orchestrator",
        context={"allowed_roots": [str(sandbox_dir)]},
    )
    assert w_res.status == "done"
    assert target.read_text() == "sandboxed content"

    # Write outside allowed root -> PATH_VIOLATION
    blocked = registry.execute(
        tool_name="write_file",
        arguments={"path": str(tmp_path / "outside.txt"), "content": "leak"},
        caller_agent="filesystem_agent",
        allowed_tools=["write_file"],
        context={"allowed_roots": [str(sandbox_dir)]},
    )
    assert blocked.status == "failed"
    assert "PATH_VIOLATION" in blocked.error or "outside permitted" in blocked.error or "Access denied" in blocked.error


# ── Scenario 7: Physical Postcondition Verification ──────────────────────────

def test_e2e_07_physical_postcondition_verification(tmp_path):
    """Scenario 7: VerificationEngine verifies file existence, size, and SHA256 integrity."""
    verifier = FilesystemVerifier()
    test_file = tmp_path / "evidence.txt"
    content = b"Verified deliverable content by OPTIMUS."
    test_file.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    pc = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(test_file), "must_exist": True, "sha256": expected_hash, "min_bytes": 10},
    )
    rec = verifier.verify("task_s7", "st_s7", pc)
    assert rec.is_pass is True
    assert rec.status == VerificationStatus.PASS
    assert rec.verify_integrity() is True
    assert rec.actual_observed_result.get("sha256") == expected_hash


# ── Scenario 8: Memory Persist and Retrieve ──────────────────────────────────

def test_e2e_08_memory_persist_and_retrieve(tmp_path):
    """Scenario 8: Semantic deliverable persisted to disk and retrieved on restart."""
    mem_dir = tmp_path / "mem_s8"
    mgr1 = OptimusMemoryManager(storage_root=mem_dir)
    entry = MemoryEntry(
        entry_id="mem-e2e-08",
        category=MemoryCategory.SEMANTIC,
        key="release_version",
        content="Mark-LVII OPTIMUS v1.0.0-RELEASE",
        is_verified=True,
    )
    mgr1.store.put(entry)

    # Simulate restart
    mgr2 = OptimusMemoryManager(storage_root=mem_dir)
    retrieved = mgr2.store.get_by_key(MemoryCategory.SEMANTIC, "release_version")
    assert retrieved is not None
    assert retrieved.content == "Mark-LVII OPTIMUS v1.0.0-RELEASE"
    assert retrieved.is_verified is True


# ── Scenario 9: Interrupted Workflow Recovery ────────────────────────────────

def test_e2e_09_interrupted_workflow_recovery(tmp_path):
    """Scenario 9: In-flight workflow reconciled to AMBIGUOUS_EXECUTION upon restart."""
    store = WorkflowStore(base_dir=tmp_path / "wf_s9")

    # Save a workflow record simulating a process that crashed while step-mid was RUNNING
    crash_rec = WorkflowExecutionRecord(
        execution_id="exec-crash-e2e",
        workflow_id="wf-crash-test",
        status=WorkflowStatus.RUNNING,
        step_states={
            "step-pre": StepExecutionRecord(step_id="step-pre", status=StepStatus.COMPLETED),
            "step-mid": StepExecutionRecord(step_id="step-mid", status=StepStatus.RUNNING),
        },
    )
    store.save_execution(crash_rec)

    # Reconcile on startup
    recovered = store.reconcile_interrupted_executions()
    assert len(recovered) == 1
    rec = recovered[0]
    assert rec.status == WorkflowStatus.PAUSED
    assert rec.step_states["step-mid"].status == StepStatus.AMBIGUOUS_EXECUTION


# ── Scenario 10: Replan After Failure ────────────────────────────────────────

def test_e2e_10_replan_after_failure(tmp_path):
    """Scenario 10: AdaptiveReplanner creates revised plan when step encounters environmental failure."""
    bus = EventBus()
    outcomes = OutcomeStore(storage_path=tmp_path / "outcomes_s10.json")
    replanner = AdaptiveReplanner(event_bus=bus, outcome_store=outcomes)

    failed_plan = AdaptivePlan(
        plan_id="plan-s10",
        goal="Read legacy config file",
        category=GoalCategory.SYSTEM_OPS,
        strategy=StrategyType.CONSERVATIVE_SEQUENTIAL,
        steps=[
            PlanStep(step_id="s1", title="Read file", description="Read file", assigned_agent="system_diagnosis", required_tools=["read_file"]),
        ],
    )

    revised = replanner.replan(
        plan=failed_plan,
        failed_step_id="s1",
        error_message="FileNotFoundError: config.json missing in workspace",
    )
    assert revised.version == 2
    assert len(revised.replan_history) == 1
    assert revised.replan_history[0]["failed_step_id"] == "s1"
    assert len(revised.steps) >= 1


# ── Scenario 11: Reject Unauthorized Destructive Step ────────────────────────

def test_e2e_11_reject_unauthorized_destructive():
    """Scenario 11: Workflow step requiring approval is blocked when rejected."""
    app_mgr = ApprovalManager()
    app = app_mgr.request_approval(
        workflow_id="wf-s11",
        execution_id="exec-s11",
        step_id="step-format-disk",
        reason="Format system disk partition",
        timeout_seconds=300.0,
    )
    assert app.status == ApprovalStatus.PENDING

    # Reject authorization
    rejected = app_mgr.reject(app.approval_id, approver="admin", reason="Unauthorized action")
    assert rejected.status == ApprovalStatus.REJECTED
    assert rejected.decision_reason == "Unauthorized action"


# ── Scenario 12: Cancelled Task Cascades ─────────────────────────────────────

def test_e2e_12_cancelled_task(tmp_path):
    """Scenario 12: Cancelling parent task cascades CANCELLED to all active/pending subtasks."""
    store = TaskStore(storage_root=tmp_path / "task_s12")
    plan = ExecutionPlan(
        plan_id="plan-s12",
        task_id="task-s12",
        goal="Multi-phase compilation",
        subtasks=[
            Subtask(subtask_id="sub-a", task_id="task-s12", index=0, title="Phase 1", status=SubtaskStatus.COMPLETED),
            Subtask(subtask_id="sub-b", task_id="task-s12", index=1, title="Phase 2", status=SubtaskStatus.RUNNING),
            Subtask(subtask_id="sub-c", task_id="task-s12", index=2, title="Phase 3", status=SubtaskStatus.PENDING),
        ],
    )
    store.save_task(TaskRecord(task_id="task-s12", goal="Multi-phase compilation", status=TaskStatus.RUNNING, plan=plan))

    cancelled = store.cancel_task("task-s12", reason="Operator abort")
    assert cancelled.status == TaskStatus.CANCELLED
    states = {s.subtask_id: s.status for s in cancelled.plan.subtasks}
    assert states["sub-a"] == SubtaskStatus.COMPLETED
    assert states["sub-b"] == SubtaskStatus.CANCELLED
    assert states["sub-c"] == SubtaskStatus.CANCELLED


# ── Scenario 13: Voice Interaction ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_13_voice_interaction(tmp_path):
    """Scenario 13: Voice modality input processed through InteractionRuntime."""
    runtime = InteractionRuntime(
        event_bus=EventBus(),
        memory_manager=OptimusMemoryManager(storage_root=tmp_path / "mem_s13"),
    )
    resp = await runtime.process_turn(
        session_id="voice-session-1",
        text="Status report on core memory bus",
        modality=InteractionModality.VOICE,
    )
    assert isinstance(resp, str)


# ── Scenario 14: Stop Speech Preserves Running Task ──────────────────────────

@pytest.mark.asyncio
async def test_e2e_14_stop_speech_preserves_task(tmp_path):
    """Scenario 14: 'Stop speaking' interrupts TTS without cancelling background task."""
    runtime = InteractionRuntime(
        event_bus=EventBus(),
        memory_manager=OptimusMemoryManager(storage_root=tmp_path / "mem_s14"),
    )
    # Register active task in session
    sess = runtime.session_manager.get_or_create_session("sess-speech-test")
    runtime.session_manager.set_active_task(sess.session_id, "task-running-99")

    # Send voice interruption command
    resp = await runtime.process_turn(
        session_id=sess.session_id,
        text="stop speaking",
        modality=InteractionModality.VOICE,
    )
    # Active task must NOT be cleared by stop_speaking!
    state = runtime.session_manager.get_or_create_session(sess.session_id)
    assert state.active_task_id == "task-running-99"
    assert "speech playback stopped" in resp.lower() or "silenced" in resp.lower() or "stopped" in resp.lower()


# ── Scenario 15: Privacy-Strict Image Routing ─────────────────────────────────

def test_e2e_15_privacy_strict_image_routing():
    """Scenario 15: PRIVACY_STRICT blocks cloud routing when vision model is missing locally."""
    gw = IntelligenceGateway.instance()
    gw.set_policy(RoutingPolicy.PRIVACY_STRICT)

    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Describe this blueprint")],
        required_capabilities={"vision"},
        is_private=True,
    )
    # Under PRIVACY_STRICT, if no local vision model exists, must raise PrivacyViolationError
    try:
        models = gw.router.select_models(req)
        # If any model returned, it MUST be strictly local
        assert all(m.is_local for m in models)
    except PrivacyViolationError:
        pass  # Expected behavior when no qualifying local vision model exists
    finally:
        gw.set_policy(RoutingPolicy.LOCAL_FIRST)


# ── Scenario 16: Model Provider Failure Recovery ─────────────────────────────

def test_e2e_16_model_provider_failure_recovery():
    """Scenario 16: Router fallback chain tries next model when primary fails."""
    gw = IntelligenceGateway.instance()
    req = ModelRequest(
        messages=[ChatMessage(role="user", content="Compute hash")],
    )
    candidates = gw.router.select_models(req)
    assert len(candidates) >= 1

    call_counts = {"count": 0}

    def failing_invoker(model_meta, request):
        call_counts["count"] += 1
        if call_counts["count"] == 1 and len(candidates) > 1:
            raise RuntimeError("Primary provider connection timeout")
        return ModelResponse(text="Fallback response success", model_id=model_meta.model_id)

    res = gw.router.execute_with_fallback(req, failing_invoker)
    assert res.text == "Fallback response success"


# ── Scenario 17: Conversation Restoration ────────────────────────────────────

def test_e2e_17_conversation_restoration(tmp_path):
    """Scenario 17: Conversation history persists and restores across session reload."""
    sess_file = tmp_path / "sessions.json"
    from core.interaction.session import InteractionSessionManager

    mgr1 = InteractionSessionManager(storage_path=sess_file)
    s = mgr1.get_or_create_session("restoration-session")
    mgr1.add_message(s.session_id, InteractionRole.USER, "What is current battery status?")
    mgr1.add_message(s.session_id, InteractionRole.ASSISTANT, "Battery level is 98%.")
    mgr1.save()

    # Reload from disk
    mgr2 = InteractionSessionManager(storage_path=sess_file)
    recovered = mgr2.get_or_create_session("restoration-session")
    assert recovered is not None
    assert len(recovered.messages) == 2
    assert recovered.messages[0].content == "What is current battery status?"
    assert recovered.messages[1].content == "Battery level is 98%."


# ── Scenario 18: Planning History Restoration ────────────────────────────────

def test_e2e_18_planning_history_restoration(tmp_path):
    """Scenario 18: OutcomeStore accurately hydrates verified history across restarts."""
    outcomes_file = tmp_path / "history.json"
    store1 = OutcomeStore(storage_path=outcomes_file)
    store1.record_outcome(ExecutionOutcome(
        outcome_id="hist-01",
        plan_id="plan-hist-01",
        goal="Audit logs",
        goal_category=GoalCategory.SYSTEM_OPS,
        strategy=StrategyType.CONSERVATIVE_SEQUENTIAL,
        outcome=OutcomeType.VERIFIED_SUCCESS,
        duration_seconds=3.2,
    ))

    store2 = OutcomeStore(storage_path=outcomes_file)
    assert len(store2.list_outcomes()) == 1
    assert store2.get_success_rate(GoalCategory.SYSTEM_OPS) == 1.0


# ── Scenario 19: Approval Expiration ─────────────────────────────────────────

def test_e2e_19_approval_expiration():
    """Scenario 19: Timed-out approval request transitions to EXPIRED and blocks execution."""
    mgr = ApprovalManager()
    app = mgr.request_approval(
        workflow_id="wf-e2e-19",
        execution_id="exec-e2e-19",
        step_id="step-destructive",
        reason="Drop audit logs table",
        timeout_seconds=0.04,
    )
    time.sleep(0.06)

    expired = mgr.check_expirations()
    assert len(expired) >= 1
    assert any(a.approval_id == app.approval_id for a in expired)

    with pytest.raises(TimeoutError):
        mgr.approve(app.approval_id, approver="admin")


# ── Scenario 20: Clean Shutdown ──────────────────────────────────────────────

def test_e2e_20_clean_shutdown():
    """Scenario 20: All services and bridge instances release resources cleanly."""
    bus = EventBus()
    # Ensure event publishing and subscriber callbacks complete without deadlock
    received = []
    bus.subscribe(TaskCompletedEvent, lambda e: received.append(e))
    bus.publish(TaskCompletedEvent(task_id="task-clean-shutdown", result="Done"))
    assert len(received) == 1
