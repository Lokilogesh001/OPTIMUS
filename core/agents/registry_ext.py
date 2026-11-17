"""core/agents/registry_ext.py — Agent Library Registration & Dispatch for OPTIMUS.

Registers the functional specialized agent library into the central AgentRegistry
and maintains typed instance dispatch.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from core.agents.base import SpecializedAgent
from core.agents.code_intel import CodeIntelligenceAgent
from core.agents.data_intel import DataIntelligenceAgent
from core.agents.executor import ExecutionAgent, create_execution_profile
from core.agents.researcher import ResearchAgent, create_researcher_profile
from core.agents.sys_diag import SystemDiagnosisAgent

if TYPE_CHECKING:
    from core.orchestrator.agents import AgentRegistry

_lock = threading.RLock()
_SPECIALIZED_AGENTS: dict[str, SpecializedAgent] = {}


def register_specialized_agents(registry: AgentRegistry) -> None:
    """Register all 5 canonical specialized agent profiles and runners into AgentRegistry."""
    with _lock:
        # 1. Code Intelligence Agent
        code_agent = CodeIntelligenceAgent()
        _SPECIALIZED_AGENTS[code_agent.name] = code_agent
        registry.register(code_agent.profile)

        # 2. System Diagnosis Agent
        sys_agent = SystemDiagnosisAgent()
        _SPECIALIZED_AGENTS[sys_agent.name] = sys_agent
        registry.register(sys_agent.profile)

        # 3. Data Intelligence Agent
        data_agent = DataIntelligenceAgent()
        _SPECIALIZED_AGENTS[data_agent.name] = data_agent
        registry.register(data_agent.profile)

        # 4. Research Agent
        research_agent = ResearchAgent()
        _SPECIALIZED_AGENTS[research_agent.name] = research_agent
        registry.register(research_agent.profile)
        # Register alias 'research_agent'
        alias_research = create_researcher_profile()
        alias_research.name = "research_agent"
        registry.register(alias_research)
        _SPECIALIZED_AGENTS["research_agent"] = research_agent

        # 5. Execution Agent
        exec_agent = ExecutionAgent()
        _SPECIALIZED_AGENTS[exec_agent.name] = exec_agent
        registry.register(exec_agent.profile)
        # Register alias 'execution'
        alias_exec = create_execution_profile()
        alias_exec.name = "execution"
        registry.register(alias_exec)
        _SPECIALIZED_AGENTS["execution"] = exec_agent


def get_specialized_agent(name: str) -> SpecializedAgent | None:
    """Retrieve an instantiated functional specialized agent by name."""
    with _lock:
        return _SPECIALIZED_AGENTS.get(name.lower())


def list_specialized_agents() -> list[SpecializedAgent]:
    """Return all instantiated functional specialized agents."""
    with _lock:
        return list(_SPECIALIZED_AGENTS.values())
