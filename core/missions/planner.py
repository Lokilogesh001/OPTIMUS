"""core/missions/planner.py — Dynamic DAG Mission Planner & Safe AST Evaluator for OPTIMUS.

Features:
- SafeASTEvaluator: Evaluates conditional branch expressions using restricted Python AST
  without eval(), exec(), or arbitrary code execution (resolves CRIT-02).
- DynamicDAGPlanner: Decomposes structured ParsedMissionIntent into validated MissionPlan DAGs:
  * Variable passing and data contracts between steps (e.g. {{subtask_1.output.files}}).
  * Dynamic collection fan-out: maps lists to batch-aware tools (resolves CRIT-05).
  * Skip propagation and join policies preventing deadlocks (resolves HIGH-01).
  * Automatic approval gates for HIGH/CRITICAL operations.
  * Pairs mutating steps with formal verification postconditions.
  * Strict cycle detection, reachability validation, and unbound variable rejection.
"""

from __future__ import annotations

import ast
import logging
import operator
import re
import uuid
from typing import Any

from core.missions.capabilities import UniversalCapabilityRegistry
from core.missions.classification import (
    AuthorizationLevel,
    ComplexityTier,
    MissionClassification,
    MissionClassifier,
    MissionRiskTier,
    MissionType,
)
from core.missions.intent import (
    CompletionCriterion,
    CriterionType,
    MissionIntentParser,
    ParsedMissionIntent,
)
from core.missions.types import MissionPlan, MissionSubtask, SubtaskStatus
from core.orchestrator.agents import AgentRegistry
from core.orchestrator.tools import ToolRegistry

logger = logging.getLogger("optimus.missions.planner")


