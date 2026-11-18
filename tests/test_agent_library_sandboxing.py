"""tests/test_agent_library_sandboxing.py — Comprehensive Test Suite for OPTIMUS Component 4.

Tests:
1. Agent registration & discovery across all 5 specialized agents.
2. Dynamic agent revocation and unregistration.
3. Dynamic tool registration and revocation.
4. Capability tier enforcement (CBAC).
5. Cross-agent permission isolation (anti-peer-to-peer / tool scoping).
6. Filesystem boundary confinement (workspace traversal prevention).
7. Prohibited command restrictions (blocking destructive / dangerous patterns).
8. Execution timeouts and active process termination.
9. Parameter schema validation & required argument checks.
10. Tool output sanitization & credential redaction.
11. Tool execution audit events (ToolExecutedEvent dispatched to EventBus).
12. Code Intelligence Agent execution (AST parsing, complexity metrics).
13. System Diagnosis Agent execution (hardware, CPU, RAM, disk diagnostics).
14. Data Intelligence Agent execution (CSV read, statistics, transformation).
15. Research Agent execution (web search, citation extraction, report creation).
16. Execution Agent execution (sandboxed command execution and process status).
17. Central Orchestrator integration with physical postcondition verification.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.agents.code_intel import CodeIntelligenceAgent
from core.agents.contracts import AgentInputContract
from core.agents.data_intel import DataIntelligenceAgent
from core.agents.executor import ExecutionAgent
from core.agents.registry_ext import (
    get_specialized_agent,
    list_specialized_agents,
    register_specialized_agents,
)
from core.agents.researcher import ResearchAgent
from core.agents.sys_diag import SystemDiagnosisAgent
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.events import EventBus, ToolExecutedEvent
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import TaskStatus, TaskStore
from core.orchestrator.tools import (
    CapabilityTier,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    sanitize_tool_output,
)
from core.sandbox.command_guard import (
    check_command_safety,
    execute_sandboxed_command,
)


@pytest.fixture
def temp_workspace():
    """Create a temporary directory simulating an isolated workspace root."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir).resolve()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def tool_registry(event_bus):
    reg = ToolRegistry()
    reg.set_event_bus(event_bus)
    return reg


@pytest.fixture
def agent_registry():
    return AgentRegistry()


# ── 1. Agent Registration & Discovery ──────────────────────────────────────────

def test_agent_registration_and_discovery(agent_registry):
    """Verify all 5 specialized agents are automatically registered and discoverable."""
    expected_agents = {
        "code_intelligence",
        "system_diagnosis",
        "data_intelligence",
        "researcher",
        "execution_agent",
    }
    registered = {a.name for a in agent_registry.list_agents()}
    for exp in expected_agents:
        assert exp in registered, f"Agent '{exp}' should be registered in AgentRegistry"

    # Verify profile capabilities and properties
    code_prof = agent_registry.get("code_intelligence")
    assert code_prof is not None
    assert "code_ast_search" in code_prof.allowed_tools
    assert CapabilityTier.EXECUTION in code_prof.allowed_tiers

    sys_prof = agent_registry.get("system_diagnosis")
    assert sys_prof is not None
    assert "cpu_status" in sys_prof.allowed_tools
    assert CapabilityTier.READ_ONLY in sys_prof.allowed_tiers

    # Verify singleton / factory retrieval
    agent_obj = get_specialized_agent("code_intelligence")
    assert isinstance(agent_obj, CodeIntelligenceAgent)
    all_spec = list_specialized_agents()
    assert len(all_spec) >= 5


# ── 2. Dynamic Agent Revocation ───────────────────────────────────────────────

