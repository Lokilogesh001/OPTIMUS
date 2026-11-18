"""core/missions/planner.py — Mission Planning Engine for OPTIMUS.

Converts high-level natural language objectives into structured MissionPlan DAGs:
- Decomposes into granular MissionSubtasks with explicit dependencies (depends_on).
- Identifies independent steps that can run concurrently (parallelizable=True).
- Matches required capabilities against ToolRegistry and AgentRegistry.
- Assigns canonical specialized agents and toolsets.
- Specifies postconditions and expected deliverables.
- Identifies approval gates for dangerous, system-modifying, or high-risk actions.
- Detects missing capabilities prior to execution.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from core.missions.types import MissionPlan, MissionSubtask
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.tools import ToolRegistry

logger = logging.getLogger(__name__)


class MissionPlanner:
    """Plans multi-step autonomous missions from natural language objectives."""

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        brain_fn: Any | None = None,
    ) -> None:
        if agent_registry is None:
            self.agent_registry = AgentRegistry()
            from core.agents.registry_ext import register_specialized_agents
            register_specialized_agents(self.agent_registry)
        else:
            self.agent_registry = agent_registry
        self.tool_registry = tool_registry or ToolRegistry()
        self.brain_fn = brain_fn

    def plan(self, objective: str) -> MissionPlan:
        """Decompose an objective into a fully validated MissionPlan DAG."""
        plan_id = f"mplan_{uuid.uuid4().hex[:8]}"
        lower = objective.lower()

        # Check for approval triggers (system modifications, deletions, service restarts, pip installs)
        is_high_risk = any(kw in lower for kw in (
            "delete", "remove", "drop", "terminate", "kill process", "uninstall",
            "modify system", "restart service", "format", "shutdown"
        ))
        risk_level = "HIGH" if is_high_risk else "LOW"
        requires_approval = is_high_risk

        # 1. Deterministic Domain Decomposition for Canonical Mission Types
        subtasks: list[MissionSubtask] = []

        # Mission Type A: Research and Report (e.g., Python release research & comparison)
        if any(kw in lower for kw in ("research and report", "research python", "python release", "compare with installed")):
            subtasks = self._plan_research_mission(plan_id, objective)

        # Mission Type B: System Intelligence & Health Audit (CPU/RAM, disk, Ollama, health)
        elif any(kw in lower for kw in ("system intelligence", "system audit", "hardware telemetry", "top consumers", "ollama inspection")):
            subtasks = self._plan_system_intelligence_mission(plan_id, objective)

        # Mission Type C: Developer Project Inspection & Test Evaluation
        elif any(kw in lower for kw in ("developer mission", "inspect project", "inspect codebase", "run tests and report", "project inspection")):
            subtasks = self._plan_developer_mission(plan_id, objective)

        # Mission Type D: Visual Computer Use & GUI Desktop Automation
        elif any(kw in lower for kw in (
            "computer use", "visual interaction", "desktop workflow", "gui automation",
            "open notepad", "type text", "click button", "desktop observation", "interact with"
        )):
            subtasks = self._plan_computer_use_mission(plan_id, objective)

        # Mission Type E: General / Multi-Domain Objective
        else:
            subtasks = self._plan_composite_mission(plan_id, objective)

        # Build plan
        plan = MissionPlan(
            plan_id=plan_id,
            objective=objective,
            subtasks=subtasks,
            version=1,
            validated=False,
            validation_errors=[],
            estimated_resources=self._estimate_resources(subtasks),
            requires_approval=requires_approval or any(s.requires_approval for s in subtasks),
            risk_level=risk_level,
        )

        # Validate DAG
        self.validate_plan(plan)
        return plan

    def validate_plan(self, plan: MissionPlan) -> bool:
        """Validate DAG integrity, cycle freedom, dependencies, and capability availability."""
        errors: list[str] = []
        subtask_map = {s.subtask_id: s for s in plan.subtasks}

        # 1. Check dependency references
        for s in plan.subtasks:
            for dep in s.depends_on:
                if dep not in subtask_map:
                    errors.append(f"Subtask '{s.title}' depends on non-existent subtask ID: {dep}")

        # 2. Cycle detection using Kahn's algorithm or DFS
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def has_cycle(curr: str) -> bool:
            visited.add(curr)
            rec_stack.add(curr)
            for dep in subtask_map.get(curr, MissionSubtask(curr, "")).depends_on:
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    return True
            rec_stack.remove(curr)
            return False

        for st_id in subtask_map:
            if st_id not in visited:
                if has_cycle(st_id):
                    errors.append(f"Cyclic dependency detected involving subtask ID: {st_id}")
                    break

        # 3. Capability detection against ToolRegistry and AgentRegistry
        available_tools = {t.name for t in self.tool_registry.list_tools()}
        tool_aliases = {
            "web_search": "search_web",
            "python_env": "python_version",
            "ollama_check": "check_ollama",
            "disk_space": "disk_status",
            "git_status": "dev_git_status",
        }
        for s in plan.subtasks:
            # Agent check
            if s.assigned_agent not in ("general", "orchestrator"):
                agent_prof = self.agent_registry.get(s.assigned_agent)
                if not agent_prof:
                    # Non-fatal if general fallback exists, but annotate
                    logger.warning("Agent '%s' not explicitly in registry, will fallback", s.assigned_agent)

            # Tools check
            for tool in s.required_tools:
                resolved_tool = tool_aliases.get(tool, tool)
                if resolved_tool not in available_tools:
                    # Detect missing capabilities early
                    errors.append(f"Subtask '{s.title}' requires unavailable tool: '{tool}'")

        plan.validation_errors = errors
        plan.validated = len(errors) == 0
        return plan.validated

    def _estimate_resources(self, subtasks: list[MissionSubtask]) -> dict[str, Any]:
        """Estimate execution resource requirements."""
        total_duration = sum(15.0 if s.parallelizable else 25.0 for s in subtasks)
        unique_tools = {t for s in subtasks for t in s.required_tools}
        unique_agents = {s.assigned_agent for s in subtasks}

        return {
            "estimated_duration_seconds": round(total_duration, 1),
            "estimated_cpu_percent": 15.0,
            "estimated_ram_mb": 128.0,
            "unique_tools_count": len(unique_tools),
            "unique_agents_count": len(unique_agents),
            "tools": sorted(list(unique_tools)),
            "agents": sorted(list(unique_agents)),
        }

    # ── Canonical Mission Archetypes ─────────────────────────────────────────

    def _plan_research_mission(self, plan_id: str, objective: str) -> list[MissionSubtask]:
        """Archetype A: Research and Comparison Mission."""
        st1_id = f"{plan_id}_st1"
        st2_id = f"{plan_id}_st2"
        st3_id = f"{plan_id}_st3"
        st4_id = f"{plan_id}_st4"

        # Step 1: Research latest release info (Research Agent)
        s1 = MissionSubtask(
            subtask_id=st1_id,
            title="Research Latest Python Releases",
            description="Query latest stable Python release details, release dates, and major features.",
            assigned_agent="researcher",
            required_tools=["web_search"],
            depends_on=[],
            parallelizable=True,
            expected_deliverables=["research_notes"],
            postconditions=[{"type": "non_empty_output"}],
        )

        # Step 2: Inspect locally installed Python environment (System Agent)
        s2 = MissionSubtask(
            subtask_id=st2_id,
            title="Inspect Local Python Environment",
            description="Determine currently active Python version, executable path, and architecture on local machine.",
            assigned_agent="system_agent",
            required_tools=["python_env"],
            depends_on=[],
            parallelizable=True,
            expected_deliverables=["local_python_version"],
            postconditions=[{"type": "non_empty_output"}],
        )

        # Step 3: Synthesize comparison and generate Markdown report (Developer Agent)
        s3 = MissionSubtask(
            subtask_id=st3_id,
            title="Generate Comparative Python Report",
            description="Synthesize research and local environment findings into a comprehensive Markdown report.",
            assigned_agent="developer",
            required_tools=["write_file"],
            depends_on=[st1_id, st2_id],
            parallelizable=False,
            expected_deliverables=["reports/python_version_comparison.md"],
            postconditions=[{"type": "file_exists", "path": "reports/python_version_comparison.md"}],
        )

        # Step 4: Physical verification of deliverable (Verification Agent)
        s4 = MissionSubtask(
            subtask_id=st4_id,
            title="Verify Deliverable Integrity",
            description="Independently verify physical existence, file size, and markdown formatting of the generated report.",
            assigned_agent="verification_agent",
            required_tools=["read_file"],
            depends_on=[st3_id],
            parallelizable=False,
            expected_deliverables=[],
            postconditions=[{"type": "verified_success"}],
        )

        return [s1, s2, s3, s4]

    def _plan_system_intelligence_mission(self, plan_id: str, objective: str) -> list[MissionSubtask]:
        """Archetype B: System Intelligence & Health Audit Mission (Zero modifications)."""
        st1_id = f"{plan_id}_st1"
        st2_id = f"{plan_id}_st2"
        st3_id = f"{plan_id}_st3"
        st4_id = f"{plan_id}_st4"
        st5_id = f"{plan_id}_st5"

        # Step 1: CPU & Memory Telemetry (System Agent)
        s1 = MissionSubtask(
            subtask_id=st1_id,
            title="Inspect CPU and Memory Vitals",
            description="Measure current CPU utilization, physical and virtual memory usage, and identify top consuming processes.",
            assigned_agent="system_agent",
            required_tools=["cpu_status", "memory_status"],
            depends_on=[],
            parallelizable=True,
            expected_deliverables=["telemetry_vitals"],
            postconditions=[{"type": "non_empty_output"}],
        )

        # Step 2: Storage & Disk Space Inspection (File / System Agent)
        s2 = MissionSubtask(
            subtask_id=st2_id,
            title="Inspect Storage and Disk Partitions",
            description="Inspect available disk partitions, free space, and storage health.",
            assigned_agent="system_agent",
            required_tools=["disk_space"],
            depends_on=[],
            parallelizable=True,
            expected_deliverables=["disk_telemetry"],
            postconditions=[{"type": "non_empty_output"}],
        )

        # Step 3: Ollama Local Runtime Inspection (System Agent)
        s3 = MissionSubtask(
            subtask_id=st3_id,
            title="Inspect Local Ollama Runtime",
            description="Check whether Ollama daemon is active, reachable on localhost, and list local installed models.",
            assigned_agent="system_agent",
            required_tools=["ollama_check"],
            depends_on=[],
            parallelizable=True,
            expected_deliverables=["ollama_status"],
            postconditions=[{"type": "non_empty_output"}],
        )

        # Step 4: Synthesize System Health Report (Data Agent)
        s4 = MissionSubtask(
            subtask_id=st4_id,
            title="Synthesize System Health Report",
            description="Aggregate CPU, memory, storage, and Ollama telemetry into a structured Markdown audit report.",
            assigned_agent="data_agent",
            required_tools=["write_file"],
            depends_on=[st1_id, st2_id, st3_id],
            parallelizable=False,
            expected_deliverables=["reports/system_health_audit.md"],
            postconditions=[{"type": "file_exists", "path": "reports/system_health_audit.md"}],
        )

        # Step 5: Verification (Verification Agent)
        s5 = MissionSubtask(
            subtask_id=st5_id,
            title="Verify Audit Report Integrity",
            description="Verify report existence and integrity without modifying any system configuration.",
            assigned_agent="verification_agent",
            required_tools=["read_file"],
            depends_on=[st4_id],
            parallelizable=False,
            expected_deliverables=[],
            postconditions=[{"type": "verified_success"}],
        )

        return [s1, s2, s3, s4, s5]

    def _plan_developer_mission(self, plan_id: str, objective: str) -> list[MissionSubtask]:
        """Archetype C: Developer Project Inspection Mission."""
        st1_id = f"{plan_id}_st1"
        st2_id = f"{plan_id}_st2"
        st3_id = f"{plan_id}_st3"
        st4_id = f"{plan_id}_st4"

        s1 = MissionSubtask(
            subtask_id=st1_id,
            title="Inspect Project Structure & Git Status",
            description="Analyze repository layout, branch state, and recently modified files.",
            assigned_agent="developer",
            required_tools=["git_status"],
            depends_on=[],
            parallelizable=False,
            expected_deliverables=["repo_layout"],
            postconditions=[{"type": "non_empty_output"}],
        )

        s2 = MissionSubtask(
            subtask_id=st2_id,
            title="Analyze Test Suite Status",
            description="Inspect available test suites and verify testing readiness.",
            assigned_agent="developer",
            required_tools=["read_file"],
            depends_on=[st1_id],
            parallelizable=False,
            expected_deliverables=["test_analysis"],
            postconditions=[{"type": "non_empty_output"}],
        )

        s3 = MissionSubtask(
            subtask_id=st3_id,
            title="Compile Project Quality Findings",
            description="Compile findings into developer assessment report.",
            assigned_agent="developer",
            required_tools=["write_file"],
            depends_on=[st2_id],
            parallelizable=False,
            expected_deliverables=["reports/developer_project_assessment.md"],
            postconditions=[{"type": "file_exists", "path": "reports/developer_project_assessment.md"}],
        )

        s4 = MissionSubtask(
            subtask_id=st4_id,
            title="Verify Report Deliverable",
            description="Verify deliverable without touching source code.",
            assigned_agent="verification_agent",
            required_tools=["read_file"],
            depends_on=[st3_id],
            parallelizable=False,
            expected_deliverables=[],
            postconditions=[{"type": "verified_success"}],
        )

        return [s1, s2, s3, s4]

    def _plan_computer_use_mission(self, plan_id: str, objective: str) -> list[MissionSubtask]:
        """Archetype D: Visual Computer Use & GUI Desktop Automation."""
        st1_id = f"{plan_id}_st1"
        st2_id = f"{plan_id}_st2"
        st3_id = f"{plan_id}_st3"
        st4_id = f"{plan_id}_st4"

        s1 = MissionSubtask(
            subtask_id=st1_id,
            title="Desktop Observation & Target Identification",
            description="Inspect desktop state, active windows, and UI element tree to locate application targets.",
            assigned_agent="computer_agent",
            required_tools=["computer_observe", "computer_find_element"],
            depends_on=[],
            parallelizable=False,
            expected_deliverables=["desktop_observation"],
            postconditions=[{"type": "non_empty_output"}],
        )

        s2 = MissionSubtask(
            subtask_id=st2_id,
            title="Execute GUI Interactions",
            description=f"Perform closed-loop UI interactions, window navigation, and typing: {objective}",
            assigned_agent="computer_agent",
            required_tools=["computer_switch_window", "computer_type_text", "computer_interact", "computer_hotkey"],
            depends_on=[st1_id],
            parallelizable=False,
            expected_deliverables=["actuation_evidence"],
            postconditions=[{"type": "non_empty_output"}],
        )

        s3 = MissionSubtask(
            subtask_id=st3_id,
            title="Compile Visual Interaction Evidence",
            description="Aggregate observation logs, UI state diffs, and verification digests into a mission report.",
            assigned_agent="data_agent",
            required_tools=["write_file"],
            depends_on=[st2_id],
            parallelizable=False,
            expected_deliverables=["reports/visual_interaction_report.md"],
            postconditions=[{"type": "file_exists", "path": "reports/visual_interaction_report.md"}],
        )

        s4 = MissionSubtask(
            subtask_id=st4_id,
            title="Verify Visual State & Deliverable Integrity",
            description="Verify that GUI postconditions hold and interaction report is valid.",
            assigned_agent="verification_agent",
            required_tools=["computer_verify_state", "read_file"],
            depends_on=[st3_id],
            parallelizable=False,
            expected_deliverables=[],
            postconditions=[{"type": "verified_success"}],
        )

        return [s1, s2, s3, s4]

    def _plan_composite_mission(self, plan_id: str, objective: str) -> list[MissionSubtask]:
        """Archetype D: Composite Multi-Agent Mission."""
        st1_id = f"{plan_id}_st1"
        st2_id = f"{plan_id}_st2"
        st3_id = f"{plan_id}_st3"

        s1 = MissionSubtask(
            subtask_id=st1_id,
            title="Gather Information and Telemetry",
            description=f"Inspect system or environment relating to: {objective}",
            assigned_agent="researcher",
            required_tools=["read_file"],
            depends_on=[],
            parallelizable=False,
            expected_deliverables=["intel_findings"],
            postconditions=[{"type": "non_empty_output"}],
        )

        s2 = MissionSubtask(
            subtask_id=st2_id,
            title="Execute Primary Objective",
            description=f"Carry out core operations for: {objective}",
            assigned_agent="developer",
            required_tools=["write_file"],
            depends_on=[st1_id],
            parallelizable=False,
            expected_deliverables=["reports/mission_summary.md"],
            postconditions=[{"type": "file_exists", "path": "reports/mission_summary.md"}],
        )

        s3 = MissionSubtask(
            subtask_id=st3_id,
            title="Verify Final Outcome",
            description="Confirm deliverables and verify postconditions.",
            assigned_agent="verification_agent",
            required_tools=["read_file"],
            depends_on=[st2_id],
            parallelizable=False,
            expected_deliverables=[],
            postconditions=[{"type": "verified_success"}],
        )

        return [s1, s2, s3]
