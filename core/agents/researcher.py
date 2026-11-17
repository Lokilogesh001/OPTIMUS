"""core/agents/researcher.py — Research Specialized Agent for OPTIMUS.

Responsibilities:
- Information gathering through authorized search tools.
- Citation tracking, source extraction, and factual verification.
- Evidence-based multi-source summarization.
- Research artifact generation.
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


def create_researcher_profile() -> AgentProfile:
    """Construct the canonical Research Agent profile."""
    return AgentProfile(
        name="researcher",
        title="Research Specialist",
        description="Gathers verified empirical leads, tracks citations, and creates evidence-backed summaries.",
        capability=AgentCapability(
            domain="research",
            allowed_tools=[
                "search_web",
                "fetch_page",
                "extract_citations",
                "read_file",
                "list_directory",
                "calculate",
                "calculator",
            ],
            allowed_tiers=[
                CapabilityTier.READ_ONLY,
                CapabilityTier.NETWORK_EXTERNAL,
            ],
            max_turns=8,
            timeout_seconds=180.0,
        ),
        instructions=(
            "You are an empirical research specialist. Every sentence must trace to an inspected source. "
            "Never invent quotes or citations. Extract verifiable sources and summarize facts concisely."
        ),
    )


class ResearchAgent(SpecializedAgent):
    """Specialized agent performing empirical research, web query execution, and source attribution."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_researcher_profile())

    def execute(
        self,
        contract: AgentInputContract,
        tool_registry: ToolRegistry,
    ) -> AgentOutputContract:
        start_time = time.perf_counter()
        context = {"allowed_roots": [contract.workspace_root]}
        side_effects: list[dict[str, Any]] = []
        deliverables: list[dict[str, Any]] = []
        reports: list[str] = []
        evidence: dict[str, Any] = {}

        # 1. Web search lead gathering
        query = contract.instructions or contract.goal
        search_res = self.call_tool(tool_registry, "search_web", {"query": query}, context=context)
        side_effects.append({"tool_name": "search_web", "status": search_res.status, "output": search_res.output[:200]})
        if search_res.is_success:
            reports.append(search_res.output)
            evidence["search"] = search_res.evidence

        # 2. Extract citations
        cite_res = self.call_tool(tool_registry, "extract_citations", {"text": search_res.output}, context=context)
        side_effects.append({"tool_name": "extract_citations", "status": cite_res.status, "output": cite_res.output[:200]})
        if cite_res.is_success:
            reports.append(cite_res.output)
            evidence["citations"] = cite_res.evidence

        # 3. Check for artifact output request (if authorized)
        if "write_file" in self.profile.allowed_tools:
            file_match = re.search(r"(?:save|write|to)\s+([\w\-/\\]+\.(?:md|txt))", contract.instructions)
            if file_match:
                out_file = file_match.group(1)
                content = f"# Research Report: {query}\n\n" + "\n\n".join(reports)
                write_res = self.call_tool(
                    tool_registry,
                    "write_file",
                    {"path": out_file, "content": content},
                    context=context,
                )
                side_effects.append({"tool_name": "write_file", "status": write_res.status, "evidence": write_res.evidence})
                if write_res.is_success:
                    deliverables.append({
                        "path": write_res.evidence.get("path"),
                        "bytes": write_res.evidence.get("bytes", 0),
                        "sha256": write_res.evidence.get("sha256", ""),
                    })

        summary = "\n\n".join(reports)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return AgentOutputContract(
            subtask_id=contract.subtask_id,
            agent_name=self.name,
            status="success",
            summary=summary,
            deliverables=deliverables,
            side_effects=side_effects,
            evidence=evidence,
            execution_time_ms=duration_ms,
        )