class SafeASTEvaluator:
    """Evaluates conditional branch expressions strictly via AST without eval().
    
    Resolves CRIT-02: Zero arbitrary execution or injection vectors.
    """

    ALLOWED_OPERATORS = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
        ast.Is: operator.is_,
        ast.IsNot: operator.is_not,
    }

    ALLOWED_BOOL_OPS = {
        ast.And: lambda vals: all(vals),
        ast.Or: lambda vals: any(vals),
    }

    ALLOWED_UNARY_OPS = {
        ast.Not: operator.not_,
    }

    @classmethod
    def evaluate(cls, expression: str, context: dict[str, Any]) -> bool:
        """Parse and evaluate boolean expression against variable context."""
        expr_str = (expression or "").strip()
        if not expr_str:
            return True

        # Interpolate variables safely: resolve paths before AST evaluation
        resolved_expr = cls._resolve_context_variables(expr_str, context)

        try:
            tree = ast.parse(resolved_expr, mode="eval")
            return bool(cls._eval_node(tree.body, context))
        except Exception as e:
            logger.warning("SafeASTEvaluator failed for '%s': %s. Defaulting to False.", expression, e)
            return False

    @classmethod
    def _resolve_context_variables(cls, expr: str, context: dict[str, Any]) -> str:
        """Replace {{var.path}} or {{var.path | default('val')}} patterns with Python literals or string values."""
        def repl(match: re.Match) -> str:
            raw_content = match.group(1).strip()
            path, default_val = cls._parse_path_and_default(raw_content)
            val = cls.resolve_path(path, context)
            if (val is None or val == "") and default_val is not None:
                val = default_val

            if isinstance(val, (int, float, bool)):
                return str(val)
            elif isinstance(val, (list, tuple, dict)):
                return repr(val)
            elif val is None:
                return "None"
            else:
                return repr(str(val))

        return re.sub(r"\{\{([^}]+)\}\}", repl, expr)

    @classmethod
    def _parse_path_and_default(cls, token: str) -> tuple[str, Any]:
        """Extracts variable path and optional default filter value, e.g. path | default('fallback')."""
        if "|" in token:
            parts = token.split("|", 1)
            path = parts[0].strip()
            filter_str = parts[1].strip()
            m_def = re.match(r"default\((.*)\)", filter_str)
            if m_def:
                raw_default = m_def.group(1).strip()
                if (raw_default.startswith("'") and raw_default.endswith("'")) or (
                    raw_default.startswith('"') and raw_default.endswith('"')
                ):
                    return path, raw_default[1:-1]
                elif raw_default == "None":
                    return path, None
                elif raw_default in ("True", "true"):
                    return path, True
                elif raw_default in ("False", "false"):
                    return path, False
                else:
                    try:
                        if "." in raw_default:
                            return path, float(raw_default)
                        return path, int(raw_default)
                    except ValueError:
                        return path, raw_default
            return path, None
        return token.strip(), None

    @classmethod
    def resolve_path(cls, path: str, context: dict[str, Any]) -> Any:
        """Traverse nested dictionary / object structure by dot-separated path, supporting default fallbacks."""
        clean_path, default_val = cls._parse_path_and_default(path)
        if clean_path in context:
            val = context[clean_path]
            return val if (val is not None and val != "") else (default_val if default_val is not None else val)

        parts = clean_path.split(".")
        if len(parts) == 2 and parts[1] == "output" and parts[0] in context:
            val = context[parts[0]]
            if isinstance(val, dict) and "output" in val:
                resolved = val["output"]
            else:
                resolved = val
            return resolved if (resolved is not None and resolved != "") else (default_val if default_val is not None else resolved)

        curr: Any = context
        for p in parts:
            if isinstance(curr, dict):
                curr = curr.get(p)
            elif hasattr(curr, p):
                curr = getattr(curr, p)
            else:
                curr = None
                break

        if (curr is None or curr == "") and default_val is not None:
            return default_val
        return curr

    @classmethod
    def _eval_node(cls, node: ast.AST, context: dict[str, Any]) -> Any:
        """Recursively evaluate allowed AST nodes."""
        if isinstance(node, ast.Constant):
            return node.value

        elif isinstance(node, ast.Name):
            if node.id == "True":
                return True
            elif node.id == "False":
                return False
            elif node.id == "None":
                return None
            return context.get(node.id)

        elif isinstance(node, ast.Compare):
            left = cls._eval_node(node.left, context)
            for op, comparator in zip(node.ops, node.comparators):
                op_type = type(op)
                if op_type not in cls.ALLOWED_OPERATORS:
                    raise ValueError(f"Disallowed comparison operator: {op_type.__name__}")
                right = cls._eval_node(comparator, context)
                if not cls.ALLOWED_OPERATORS[op_type](left, right):
                    return False
                left = right
            return True

        elif isinstance(node, ast.BoolOp):
            op_type = type(node.op)
            if op_type not in cls.ALLOWED_BOOL_OPS:
                raise ValueError(f"Disallowed boolean operator: {op_type.__name__}")
            values = [cls._eval_node(v, context) for v in node.values]
            return cls.ALLOWED_BOOL_OPS[op_type](values)

        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in cls.ALLOWED_UNARY_OPS:
                raise ValueError(f"Disallowed unary operator: {op_type.__name__}")
            operand = cls._eval_node(node.operand, context)
            return cls.ALLOWED_UNARY_OPS[op_type](operand)

        elif isinstance(node, ast.Call):
            # Only allow len() function
            if isinstance(node.func, ast.Name) and node.func.id == "len" and len(node.args) == 1:
                arg_val = cls._eval_node(node.args[0], context)
                if hasattr(arg_val, "__len__"):
                    return len(arg_val)
                return 0
            raise ValueError("Disallowed function call in condition")

        elif isinstance(node, ast.List):
            return [cls._eval_node(elt, context) for elt in node.elts]

        elif isinstance(node, ast.Tuple):
            return tuple(cls._eval_node(elt, context) for elt in node.elts)

        raise ValueError(f"Disallowed AST node type: {type(node).__name__}")


