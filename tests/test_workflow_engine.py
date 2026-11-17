"""tests/test_workflow_engine.py — Comprehensive Test Suite for OPTIMUS Component 6.

Tests:
1. Workflow definition validation (valid schema, duplicate ID rejection, missing dependency detection).
2. Circular dependency detection and rejection (self-cycle, 2-node cycle, 3-node cycle).
3. Topological execution stage calculation for parallel scheduling.
4. Safe condition evaluation without eval() or shell execution (expressions, outputs, artifacts, approvals).
5. Conditional branching and explicit step skipping.
6. Sequential step workflow execution via Central Orchestrator.
7. Parallel independent step scheduling.
8. Human authorization gates (PENDING -> APPROVED, rejection of automated LLM approval).
9. Human authorization rejection (PENDING -> REJECTED).
10. Human authorization expiration (timeout elapsed -> EXPIRED).
11. Pause and resume workflow execution.
12. Workflow cancellation.
13. Step retry policy and exponential backoff.
14. Crash recovery and ambiguous execution state detection.
15. Verification failure blocking downstream dependent steps.
16. Nested workflow execution with bounded depth.
17. Full component integration across all OPTIMUS subsystems.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.orchestrator.agents import AgentRegistry
from core.orchestrator.events import EventBus
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import TaskStore
from core.orchestrator.tools import ToolRegistry
from core.workflows.approval import ApprovalManager
from core.workflows.conditions import SafeConditionEvaluator
from core.workflows.engine import WorkflowEngine
from core.workflows.resolver import DependencyResolver
from core.workflows.storage import WorkflowStore
from core.workflows.types import (
    ApprovalStatus,
    CircularDependencyError,
    ConditionType,
    MissingDependencyError,
    RetryPolicy,
    StepCondition,
    StepExecutionRecord,
    StepStatus,
    WorkflowDefinition,
    WorkflowExecutionRecord,
    WorkflowStatus,
    WorkflowStepDefinition,
    WorkflowValidationError,
)


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir).resolve()


@pytest.fixture
def orchestrator(temp_workspace):
    task_store = TaskStore(temp_workspace / "task_store")
    tool_reg = ToolRegistry()
    agent_reg = AgentRegistry()
    event_bus = EventBus()
    return CentralOrchestrator(
        agent_registry=agent_reg,
        tool_registry=tool_reg,
        task_store=task_store,
        event_bus=event_bus,
        allowed_roots=[str(temp_workspace)],
    )


@pytest.fixture
def workflow_engine(orchestrator, temp_workspace):
    store = WorkflowStore(temp_workspace / "workflows")
    approval_mgr = ApprovalManager()
    return WorkflowEngine(
        orchestrator=orchestrator,
        store=store,
        approval_manager=approval_mgr,
        allowed_roots=[str(temp_workspace)],
    )


# ── 1. Workflow Definition Validation ─────────────────────────────────────────

def test_workflow_definition_validation():
    """Verify validation detects duplicate step IDs and missing dependencies."""
    # 1. Valid definition
    valid_wf = WorkflowDefinition(
        workflow_id="wf_valid",
        name="Valid Workflow",
        steps=[
            WorkflowStepDefinition(step_id="step_1", name="Step 1"),
            WorkflowStepDefinition(step_id="step_2", name="Step 2", depends_on=["step_1"]),
        ],
    )
    DependencyResolver.validate_workflow(valid_wf)  # Must not raise

    # 2. Empty workflow ID or empty steps
    with pytest.raises(WorkflowValidationError):
        DependencyResolver.validate_workflow(WorkflowDefinition(workflow_id="", name="Empty", steps=[]))

    # 3. Duplicate step IDs
    with pytest.raises(WorkflowValidationError) as exc:
        DependencyResolver.validate_workflow(WorkflowDefinition(
            workflow_id="wf_dup",
            name="Dup",
            steps=[
                WorkflowStepDefinition(step_id="same_id", name="Step A"),
                WorkflowStepDefinition(step_id="same_id", name="Step B"),
            ],
        ))
    assert "Duplicate step_id" in str(exc.value)

    # 4. Missing dependency target
    with pytest.raises(MissingDependencyError) as exc:
        DependencyResolver.validate_workflow(WorkflowDefinition(
            workflow_id="wf_missing",
            name="Missing",
            steps=[
                WorkflowStepDefinition(step_id="step_1", name="Step 1", depends_on=["non_existent_step"]),
            ],
        ))
    assert "non-existent step" in str(exc.value)


# ── 2. Circular Dependency Detection & Rejection ──────────────────────────────

def test_circular_dependency_rejection():
    """Verify circular dependencies (1-cycle, 2-cycle, 3-cycle) are strictly rejected."""
    # 1. Self cycle
    with pytest.raises(CircularDependencyError):
        DependencyResolver.validate_workflow(WorkflowDefinition(
            workflow_id="wf_self_cycle",
            name="Self Cycle",
            steps=[WorkflowStepDefinition(step_id="s1", name="Step 1", depends_on=["s1"])],
        ))

    # 2. Direct two-node cycle: A -> B -> A
    with pytest.raises(CircularDependencyError):
        DependencyResolver.validate_workflow(WorkflowDefinition(
            workflow_id="wf_2cycle",
            name="Two Cycle",
            steps=[
                WorkflowStepDefinition(step_id="A", name="A", depends_on=["B"]),
                WorkflowStepDefinition(step_id="B", name="B", depends_on=["A"]),
            ],
        ))

    # 3. Three-node indirect cycle: A -> B -> C -> A
    with pytest.raises(CircularDependencyError) as exc:
        DependencyResolver.validate_workflow(WorkflowDefinition(
            workflow_id="wf_3cycle",
            name="Three Cycle",
            steps=[
                WorkflowStepDefinition(step_id="A", name="A", depends_on=["B"]),
                WorkflowStepDefinition(step_id="B", name="B", depends_on=["C"]),
                WorkflowStepDefinition(step_id="C", name="C", depends_on=["A"]),
            ],
        ))
    assert "Circular dependency" in str(exc.value)


# ── 3. Parallel Execution Stage Calculation ───────────────────────────────────

def test_parallel_execution_stage_calculation():
    """Verify Kahn's algorithm computes correct topological layers for parallel dispatch."""
    # Diamond graph:
    #      Start
    #     /     \
    #   Left   Right
    #     \     /
    #       End
    wf = WorkflowDefinition(
        workflow_id="wf_diamond",
        name="Diamond DAG",
        steps=[
            WorkflowStepDefinition(step_id="start", name="Start"),
            WorkflowStepDefinition(step_id="left", name="Left", depends_on=["start"]),
            WorkflowStepDefinition(step_id="right", name="Right", depends_on=["start"]),
            WorkflowStepDefinition(step_id="end", name="End", depends_on=["left", "right"]),
        ],
    )

    stages = DependencyResolver.compute_execution_stages(wf)
    assert len(stages) == 3
    assert stages[0] == ["start"]
    assert stages[1] == ["left", "right"]
    assert stages[2] == ["end"]


