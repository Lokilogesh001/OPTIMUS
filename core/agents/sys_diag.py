"""core/agents/sys_diag.py — System Diagnosis Specialized Agent for OPTIMUS.

Responsibilities:
- Hardware, OS, and platform diagnostics.
- CPU, RAM, storage, and GPU utilization metrics.
- Running process tree diagnostics.
- Environment health evaluation.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from core.agents.base import SpecializedAgent
from core.agents.contracts import AgentInputContract, AgentOutputContract
from core.orchestrator.agents import AgentCapability, AgentProfile
from core.orchestrator.tools import CapabilityTier

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry


def create_system_diagnosis_profile() -> AgentProfile:
    """Construct the canonical System Diagnosis Agent profile."""
    return AgentProfile(
        name="system_diagnosis",
        title="System Diagnosis Specialist",
        description="Inspects hardware, monitors CPU/RAM/GPU/disk, and diagnoses running processes.",
        capability=AgentCapability(
            domain="system",
            allowed_tools=[
                "system_info",
                "cpu_status",
                "memory_status",
                "disk_status",
                "gpu_status",
                "process_diagnostics",
                "ollama_status",
                "check_ollama",
                "python_version",
                "computer_audit",
            ],
            allowed_tiers=[CapabilityTier.READ_ONLY],
            max_turns=6,
            timeout_seconds=120.0,
        ),
        instructions=(
            "You are an infrastructure and systems diagnostic specialist. Measure CPU, RAM, "
            "disk, GPU, and process metrics accurately. Report diagnostic facts without speculation."
        ),
    )


class SystemDiagnosisAgent(SpecializedAgent):
    """Specialized agent performing system health monitoring and resource diagnostics."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_system_diagnosis_profile())

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

        text = (contract.goal + " " + contract.instructions).lower()

        # Check target metrics requested or run full sweep
        check_audit = any(kw in text for kw in ("audit", "computer audit", "system audit", "complete audit"))
        if check_audit:
            audit_res = self.call_tool(tool_registry, "computer_audit", {}, context=context)
            side_effects.append({"tool_name": "computer_audit", "status": audit_res.status, "output": audit_res.output[:200]})
            if audit_res.is_success:
                return AgentOutputContract(
                    subtask_id=contract.subtask_id,
                    agent_name=self.name,
                    status="success",
                    summary=audit_res.output,
                    deliverables=[],
                    side_effects=side_effects,
                    evidence=audit_res.evidence,
                    duration_ms=(time.perf_counter() - start_time) * 1000.0,
                )

        check_all = any(kw in text for kw in ("all", "sweep", "full", "health", "system", "diagnos"))
        check_cpu = check_all or any(kw in text for kw in ("cpu", "processor", "load"))
        check_mem = check_all or any(kw in text for kw in ("ram", "memory", "swap"))
        check_disk = check_all or any(kw in text for kw in ("disk", "storage", "drive", "space"))
        check_gpu = check_all or any(kw in text for kw in ("gpu", "graphics", "vram"))
        check_proc = any(kw in text for kw in ("process", "pid", "top", "task"))
        check_ollama = any(kw in text for kw in ("ollama", "local llm", "local model"))
        check_py = any(kw in text for kw in ("python", "python version", "dev tool", "environment"))

        # 1. System Info
        sys_res = self.call_tool(tool_registry, "system_info", {}, context=context)
        side_effects.append({"tool_name": "system_info", "status": sys_res.status, "output": sys_res.output[:200]})
        if sys_res.is_success:
            reports.append(sys_res.output)
            evidence["system_info"] = sys_res.evidence

        # 2. CPU
        if check_cpu:
            cpu_res = self.call_tool(tool_registry, "cpu_status", {}, context=context)
            side_effects.append({"tool_name": "cpu_status", "status": cpu_res.status, "output": cpu_res.output[:200]})
            if cpu_res.is_success:
                reports.append(cpu_res.output)
                evidence["cpu"] = cpu_res.evidence

        # 3. RAM Memory
        if check_mem:
            mem_res = self.call_tool(tool_registry, "memory_status", {}, context=context)
            side_effects.append({"tool_name": "memory_status", "status": mem_res.status, "output": mem_res.output[:200]})
            if mem_res.is_success:
                reports.append(mem_res.output)
                evidence["memory"] = mem_res.evidence

        # 4. Storage Disk
        if check_disk:
            disk_res = self.call_tool(tool_registry, "disk_status", {}, context=context)
            side_effects.append({"tool_name": "disk_status", "status": disk_res.status, "output": disk_res.output[:200]})
            if disk_res.is_success:
                reports.append(disk_res.output)
                evidence["disk"] = disk_res.evidence

        # 5. GPU
        if check_gpu:
            gpu_res = self.call_tool(tool_registry, "gpu_status", {}, context=context)
            side_effects.append({"tool_name": "gpu_status", "status": gpu_res.status, "output": gpu_res.output[:200]})
            if gpu_res.is_success:
                reports.append(gpu_res.output)
                evidence["gpu"] = gpu_res.evidence

        # 6. Process Diagnostics
        if check_proc:
            proc_res = self.call_tool(tool_registry, "process_diagnostics", {"top_n": 8}, context=context)
            side_effects.append({"tool_name": "process_diagnostics", "status": proc_res.status, "output": proc_res.output[:200]})
            if proc_res.is_success:
                reports.append(proc_res.output)
                evidence["processes"] = proc_res.evidence

        # 7. Ollama Status
        if check_ollama:
            ollama_res = self.call_tool(tool_registry, "ollama_status", {}, context=context)
            side_effects.append({"tool_name": "ollama_status", "status": ollama_res.status, "output": ollama_res.output[:200]})
            if ollama_res.is_success:
                reports.append(ollama_res.output)
                evidence["ollama"] = ollama_res.evidence

        # 8. Python Version & Environment
        if check_py:
            py_res = self.call_tool(tool_registry, "python_version", {}, context=context)
            side_effects.append({"tool_name": "python_version", "status": py_res.status, "output": py_res.output[:200]})
            if py_res.is_success:
                reports.append(py_res.output)
                evidence["python_environment"] = py_res.evidence

        summary = "\n\n".join(reports)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return AgentOutputContract(
            subtask_id=contract.subtask_id,
            agent_name=self.name,
            status="success",
            summary=summary,
            deliverables=[],
            side_effects=side_effects,
            evidence=evidence,
            execution_time_ms=duration_ms,
        )
