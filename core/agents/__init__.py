"""core/agents — Specialized Functional Agent Library for OPTIMUS.

Provides:
- Developer Agent (8-phase engineering lifecycle: understand, inspect, plan, approve, implement, execute, verify, report).
- Code Intelligence Agent (code structure, AST search, metrics, testing).
- System Diagnosis Agent (CPU, RAM, GPU, storage, processes).
- Data Intelligence Agent (tabular CSV/JSON, transformations, statistics).
- Research Agent (web queries, citations, source verification).
- Execution Agent (sandboxed commands, scripts, process management).
- Verification Agent (independent postconditions, physical files, ledger recording).
- Typed Input/Output contracts and failure policies.
"""

from core.agents.base import SpecializedAgent
from core.agents.code_intel import (
    CodeIntelligenceAgent,
    create_code_intelligence_profile,
)
from core.agents.contracts import (
    AgentFailurePolicy,
    AgentInputContract,
    AgentOutputContract,
)
from core.agents.data_intel import (
    DataIntelligenceAgent,
    create_data_intelligence_profile,
)
from core.agents.developer import DeveloperAgent, create_developer_profile
from core.agents.executor import ExecutionAgent, create_execution_profile
from core.agents.registry_ext import (
    get_specialized_agent,
    list_specialized_agents,
    register_specialized_agents,
)
from core.agents.researcher import ResearchAgent, create_researcher_profile
from core.agents.sys_diag import SystemDiagnosisAgent, create_system_diagnosis_profile
from core.agents.verifier import VerificationAgent, create_verification_agent_profile

__all__ = [
    "AgentFailurePolicy",
    "AgentInputContract",
    "AgentOutputContract",
    "CodeIntelligenceAgent",
    "DataIntelligenceAgent",
    "DeveloperAgent",
    "ExecutionAgent",
    "ResearchAgent",
    "SpecializedAgent",
    "SystemDiagnosisAgent",
    "VerificationAgent",
    "create_code_intelligence_profile",
    "create_data_intelligence_profile",
    "create_developer_profile",
    "create_execution_profile",
    "create_researcher_profile",
    "create_system_diagnosis_profile",
    "create_verification_agent_profile",
    "get_specialized_agent",
    "list_specialized_agents",
    "register_specialized_agents",
]