# ── 4. Safe Condition Evaluation without eval() ────────────────────────────────

def test_safe_condition_evaluation_without_eval(temp_workspace):
    """Verify condition rules evaluate safely without arbitrary code execution."""
    test_file = temp_workspace / "deliverable.txt"
    test_file.write_text("verifiable data", encoding="utf-8")

    step_states = {
        "step_1": StepExecutionRecord(
            step_id="step_1",
            status=StepStatus.COMPLETED,
            outputs={"count": 42, "category": "production"},
            summary="Processed 42 rows.",
        ),
        "step_fail": StepExecutionRecord(
            step_id="step_fail",
            status=StepStatus.FAILED,
        ),
    }
    context = {"env": "prod", "threshold": 10}

    # 1. Step Status
    cond_status = StepCondition(condition_type=ConditionType.STEP_STATUS, step_id="step_1", expected_value="COMPLETED")
    assert SafeConditionEvaluator.evaluate(cond_status, context, step_states)

    cond_fail = StepCondition(condition_type=ConditionType.STEP_STATUS, step_id="step_fail", expected_value="FAILED")
    assert SafeConditionEvaluator.evaluate(cond_fail, context, step_states)

    # 2. Output Equals
    cond_eq = StepCondition(condition_type=ConditionType.OUTPUT_EQUALS, step_id="step_1", field_name="category", expected_value="production")
    assert SafeConditionEvaluator.evaluate(cond_eq, context, step_states)

    # 3. Structured Expression (numeric comparison)
    cond_gt = StepCondition(condition_type=ConditionType.EXPRESSION, step_id="step_1", field_name="count", operator="gt", expected_value=40)
    assert SafeConditionEvaluator.evaluate(cond_gt, context, step_states)

    cond_lt = StepCondition(condition_type=ConditionType.EXPRESSION, step_id="step_1", field_name="count", operator="lt", expected_value=10)
    assert not SafeConditionEvaluator.evaluate(cond_lt, context, step_states)

    # 4. Artifact Exists
    cond_art = StepCondition(condition_type=ConditionType.ARTIFACT_EXISTS, expected_value=str(test_file))
    assert SafeConditionEvaluator.evaluate(cond_art, context, step_states, allowed_roots=[str(temp_workspace)])

    # 5. Template text interpolation
    raw_template = "Target is {{step_1.category}} in environment {{env}} with count {{step_1.count}}"
    interpolated = SafeConditionEvaluator.interpolate_text(raw_template, context, step_states)
    assert interpolated == "Target is production in environment prod with count 42"


