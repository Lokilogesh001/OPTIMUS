"""tests/test_core_orchestrator.py — Comprehensive Test Suite for OPTIMUS Core Orchestrator.

Verifies:
1. EventBus typed publishing, async/sync dispatch, subscriber error isolation.
2. Capability-based access control (CBAC), safe arithmetic evaluator, sandboxed filesystem tools.
3. Path traversal security (preventing escaping allowed_roots).
4. AgentRegistry registration, markdown profile discovery, scoped tool exposure.
5. TaskStore atomic persistence, retrieval, status lifecycle, cascading cancellation.
6. CentralOrchestrator hierarchical planning, subtask execution, independent verification.
7. Anti-peer-to-peer delegation rule enforcement.
8. Error failsafes preventing orphaned or hanging tasks.
9. Desktop UI & voice bridge integration.
10. Action loader compatibility (optimus_orchestrator and background_tasks).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# Ensure Mark-LVII-main is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.orchestrator import desktop
from core.orchestrator.agents import (
    AgentRegistry,
)
from core.orchestrator.events import (
    EventBus,
    PlanCreatedEvent,
    SubtaskCompletedEvent,
    SubtaskDelegatedEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
)
from core.orchestrator.orchestrator import (
    CentralOrchestrator,
)
from core.orchestrator.runtime import (
    Subtask,
    SubtaskStatus,
    TaskRecord,
    TaskStatus,
    TaskStore,
    new_subtask_id,
    new_task_id,
)
from core.orchestrator.tools import (
    CapabilityTier,
    ToolRegistry,
    safe_eval_expression,
)

# ============================================================================
# 1. EventBus Tests
# ============================================================================

def test_event_bus_sync_and_async_dispatch():
    bus = EventBus()
    received = []

    def sync_listener(event: TaskCreatedEvent):
        received.append(("sync", event.goal))

    async def async_listener(event: TaskCreatedEvent):
        received.append(("async", event.goal))

    bus.subscribe(TaskCreatedEvent, sync_listener)
    bus.subscribe(TaskCreatedEvent, async_listener)

    bus.publish(TaskCreatedEvent(task_id="t-1", goal="Test the event bus"))

    assert len(received) == 2
    assert ("sync", "Test the event bus") in received
    assert ("async", "Test the event bus") in received


def test_event_bus_error_isolation():
    bus = EventBus()
    received = []

    def failing_listener(event: TaskCreatedEvent):
        raise RuntimeError("Handler crash intentional")

    def good_listener(event: TaskCreatedEvent):
        received.append(event.task_id)

    bus.subscribe(TaskCreatedEvent, failing_listener)
    bus.subscribe(TaskCreatedEvent, good_listener)

    # Publishing should not raise even if one handler crashes
    bus.publish(TaskCreatedEvent(task_id="t-2", goal="Resilience test"))

    assert received == ["t-2"]


# ============================================================================
# 2. ToolRegistry & CBAC Security Tests
# ============================================================================

def test_safe_eval_expression():
    assert safe_eval_expression("2 + 2") == 4
    assert safe_eval_expression("(10 * 5) / 2") == 25.0
    assert safe_eval_expression("2 ** 8") == 256

    with pytest.raises(ValueError, match="Unsupported syntax or operation"):
        safe_eval_expression("__import__('os').system('dir')")

    with pytest.raises(ValueError):
        safe_eval_expression("open('secret.txt')")


def test_tool_registry_sandboxed_filesystem(tmp_path):
    registry = ToolRegistry()

    # Allowed workspace
    allowed_dir = tmp_path / "workspace"
    allowed_dir.mkdir()
    test_file = allowed_dir / "output.txt"

    # Context authorizing workspace write
    ctx = {
        "agent": "filesystem",
        "tier": CapabilityTier.WORKSPACE_WRITE,
        "allowed_roots": [str(allowed_dir)],
    }

    # 1. Write file
    write_res = registry.execute(
        "write_file",
        {"path": str(test_file), "content": "Hello OPTIMUS"},
        permission_context=ctx,
    )
    assert write_res.status == "done"
    assert test_file.exists()
    assert test_file.read_text(encoding="utf-8") == "Hello OPTIMUS"
    assert "sha256" in write_res.evidence

    # 2. Read file
    read_res = registry.execute(
        "read_file",
        {"path": str(test_file)},
        permission_context=ctx,
    )
    assert read_res.status == "done"
    assert read_res.output == "Hello OPTIMUS"

    # 3. Path traversal attack rejection
    outside_file = tmp_path / "forbidden.txt"
    outside_res = registry.execute(
        "write_file",
        {"path": str(outside_file), "content": "Exploit attempt"},
        permission_context=ctx,
    )
    assert outside_res.status == "failed"
    assert "outside permitted directories" in outside_res.error
    assert not outside_file.exists()


def test_tool_registry_capability_tier_denial(tmp_path):
    registry = ToolRegistry()
    allowed_dir = tmp_path / "workspace"
    allowed_dir.mkdir()

    # Context has only READ_ONLY tier
    read_only_ctx = {
        "agent": "researcher",
        "tier": CapabilityTier.READ_ONLY,
        "allowed_roots": [str(allowed_dir)],
    }

    # Attempting to invoke write_file (WORKSPACE_WRITE) with READ_ONLY credentials
    res = registry.execute(
        "write_file",
        {"path": str(allowed_dir / "blocked.txt"), "content": "blocked"},
        permission_context=read_only_ctx,
    )
    assert res.status == "failed"
    assert "requires capability tier" in res.error


# ============================================================================
# 3. AgentRegistry Tests
# ============================================================================

def test_agent_registry_markdown_discovery(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    md_content = """# Data Analyst Agent
description: Specialized agent for computing metrics and processing tabular data.
minutes: 15
tools: calculate, read_file
tier: WORKSPACE_WRITE
system: You are a data analyst.
"""
    (agents_dir / "analyst.md").write_text(md_content, encoding="utf-8")

    registry = AgentRegistry()
    registry.discover_directory(agents_dir)

    agent = registry.get("analyst")
    assert agent is not None
    assert agent.name == "analyst"
    assert "calculate" in agent.allowed_tools
    assert "read_file" in agent.allowed_tools
    assert agent.capability_tier == CapabilityTier.WORKSPACE_WRITE


def test_agent_scoped_tool_execution(tmp_path):
    tool_reg = ToolRegistry()
    agent_reg = AgentRegistry()

    # Researcher only has read_file and calculate
    scoped_tools = agent_reg.get_scoped_tools("researcher", tool_reg)
    assert "read_file" in scoped_tools
    assert "calculate" in scoped_tools
    assert "write_file" not in scoped_tools  # write_file excluded because unauthorized for researcher


# ============================================================================
# 4. TaskStore & Runtime Persistence Tests
# ============================================================================

def test_task_store_atomic_persistence_and_retrieval(tmp_path):
    storage_dir = tmp_path / "orchestrator_storage"
    store = TaskStore(storage_dir)

    task = TaskRecord(
        task_id=new_task_id(),
        goal="Compile system status report",
        status=TaskStatus.RUNNING,
    )
    subtask = Subtask(
        subtask_id=new_subtask_id(),
        parent_task_id=task.task_id,
        index=1,
        title="Collect disk metrics",
        assigned_agent="filesystem",
        status=SubtaskStatus.COMPLETED,
        result="Disk healthy",
    )
    task.subtasks.append(subtask)

    store.save(task)

    # Retrieve from separate instance reading the same directory
    store2 = TaskStore(storage_dir)
    loaded = store2.get(task.task_id)

    assert loaded is not None
    assert loaded.task_id == task.task_id
    assert loaded.goal == "Compile system status report"
    assert loaded.status == TaskStatus.RUNNING
    assert len(loaded.subtasks) == 1
    assert loaded.subtasks[0].title == "Collect disk metrics"
    assert loaded.subtasks[0].status == SubtaskStatus.COMPLETED


def test_task_store_cascading_cancellation(tmp_path):
    storage_dir = tmp_path / "storage_cancel"
    store = TaskStore(storage_dir)

    task = TaskRecord(
        task_id=new_task_id(),
        goal="Long multi-stage workflow",
        status=TaskStatus.RUNNING,
    )
    st1 = Subtask(
        subtask_id=new_subtask_id(),
        parent_task_id=task.task_id,
        index=1,
        title="Step 1",
        status=SubtaskStatus.COMPLETED,
    )
    st2 = Subtask(
        subtask_id=new_subtask_id(),
        parent_task_id=task.task_id,
        index=2,
        title="Step 2",
        status=SubtaskStatus.RUNNING,
    )
    st3 = Subtask(
        subtask_id=new_subtask_id(),
        parent_task_id=task.task_id,
        index=3,
        title="Step 3",
        status=SubtaskStatus.PENDING,
    )
    task.subtasks.extend([st1, st2, st3])
    store.save(task)

    # Cancel task
    cancelled = store.cancel_task(task.task_id, reason="User clicked stop")
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.error == "User clicked stop"

    # Completed step remains completed
    assert cancelled.subtasks[0].status == SubtaskStatus.COMPLETED
    # Active and pending steps are cancelled
    assert cancelled.subtasks[1].status == SubtaskStatus.CANCELLED
    assert cancelled.subtasks[2].status == SubtaskStatus.CANCELLED


# ============================================================================
# 5. CentralOrchestrator Execution & Independent Verification Tests
# ============================================================================

def test_central_orchestrator_deterministic_run(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = tmp_path / "storage"
    storage.mkdir()

    event_bus = EventBus()
    events_log = []
    event_bus.subscribe(TaskCreatedEvent, lambda e: events_log.append("created"))
    event_bus.subscribe(PlanCreatedEvent, lambda e: events_log.append("planned"))
    event_bus.subscribe(SubtaskDelegatedEvent, lambda e: events_log.append(f"delegated_{e.assigned_agent}"))
    event_bus.subscribe(SubtaskCompletedEvent, lambda e: events_log.append("subtask_done"))
    event_bus.subscribe(TaskCompletedEvent, lambda e: events_log.append("completed"))

    orchestrator = CentralOrchestrator(
        task_store=TaskStore(storage),
        event_bus=event_bus,
        allowed_roots=[str(workspace)],
    )

    # Run task: Calculate 15 * 40 and write result
    goal = "Calculate 15 * 40 and save summary to analysis.txt"
    record = orchestrator.run_task(goal)

    assert record.status == TaskStatus.COMPLETED
    assert len(record.subtasks) >= 1
    assert "created" in events_log
    assert "planned" in events_log
    assert "completed" in events_log

    # Check that task store has record
    persisted = orchestrator.task_store.get(record.task_id)
    assert persisted is not None
    assert persisted.status == TaskStatus.COMPLETED


def test_independent_physical_verification(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = tmp_path / "storage"

    orchestrator = CentralOrchestrator(
        task_store=TaskStore(storage),
        allowed_roots=[str(workspace)],
    )

    # 1. Subtask claims a deliverable that does exist on disk
    target_file = workspace / "report.md"
    target_file.write_text("Confirmed content on disk", encoding="utf-8")

    st_pass = Subtask(
        subtask_id=new_subtask_id(),
        parent_task_id="t-v",
        index=1,
        title="Generate report",
        assigned_agent="filesystem",
        expected_deliverables=[str(target_file)],
        result="Generated successfully",
    )

    ok, verdict, *_ = orchestrator._verify_deliverables(st_pass, [str(target_file)])
    assert ok is True
    assert "SHA-256" in verdict
    assert "byte(s)" in verdict

    # 2. Subtask claims a deliverable that does NOT exist on disk
    phantom_file = workspace / "phantom.md"
    st_fail = Subtask(
        subtask_id=new_subtask_id(),
        parent_task_id="t-v",
        index=2,
        title="Generate phantom",
        assigned_agent="filesystem",
        expected_deliverables=[str(phantom_file)],
        result="Claimed success",
    )

    ok_fail, verdict_fail, *_ = orchestrator._verify_deliverables(st_fail, [str(phantom_file)])
    assert ok_fail is False
    assert "Missing deliverable file" in verdict_fail


def test_anti_peer_to_peer_delegation_constraint(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    orchestrator = CentralOrchestrator(
        task_store=TaskStore(tmp_path / "storage"),
        allowed_roots=[str(workspace)],
    )

    # Sibling delegation attempt: worker agent attempts to call delegate_subtask directly
    _ = Subtask(
        subtask_id=new_subtask_id(),
        parent_task_id="t-peer",
        index=1,
        title="Unauthorized delegation",
        assigned_agent="researcher",
    )

    # When researcher tries to delegate to calculator without orchestrator
    scoped_tools = orchestrator.agent_registry.get_scoped_tools("researcher", orchestrator.tool_registry)
    assert "delegate_subtask" not in scoped_tools
    assert "orchestrate" not in scoped_tools

    # Invoking a delegation tool via tool registry is blocked
    res = orchestrator.tool_registry.execute(
        "delegate_subtask",
        {"target_agent": "calculator"},
        permission_context={"agent": "researcher", "tier": CapabilityTier.READ_ONLY},
    )
    assert res.status == "failed"
    assert "not registered" in res.error or "denied" in res.error


def test_orchestrator_background_worker(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = tmp_path / "storage"

    orchestrator = CentralOrchestrator(
        task_store=TaskStore(storage),
        allowed_roots=[str(workspace)],
    )

    # Start non-blocking task
    record = orchestrator.start("Evaluate 128 * 2")
    assert record.task_id is not None

    # Wait briefly for worker thread to complete
    for _ in range(30):
        time.sleep(0.1)
        updated = orchestrator.task_store.get(record.task_id)
        if updated and updated.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            break

    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED


# ============================================================================
# 6. Desktop Integration and Action Loader Tests
# ============================================================================

class MockUIPlayer:
    def __init__(self):
        self.logs = []
        self.contents = []
        self.speeches = []

    def write_log(self, text: str):
        self.logs.append(text)

    def show_content(self, title: str, body: str):
        self.contents.append((title, body))

    def request_say(self, text: str):
        self.speeches.append(text)


def test_desktop_bridge_integration(tmp_path):
    mock_player = MockUIPlayer()
    orch = desktop.ensure(mock_player)
    assert orch is not None

    # Run a quick task through the singleton
    record = orch.run_task("Compute 100 * 5")
    assert record.status == TaskStatus.COMPLETED

    # Verify that mock_player received event notifications
    assert any("OPTIMUS: Task initialized" in log for log in mock_player.logs)
    assert any("OPTIMUS: Task COMPLETED" in log for log in mock_player.logs)
    assert len(mock_player.contents) >= 1
    assert len(mock_player.speeches) >= 1


def test_actions_optimus_orchestrator():
    from actions.optimus_orchestrator import TOOL, optimus_orchestrator

    assert TOOL["name"] == "optimus_orchestrator"
    assert "plan" in TOOL["parameters"]["properties"]["action"]["enum"]
    assert "start" in TOOL["parameters"]["properties"]["action"]["enum"]

    mock_player = MockUIPlayer()

    # 1. Plan action
    plan_out = optimus_orchestrator({"action": "plan", "goal": "Analyze dataset metrics"}, player=mock_player)
    assert "status=done" in plan_out
    assert "subtask_count=" in plan_out

    # 2. Agents list action
    agents_out = optimus_orchestrator({"action": "agents"}, player=mock_player)
    assert "status=done" in agents_out
    assert "researcher" in agents_out or "general" in agents_out

    # 3. Tools list action
    tools_out = optimus_orchestrator({"action": "tools"}, player=mock_player)
    assert "status=done" in tools_out
    assert "calculate" in tools_out

    # 4. Execute sync action
    exec_out = optimus_orchestrator({"action": "execute_sync", "goal": "Calculate 50 * 2"}, player=mock_player)
    assert "status=done" in exec_out
    assert "status=completed" in exec_out.lower()


def test_actions_background_tasks_optimus_routing():
    from actions.background_tasks import TOOL, background_tasks

    assert "orchestrate" in TOOL["parameters"]["properties"]["action"]["enum"]
    assert "optimus" in TOOL["parameters"]["properties"]["agent"]["enum"]

    mock_player = MockUIPlayer()
    out = background_tasks({"action": "orchestrate", "task": "Compute 77 + 33"}, player=mock_player)
    assert "status=started" in out
    assert "task_id=" in out
    assert "agent=optimus-orchestrator" in out