def test_dynamic_agent_revocation(agent_registry):
    """Verify dynamic agent unregistration and revocation at runtime."""
    assert agent_registry.is_registered("code_intelligence")
    agent_registry.unregister("code_intelligence")
    assert not agent_registry.is_registered("code_intelligence")
    assert agent_registry.get("code_intelligence") is None

    # Revoke another
    assert agent_registry.is_registered("system_diagnosis")
    agent_registry.revoke("system_diagnosis")
    assert not agent_registry.is_registered("system_diagnosis")

    # Re-registering works cleanly
    register_specialized_agents(agent_registry)
    assert agent_registry.is_registered("code_intelligence")
    assert agent_registry.is_registered("system_diagnosis")


# ── 3. Dynamic Tool Registration & Revocation ─────────────────────────────────

def test_dynamic_tool_registration_and_revocation(tool_registry):
    """Verify tools can be dynamically registered, revoked, and removed at runtime."""
    assert tool_registry.is_registered("calculator")
    res1 = tool_registry.execute("calculator", {"expression": "10 + 20"})
    assert res1.is_success
    assert res1.output == "30"

    # Revoke tool
    tool_registry.revoke("calculator")
    assert not tool_registry.is_registered("calculator")

    # Invocation of revoked tool must fail with UNKNOWN_TOOL error
    res2 = tool_registry.execute("calculator", {"expression": "10 + 20"})
    assert not res2.is_success
    assert "UNKNOWN_TOOL" in (res2.error or "")

    # Re-register dynamic custom tool
    def custom_echo(params, context=None):
        return ToolResult(status="done", output=f"Echo: {params.get('msg')}")

    custom_tool = ToolDefinition(
        name="custom_echo",
        description="Dynamic test tool",
        parameters={"type": "OBJECT", "properties": {"msg": {"type": "STRING"}}, "required": ["msg"]},
        capability_tier=CapabilityTier.READ_ONLY,
        handler=custom_echo,
    )
    tool_registry.register(custom_tool)
    assert tool_registry.is_registered("custom_echo")
    res3 = tool_registry.execute("custom_echo", {"msg": "hello"})
    assert res3.is_success
    assert res3.output == "Echo: hello"


# ── 4. Capability Tier Enforcement (CBAC) ─────────────────────────────────────

def test_capability_tier_enforcement(tool_registry):
    """Verify agents cannot invoke tools above their allowed capability tier."""
    # System diagnosis agent only has READ_ONLY tier
    sys_agent = "system_diagnosis"
    read_tiers = [CapabilityTier.READ_ONLY]

    # Attempting to call write_file (WORKSPACE_WRITE) with READ_ONLY tier must be rejected
    res = tool_registry.execute(
        tool_name="write_file",
        arguments={"path": "test.txt", "content": "data"},
        caller_agent=sys_agent,
        allowed_tools=["write_file", "system_info"],
        allowed_tiers=read_tiers,
    )
    assert not res.is_success
    assert "TIER_VIOLATION" in (res.error or "")


# ── 5. Cross-Agent Permission Isolation ───────────────────────────────────────

def test_cross_agent_permission_isolation(tool_registry):
    """Verify an agent cannot invoke a tool outside its declared allowed_tools scope."""
    # Data intelligence agent is not allowed to call execute_command
    res = tool_registry.execute(
        tool_name="execute_command",
        arguments={"command": "dir"},
        caller_agent="data_intelligence",
        allowed_tools=["data_read_tabular", "data_compute_statistics"],
        allowed_tiers=[CapabilityTier.READ_ONLY, CapabilityTier.WORKSPACE_WRITE],
    )
    assert not res.is_success
    assert "TOOL_NOT_PERMITTED" in (res.error or "")


# ── 6. Filesystem Boundary Confinement ────────────────────────────────────────

def test_filesystem_boundary_confinement(tool_registry, temp_workspace):
    """Verify that path traversal outside allowed roots is strictly blocked."""
    context = {"allowed_roots": [str(temp_workspace)]}

    # Attempt path traversal outside temp_workspace
    bad_path = str(temp_workspace / ".." / "outside_file.txt")
    res = tool_registry.execute(
        tool_name="write_file",
        arguments={"path": bad_path, "content": "malicious write"},
        caller_agent="filesystem",
        allowed_tools=["write_file"],
        allowed_tiers=[CapabilityTier.WORKSPACE_WRITE],
        context=context,
    )
    assert not res.is_success
    assert "PATH_VIOLATION" in (res.error or "")


