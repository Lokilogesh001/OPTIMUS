"""core/agents/code_intel.py — Code Intelligence Specialized Agent for OPTIMUS.

Responsibilities:
- Codebase exploration and structural AST analysis.
- Code explanation and dependency discovery.
- Syntax checking and bug investigation.
- Controlled code modification within workspace boundaries.
- Test execution and result interpretation.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from core.agents.base import SpecializedAgent
from core.agents.contracts import AgentInputContract, AgentOutputContract
from core.orchestrator.agents import AgentCapability, AgentProfile
from core.orchestrator.tools import CapabilityTier

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry


def create_code_intelligence_profile() -> AgentProfile:
    """Construct the canonical Code Intelligence Agent profile."""
    return AgentProfile(
        name="code_intelligence",
        title="Code Intelligence Specialist",
        description="Explores codebases, parses ASTs, analyzes dependencies, diagnoses bugs, and runs tests.",
        capability=AgentCapability(
            domain="code",
            allowed_tools=[
                "read_file",
                "write_file",
                "list_directory",
                "code_ast_search",
                "code_explain",
                "code_test_runner",
            ],
            allowed_tiers=[
                CapabilityTier.READ_ONLY,
                CapabilityTier.WORKSPACE_WRITE,
                CapabilityTier.EXECUTION,
            ],
            max_turns=8,
            timeout_seconds=180.0,
        ),
        instructions=(
            "You are a principal software engineer and static analysis specialist. "
            "Inspect code through AST and syntax tools, explain structures concisely, "
            "and verify code execution through automated tests. Never guess imports or functions."
        ),
    )


class CodeIntelligenceAgent(SpecializedAgent):
    """Specialized agent performing real static code analysis, AST parsing, and test verification."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_code_intelligence_profile())

    def execute(
        self,
        contract: AgentInputContract,
        tool_registry: ToolRegistry,
    ) -> AgentOutputContract:
        start_time = time.perf_counter()
        context = {"allowed_roots": [contract.workspace_root]}
        side_effects: list[dict[str, Any]] = []
        deliverables: list[dict[str, Any]] = []
        findings: list[str] = []
        overall_evidence: dict[str, Any] = {}

        text = (contract.goal + " " + contract.instructions).lower()

        # 1. Test runner flow
        if any(kw in text for kw in ("test", "pytest", "unit test", "run tests")):
            test_target = "tests"
            file_match = re.search(r"(\w+\.(?:py|md))", contract.instructions)
            if file_match and "test" in file_match.group(1):
                test_target = file_match.group(1)

            res = self.call_tool(
                tool_registry,
                "code_test_runner",
                {"test_path": test_target},
                context=context,
            )
            side_effects.append({
                "tool_name": "code_test_runner",
                "status": res.status,
                "output": res.output[:500],
            })
            if res.is_success:
                findings.append(f"Test run completed:\n{res.output[:800]}")
                overall_evidence["test_runner"] = res.evidence
            else:
                findings.append(f"Test run reported failures or error: {res.error or res.output[:400]}")

        # 2. AST structural search flow
        target_file_match = re.search(r"([\w\-/\\]+\.py)", contract.instructions)
        if target_file_match:
            target_py = target_file_match.group(1)
            ast_res = self.call_tool(
                tool_registry,
                "code_ast_search",
                {"path": target_py},
                context=context,
            )
            side_effects.append({"tool_name": "code_ast_search", "status": ast_res.status})
            if ast_res.is_success:
                findings.append(ast_res.output)
                overall_evidence["ast"] = ast_res.evidence
            else:
                findings.append(f"AST search error: {ast_res.error}")

            # Explain if asked
            if any(kw in text for kw in ("explain", "complexity", "metrics", "loc", "structure")):
                exp_res = self.call_tool(
                    tool_registry,
                    "code_explain",
                    {"path": target_py},
                    context=context,
                )
                if exp_res.is_success:
                    findings.append(exp_res.output)
                    overall_evidence["metrics"] = exp_res.evidence

        # 3. Directory listing fallback if no specific file was identified
        if not findings:
            list_res = self.call_tool(
                tool_registry,
                "list_directory",
                {"path": "."},
                context=context,
            )
            findings.append(f"Workspace file inventory:\n{list_res.output}")

        summary = "\n\n".join(findings) or "Code intelligence operations concluded."
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return AgentOutputContract(
            subtask_id=contract.subtask_id,
            agent_name=self.name,
            status="success",
            summary=summary,
            deliverables=deliverables,
            side_effects=side_effects,
            evidence=overall_evidence,
            execution_time_ms=duration_ms,
        )
