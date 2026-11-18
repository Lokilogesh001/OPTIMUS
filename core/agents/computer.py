"""core/agents/computer.py — Computer Use & Visual Interaction Specialized Agent.

Phase 6 of Milestone 9: Computer Use & Visual Intelligence.
Perceives desktop state, inspects application interfaces, resolves visual targets,
and conducts safe closed-loop actuation lifecycles.
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


def create_computer_agent_profile() -> AgentProfile:
    """Construct the canonical Computer Agent profile."""
    return AgentProfile(
        name="computer_agent",
        title="Visual Computer Use & Desktop Interaction Specialist",
        description="Perceives the desktop, analyzes application interfaces, resolves UI elements with confidence scoring, and executes safe bounded computer interactions.",
        capability=AgentCapability(
            domain="computer_use",
            allowed_tools=[
                "computer_observe",
                "computer_find_element",
                "computer_interact",
                "computer_type_text",
                "computer_hotkey",
                "computer_switch_window",
                "computer_verify_state",
                "open_app",
                "read_file",
            ],
            allowed_tiers=[
                CapabilityTier.READ_ONLY,
                CapabilityTier.EXECUTION,
                CapabilityTier.SYSTEM_MUTATING,
            ],
            max_turns=10,
            timeout_seconds=180.0,
        ),
        instructions=(
            "You are OPTIMUS's Visual Computer Interaction Specialist. "
            "Always follow the 7-step lifecycle: OBSERVE -> INTERPRET -> PLAN -> AUTHORIZE -> ACT -> OBSERVE AGAIN -> VERIFY. "
            "Never hallucinate coordinates. Always anchor clicks to verified UI elements or exact screen bounds. "
            "Verify postconditions before declaring completion."
        ),
    )


class ComputerAgent(SpecializedAgent):
    """Specialized agent orchestrating visual computer interactions and desktop workflows."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_computer_agent_profile())

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

        objective = contract.goal.strip()

        # Step 1: Initial Observation
        obs_res = tool_registry.execute(
            "computer_observe",
            arguments={"capture_screenshot": False},
            caller_agent=self.name,
            context=context,
        )
        reports.append(obs_res.output)
        if obs_res.evidence:
            evidence["initial_observation"] = obs_res.evidence

        # Check if the objective specifies an app to open or switch to
        open_match = re.search(r"(?:open|launch|start|switch to)\s+([a-zA-Z0-9_\-\.\s]+?)(?:$|\band\b|\bto\b)", objective, re.I)
        target_app = open_match.group(1).strip() if open_match else ""

        if target_app and target_app.lower() not in ("desktop", "computer", "screen"):
            sw_res = tool_registry.execute(
                "computer_switch_window",
                arguments={"window_title": target_app},
                caller_agent=self.name,
                context=context,
            )
            reports.append(sw_res.output)
            if sw_res.evidence:
                evidence["window_switch"] = sw_res.evidence
            side_effects.append({"action": "switch_window", "target": target_app})

        # Check for typing text request
        type_match = re.search(r"(?:type|write|enter)\s+['\"](.*?)['\"]", objective, re.I)
        if type_match:
            text_to_type = type_match.group(1)
            type_res = tool_registry.execute(
                "computer_type_text",
                arguments={"text": text_to_type},
                caller_agent=self.name,
                context=context,
            )
            reports.append(type_res.output)
            if type_res.evidence:
                evidence["typing"] = type_res.evidence
            side_effects.append({"action": "type_text", "status": type_res.status})

        # Check for hotkey request (e.g. ctrl+s)
        hotkey_match = re.search(r"(?:press|hotkey)\s+(ctrl\+[a-z0-9]|alt\+[a-z0-9]|enter|esc)", objective, re.I)
        if hotkey_match:
            raw_hk = hotkey_match.group(1).lower()
            keys = raw_hk.split("+")
            hk_res = tool_registry.execute(
                "computer_hotkey",
                arguments={"keys": keys},
                caller_agent=self.name,
                context=context,
            )
            reports.append(hk_res.output)
            side_effects.append({"action": "hotkey", "keys": keys})

        # Check for clicking an element
        click_match = re.search(r"(?:click|press button)\s+['\"]?([a-zA-Z0-9_\-\s]+?)['\"]?(?:\s+button)?(?:$|\band\b)", objective, re.I)
        if click_match and not target_app.lower().startswith(click_match.group(1).lower()):
            btn_name = click_match.group(1).strip()
            if btn_name.lower() not in ("the", "a", "button"):
                click_res = tool_registry.execute(
                    "computer_interact",
                    arguments={"action_type": "click", "target_name": btn_name, "target_role": "button"},
                    caller_agent=self.name,
                    context=context,
                )
                reports.append(click_res.output)
                if click_res.evidence:
                    evidence["click_interaction"] = click_res.evidence
                side_effects.append({"action": "click", "target": btn_name})

        # Final Verification
        verify_res = tool_registry.execute(
            "computer_verify_state",
            arguments={},
            caller_agent=self.name,
            context=context,
        )
        reports.append(verify_res.output)
        if verify_res.evidence:
            evidence["final_verification"] = verify_res.evidence

        duration = (time.perf_counter() - start_time) * 1000.0
        return AgentOutputContract(
            subtask_id=contract.subtask_id,
            agent_name=self.name,
            status="success",
            summary=f"Visual computer interaction completed for objective: '{objective}'.\n" + "\n\n".join(reports),
            deliverables=[{"type": "observation_report", "text": "\n\n".join(reports)}],
            evidence=evidence,
            side_effects=side_effects,
            execution_time_ms=duration,
        )
