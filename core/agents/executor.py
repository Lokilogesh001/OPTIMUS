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
                "open_app",
                "open_directory",
                "open_folder",
                "open_in_explorer",
                "read_file",
                "write_file",
                "create_directory",
                "create_folder",
                "copy_file",
                "move_file",
                "batch_move_files",
                "find_files",
                "search_files",
                "delete_file",
                "largest_files",
                "list_directory",
                "computer_observe",
                "computer_find_element",
                "computer_interact",
                "computer_type_text",
                "computer_hotkey",
                "computer_switch_window",
                "computer_verify_state",
                "enumerate_windows",
                "focus_window",
                "enumerate_browser_tabs",
                "switch_browser_tab",
                "capture_visual_state",
                "explain_desktop_state",
            ],
            allowed_tiers=[
                CapabilityTier.READ_ONLY,
                CapabilityTier.WORKSPACE_WRITE,
                CapabilityTier.EXECUTION,
                CapabilityTier.SYSTEM_MUTATING,
            ],
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

        # 0a. Application launch detection
        low_text = text.lower()
        app_match = re.search(r"(?:open|launch|start)\s+(?:application\s+|app\s+)?([a-zA-Z0-9_\-\.]+)", low_text)
        if app_match and any(kw in low_text for kw in ("notepad", "calc", "calculator", "terminal", "cmd", "explorer", "code", "app")):
            target_app = app_match.group(1).strip()
            res = self.call_tool(
                tool_registry,
                "open_app",
                {"app_name": target_app},
                context=context,
            )
            side_effects.append({
                "tool_name": "open_app",
                "status": res.status,
                "output": res.output[:500],
                "error": res.error,
            })
            reports.append(res.output if res.is_success else f"Launch Error: {res.error}")
            evidence["open_app"] = res.evidence
            status = "success" if res.is_success else "failed"

        # 0b. Open directory / folder detection
        elif any(kw in low_text for kw in ("open downloads", "open folder", "open directory", "explore folder", "open desktop")):
            folder_target = "downloads"
            if "desktop" in low_text:
                folder_target = "desktop"
            elif "documents" in low_text:
                folder_target = "documents"
            elif "workspace" in low_text:
                folder_target = "workspace"
            else:
                m = re.search(r"(?:open|explore)\s+(?:folder\s+|directory\s+)?([a-zA-Z0-9_\-\.\/\\]+)", low_text)
                if m:
                    folder_target = m.group(1)

            res = self.call_tool(
                tool_registry,
                "open_directory",
                {"path": folder_target},
                context=context,
            )
            side_effects.append({
                "tool_name": "open_directory",
                "status": res.status,
                "output": res.output[:500],
                "error": res.error,
            })
            reports.append(res.output if res.is_success else f"Open Directory Error: {res.error}")
            evidence["open_directory"] = res.evidence
            status = "success" if res.is_success else "failed"

        # 1. Script runner detection
        elif "script" in text.lower() and (script_match := re.search(r"([\w\-/\\]+\.py)", text)):
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
