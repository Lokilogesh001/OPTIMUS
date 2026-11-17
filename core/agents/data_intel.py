"""core/agents/data_intel.py — Data Intelligence Specialized Agent for OPTIMUS.

Responsibilities:
- CSV, JSON, and tabular data processing.
- Data cleaning, transformation, and column selection.
- Statistical analysis (mean, median, range, stddev, null counts).
- Structured tabular report and artifact generation.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.agents.base import SpecializedAgent
from core.agents.contracts import AgentInputContract, AgentOutputContract
from core.orchestrator.agents import AgentCapability, AgentProfile
from core.orchestrator.tools import CapabilityTier

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry


def create_data_intelligence_profile() -> AgentProfile:
    """Construct the canonical Data Intelligence Agent profile."""
    return AgentProfile(
        name="data_intelligence",
        title="Data Intelligence Specialist",
        description="Processes tabular datasets (CSV/JSON), transforms data, and computes statistical metrics.",
        capability=AgentCapability(
            domain="data",
            allowed_tools=[
                "data_read_tabular",
                "data_transform",
                "data_compute_statistics",
                "calculator",
                "read_file",
                "write_file",
            ],
            allowed_tiers=[CapabilityTier.READ_ONLY, CapabilityTier.WORKSPACE_WRITE],
            max_turns=8,
            timeout_seconds=180.0,
        ),
        instructions=(
            "You are a data engineer and quantitative analyst. Process datasets accurately, "
            "verify row counts and null values, compute statistics, and generate verified data artifacts."
        ),
    )


class DataIntelligenceAgent(SpecializedAgent):
    """Specialized agent performing tabular dataset inspection, statistical calculations, and transformations."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_data_intelligence_profile())

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

        text = (contract.goal + " " + contract.instructions).lower()
        file_match = re.search(r"([\w\-/\\]+\.(?:csv|tsv|json))", contract.instructions)

        if file_match:
            data_file = file_match.group(1)

            # 1. Inspect & Read dataset
            read_res = self.call_tool(
                tool_registry,
                "data_read_tabular",
                {"path": data_file, "max_rows": 5},
                context=context,
            )
            side_effects.append({
                "tool_name": "data_read_tabular",
                "status": read_res.status,
                "output": read_res.output[:200],
            })
            if read_res.is_success:
                reports.append(read_res.output)
                evidence["tabular_meta"] = read_res.evidence

            # 2. Compute statistics if requested or suitable
            if any(kw in text for kw in ("stat", "metric", "mean", "median", "summary", "analysis")):
                stats_res = self.call_tool(
                    tool_registry,
                    "data_compute_statistics",
                    {"path": data_file},
                    context=context,
                )
                side_effects.append({
                    "tool_name": "data_compute_statistics",
                    "status": stats_res.status,
                    "output": stats_res.output[:200],
                })
                if stats_res.is_success:
                    reports.append(stats_res.output)
                    evidence["stats"] = stats_res.evidence

            # 3. Transform data if requested
            if any(kw in text for kw in ("transform", "filter", "subset", "export", "clean")):
                out_match = re.search(r"(?:to|into|as)\s+([\w\-/\\]+\.(?:csv|json))", contract.instructions)
                out_path = out_match.group(1) if out_match else f"transformed_{Path(data_file).name}"

                tr_res = self.call_tool(
                    tool_registry,
                    "data_transform",
                    {"path": data_file, "output_path": out_path},
                    context=context,
                )
                side_effects.append({
                    "tool_name": "data_transform",
                    "status": tr_res.status,
                    "evidence": tr_res.evidence,
                })
                if tr_res.is_success:
                    reports.append(tr_res.output)
                    deliverables.append({
                        "path": tr_res.evidence.get("path"),
                        "bytes": tr_res.evidence.get("bytes", 0),
                        "sha256": tr_res.evidence.get("sha256", ""),
                    })

        # Calculation fallback
        math_match = re.search(r"(\d+[\s\*\+\-\/\^]+\d+)", text)
        if math_match:
            calc_res = self.call_tool(tool_registry, "calculator", {"expression": math_match.group(1)})
            if calc_res.is_success:
                reports.append(f"Calculation: {math_match.group(1)} = {calc_res.output}")

        summary = "\n\n".join(reports) or "Data intelligence operations completed."
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