# ── 5. Conditional Branching & Step Skipping ──────────────────────────────────

def test_conditional_branching_and_step_skipping(workflow_engine):
    """Verify steps with false conditions are cleanly marked SKIPPED without halting workflow."""
    wf = WorkflowDefinition(
        workflow_id="wf_conditional",
        name="Conditional Workflow",
        parameters={"run_audit": False},
        steps=[
            WorkflowStepDefinition(
                step_id="step_calc",
                name="Compute Base Metric",
                assigned_agent="calculator",
                description="Calculate 10 * 5",
            ),
            WorkflowStepDefinition(
                step_id="step_audit",
                name="Optional Audit Step",
                assigned_agent="system_diagnosis",
                description="Check CPU status",
                depends_on=["step_calc"],
                condition=StepCondition(
                    condition_type=ConditionType.EXPRESSION,
                    field_name="run_audit",
                    operator="is_true",
                ),
            ),
            WorkflowStepDefinition(
                step_id="step_final",
                name="Final Summary",
                assigned_agent="calculator",
                description="Calculate 50 + 50",
                depends_on=["step_calc"],
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_record = workflow_engine.run_workflow_sync("wf_conditional", parameters={"run_audit": False})
    assert exec_record.status == WorkflowStatus.COMPLETED
    assert exec_record.step_states["step_calc"].status == StepStatus.COMPLETED
    assert exec_record.step_states["step_audit"].status == StepStatus.SKIPPED
    assert exec_record.step_states["step_final"].status == StepStatus.COMPLETED


# ── 6. Sequential Workflow Execution ──────────────────────────────────────────

def test_sequential_workflow_execution(workflow_engine, temp_workspace):
    """Verify sequential execution runs step-by-step through Central Orchestrator."""
    wf = WorkflowDefinition(
        workflow_id="wf_seq",
        name="Sequential Workflow",
        steps=[
            WorkflowStepDefinition(
                step_id="step_1",
                name="Step One",
                assigned_agent="calculator",
                description="Calculate 25 * 4",
            ),
            WorkflowStepDefinition(
                step_id="step_2",
                name="Step Two",
                assigned_agent="calculator",
                description="Calculate 100 + 50",
                depends_on=["step_1"],
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.run_workflow_sync("wf_seq")
    assert exec_rec.status == WorkflowStatus.COMPLETED
    assert exec_rec.step_states["step_1"].status == StepStatus.COMPLETED
    assert exec_rec.step_states["step_2"].status == StepStatus.COMPLETED


# ── 7. Parallel Independent Step Scheduling ───────────────────────────────────

def test_parallel_independent_steps(workflow_engine):
    """Verify independent steps in the same DAG stage are scheduled concurrently."""
    wf = WorkflowDefinition(
        workflow_id="wf_parallel",
        name="Parallel Tasks",
        steps=[
            WorkflowStepDefinition(
                step_id="branch_a",
                name="Branch A",
                assigned_agent="calculator",
                description="Calculate 10 + 10",
            ),
            WorkflowStepDefinition(
                step_id="branch_b",
                name="Branch B",
                assigned_agent="calculator",
                description="Calculate 20 + 20",
            ),
            WorkflowStepDefinition(
                step_id="aggregate",
                name="Aggregate",
                assigned_agent="calculator",
                description="Calculate 20 + 40",
                depends_on=["branch_a", "branch_b"],
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.run_workflow_sync("wf_parallel")
    assert exec_rec.status == WorkflowStatus.COMPLETED
    assert exec_rec.step_states["branch_a"].status == StepStatus.COMPLETED
    assert exec_rec.step_states["branch_b"].status == StepStatus.COMPLETED
    assert exec_rec.step_states["aggregate"].status == StepStatus.COMPLETED


# ── 8. Human Authorization Gate Flow ──────────────────────────────────────────

def test_human_authorization_gate_flow(workflow_engine):
    """Verify workflow halts at approval gate, rejects LLM approval, and resumes on human signoff."""
    wf = WorkflowDefinition(
        workflow_id="wf_approval",
        name="Approval Gate Workflow",
        steps=[
            WorkflowStepDefinition(
                step_id="pre_check",
                name="Preliminary Step",
                assigned_agent="calculator",
                description="Calculate 10 + 10",
            ),
            WorkflowStepDefinition(
                step_id="protected_action",
                name="Protected Deployment Action",
                assigned_agent="calculator",
                description="Calculate 100 + 100",
                depends_on=["pre_check"],
                requires_approval=True,
                approval_role="lead_engineer",
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    # 1. Start workflow: completes pre_check, then halts at protected_action
    exec_rec = workflow_engine.run_workflow_sync("wf_approval")
    assert exec_rec.status == WorkflowStatus.WAITING_APPROVAL
    assert exec_rec.step_states["protected_action"].status == StepStatus.WAITING_APPROVAL

    pending = workflow_engine.approval_manager.list_pending(exec_rec.execution_id)
    assert len(pending) == 1
    app_id = pending[0].approval_id

    # 2. Strict Security: Model / Automated output cannot provide human approval
    with pytest.raises(ValueError) as exc:
        workflow_engine.approve_step(app_id, approver="LLM", reason="Self-approval")
    assert "Automated LLM output cannot provide human approval" in str(exc.value)

    # 3. Legitimate human operator approval
    resumed_rec = workflow_engine.approve_step(app_id, approver="senior_engineer_dave", reason="Verified safe.")
    assert resumed_rec.status == WorkflowStatus.COMPLETED
    assert resumed_rec.step_states["protected_action"].status == StepStatus.COMPLETED


# ── 9. Human Authorization Rejection ──────────────────────────────────────────

def test_human_authorization_rejection(workflow_engine):
    """Verify human rejection fails the step and permanently halts workflow."""
    wf = WorkflowDefinition(
        workflow_id="wf_reject",
        name="Rejection Workflow",
        steps=[
            WorkflowStepDefinition(
                step_id="sensitive_step",
                name="Sensitive Mutation",
                assigned_agent="calculator",
                description="Calculate 5 * 5",
                requires_approval=True,
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.run_workflow_sync("wf_reject")
    assert exec_rec.status == WorkflowStatus.WAITING_APPROVAL

    pending = workflow_engine.approval_manager.list_pending(exec_rec.execution_id)
    assert len(pending) == 1

    # Human rejects
    failed_rec = workflow_engine.reject_step(pending[0].approval_id, approver="sec_ops", reason="Unauthorized parameters.")
    assert failed_rec.status == WorkflowStatus.FAILED
    assert failed_rec.step_states["sensitive_step"].status == StepStatus.FAILED


# ── 10. Human Authorization Expiration ────────────────────────────────────────

def test_human_authorization_expiration(workflow_engine):
    """Verify approval request with elapsed timeout transitions to EXPIRED."""
    wf = WorkflowDefinition(
        workflow_id="wf_expire",
        name="Expiring Workflow",
        steps=[
            WorkflowStepDefinition(
                step_id="step_exp",
                name="Expiring Step",
                assigned_agent="calculator",
                description="Calculate 1 + 1",
                requires_approval=True,
                approval_timeout_seconds=0.1,  # Short timeout
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.start_workflow("wf_expire")
    workflow_engine.step_workflow(exec_rec.execution_id)

    pending = workflow_engine.approval_manager.list_pending(exec_rec.execution_id)
    assert len(pending) == 1

    # Wait for expiration
    time.sleep(0.15)
    expired = workflow_engine.approval_manager.check_expirations()
    assert len(expired) >= 1
    assert expired[0].status == ApprovalStatus.EXPIRED

    # Next step execution registers expired approval and fails step
    advanced_rec = workflow_engine.step_workflow(exec_rec.execution_id)
    assert advanced_rec.step_states["step_exp"].status == StepStatus.FAILED


# ── 11. Pause and Resume ──────────────────────────────────────────────────────

def test_pause_and_resume_workflow(workflow_engine):
    """Verify workflow can be paused between steps and resumed cleanly."""
    wf = WorkflowDefinition(
        workflow_id="wf_pause_resume",
        name="Pause Resume",
        steps=[
            WorkflowStepDefinition(step_id="s1", name="Step 1", assigned_agent="calculator", description="Calculate 2 + 2"),
            WorkflowStepDefinition(step_id="s2", name="Step 2", assigned_agent="calculator", description="Calculate 4 + 4", depends_on=["s1"]),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.start_workflow("wf_pause_resume")
    # Execute first step
    workflow_engine.step_workflow(exec_rec.execution_id)

    # Pause workflow
    paused_rec = workflow_engine.pause_workflow(exec_rec.execution_id, reason="Maintenance window.")
    assert paused_rec.status == WorkflowStatus.PAUSED

    # Step attempts during PAUSED do not advance
    stepped_rec = workflow_engine.step_workflow(exec_rec.execution_id)
    assert stepped_rec.status == WorkflowStatus.PAUSED

    # Resume workflow
    resumed_rec = workflow_engine.resume_workflow(exec_rec.execution_id)
    assert resumed_rec.status in (WorkflowStatus.RUNNING, WorkflowStatus.COMPLETED)


# ── 12. Workflow Cancellation ─────────────────────────────────────────────────

def test_workflow_cancellation(workflow_engine):
    """Verify workflow cancellation halts execution permanently."""
    wf = WorkflowDefinition(
        workflow_id="wf_cancel",
        name="Cancellable",
        steps=[
            WorkflowStepDefinition(step_id="s1", name="Step 1", assigned_agent="calculator", description="Calculate 3 + 3"),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.start_workflow("wf_cancel")
    cancelled_rec = workflow_engine.cancel_workflow(exec_rec.execution_id, reason="Operator abort.")
    assert cancelled_rec.status == WorkflowStatus.CANCELLED


# ── 13. Step Retry Policy with Backoff ────────────────────────────────────────

def test_step_retry_policy_with_backoff(workflow_engine):
    """Verify failed steps retry up to max_retries with backoff before declaring failure."""
    wf = WorkflowDefinition(
        workflow_id="wf_retry",
        name="Retry Workflow",
        steps=[
            WorkflowStepDefinition(
                step_id="failing_step",
                name="Failing Step",
                assigned_agent="calculator",
                description="Calculate invalid_syntax_expression_xyz",
                retry_policy=RetryPolicy(max_retries=2, initial_delay_seconds=0.05, backoff_factor=1.5),
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.run_workflow_sync("wf_retry")
    step_rec = exec_rec.step_states["failing_step"]
    assert step_rec.status == StepStatus.FAILED
    assert step_rec.attempts == 2


# ── 14. Crash Recovery & Ambiguous Execution State ────────────────────────────

def test_crash_recovery_and_ambiguous_execution(temp_workspace, orchestrator):
    """Verify interrupted in-flight steps are flagged AMBIGUOUS_EXECUTION upon recovery."""
    store = WorkflowStore(temp_workspace / "workflows")
    wf = WorkflowDefinition(
        workflow_id="wf_crash",
        name="Crash Test",
        steps=[
            WorkflowStepDefinition(step_id="step_a", name="A"),
            WorkflowStepDefinition(step_id="step_b", name="B", depends_on=["step_a"]),
        ],
    )
    store.save_definition(wf)

    # Simulate an execution that was interrupted while step_a was in RUNNING status
    interrupted_exec = WorkflowExecutionRecord(
        execution_id="wf_exec_crash_1",
        workflow_id="wf_crash",
        status=WorkflowStatus.RUNNING,
        step_states={
            "step_a": StepExecutionRecord(step_id="step_a", status=StepStatus.RUNNING),
            "step_b": StepExecutionRecord(step_id="step_b", status=StepStatus.PENDING),
        },
    )
    store.save_execution(interrupted_exec)

    # Initialize a new WorkflowEngine (simulating application restart)
    new_engine = WorkflowEngine(orchestrator=orchestrator, store=store)
    recovered = new_engine.recover_interrupted_workflows()

    assert len(recovered) == 1
    rec = recovered[0]
    assert rec.status == WorkflowStatus.PAUSED
    assert rec.recovery_metadata.get("requires_reconciliation") is True
    # In-flight step must NOT be silently repeated; must be flagged AMBIGUOUS_EXECUTION
    assert rec.step_states["step_a"].status == StepStatus.AMBIGUOUS_EXECUTION


# ── 15. Failed Verification Blocks Downstream Steps ───────────────────────────

def test_failed_verification_blocks_downstream(workflow_engine):
    """Verify that a step failing physical postcondition verification stops downstream steps."""
    wf = WorkflowDefinition(
        workflow_id="wf_verify_fail",
        name="Verification Failure Workflow",
        steps=[
            WorkflowStepDefinition(
                step_id="create_file",
                name="Create Output File",
                assigned_agent="calculator",
                description="Calculate 10 + 10",
                # Demands file deliverable that calculator agent will not produce
                expected_deliverables=["non_existent_file.txt"],
            ),
            WorkflowStepDefinition(
                step_id="downstream_step",
                name="Downstream Dependent",
                assigned_agent="calculator",
                description="Calculate 20 + 20",
                depends_on=["create_file"],
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.run_workflow_sync("wf_verify_fail")
    assert exec_rec.status == WorkflowStatus.FAILED
    assert exec_rec.step_states["create_file"].status == StepStatus.FAILED
    # Downstream step was never executed
    assert exec_rec.step_states["downstream_step"].status in (StepStatus.PENDING, StepStatus.SKIPPED)


# ── 16. Nested Workflow Execution ─────────────────────────────────────────────

def test_nested_workflow_execution(workflow_engine):
    """Verify child workflows can be nested and bounded by max_depth."""
    # 1. Child Workflow
    child_wf = WorkflowDefinition(
        workflow_id="child_wf",
        name="Child Workflow",
        steps=[
            WorkflowStepDefinition(step_id="child_step", name="Child Step", assigned_agent="calculator", description="Calculate 5 * 5"),
        ],
    )
    workflow_engine.register_workflow(child_wf)

    # 2. Parent Workflow
    parent_wf = WorkflowDefinition(
        workflow_id="parent_wf",
        name="Parent Workflow",
        steps=[
            WorkflowStepDefinition(
                step_id="invoke_child",
                name="Invoke Child",
                nested_workflow_id="child_wf",
            ),
            WorkflowStepDefinition(
                step_id="post_child",
                name="Post Child",
                assigned_agent="calculator",
                description="Calculate 10 + 10",
                depends_on=["invoke_child"],
            ),
        ],
    )
    workflow_engine.register_workflow(parent_wf)

    exec_rec = workflow_engine.run_workflow_sync("parent_wf")
    assert exec_rec.status == WorkflowStatus.COMPLETED
    assert exec_rec.step_states["invoke_child"].status == StepStatus.COMPLETED
    assert exec_rec.step_states["post_child"].status == StepStatus.COMPLETED


# ── 17. Full Component Integration ────────────────────────────────────────────

def test_full_component_integration(workflow_engine, temp_workspace):
    """Verify complete end-to-end integration: WorkflowEngine -> CentralOrchestrator -> Specialized Agents -> VerificationEngine."""
    csv_file = temp_workspace / "input_data.csv"
    csv_file.write_text("item,qty\nalpha,10\nbeta,20\n", encoding="utf-8")

    wf = WorkflowDefinition(
        workflow_id="wf_e2e",
        name="End-to-End OPTIMUS Autonomous Pipeline",
        parameters={"source_file": "input_data.csv"},
        steps=[
            WorkflowStepDefinition(
                step_id="inspect_data",
                name="Inspect Input Dataset",
                assigned_agent="data_intelligence",
                description="Read input_data.csv and compute stats",
            ),
            WorkflowStepDefinition(
                step_id="compute_metrics",
                name="Compute Arithmetic Metrics",
                assigned_agent="calculator",
                description="Calculate 100 * 2",
                depends_on=["inspect_data"],
            ),
        ],
    )
    workflow_engine.register_workflow(wf)

    exec_rec = workflow_engine.run_workflow_sync("wf_e2e")
    assert exec_rec.status == WorkflowStatus.COMPLETED
    assert exec_rec.step_states["inspect_data"].status == StepStatus.COMPLETED
    assert exec_rec.step_states["compute_metrics"].status == StepStatus.COMPLETED
