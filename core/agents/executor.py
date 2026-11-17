"""core/agents/executor.py — Execution Specialized Agent for OPTIMUS.

Responsibilities:
- Controlled sandboxed command execution.
- Script execution and process lifecycle management.
- Timeout handling and active process termination.
- Execution outcome and exit status reporting.
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


def create_execution_profile() -> AgentProfile:
    """Construct the canonical Execution Agent profile."""
    return AgentProfile(
        name="execution_agent",
        title="Command & Process Execution Specialist",
        description="Executes sandboxed CLI commands, runs scripts, and manages process lifecycles safely.",
        capability=AgentCapability(
            domain="execution",
            allowed_tools=[
                "execute_command",
                "script_runner",
                "process_status",
                "process_kill",
                "read_file",
                "list_directory",
            ],
            allowed_tiers=[CapabilityTier.READ_ONLY, CapabilityTier.EXECUTION],
            max_turns=6,
            timeout_seconds=120.0,
        ),
        instructions=(
            "You are a sandboxed execution specialist. Execute commands safely within workspace boundaries. "
            "Never execute destructive, privilege-escalating, or prohibited commands. Report exact exit codes."
        ),
    )


class ExecutionAgent(SpecializedAgent):
    """Specialized agent coordinating controlled command execution and process management."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_execution_profile())

    def execute(
        self,
        contract: AgentInputContract,
        tool_registry: ToolRegistry,
    ) -> AgentOutputContract:
        start_time = time.perf_counter()
        context = {"allowed_roots": [contract.workspace_root]}
        side_effects: list[dict[str, Any]] = []
        reports: list[str] = []
        evidence: dict[str, Any] = {}

        text = contract.instructions or contract.goal

        # 1. Script runner detection
        script_match = re.search(r"([\w\-/\\]+\.py)", text)
        if "script" in text.lower() and script_match:
            script_path = script_match.group(1)
            res = self.call_tool(
                tool_registry,
                "script_runner",
                {"script_path": script_path, "timeout": contract.timeout_seconds},
                context=context,
            )
            side_effects.append({
                "tool_name": "script_runner",
                "status": res.status,
                "output": res.output[:500],
            })
            reports.append(res.output)
            evidence["script"] = res.evidence
            status = "success" if res.is_success else "failed"

        # 2. General command execution
        else:
            # Extract raw command if enclosed in quotes or backticks, or strip prefix
            cmd_match = re.search(r"`([^`]+)`", text)
            cmd = cmd_match.group(1) if cmd_match else text

            res = self.call_tool(
                tool_registry,
                "execute_command",
                {"command": cmd, "timeout": contract.timeout_seconds},
                context=context,
            )
            side_effects.append({
                "tool_name": "execute_command",
                "status": res.status,
                "output": res.output[:500],
                "error": res.error,
            })
            reports.append(res.output if res.is_success else f"Command Error: {res.error}")
            evidence["command"] = res.evidence
            status = "success" if res.is_success else "failed"

        summary = "\n\n".join(reports)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return AgentOutputContract(
            subtask_id=contract.subtask_id,
            agent_name=self.name,
            status=status,
            summary=summary,
            deliverables=[],
            side_effects=side_effects,
            evidence=evidence,
            execution_time_ms=duration_ms,
            error=None if status == "success" else summary,
        )