# ── 7. Prohibited Command Restrictions ────────────────────────────────────────

@pytest.mark.parametrize("dangerous_cmd, rule_keyword", [
    ("rm -rf /", "root deletion"),
    ("rm -rf $HOME", "root deletion"),
    ("rmdir /s /q C:\\", "system root deletion"),
    ("format D:", "formatting"),
    ("sudo rm -rf /var", "escalation"),
    (":(){ :|:& };:", "Fork bomb"),
    ("netsh advfirewall set allprofiles state off", "Firewall"),
])
def test_command_guard_prohibited_patterns(dangerous_cmd, rule_keyword):
    """Verify dangerous and destructive commands are immediately intercepted and blocked."""
    is_safe, reason = check_command_safety(dangerous_cmd)
    assert not is_safe
    assert rule_keyword.lower() in reason.lower()

    # Sandboxed execution must also reject it without spawning process
    res = execute_sandboxed_command(dangerous_cmd, cwd=Path.cwd())
    assert not res.is_success
    assert "COMMAND_PROHIBITED" in (res.error or "")


# ── 8. Execution Timeout & Process Termination ────────────────────────────────

def test_execution_timeout_and_process_termination(temp_workspace):
    """Verify commands exceeding timeout are actively terminated and return failure."""
    # Spawn a python process sleeping for 10 seconds with a 0.5s timeout
    py_exe = sys.executable
    cmd = f'"{py_exe}" -c "import time; time.sleep(10)"'

    start = time.perf_counter()
    res = execute_sandboxed_command(cmd, cwd=temp_workspace, timeout=0.5, allowed_roots=[str(temp_workspace)])
    elapsed = time.perf_counter() - start

    assert not res.is_success
    assert "COMMAND_TIMEOUT" in (res.error or "")
    assert elapsed < 3.0, f"Process termination took too long: {elapsed}s"


# ── 9. Parameter Schema Validation ────────────────────────────────────────────

def test_parameter_schema_validation(tool_registry):
    """Verify tool calls missing required parameters fail cleanly before execution."""
    # code_ast_search requires 'path'
    res = tool_registry.execute(
        tool_name="code_ast_search",
        arguments={},  # missing required 'path'
        caller_agent="code_intelligence",
        allowed_tools=["code_ast_search"],
        allowed_tiers=[CapabilityTier.READ_ONLY],
    )
    assert not res.is_success
    assert "VALIDATION_ERROR" in (res.error or "")
    assert "path" in (res.error or "")


# ── 10. Output Sanitization & Credential Redaction ────────────────────────────

def test_output_sanitization_and_redaction():
    """Verify API keys, bearer tokens, and secrets in tool outputs are redacted."""
    raw_leak = "API Response: authorization error with sk-ant-api03-abcdef1234567890abcdef1234567890 in header"
    sanitized = sanitize_tool_output(raw_leak)
    assert "sk-ant-api03-" not in sanitized
    assert "[REDACTED_API_KEY]" in sanitized

    # Test Bearer token redaction
    bearer_leak = "Header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef"
    sanitized_bearer = sanitize_tool_output(bearer_leak)
    assert "Bearer [REDACTED_TOKEN]" in sanitized_bearer

    # Test truncation
    long_text = "A" * 60000
    truncated = sanitize_tool_output(long_text, max_chars=1000)
    assert len(truncated) < 1200
    assert "[Output truncated" in truncated


# ── 11. Tool Execution Audit Events ───────────────────────────────────────────