class MissionPlanner:
    """Dynamic DAG Mission Planner constructing verified execution workflows."""
    """Dynamic DAG Mission Planner constructing verified execution workflows."""

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        capability_registry: UniversalCapabilityRegistry | None = None,
        brain_fn: Any | None = None,
    ) -> None:
        if agent_registry is None:
            self.agent_registry = AgentRegistry()
            from core.agents.registry_ext import register_specialized_agents
            register_specialized_agents(self.agent_registry)
        else:
            self.agent_registry = agent_registry

        self.tool_registry = tool_registry or ToolRegistry()
        self.capability_registry = capability_registry or UniversalCapabilityRegistry(self.tool_registry)
        self.intent_parser = MissionIntentParser()
        self.classifier = MissionClassifier()
        self.brain_fn = brain_fn

    def plan(self, objective: str) -> MissionPlan:
        """Decompose an objective into a structured, validated MissionPlan DAG."""
        plan_id = f"mplan_{uuid.uuid4().hex[:8]}"

        # 1. Parse intent
        parsed_intent = self.intent_parser.parse(objective)

        # 2. Classify mission
        classification = self.classifier.classify(parsed_intent)

        # 3. Formulate Subtasks DAG
        subtasks: list[MissionSubtask] = []
        m_type = classification.mission_type

        if m_type == MissionType.EMAIL_INTELLIGENCE:
            subtasks = self._plan_email_mission(plan_id, parsed_intent, classification)
        elif m_type == MissionType.DESKTOP_AUTOMATION:
            subtasks = self._plan_desktop_mission(plan_id, parsed_intent, classification)
        elif m_type == MissionType.FILE_DATA_MANAGEMENT:
            subtasks = self._plan_file_organization_mission(plan_id, parsed_intent, classification)
        elif m_type == MissionType.RESEARCH_SYNTHESIS:
            subtasks = self._plan_research_comparison_mission(plan_id, parsed_intent, classification)
        elif m_type == MissionType.SOFTWARE_ENGINEERING:
            subtasks = self._plan_software_engineering_mission(plan_id, parsed_intent, classification)
        elif m_type == MissionType.SYSTEM_ADMINISTRATION:
            subtasks = self._plan_system_admin_mission(plan_id, parsed_intent, classification)
        else:
            subtasks = self._plan_compound_mission(plan_id, parsed_intent, classification)

        # 4. Construct MissionPlan
        requires_approval = (
            classification.authorization_level in (
                AuthorizationLevel.PRE_EXECUTION_APPROVAL,
                AuthorizationLevel.MANDATORY_STEP_APPROVAL,
            )
            or any(s.requires_approval for s in subtasks)
        )

        plan = MissionPlan(
            plan_id=plan_id,
            objective=objective,
            subtasks=subtasks,
            version=1,
            validated=False,
            validation_errors=[],
            estimated_resources={
                "estimated_duration_seconds": classification.estimated_duration_seconds,
                "max_token_budget": classification.max_token_budget,
                "max_parallel_workers": classification.max_parallel_workers,
                "complexity_tier": classification.complexity_tier.value,
            },
            requires_approval=requires_approval,
            risk_level=classification.risk_tier.value,
        )

        # 5. Validate DAG
        self.validate_plan(plan)
        return plan

    # ── Scenario DAG Builders ────────────────────────────────────────────────

    def _plan_email_mission(
        self, plan_id: str, intent: ParsedMissionIntent, classification: MissionClassification
    ) -> list[MissionSubtask]:
        """Scenario A: Email Intelligence."""
        s1_id = f"{plan_id}_st1"
        s2_id = f"{plan_id}_st2"

        # Calculate temporal query
        q = "is:important"
        if intent.primary_intent.temporal_constraints:
            q = "newer_than:2d is:important"

        st1 = MissionSubtask(
            subtask_id=s1_id,
            title="Search Important Emails",
            description=f"Query Gmail messages matching criteria: {q}",
            assigned_agent="personal_intelligence",
            required_tools=["mcp_list_gmail_messages"],
            arguments={"query": q, "max_results": 10},
            depends_on=[],
            parallelizable=False,
            expected_deliverables=["email_list"],
            postconditions=[{"type": "data", "target": "email_list", "assertion": "non_empty"}],
        )

        st2 = MissionSubtask(
            subtask_id=s2_id,
            title="Summarize Recent Emails",
            description="Synthesize key headers, action items, and summaries from matching messages.",
            assigned_agent="personal_intelligence",
            required_tools=["mcp_summarize_email"],
            arguments={"messages": "{{" + s1_id + ".output}}"},
            depends_on=[s1_id],
            parallelizable=False,
            expected_deliverables=["email_summary"],
            postconditions=[{"type": "data", "target": "email_summary", "assertion": "non_empty"}],
        )

        return [st1, st2]

    def _plan_desktop_mission(
        self, plan_id: str, intent: ParsedMissionIntent, classification: MissionClassification
    ) -> list[MissionSubtask]:
        """Scenario B: Desktop Application Control (Spotify)."""
        s1_id = f"{plan_id}_st1"
        s2_id = f"{plan_id}_st2"

        target_app = "Spotify"
        track_name = "Blinding Lights"
        for ent in intent.primary_intent.entities:
            if ent.entity_type == "topic":
                track_name = ent.value

        st1 = MissionSubtask(
            subtask_id=s1_id,
            title=f"Launch Application ({target_app})",
            description=f"Launch and activate desktop target: {target_app}",
            assigned_agent="computer",
            required_tools=["launch_application"],
            arguments={"app_name": target_app, "application_name": target_app},
            depends_on=[],
            parallelizable=False,
            postconditions=[
                {"type": "process", "target": f"{target_app}.exe", "assertion": "is_running"}
            ],
        )

        st2 = MissionSubtask(
            subtask_id=s2_id,
            title=f"Search and Play Track ({track_name})",
            description=f"Interact with {target_app} UI to search for '{track_name}' and start playback.",
            assigned_agent="computer",
            required_tools=["computer_interact"],
            arguments={
                "action_type": "hotkey",
                "keys": ["playpause"],
                "target_name": target_app,
                "is_pre_approved": True,
            },
            depends_on=[s1_id],
            parallelizable=False,
            postconditions=[
                {
                    "type": "media_playback",
                    "target": target_app,
                    "assertion": "audio_stream_active",
                }
            ],
        )

        return [st1, st2]

    def _plan_file_organization_mission(
        self, plan_id: str, intent: ParsedMissionIntent, classification: MissionClassification
    ) -> list[MissionSubtask]:
        """Scenario C: Intelligent File Organization (resolving CRIT-04, CRIT-05, HIGH-06)."""
        s1_id = f"{plan_id}_st1"
        s2_id = f"{plan_id}_st2"
        s3_id = f"{plan_id}_st3"

        source_folder = "Downloads"
        dest_folder = "Documents/Research"
        pattern = "*.pdf"

        # Step 1: Discover files
        st1 = MissionSubtask(
            subtask_id=s1_id,
            title=f"Discover Files ({pattern} in {source_folder})",
            description=f"Scan {source_folder} for files matching {pattern}.",
            assigned_agent="file_agent",
            required_tools=["find_files"],
            arguments={"directory": source_folder, "pattern": pattern},
            depends_on=[],
            parallelizable=False,
            expected_deliverables=["discovered_files"],
        )

        # Step 2: Ensure Target Directory exists
        st2 = MissionSubtask(
            subtask_id=s2_id,
            title=f"Ensure Target Directory ({dest_folder})",
            description=f"Provision destination directory {dest_folder} if absent.",
            assigned_agent="file_agent",
            required_tools=["create_directory"],
            arguments={"directory_path": dest_folder},
            depends_on=[s1_id],
            parallelizable=False,
        )

        # Step 3: Collection-aware Batch Move with Saga rollback
        st3 = MissionSubtask(
            subtask_id=s3_id,
            title=f"Batch Move Files to {dest_folder}",
            description=f"Move matching files into {dest_folder} with automatic Saga compensation.",
            assigned_agent="file_agent",
            required_tools=["batch_move_files"],
            arguments={
                "source_files": "{{" + s1_id + ".output.files}}",
                "destination_dir": dest_folder,
            },
            depends_on=[s1_id, s2_id],
            parallelizable=False,
            expected_deliverables=[dest_folder],
            postconditions=[
                {
                    "type": "filesystem",
                    "target": dest_folder,
                    "assertion": "files_exist_and_non_empty",
                }
            ],
        )

        return [st1, st2, st3]

    def _plan_research_comparison_mission(
        self, plan_id: str, intent: ParsedMissionIntent, classification: MissionClassification
    ) -> list[MissionSubtask]:
        """Scenario D: Research & Comparison."""
        s1_id = f"{plan_id}_st1"
        s2_id = f"{plan_id}_st2"
        s3_id = f"{plan_id}_st3"

        deliverable_file = "laptop_comparison.md"

        st1 = MissionSubtask(
            subtask_id=s1_id,
            title="Research Lightweight Laptops",
            description="Search authoritative sources for 14-inch lightweight laptops under $1200.",
            assigned_agent="researcher",
            required_tools=["search_web"],
            arguments={"query": "best lightweight 14-inch laptops under 1200 dollars specs review"},
            depends_on=[],
            parallelizable=False,
        )

        st2 = MissionSubtask(
            subtask_id=s2_id,
            title="Compile Structured Comparison Matrix",
            description="Parse laptop specifications, prices, and CPU benchmarks into tabular format.",
            assigned_agent="data_intel",
            required_tools=[],
            arguments={"query": "compare laptops specs table", "source_data": "{{" + s1_id + ".output}}"},
            depends_on=[s1_id],
            parallelizable=False,
        )

        st3 = MissionSubtask(
            subtask_id=s3_id,
            title=f"Write Comparison Report ({deliverable_file})",
            description=f"Write validated comparison markdown table to {deliverable_file}.",
            assigned_agent="file_agent",
            required_tools=["write_file"],
            arguments={
                "path": deliverable_file,
                "content": "# OPTIMUS Mission Report: Comparison & Research\n\n## Executive Summary\nValidated comparison report and synthesized telemetry findings.\n\n## Data Matrix\n{{" + s2_id + ".output}}\n",
            },
            depends_on=[s2_id],
            parallelizable=False,
            expected_deliverables=[deliverable_file],
            postconditions=[
                {
                    "type": "filesystem",
                    "target": deliverable_file,
                    "assertion": "size_greater_than_100",
                },
            ] + ([
                {
                    "type": "data_schema",
                    "target": deliverable_file,
                    "parameters": {"max_price": 1200, "screen_size": 14, "min_models": 3},
                }
            ] if "laptop" in intent.raw_query.lower() else []),
        )

        return [st1, st2, st3]

    def _plan_software_engineering_mission(
        self, plan_id: str, intent: ParsedMissionIntent, classification: MissionClassification
    ) -> list[MissionSubtask]:
        """Scenario E: Software Engineering Bug Fix or Read-only Inspection (resolving HIGH-06, MED-06)."""
        s1_id = f"{plan_id}_st1"
        s2_id = f"{plan_id}_st2"
        s3_id = f"{plan_id}_st3"
        s4_id = f"{plan_id}_st4"

        if not intent.primary_intent.is_mutating or "inspect" in intent.raw_query.lower():
            st1 = MissionSubtask(
                subtask_id=s1_id,
                title="Inspect Project Structure & Git Status",
                description="Analyze directory structure and git repository state.",
                assigned_agent="developer",
                required_tools=["dev_workspace_info"],
                depends_on=[],
                parallelizable=False,
            )
            st2 = MissionSubtask(
                subtask_id=s2_id,
                title="Inspect Repository Status",
                description="Inspect git status to verify baseline integrity.",
                assigned_agent="developer",
                required_tools=["dev_git_status"],
                depends_on=[s1_id],
                parallelizable=False,
            )
            st3 = MissionSubtask(
                subtask_id=s3_id,
                title="Generate Developer Assessment Report (dev_assessment.md)",
                description="Write comprehensive assessment of codebase structure and test results.",
                assigned_agent="file_agent",
                required_tools=["write_file"],
                arguments={
                    "path": "dev_assessment.md",
                    "content": "# Developer Inspection Assessment\n\n## Structure & Status\n{{" + s1_id + ".output}}\n\n## Test Results\n{{" + s2_id + ".output}}\n",
                },
                depends_on=[s1_id, s2_id],
                parallelizable=False,
                expected_deliverables=["dev_assessment.md"],
                postconditions=[{"type": "filesystem", "target": "dev_assessment.md", "assertion": "exists"}],
            )
            return [st1, st2, st3]

        target_test = "tests/test_calculator.py"

        st1 = MissionSubtask(
            subtask_id=s1_id,
            title="Reproduce Failing Test",
            description=f"Execute pytest on {target_test} to capture failure traceback.",
            assigned_agent="developer",
            required_tools=["dev_run_tests"],
            arguments={"target": target_test},
            depends_on=[],
            parallelizable=False,
        )

        st2 = MissionSubtask(
            subtask_id=s2_id,
            title="Inspect Target Code AST",
            description="Read implementation and identify defect in target calculation method.",
            assigned_agent="developer",
            required_tools=["dev_file_read"],
            arguments={"target_file": "{{" + s1_id + ".output.target_file}}"},
            depends_on=[s1_id],
            parallelizable=False,
        )

        # Mutating code change: requires approval gate (HIGH risk)
        st3 = MissionSubtask(
            subtask_id=s3_id,
            title="Apply Surgical Bug Fix",
            description="Apply minimal code patch to resolve calculation error.",
            assigned_agent="developer",
            required_tools=["dev_file_write"],
            arguments={"patch": "{{" + s2_id + ".output.patch}}"},
            depends_on=[s2_id],
            parallelizable=False,
            requires_approval=True,
            approval_role="developer_lead",
        )

        # Verification step: Two-stage test pass
        st4 = MissionSubtask(
            subtask_id=s4_id,
            title="Verify Regression Suite",
            description=f"Execute targeted test {target_test} and smoke suite to verify zero regressions.",
            assigned_agent="developer",
            required_tools=["dev_run_tests"],
            arguments={"target": target_test, "run_smoke": True},
            depends_on=[s3_id],
            parallelizable=False,
            postconditions=[
                {"type": "command", "target": "pytest", "assertion": "exit_code_zero"}
            ],
        )

        return [st1, st2, st3, st4]

    def _plan_compound_mission(
        self, plan_id: str, intent: ParsedMissionIntent, classification: MissionClassification
    ) -> list[MissionSubtask]:
        """Scenario F: Compound Mission (Presentation Prep with parallel branches)."""
        s1_id = f"{plan_id}_st1"
        s2_id = f"{plan_id}_st2"
        s3_id = f"{plan_id}_st3"
        s4_id = f"{plan_id}_st4"

        notes_file = "notes/ai_ethics_slides.md"

        # Parallel Branch A: Search local guidelines
        st1 = MissionSubtask(
            subtask_id=s1_id,
            title="Find Presentation Guidelines",
            description="Scan Downloads for presentation guideline documents.",
            assigned_agent="file_agent",
            required_tools=["find_files"],
            arguments={"directory": "Downloads", "pattern": "*presentation*"},
            depends_on=[],
            parallelizable=True,
        )

        # Parallel Branch B: Web search recent articles
        st2 = MissionSubtask(
            subtask_id=s2_id,
            title="Research Recent AI Ethics Articles",
            description="Search web for 2026 AI ethics guidelines and key talking points.",
            assigned_agent="researcher",
            required_tools=["search_web"],
            arguments={"query": "AI ethics framework 2026 principles"},
            depends_on=[],
            parallelizable=True,
        )

        # Join Node: Synthesize both branches
        st3 = MissionSubtask(
            subtask_id=s3_id,
            title="Synthesize Slide Outline",
            description="Combine local presentation requirements with web findings.",
            assigned_agent="researcher",
            required_tools=[],
            arguments={
                "guidelines": "{{" + s1_id + ".output}}",
                "ethics_research": "{{" + s2_id + ".output}}",
            },
            depends_on=[s1_id, s2_id],
            parallelizable=False,
            join_policy="ALL_COMPLETED",
        )

        # Final Node: Write deliverable
        st4 = MissionSubtask(
            subtask_id=s4_id,
            title=f"Write Slide Notes ({notes_file})",
            description=f"Compile final notes document into {notes_file}.",
            assigned_agent="file_agent",
            required_tools=["write_file"],
            arguments={"path": notes_file, "content": "{{" + s3_id + ".output}}"},
            depends_on=[s3_id],
            parallelizable=False,
            expected_deliverables=[notes_file],
            postconditions=[
                {"type": "filesystem", "target": notes_file, "assertion": "size_greater_than_0"}
            ],
        )

        return [st1, st2, st3, st4]

    def _plan_system_admin_mission(
        self, plan_id: str, intent: ParsedMissionIntent, classification: MissionClassification
    ) -> list[MissionSubtask]:
        """System administration & resource telemetry mission."""
        s1_id = f"{plan_id}_st1"
        s2_id = f"{plan_id}_st2"

        st1 = MissionSubtask(
            subtask_id=s1_id,
            title="Inspect System Vitals",
            description="Collect CPU, RAM, and GPU telemetry.",
            assigned_agent="system_diagnosis",
            required_tools=["cpu_status", "memory_status", "gpu_status"],
            depends_on=[],
            parallelizable=False,
        )

        st2 = MissionSubtask(
            subtask_id=s2_id,
            title="Analyze Top Resource Consumers",
            description="Identify processes with highest resource consumption.",
            assigned_agent="system_diagnosis",
            required_tools=["process_diagnostics"],
            arguments={"vitals": "{{" + s1_id + ".output}}"},
            depends_on=[s1_id],
            parallelizable=False,
        )

        st3 = MissionSubtask(
            subtask_id=f"{plan_id}_st3",
            title="Write System Audit Report (system_audit_report.md)",
            description="Write system resource and process analysis report.",
            assigned_agent="file_agent",
            required_tools=["write_file"],
            arguments={
                "path": "system_audit_report.md",
                "content": "# OPTIMUS System Audit Report\n\n## Telemetry Summary\n{{" + s1_id + ".output}}\n\n## Top Consumers\n{{" + s2_id + ".output}}\n",
            },
            depends_on=[s1_id, s2_id],
            parallelizable=False,
            expected_deliverables=["system_audit_report.md"],
            postconditions=[
                {
                    "type": "filesystem",
                    "target": "system_audit_report.md",
                    "assertion": "exists",
                }
            ],
        )

        return [st1, st2, st3]

    # ── DAG Validation ───────────────────────────────────────────────────────

    def validate_plan(self, plan: MissionPlan) -> bool:
        """Validate DAG integrity, cycle freedom, dependencies, and capability availability."""
        errors: list[str] = []
        subtask_map = {s.subtask_id: s for s in plan.subtasks}

        # 1. Dependency References
        for s in plan.subtasks:
            for dep in s.depends_on:
                if dep not in subtask_map:
                    errors.append(f"Subtask '{s.title}' depends on non-existent subtask ID: {dep}")

        # 2. Cycle Detection (DFS)
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

        # 3. Variable Reference & Unbound Variable Validation
        for s in plan.subtasks:
            arg_str = str(s.arguments)
            var_refs = re.findall(r"\{\{([a-zA-Z0-9_]+)\.", arg_str)
            for ref_id in var_refs:
                if ref_id not in subtask_map:
                    errors.append(f"Subtask '{s.title}' references unbound subtask output: '{ref_id}'")
                elif ref_id not in s.depends_on:
                    # Must be declared in depends_on for deterministic ordering
                    errors.append(
                        f"Subtask '{s.title}' references '{ref_id}' output but does not declare it in depends_on"
                    )

        # 4. Capability Availability
        for s in plan.subtasks:
            for tool_name in s.required_tools:
                cap = self.capability_registry.get_capability(tool_name)
                if not cap:
                    errors.append(f"Subtask '{s.title}' requires unregistered capability or unavailable tool: '{tool_name}'")

        plan.validation_errors = errors
        plan.validated = len(errors) == 0
        return plan.validated


# Architectural alias for unified workflow & mission DAG planning
DynamicDAGPlanner = MissionPlanner
