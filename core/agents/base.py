"""core/agents/base.py — Abstract Base Class for Functional Specialized Agents.

Defines:
- SpecializedAgent: Base class with typed contract execution, tool permission scoping,
  and anti-peer-to-peer delegation enforcement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from core.agents.contracts import AgentInputContract, AgentOutputContract
from core.orchestrator.agents import AgentProfile

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry


class SpecializedAgent(ABC):
    """Abstract base class for all functional domain specialized agents in OPTIMUS."""

    name: str
    profile: AgentProfile

    def __init__(self, profile: AgentProfile) -> None:
        self.profile = profile
        self.name = profile.name

    @abstractmethod
    def execute(
        self,
        contract: AgentInputContract,
        tool_registry: ToolRegistry,
    ) -> AgentOutputContract:
        """Execute a subtask within declared capabilities using real tool infrastructure."""
        raise NotImplementedError

    def call_tool(
        self,
        tool_registry: ToolRegistry,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Invoke a tool safely through the ToolRegistry enforcing agent permissions."""
        return tool_registry.execute(
            tool_name=tool_name,
            arguments=arguments,
            caller_agent=self.name,
            allowed_tools=self.profile.allowed_tools,
            allowed_tiers=self.profile.allowed_tiers,
            context=context,
        )