def test_audit_event_emission(tool_registry, event_bus):
    """Verify every tool execution dispatches a ToolExecutedEvent to the EventBus."""
    events_received: list[ToolExecutedEvent] = []

    def handler(ev):
        if isinstance(ev, ToolExecutedEvent):
            events_received.append(ev)

    event_bus.subscribe(ToolExecutedEvent, handler)

    # 1. Successful execution
    res1 = tool_registry.execute(
        tool_name="calculator",
        arguments={"expression": "50 * 2"},
        caller_agent="calculator_agent",
        allowed_tools=["calculator"],
        allowed_tiers=[CapabilityTier.READ_ONLY],
    )
    assert res1.is_success

    # 2. Denied execution (tier violation)
    res2 = tool_registry.execute(
        tool_name="write_file",
        arguments={"path": "abc.txt", "content": "test"},
        caller_agent="read_only_agent",
        allowed_tools=["write_file"],
        allowed_tiers=[CapabilityTier.READ_ONLY],
    )
    assert not res2.is_success

    assert len(events_received) == 2
    assert events_received[0].tool_name == "calculator"
    assert events_received[0].caller_agent == "calculator_agent"
    assert events_received[0].status == "done"

    assert events_received[1].tool_name == "write_file"
    assert events_received[1].caller_agent == "read_only_agent"
    assert events_received[1].status == "failed"


# ── 12. Code Intelligence Agent Execution ─────────────────────────────────────

def test_code_intelligence_agent_execution(tool_registry, temp_workspace):
    """Verify CodeIntelligenceAgent parses AST, extracts metrics, and handles code."""
    # Create sample Python source file
    sample_file = temp_workspace / "sample_math.py"
    sample_file.write_text(
        "import math\n\n"
        "class Calculator:\n"
        "    \"\"\"Docstring.\"\"\"\n"
        "    def add(self, a, b):\n"
        "        if a > 0:\n"
        "            return a + b\n"
        "        return b\n\n"
        "def compute_root(x):\n"
        "    return math.sqrt(x)\n",
        encoding="utf-8",
    )

    agent = CodeIntelligenceAgent()
    contract = AgentInputContract(
        task_id="t_code_1",
        subtask_id="st_code_1",
        goal="Analyze sample_math.py structure and complexity",
        instructions="Inspect AST and explain code for sample_math.py",
        workspace_root=str(temp_workspace),
    )

    out = agent.execute(contract, tool_registry)
    assert out.is_success
    assert "sample_math.py" in out.summary
    assert "Calculator" in out.summary
    assert "add" in out.summary
    assert "compute_root" in out.summary
    assert len(out.evidence) >= 1


# ── 13. System Diagnosis Agent Execution ──────────────────────────────────────

def test_system_diagnosis_agent_execution(tool_registry, temp_workspace):
    """Verify SystemDiagnosisAgent captures hardware and telemetry information."""
    agent = SystemDiagnosisAgent()
    contract = AgentInputContract(
        task_id="t_sys_1",
        subtask_id="st_sys_1",
        goal="Check system diagnostics, CPU, memory and disk",
        instructions="Report full diagnostic health sweep of hardware and OS",
        workspace_root=str(temp_workspace),
    )

    out = agent.execute(contract, tool_registry)
    assert out.is_success
    assert "System Diagnosis Information" in out.summary
    assert "CPU Status" in out.summary
    assert "RAM Memory Status" in out.summary
    assert "Storage Disk Status" in out.summary
    assert "system_info" in out.evidence
    assert "cpu" in out.evidence
    assert len(out.side_effects) >= 3


# ── 14. Data Intelligence Agent Execution ─────────────────────────────────────

