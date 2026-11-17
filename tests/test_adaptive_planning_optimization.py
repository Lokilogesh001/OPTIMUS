"""tests/test_adaptive_planning_optimization.py — Comprehensive Test Suite for OPTIMUS Component 9.

Validates the complete Adaptive Planning & Evidence-Driven Self-Improvement Subsystem:
1. Goal interpretation, classification, complexity scoring, and destructive risk detection.
2. Structured subtask decomposition, conjunction splitting, and dependency resolution.
3. Strategy selector: Conservative Sequential, Fast Parallel, Specialized Minimal, Robust Fault-Tolerant.
4. Deterministic plan validator: DAG acyclicity (cycle detection), self-dependency, dangling refs.
5. Plan validator: Agent registry checking, CBAC tool whitelists, and destructive approval enforcement.
6. AdaptivePlanner: Formulates valid version 1 plan and dispatches PlanFormulatedEvent.
7. Component 6 interoperability: Export AdaptivePlan to WorkflowDefinition DAG.
8. Diagnostic failure classification: TRANSIENT, ENVIRONMENTAL, CAPABILITY_MISMATCH, LOGICAL, PERMISSIONS_DENIED.
9. Replanner remediation for transient failure: Retry reinforcement and timeout extension.
10. Replanner remediation for environmental failure: Prerequisite diagnosis step injection.
11. Replanner remediation for capability mismatch: Agent substitution and tool fallback.
12. Replanner remediation for permissions denied: Human admin approval gating and risk escalation.
13. OutcomeStore: Physical evidence enforcement, empirical success rate, and procedural memory promotion.
14. Optimizer sample size threshold: Requires >= 3 observations before recommending changes.
15. Optimizer evidence analysis: Agent deprioritization and strategy preference adjustments.
16. Strict safety lifecycle: Observe -> Recommend -> Apply with full rollback reversibility.
17. AdaptiveExecutionEngine: Self-healing execution loop with automated replanning.
18. Cooperative plan cancellation handling.
19. Control Center UI integration and ServiceBridge wiring.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Force offscreen rendering for headless UI test
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PyQt6.QtWidgets import QApplication

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.agents import register_specialized_agents
from core.memory import OptimusMemoryManager
from core.orchestrator import AgentRegistry, EventBus, ToolRegistry
from core.orchestrator.tools import ToolDefinition
from core.planning import (
    AdaptiveExecutionEngine,
    AdaptivePlan,
    AdaptivePlanner,
    AdaptiveReplanner,
    EvidenceDrivenOptimizer,
    ExecutionOutcome,
    FailureType,
    GoalAnalyzer,
    GoalCategory,
    GoalDecomposition,
    OutcomeStore,
    OutcomeType,
    PlanRiskLevel,
    PlanStep,
    PlanValidator,
    StrategySelector,
    StrategyType,
)
from core.planning.events import (
    PlanFormulatedEvent,
)
from gui.optimus import (
    OptimusPlanningSection,
    OptimusServiceBridge,
)


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def temp_outcome_file(tmp_path):
    f = tmp_path / "test_planning_outcomes.json"
    return f


@pytest.fixture
def outcome_store(temp_outcome_file, tmp_path):
    mem_mgr = OptimusMemoryManager(storage_root=tmp_path / "memory")
    return OutcomeStore(storage_path=temp_outcome_file, memory_manager=mem_mgr)


@pytest.fixture
def agent_registry():
    reg = AgentRegistry()
    register_specialized_agents(reg)
    return reg


@pytest.fixture
def tool_registry():
    tr = ToolRegistry()
    tr.register(ToolDefinition(name="read_file", description="Read file", function=lambda path: "content"))
    tr.register(ToolDefinition(name="write_file", description="Write file", function=lambda path, c="": True))
    tr.register(ToolDefinition(name="system_telemetry", description="System telemetry", function=dict))
    return tr


# ── Test Cases ───────────────────────────────────────────────────────────────

def test_goal_analyzer_classification_and_complexity():
    """Test 1: Verify GoalAnalyzer classifies categories, scores complexity, and detects destructive risks."""
    analyzer = GoalAnalyzer()

    # Code modification goal
    d_code = analyzer.analyze("Refactor the python module and optimize database queries")
    assert d_code.category == GoalCategory.CODE_MODIFICATION
    assert d_code.risk_level == PlanRiskLevel.LOW
    assert "code_intelligence" in d_code.required_capabilities

    # Destructive system goal
    d_destr = analyzer.analyze("Delete all temporary cache files and remove root logs")
    assert d_destr.is_destructive is True
    assert d_destr.risk_level in (PlanRiskLevel.HIGH, PlanRiskLevel.CRITICAL)
    assert d_destr.complexity_score >= 3.0

    # Data pipeline goal
    d_data = analyzer.analyze("Parse sales CSV table, clean records, and aggregate summary metrics")
    assert d_data.category == GoalCategory.DATA_PIPELINE
    assert "data_intelligence" in d_data.required_capabilities


def test_goal_decomposition_into_steps():
    """Test 2: Verify multi-stage goals are decomposed into sequential, dependency-wired PlanSteps."""
    analyzer = GoalAnalyzer()
    goal = "Search codebase for memory leaks then profile CPU usage and after that write diagnostic report to summary.txt"

    steps = analyzer.decompose_into_steps(goal, GoalCategory.GENERAL)
    assert len(steps) >= 3

    # Check step IDs and sequential dependencies
    assert steps[0].step_id == "step_1"
    assert steps[1].step_id == "step_2"
    assert steps[2].step_id == "step_3"

    assert steps[0].depends_on == []
    assert steps[1].depends_on == ["step_1"]
    assert steps[2].depends_on == ["step_2"]

    # Deliverables extraction
    assert "summary.txt" in steps[2].expected_deliverables


def test_strategy_selector_risk_and_complexity(outcome_store):
    """Test 3: Verify StrategySelector adapts strategy based on risk, complexity, and empirical history."""
    selector = StrategySelector(outcome_store=outcome_store)

    # 1. High risk / destructive MUST choose CONSERVATIVE_SEQUENTIAL
    decomp_high = GoalDecomposition(goal="Purge database", risk_level=PlanRiskLevel.HIGH, is_destructive=True)
    steps_dummy = [PlanStep(step_id="s1", title="Purge", is_destructive=True, requires_approval=True)]
    strat = selector.select_strategy(decomp_high, steps_dummy)
    assert strat == StrategyType.CONSERVATIVE_SEQUENTIAL

    # 2. Minimal single-step task -> SPECIALIZED_MINIMAL
    decomp_mini = GoalDecomposition(goal="Quick read", complexity_score=1.5, risk_level=PlanRiskLevel.LOW)
    steps_mini = [PlanStep(step_id="s1", title="Read")]
    assert selector.select_strategy(decomp_mini, steps_mini) == StrategyType.SPECIALIZED_MINIMAL

    # 3. Independent multi-step low-risk task -> FAST_PARALLEL
    decomp_par = GoalDecomposition(goal="Parallel tasks", complexity_score=3.0, risk_level=PlanRiskLevel.LOW)
    steps_par = [
        PlanStep(step_id="s1", title="Task 1", is_parallelizable=True),
        PlanStep(step_id="s2", title="Task 2", is_parallelizable=True),
    ]
    assert selector.select_strategy(decomp_par, steps_par) == StrategyType.FAST_PARALLEL

    # 4. Explicit override
    selector.set_override(StrategyType.ROBUST_FAULT_TOLERANT)
    assert selector.select_strategy(decomp_mini, steps_mini) == StrategyType.ROBUST_FAULT_TOLERANT
    selector.set_override(None)


def test_plan_validator_acyclic_dag_enforcement():
    """Test 4: Verify PlanValidator rejects cyclic dependencies, self-dependencies, and dangling references."""
    validator = PlanValidator()

    # Self dependency
    plan_self = AdaptivePlan(
        steps=[PlanStep(step_id="s1", title="Step 1", depends_on=["s1"])]
    )
    is_valid, errors = validator.validate(plan_self)
    assert is_valid is False
    assert any("self-dependency" in e for e in errors)

    # Dangling reference
    plan_dangling = AdaptivePlan(
        steps=[PlanStep(step_id="s1", title="Step 1", depends_on=["non_existent_step"])]
    )
    is_valid, errors = validator.validate(plan_dangling)
    assert is_valid is False
    assert any("non-existent" in e for e in errors)

    # Cyclic dependency loop: s1 -> s2 -> s3 -> s1
    plan_cycle = AdaptivePlan(
        steps=[
            PlanStep(step_id="s1", title="1", depends_on=["s3"]),
            PlanStep(step_id="s2", title="2", depends_on=["s1"]),
            PlanStep(step_id="s3", title="3", depends_on=["s2"]),
        ]
    )
    is_valid, errors = validator.validate(plan_cycle)
    assert is_valid is False
    assert any("Cyclic dependency loop" in e for e in errors)


def test_plan_validator_agent_and_cbac_safeguards(agent_registry):
    """Test 5: Verify PlanValidator enforces agent registration and mandatory destructive approvals."""
    validator = PlanValidator(agent_registry=agent_registry)

    # Unregistered agent
    plan_unknown_agent = AdaptivePlan(
        steps=[PlanStep(step_id="s1", title="Task", assigned_agent="non_existent_rogue_agent")]
    )
    is_valid, errors = validator.validate(plan_unknown_agent)
    assert is_valid is False
    assert any("Unrecognized agent" in e for e in errors)

    # Destructive step lacking mandatory approval
    plan_unapproved_destr = AdaptivePlan(
        risk_level=PlanRiskLevel.HIGH,
        steps=[PlanStep(step_id="s1", title="Drop table", is_destructive=True, requires_approval=False, assigned_agent="general")]
    )
    is_valid, errors = validator.validate(plan_unapproved_destr)
    assert is_valid is False
    assert any("Destructive operations require mandatory operator approval" in e for e in errors)


def test_adaptive_planner_formulates_valid_plan(agent_registry, outcome_store):
    """Test 6: Verify AdaptivePlanner coordinates formulation, validation, and event dispatch."""
    bus = EventBus()
    dispatched_events = []
    bus.subscribe(PlanFormulatedEvent, lambda e: dispatched_events.append(e))

    planner = AdaptivePlanner(
        event_bus=bus,
        agent_registry=agent_registry,
        outcome_store=outcome_store,
    )

    plan = planner.create_plan("Analyze python files and profile cpu performance")
    assert plan.version == 1
    assert len(plan.steps) >= 1
    assert plan.risk_level in (PlanRiskLevel.LOW, PlanRiskLevel.MEDIUM)
    assert len(dispatched_events) == 1
    assert dispatched_events[0].plan_id == plan.plan_id


def test_planner_export_to_workflow_interoperability(agent_registry):
    """Test 7: Verify AdaptivePlan cleanly converts to Component 6 WorkflowDefinition contracts."""
    planner = AdaptivePlanner(agent_registry=agent_registry)
    plan = planner.create_plan("Refactor source code then run pytest tests")

    wf = planner.export_to_workflow(plan)
    assert wf.workflow_id == f"wf-{plan.plan_id}"
    assert len(wf.steps) == len(plan.steps)
    assert wf.steps[0].step_id == plan.steps[0].step_id
    assert wf.steps[0].assigned_agent == plan.steps[0].assigned_agent


def test_failure_diagnosis_classification():
    """Test 8: Verify root-cause failure diagnosis accurately classifies error patterns."""
    replanner = AdaptiveReplanner()
    step = PlanStep(step_id="s1", title="Test step")

    # Transient
    assert replanner.diagnose_failure(step, "Connection timed out after 30000ms") == FailureType.TRANSIENT
    assert replanner.diagnose_failure(step, "Rate limit exceeded; backoff required") == FailureType.TRANSIENT

    # Environmental
    assert replanner.diagnose_failure(step, "FileNotFoundError: [Errno 2] No such file or directory") == FailureType.ENVIRONMENTAL
    assert replanner.diagnose_failure(step, "OSError: Port in use: 8080") == FailureType.ENVIRONMENTAL

    # Capability mismatch
    assert replanner.diagnose_failure(step, "Missing tool: 'code_ast_search' not registered") == FailureType.CAPABILITY_MISMATCH

    # Permissions denied
    assert replanner.diagnose_failure(step, "Permission denied: CBAC tier restriction prevents tool invocation") == FailureType.PERMISSIONS_DENIED

    # Logical
    assert replanner.diagnose_failure(step, "AssertionError: Postcondition failed: checksum mismatch") == FailureType.LOGICAL


def test_adaptive_replan_transient_failure(agent_registry):
    """Test 9: Verify replanning a transient failure doubles timeout and increases retries."""
    bus = EventBus()
    replanner = AdaptiveReplanner(agent_registry=agent_registry, event_bus=bus)

    plan = AdaptivePlan(
        plan_id="plan-trans-01",
        version=1,
        steps=[PlanStep(step_id="s1", title="API Fetch", retry_count=1, timeout_seconds=30.0, assigned_agent="general")]
    )

    revised = replanner.replan(
        plan=plan,
        failed_step_id="s1",
        error_message="Gateway timeout 504",
    )

    assert revised.version == 2
    assert revised.steps[0].retry_count >= 3
    assert revised.steps[0].timeout_seconds >= 60.0
    assert len(revised.replan_history) == 1
    assert revised.replan_history[0]["failure_type"] == "TRANSIENT"


def test_adaptive_replan_environmental_failure(agent_registry):
    """Test 10: Verify replanning an environmental failure injects prerequisite remediation step."""
    replanner = AdaptiveReplanner(agent_registry=agent_registry)

    plan = AdaptivePlan(
        plan_id="plan-env-01",
        version=1,
        steps=[PlanStep(step_id="step_read", title="Read Logs", assigned_agent="general")]
    )

    revised = replanner.replan(
        plan=plan,
        failed_step_id="step_read",
        error_message="FileNotFoundError: directory missing",
    )

    assert revised.version == 2
    assert len(revised.steps) == 2  # Injected remediation step + target step
    assert revised.steps[0].step_id == "remediate_env_step_read"
    assert revised.steps[1].depends_on == ["remediate_env_step_read"]


def test_adaptive_replan_capability_mismatch(agent_registry):
    """Test 11: Verify replanning a capability mismatch reassigns agent and uses fallback tools."""
    replanner = AdaptiveReplanner(agent_registry=agent_registry)

    plan = AdaptivePlan(
        plan_id="plan-cap-01",
        version=1,
        steps=[PlanStep(step_id="s1", title="Specialized task", assigned_agent="code_intelligence", required_tools=["code_ast_search"])]
    )

    revised = replanner.replan(
        plan=plan,
        failed_step_id="s1",
        error_message="Missing tool: code_ast_search not found in sandbox",
    )

    assert revised.version == 2
    assert revised.steps[0].assigned_agent != "code_intelligence"
    assert "read_file" in revised.steps[0].required_tools


def test_adaptive_replan_permissions_denied(agent_registry):
    """Test 12: Verify replanning permissions failure elevates approval requirement to admin."""
    replanner = AdaptiveReplanner(agent_registry=agent_registry)

    plan = AdaptivePlan(
        plan_id="plan-perm-01",
        version=1,
        risk_level=PlanRiskLevel.LOW,
        steps=[PlanStep(step_id="s1", title="Write sensitive file", assigned_agent="general", requires_approval=False)]
    )

    revised = replanner.replan(
        plan=plan,
        failed_step_id="s1",
        error_message="CBAC tier restriction: Permission denied",
    )

    assert revised.version == 2
    assert revised.steps[0].requires_approval is True
    assert revised.steps[0].approval_role == "admin"
    assert revised.risk_level == PlanRiskLevel.HIGH


def test_outcome_store_physical_evidence_recording(outcome_store):
    """Test 13: Verify OutcomeStore persists verified physical evidence and promotes to procedural memory."""
    # Record success
    o1 = ExecutionOutcome(
        outcome_id="out-01",
        plan_id="plan-1",
        goal="Parse JSON and verify schema",
        goal_category=GoalCategory.DATA_PIPELINE,
        strategy=StrategyType.CONSERVATIVE_SEQUENTIAL,
        outcome=OutcomeType.VERIFIED_SUCCESS,
        duration_seconds=2.5,
        verified_evidence_count=2,
        recovery_attempts=1,
        agents_used=["data_intelligence"],
        tools_used=["read_file"],
    )
    outcome_store.record_outcome(o1)

    # Record failure
    o2 = ExecutionOutcome(
        outcome_id="out-02",
        plan_id="plan-2",
        goal="Parse malformed JSON",
        goal_category=GoalCategory.DATA_PIPELINE,
        strategy=StrategyType.CONSERVATIVE_SEQUENTIAL,
        outcome=OutcomeType.VERIFIED_FAILURE,
        duration_seconds=1.2,
        verified_evidence_count=0,
        agents_used=["data_intelligence"],
    )
    outcome_store.record_outcome(o2)

    assert outcome_store.get_success_rate(GoalCategory.DATA_PIPELINE) == 0.5
    assert outcome_store.get_recovery_rate() == 1.0

    # Verify procedural memory promotion occurred for the recovered plan
    procedural_memories = outcome_store.memory_manager.store.search("procedural_pattern_data_pipeline")
    assert len(procedural_memories) >= 1


def test_optimizer_sample_threshold_and_confidence(outcome_store):
    """Test 14: Verify EvidenceDrivenOptimizer requires >= 3 empirical observations before recommending."""
    optimizer = EvidenceDrivenOptimizer(outcome_store=outcome_store, min_samples_threshold=3)

    # Only 2 failures: should NOT recommend yet
    for i in range(2):
        outcome_store.record_outcome(
            ExecutionOutcome(
                outcome_id=f"out-under-{i}",
                goal_category=GoalCategory.SYSTEM_OPS,
                outcome=OutcomeType.VERIFIED_FAILURE,
                agents_used=["system_diagnosis"],
            )
        )

    recs = optimizer.evaluate_optimizations()
    assert len(recs) == 0

    # 3rd observation: now meets threshold
    outcome_store.record_outcome(
        ExecutionOutcome(
            outcome_id="out-under-3",
            goal_category=GoalCategory.SYSTEM_OPS,
            outcome=OutcomeType.VERIFIED_FAILURE,
            agents_used=["system_diagnosis"],
        )
    )

    recs = optimizer.evaluate_optimizations()
    assert len(recs) >= 1
    assert recs[0].observed_samples >= 3
    assert recs[0].confidence_score >= 0.65


def test_optimizer_recommends_strategy_improvement(outcome_store):
    """Test 15: Verify optimizer recommends CONSERVATIVE_SEQUENTIAL when FAST_PARALLEL demonstrates high failure."""
    strategy_selector = StrategySelector(outcome_store=outcome_store)
    optimizer = EvidenceDrivenOptimizer(
        outcome_store=outcome_store,
        strategy_selector=strategy_selector,
        min_samples_threshold=3,
    )

    # Record 3 failures with FAST_PARALLEL in CODE_MODIFICATION
    for i in range(3):
        outcome_store.record_outcome(
            ExecutionOutcome(
                outcome_id=f"out-fp-{i}",
                goal_category=GoalCategory.CODE_MODIFICATION,
                strategy=StrategyType.FAST_PARALLEL,
                outcome=OutcomeType.VERIFIED_FAILURE,
                agents_used=["code_intelligence"],
            )
        )

    recs = optimizer.evaluate_optimizations()
    strat_recs = [r for r in recs if r.target_type == "strategy_selection"]
    assert len(strat_recs) >= 1
    assert "CONSERVATIVE_SEQUENTIAL" in strat_recs[0].description


def test_optimizer_safe_lifecycle_apply_and_rollback(outcome_store):
    """Test 16: Verify Observe -> Recommend -> Apply lifecycle and safe rollback capability."""
    strategy_selector = StrategySelector(outcome_store=outcome_store)
    optimizer = EvidenceDrivenOptimizer(
        outcome_store=outcome_store,
        strategy_selector=strategy_selector,
        min_samples_threshold=3,
    )

    for i in range(3):
        outcome_store.record_outcome(
            ExecutionOutcome(
                outcome_id=f"out-opt-{i}",
                goal_category=GoalCategory.RESEARCH,
                strategy=StrategyType.FAST_PARALLEL,
                outcome=OutcomeType.VERIFIED_FAILURE,
            )
        )

    recs = optimizer.evaluate_optimizations()
    assert len(recs) >= 1
    rec = recs[0]

    # Cannot apply without approval
    with pytest.raises(PermissionError):
        optimizer.apply_recommendation(rec.recommendation_id, operator_approval=False)

    # Apply with approval
    applied = optimizer.apply_recommendation(rec.recommendation_id, operator_approval=True)
    assert applied is True
    assert rec.is_applied is True
    assert strategy_selector._strategy_biases.get(StrategyType.FAST_PARALLEL.value) == -0.5

    # Safe Rollback
    rolled_back = optimizer.rollback_recommendation(rec.recommendation_id)
    assert rolled_back is True
    assert rec.is_applied is False
    assert strategy_selector._strategy_biases.get(StrategyType.FAST_PARALLEL.value) == 0.0


def test_adaptive_executor_self_healing_execution_loop(outcome_store, agent_registry):
    """Test 17: Verify AdaptiveExecutionEngine attempts replan on step failure and reaches verified success."""
    replanner = AdaptiveReplanner(agent_registry=agent_registry, outcome_store=outcome_store)

    attempt_counter = {"count": 0}

    def flaky_runner(step: PlanStep) -> dict:
        attempt_counter["count"] += 1
        if attempt_counter["count"] == 1:
            # First attempt fails with transient timeout
            return {"success": False, "error": "Gateway timeout 504"}
        # Subsequent attempt succeeds
        return {"success": True, "deliverables": ["data.json"], "evidence": {"status": "PASSED"}}

    engine = AdaptiveExecutionEngine(
        outcome_store=outcome_store,
        replanner=replanner,
        step_runner=flaky_runner,
    )

    plan = AdaptivePlan(
        plan_id="plan-self-heal",
        goal="Download API payload",
        steps=[PlanStep(step_id="step_fetch", title="Fetch data", assigned_agent="general")]
    )

    final_outcome, final_plan = engine.execute_plan(plan, max_replans=2)
    assert final_outcome == OutcomeType.VERIFIED_SUCCESS
    assert final_plan.version >= 2
    assert attempt_counter["count"] == 2

    # Verify outcome recorded
    outcomes = outcome_store.list_outcomes()
    assert any(o.plan_id == "plan-self-heal" and o.outcome == OutcomeType.VERIFIED_SUCCESS for o in outcomes)


def test_adaptive_executor_cooperative_cancellation(outcome_store):
    """Test 18: Verify AdaptiveExecutionEngine cooperative cancellation halts execution and records USER_CANCELLED."""
    engine = AdaptiveExecutionEngine(outcome_store=outcome_store)

    plan = AdaptivePlan(
        plan_id="plan-cancel-01",
        goal="Long running background batch",
        steps=[
            PlanStep(step_id="s1", title="Step 1", assigned_agent="general"),
            PlanStep(step_id="s2", title="Step 2", assigned_agent="general", depends_on=["s1"]),
        ]
    )

    # Request cancellation before or during execution
    engine.request_cancellation()
    final_outcome, _ = engine.execute_plan(plan)
    assert final_outcome == OutcomeType.USER_CANCELLED

    outcomes = outcome_store.list_outcomes()
    assert any(o.plan_id == "plan-cancel-01" and o.outcome == OutcomeType.USER_CANCELLED for o in outcomes)


def test_planning_service_bridge_and_ui_wiring(outcome_store, qapp):
    """Test 19: Verify OptimusServiceBridge wires planning APIs, signals, and renders SectionPlanning."""
    bridge = OptimusServiceBridge.instance()

    # Formulate plan through bridge API
    plan_dict = bridge.create_adaptive_plan("Analyze code performance and run tests")
    assert "plan_id" in plan_dict
    assert len(plan_dict["steps"]) >= 1

    # Query metrics
    metrics = bridge.get_planning_metrics()
    assert "overall_success_rate" in metrics
    assert "recovery_rate" in metrics

    # Render SectionPlanning UI
    sec = OptimusPlanningSection(bridge)
    qapp.processEvents()

    assert sec.goal_input is not None
    assert sec.table_steps.columnCount() == 6
    assert sec.table_replan.columnCount() == 5
    assert sec.table_agents.columnCount() == 4
    assert sec.table_recs.columnCount() == 5

    # Test programmatic formulation through UI
    sec.goal_input.setText("Inspect memory and profile cpu")
    sec._on_formulate_clicked()
    qapp.processEvents()

    assert sec.table_steps.rowCount() >= 1
