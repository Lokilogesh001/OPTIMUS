"""core/agents/registry_ext.py — Agent Library Registration & Dispatch for OPTIMUS.

Registers the functional specialized agent library into the central AgentRegistry
and maintains typed instance dispatch for the 7 canonical specialized agents:
1. Research Agent (researcher / research_agent)
2. Developer Agent (developer / developer_agent)
3. System Agent (system_agent / system_diagnosis)
4. File Agent (file_agent / execution)
5. Data Agent (data_agent / data_intel)
6. Verification Agent (verification_agent / verifier)
7. Planning Agent (planning_agent / planner)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from core.agents.base import SpecializedAgent
from core.agents.code_intel import CodeIntelligenceAgent
from core.agents.data_intel import DataIntelligenceAgent
from core.agents.developer import DeveloperAgent, create_developer_profile
from core.agents.executor import ExecutionAgent, create_execution_profile
from core.agents.planner import PlanningAgent, create_planning_agent_profile
from core.agents.researcher import ResearchAgent, create_researcher_profile
from core.agents.sys_diag import SystemDiagnosisAgent
from core.agents.verifier import VerificationAgent, create_verification_agent_profile

if TYPE_CHECKING:
    from core.orchestrator.agents import AgentRegistry

_lock = threading.RLock()
_SPECIALIZED_AGENTS: dict[str, SpecializedAgent] = {}


def register_specialized_agents(registry: AgentRegistry) -> None:
    """Register all canonical specialized agent profiles and runners into AgentRegistry."""
    with _lock:
        # 1. Research Agent (researcher / research_agent)
        research_agent = ResearchAgent()
        _SPECIALIZED_AGENTS[research_agent.name] = research_agent
        registry.register(research_agent.profile)
        alias_research = create_researcher_profile()
        alias_research.name = "research_agent"
        registry.register(alias_research)
        _SPECIALIZED_AGENTS["research_agent"] = research_agent
        _SPECIALIZED_AGENTS["researcher"] = research_agent

        # 2. Developer Agent (developer / developer_agent)
        dev_agent = DeveloperAgent()
        _SPECIALIZED_AGENTS[dev_agent.name] = dev_agent
        registry.register(dev_agent.profile)
        alias_dev = create_developer_profile()
        alias_dev.name = "developer"
        registry.register(alias_dev)
        _SPECIALIZED_AGENTS["developer_agent"] = dev_agent
        _SPECIALIZED_AGENTS["developer"] = dev_agent

        # 3. System Agent (system_agent / system_diagnosis)
        sys_agent = SystemDiagnosisAgent()
        _SPECIALIZED_AGENTS[sys_agent.name] = sys_agent
        registry.register(sys_agent.profile)
        alias_sys = sys_agent.profile
        alias_sys_prof = create_developer_profile()  # dummy clone
        alias_sys_prof.name = "system_agent"
        alias_sys_prof.title = "System & Hardware Diagnosis Agent"
        alias_sys_prof.description = sys_agent.profile.description
        alias_sys_prof.capability = sys_agent.profile.capability
        registry.register(alias_sys_prof)
        _SPECIALIZED_AGENTS["system_agent"] = sys_agent
        _SPECIALIZED_AGENTS["system_diagnosis"] = sys_agent

        # 4. File Agent (file_agent / execution)
        exec_agent = ExecutionAgent()
        _SPECIALIZED_AGENTS[exec_agent.name] = exec_agent
        registry.register(exec_agent.profile)
        alias_file = create_execution_profile()
        alias_file.name = "file_agent"
        alias_file.title = "Filesystem & Execution Agent"
        registry.register(alias_file)
        _SPECIALIZED_AGENTS["file_agent"] = exec_agent
        _SPECIALIZED_AGENTS["execution"] = exec_agent
        _SPECIALIZED_AGENTS["execution_agent"] = exec_agent

        # 5. Data Agent (data_agent / data_intel)
        data_agent = DataIntelligenceAgent()
        _SPECIALIZED_AGENTS[data_agent.name] = data_agent
        registry.register(data_agent.profile)
        alias_data = data_agent.profile
        alias_data_prof = create_developer_profile()
        alias_data_prof.name = "data_agent"
        alias_data_prof.title = "Data Intelligence Agent"
        alias_data_prof.description = data_agent.profile.description
        alias_data_prof.capability = data_agent.profile.capability
        registry.register(alias_data_prof)
        _SPECIALIZED_AGENTS["data_agent"] = data_agent
        _SPECIALIZED_AGENTS["data_intel"] = data_agent

        # 6. Verification Agent (verification_agent / verifier)
        verif_agent = VerificationAgent()
        _SPECIALIZED_AGENTS[verif_agent.name] = verif_agent
        registry.register(verif_agent.profile)
        alias_verif = create_verification_agent_profile()
        alias_verif.name = "verifier"
        registry.register(alias_verif)
        _SPECIALIZED_AGENTS["verifier"] = verif_agent
        _SPECIALIZED_AGENTS["verification_agent"] = verif_agent

        # 7. Planning Agent (planning_agent / planner)
        plan_agent = PlanningAgent()
        _SPECIALIZED_AGENTS[plan_agent.name] = plan_agent
        registry.register(plan_agent.profile)
        alias_plan = create_planning_agent_profile()
        alias_plan.name = "planner"
        registry.register(alias_plan)
        _SPECIALIZED_AGENTS["planner"] = plan_agent
        _SPECIALIZED_AGENTS["planning_agent"] = plan_agent

        # Code Intelligence Agent (helper)
        code_agent = CodeIntelligenceAgent()
        _SPECIALIZED_AGENTS[code_agent.name] = code_agent
        registry.register(code_agent.profile)


def get_specialized_agent(name: str) -> SpecializedAgent | None:
    """Retrieve an instantiated functional specialized agent by name."""
    with _lock:
        return _SPECIALIZED_AGENTS.get(name.lower())


def list_specialized_agents() -> list[SpecializedAgent]:
    """Return all unique instantiated functional specialized agents."""
    with _lock:
        seen = set()
        res = []
        for a in _SPECIALIZED_AGENTS.values():
            if id(a) not in seen:
                seen.add(id(a))
                res.append(a)
        return res
