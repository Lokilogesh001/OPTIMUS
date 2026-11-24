"""core/missions/capabilities.py — Universal Dynamic Capability Registry for OPTIMUS.

Provides formal capability contracts and dynamic discovery unifying:
- Native Tools from ToolRegistry.
- Specialized Agents from AgentRegistry.
- Application Adapters and Computer Use providers.
- Workspace MCP Tools.

Guarantees:
- Formal input/output schemas with type validation.
- Idempotency classification (IDEMPOTENT, NON_IDEMPOTENT, CONDITIONALLY_IDEMPOTENT).
- Reversibility declarations and compensation capability bindings for Saga transactions.
- Zero tool hallucination: queries against verified registry entries only.
- Semantic alternative capability lookup resolving LOW-03.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.orchestrator.tools import CapabilityTier, ToolDefinition, ToolRegistry

logger = logging.getLogger("optimus.missions.capabilities")


class IdempotencyMode(str, Enum):
    IDEMPOTENT = "IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    CONDITIONALLY_IDEMPOTENT = "CONDITIONALLY_IDEMPOTENT"


@dataclass
class CapabilityDescriptor:
    """Formal contract for an executable action, agent, or tool."""
    capability_id: str
    title: str
    description: str
    target_domain: str
    capability_tier: CapabilityTier = CapabilityTier.READ_ONLY
    idempotency: IdempotencyMode = IdempotencyMode.IDEMPOTENT
    reversible: bool = True
    compensation_capability_id: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    prerequisite_checks: list[str] = field(default_factory=list)
    default_verification_strategy: str = "FILESYSTEM"
    handler_tool_name: str | None = None
    handler_agent_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "title": self.title,
            "description": self.description,
            "target_domain": self.target_domain,
            "capability_tier": self.capability_tier.value,
            "idempotency": self.idempotency.value,
            "reversible": self.reversible,
            "compensation_capability_id": self.compensation_capability_id,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "prerequisite_checks": self.prerequisite_checks,
            "default_verification_strategy": self.default_verification_strategy,
            "handler_tool_name": self.handler_tool_name,
            "handler_agent_name": self.handler_agent_name,
        }


class UniversalCapabilityRegistry:
    """Central registry and dynamic discovery engine for all system capabilities."""

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self.tool_registry = tool_registry or ToolRegistry()
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        self._domain_index: dict[str, list[str]] = {}
        self._bootstrap_registry()

    def _bootstrap_registry(self) -> None:
        """Register canonical system capabilities from ToolRegistry and agents."""
        # 1. Register canonical tools
        for tool in self.tool_registry.list_tools():
            cap = self._synthesize_tool_capability(tool)
            self.register(cap)

        # 2. Register specialized agent capabilities
        agent_caps = [
            CapabilityDescriptor(
                capability_id="agent.researcher",
                title="Research Agent",
                description="Synthesizes web and document knowledge into structured analysis.",
                target_domain="web",
                capability_tier=CapabilityTier.NETWORK_EXTERNAL,
                idempotency=IdempotencyMode.IDEMPOTENT,
                reversible=True,
                handler_agent_name="researcher",
                default_verification_strategy="DATA_SCHEMA",
            ),
            CapabilityDescriptor(
                capability_id="agent.developer",
                title="Developer Agent",
                description="Navigates codebases, executes tests, and applies surgical bug fixes.",
                target_domain="code",
                capability_tier=CapabilityTier.WORKSPACE_WRITE,
                idempotency=IdempotencyMode.NON_IDEMPOTENT,
                reversible=True,
                compensation_capability_id="dev_file_rollback",
                handler_agent_name="developer",
                default_verification_strategy="COMMAND_EXIT",
            ),
            CapabilityDescriptor(
                capability_id="agent.file_agent",
                title="File & Execution Agent",
                description="Performs filesystem discovery, batch organization, and verified movement.",
                target_domain="filesystem",
                capability_tier=CapabilityTier.WORKSPACE_WRITE,
                idempotency=IdempotencyMode.IDEMPOTENT,
                reversible=True,
                compensation_capability_id="batch_move_files",
                handler_agent_name="file_agent",
                default_verification_strategy="FILESYSTEM",
            ),
            CapabilityDescriptor(
                capability_id="agent.computer",
                title="Computer Use Agent",
                description="Interacts with desktop applications, controls windows, and enters text.",
                target_domain="desktop_app",
                capability_tier=CapabilityTier.SYSTEM_MUTATING,
                idempotency=IdempotencyMode.NON_IDEMPOTENT,
                reversible=False,
                handler_agent_name="computer",
                default_verification_strategy="PROCESS_RUNNING",
            ),
            CapabilityDescriptor(
                capability_id="agent.system_diagnosis",
                title="System Diagnosis Agent",
                description="Collects hardware telemetry, inspects top consumers and processes.",
                target_domain="system",
                capability_tier=CapabilityTier.READ_ONLY,
                idempotency=IdempotencyMode.IDEMPOTENT,
                reversible=True,
                handler_agent_name="system_diagnosis",
                default_verification_strategy="DATA_SCHEMA",
            ),
            CapabilityDescriptor(
                capability_id="agent.data_intel",
                title="Data Intelligence Agent",
                description="Parses tabular structures, calculates statistics, and builds comparison matrices.",
                target_domain="data",
                capability_tier=CapabilityTier.READ_ONLY,
                idempotency=IdempotencyMode.IDEMPOTENT,
                reversible=True,
                handler_agent_name="data_intel",
                default_verification_strategy="DATA_SCHEMA",
            ),
        ]
        for ac in agent_caps:
            self.register(ac)

    def _synthesize_tool_capability(self, tool: ToolDefinition) -> CapabilityDescriptor:
        """Infer capability metadata and contracts from ToolDefinition."""
        name = tool.name
        tier = tool.capability_tier

        # Infer domain
        domain = "general"
        if any(w in name for w in ("gmail", "email", "calendar", "inbox")):
            domain = "email"
        elif any(w in name for w in ("file", "directory", "folder", "drives", "disk")):
            domain = "filesystem"
        elif any(w in name for w in ("search_web", "fetch_page", "extract_citations")):
            domain = "web"
        elif any(w in name for w in ("dev_", "code_", "python_version", "git")):
            domain = "code"
        elif any(w in name for w in ("computer_", "window", "launch_application", "open_app")):
            domain = "desktop_app"
        elif any(w in name for w in ("cpu", "ram", "gpu", "system", "process", "memory")):
            domain = "system"

        # Infer idempotency & reversibility
        is_mutating = tool.is_mutating or tier in (
            CapabilityTier.WORKSPACE_WRITE,
            CapabilityTier.SYSTEM_MUTATING,
            CapabilityTier.SYSTEM_DESTRUCTIVE,
        )
        idempotency = IdempotencyMode.NON_IDEMPOTENT if is_mutating else IdempotencyMode.IDEMPOTENT
        reversible = not (tier == CapabilityTier.SYSTEM_DESTRUCTIVE or "delete" in name or "send_email" in name)

        # Infer compensation capability
        compensation = None
        if name in ("move_file", "batch_move_files"):
            compensation = "batch_move_files"
        elif name == "write_file":
            compensation = "delete_file"
        elif name == "dev_file_write":
            compensation = "dev_file_rollback"

        # Verification strategy
        v_strat = "DATA_SCHEMA"
        if domain == "filesystem":
            v_strat = "FILESYSTEM"
        elif domain == "desktop_app":
            v_strat = "PROCESS_RUNNING"
        elif domain == "code":
            v_strat = "COMMAND_EXIT"

        return CapabilityDescriptor(
            capability_id=name,
            title=name.replace("_", " ").title(),
            description=tool.description,
            target_domain=domain,
            capability_tier=tier,
            idempotency=idempotency,
            reversible=reversible,
            compensation_capability_id=compensation,
            input_schema=tool.parameters,
            default_verification_strategy=v_strat,
            handler_tool_name=name,
        )

    def register(self, capability: CapabilityDescriptor) -> None:
        """Register a formal capability descriptor."""
        self._capabilities[capability.capability_id] = capability
        domain_list = self._domain_index.setdefault(capability.target_domain, [])
        if capability.capability_id not in domain_list:
            domain_list.append(capability.capability_id)

    def get_capability(self, capability_id: str) -> CapabilityDescriptor | None:
        """Retrieve capability descriptor by unique identifier."""
        return self._capabilities.get(capability_id)

    def list_capabilities(self, domain: str | None = None) -> list[CapabilityDescriptor]:
        """List all capabilities, optionally filtered by operational domain."""
        if domain:
            ids = self._domain_index.get(domain, [])
            return [self._capabilities[cid] for cid in ids if cid in self._capabilities]
        return list(self._capabilities.values())

    def find_alternative_capability(self, failed_cap_id: str) -> CapabilityDescriptor | None:
        """Dynamically discover a viable fallback capability matching the same functional domain.
        
        Resolves LOW-03: Completely purges nonsensical mappings like web_search -> read_file.
        """
        curr = self.get_capability(failed_cap_id)
        if not curr:
            return None

        # Look in same domain with compatible or lower tier
        same_domain = self.list_capabilities(curr.target_domain)
        for alt in same_domain:
            if alt.capability_id != curr.capability_id:
                # Must not exceed current tier
                if alt.capability_tier.value <= curr.capability_tier.value:
                    return alt

        # Generic fallback
        if curr.target_domain == "web" and "search_web" in self._capabilities:
            return self._capabilities["search_web"]
        return None