def test_data_intelligence_agent_execution(tool_registry, temp_workspace):
    """Verify DataIntelligenceAgent reads CSV, computes statistics, and transforms data."""
    csv_file = temp_workspace / "sales.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["item", "category", "price", "quantity"])
        writer.writerow(["Widget A", "Hardware", "10.5", "100"])
        writer.writerow(["Widget B", "Hardware", "25.0", "40"])
        writer.writerow(["Service C", "Support", "150.0", "5"])

    agent = DataIntelligenceAgent()
    contract = AgentInputContract(
        task_id="t_data_1",
        subtask_id="st_data_1",
        goal="Analyze sales.csv and transform data",
        instructions="Read sales.csv, compute stats, and transform into transformed_sales.csv",
        workspace_root=str(temp_workspace),
    )

    out = agent.execute(contract, tool_registry)
    assert out.is_success
    assert "Tabular Dataset: `sales.csv`" in out.summary
    assert "Statistical Analysis" in out.summary
    assert len(out.deliverables) >= 1

    # Verify physical file creation of transformed dataset
    transformed_file = temp_workspace / "transformed_sales.csv"
    assert transformed_file.is_file()
    assert transformed_file.stat().st_size > 0


# ── 15. Research Agent Execution ──────────────────────────────────────────────

def test_research_agent_execution(tool_registry, temp_workspace):
    """Verify ResearchAgent conducts information gathering and citation extraction."""
    agent = ResearchAgent()
    contract = AgentInputContract(
        task_id="t_res_1",
        subtask_id="st_res_1",
        goal="Research OPTIMUS hierarchical multi-agent architecture",
        instructions="Gather references and synthesize citation findings",
        workspace_root=str(temp_workspace),
    )

    out = agent.execute(contract, tool_registry)
    assert out.is_success
    assert "Research Search Results" in out.summary
    assert "Extracted Citations" in out.summary
    assert len(out.evidence.get("citations", {}).get("citations", [])) >= 1


# ── 16. Execution Agent Execution ─────────────────────────────────────────────

def test_execution_agent_sandboxed_run(tool_registry, temp_workspace):
    """Verify ExecutionAgent runs sandboxed scripts and commands safely."""
    # 1. Command execution
    agent = ExecutionAgent()
    contract = AgentInputContract(
        task_id="t_exec_1",
        subtask_id="st_exec_1",
        goal="Run a harmless echo command",
        instructions="`python -c \"print('OPTIMUS_EXECUTION_SUCCESS')\"`",
        workspace_root=str(temp_workspace),
    )

    out = agent.execute(contract, tool_registry)
    assert out.is_success
    assert "OPTIMUS_EXECUTION_SUCCESS" in out.summary

    # 2. Prohibited command blocked through execution agent
    bad_contract = AgentInputContract(
        task_id="t_exec_2",
        subtask_id="st_exec_2",
        goal="Attempt root wipe",
        instructions="`rm -rf /`",
        workspace_root=str(temp_workspace),
    )
    bad_out = agent.execute(bad_contract, tool_registry)
    assert not bad_out.is_success
    assert any(term in (bad_out.error or bad_out.summary) for term in ("COMMAND_PROHIBITED", "APPROVAL_REQUIRED"))


# ── 17. Central Orchestrator Integration with Verification ─────────────────────

def test_orchestrator_integration_with_specialized_agents(temp_workspace):
    """Verify CentralOrchestrator delegates to specialized agents and verifies deliverables."""
    task_store = TaskStore(temp_workspace / "task_store")
    tool_reg = ToolRegistry()
    agent_reg = AgentRegistry()
    event_bus = EventBus()

    orchestrator = CentralOrchestrator(
        agent_registry=agent_reg,
        tool_registry=tool_reg,
        task_store=task_store,
        event_bus=event_bus,
        allowed_roots=[str(temp_workspace)],
    )

    # Prepare data input
    data_path = temp_workspace / "dataset.csv"
    data_path.write_text("id,val\n1,10\n2,20\n3,30\n", encoding="utf-8")

    goal = "Process dataset.csv and transform data to export.csv"
    plan = orchestrator.plan(goal)
    assert len(plan.subtasks) >= 1
    assert any(st.assigned_agent == "data_intelligence" for st in plan.subtasks)

    # Run workflow
    task_rec = orchestrator.execute_task(goal)
    assert task_rec.status == TaskStatus.COMPLETED

    # Verify physical file creation
    export_file = temp_workspace / "export.csv"
    assert export_file.is_file()
    assert export_file.stat().st_size > 0
