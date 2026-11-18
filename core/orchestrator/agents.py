"""core/orchestrator/agents.py — Agent Registry & Capability Envelopes for OPTIMUS.

Registers, discovers, and configures domain-specific specialized agents.
Enforces that agents receive only the tools and context they are authorized
to access, and strictly prevents unauthorized agent access or peer-to-peer delegation.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.orchestrator.tools import CapabilityTier

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


@dataclass
class AgentCapability:
    """Operational capability boundaries assigned to a specialized agent."""
    domain: str = "general"
    allowed_tools: list[str] = field(default_factory=list)
    allowed_tiers: list[CapabilityTier] = field(default_factory=lambda: [CapabilityTier.READ_ONLY])
    allowed_context_roles: list[str] = field(default_factory=lambda: ["*"])
    max_turns: int = 6
    timeout_seconds: float = 120.0


@dataclass
class AgentProfile:
    """Complete specification of a specialized agent role."""
    name: str
    title: str = ""
    description: str = ""
    capability: AgentCapability = field(default_factory=AgentCapability)
    instructions: str = ""

    @property
    def domain(self) -> str:
        return self.capability.domain

    @property
    def allowed_tools(self) -> list[str]:
        return self.capability.allowed_tools

    @property
    def allowed_tiers(self) -> list[CapabilityTier]:
        return self.capability.allowed_tiers

    @property
    def capability_tier(self) -> CapabilityTier:
        return self.capability.allowed_tiers[-1] if self.capability.allowed_tiers else CapabilityTier.READ_ONLY


class AgentRegistry:
    """Thread-safe registry managing specialized agent profiles and capabilities."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: dict[str, AgentProfile] = {}
        self._register_default_agents()
        try:
            from core.agents.registry_ext import register_specialized_agents
            register_specialized_agents(self)
        except ImportError:
            pass

    def _register_default_agents(self) -> None:
        """Register the baseline canonical domain agents."""

        # 1. Researcher
        self.register(AgentProfile(
            name="researcher",
            title="Research Specialist",
            description="Performs deep data gathering, file inspection, and content exploration.",
            capability=AgentCapability(
                domain="research",
                allowed_tools=["read_file", "list_directory", "calculate", "calculator", "search_web", "find_files", "largest_files"],
                allowed_tiers=[CapabilityTier.READ_ONLY, CapabilityTier.NETWORK_EXTERNAL],
                max_turns=5,
                timeout_seconds=90.0,
            ),
            instructions=(
                "You are an empirical research agent. Gather facts, read files and verify data. "
                "Never invent quotes or cite sources you have not inspected."
            ),
        ))

        # 2. Calculator
        self.register(AgentProfile(
            name="calculator",
            title="Mathematical Computation Specialist",
            description="Performs precise mathematical, statistical, and arithmetic calculations.",
            capability=AgentCapability(
                domain="math",
                allowed_tools=["calculator"],
                allowed_tiers=[CapabilityTier.READ_ONLY],
                max_turns=4,
                timeout_seconds=60.0,
            ),
            instructions=(
                "You are a calculation specialist. Use the calculator tool for all mathematical "
                "operations. Output exact numerical answers with concise explanation."
            ),
        ))

        # 3. Filesystem Operator
        self.register(AgentProfile(
            name="filesystem",
            title="Workspace Filesystem Specialist",
            description="Manages workspace files, directories, data persistence, and artifact generation.",
            capability=AgentCapability(
                domain="io",
                allowed_tools=[
                    "read_file",
                    "write_file",
                    "list_directory",
                    "open_directory",
                    "open_folder",
                    "find_files",
                    "largest_files",
                    "create_directory",
                    "create_folder",
                ],
                allowed_tiers=[CapabilityTier.READ_ONLY, CapabilityTier.WORKSPACE_WRITE],
                max_turns=6,
                timeout_seconds=120.0,
            ),
            instructions=(
                "You are a filesystem operator. Create, update, and inspect workspace files safely "
                "within designated directory boundaries. Verify file contents before completing."
            ),
        ))

        # 4. General Assistant
        self.register(AgentProfile(
            name="general",
            title="General Multi-Domain Assistant",
            description="Handles general multi-step tasks across calculation, file operations, system actions, and synthesis.",
            capability=AgentCapability(
                domain="general",
                allowed_tools=[
                    "calculator",
                    "read_file",
                    "write_file",
                    "list_directory",
                    "open_app",
                    "open_directory",
                    "open_folder",
                    "find_files",
                    "largest_files",
                    "create_directory",
                    "create_folder",
                    "ollama_status",
                    "check_ollama",
                    "python_version",
                    "computer_audit",
                    "search_web",
                    "cpu_status",
                    "memory_status",
                    "disk_status",
                    "gpu_status",
                    "system_info",
                ],
                allowed_tiers=[CapabilityTier.READ_ONLY, CapabilityTier.WORKSPACE_WRITE, CapabilityTier.EXECUTION, CapabilityTier.NETWORK_EXTERNAL],
                max_turns=8,
                timeout_seconds=180.0,
            ),
            instructions=(
                "You are a general agent capable of coordinating and executing computer tasks. "
                "Execute authorized tools directly to gather real facts and perform actions. "
                "Never write scripts instructing the user to perform tasks manually when tools exist."
            ),
        ))

    def register(self, profile: AgentProfile) -> None:
        """Register or override an agent profile in the registry."""
        with self._lock:
            self._agents[profile.name.lower()] = profile

    def unregister(self, name: str) -> bool:
        """Dynamically unregister an agent profile from the registry."""
        with self._lock:
            return self._agents.pop(name.lower(), None) is not None

    revoke = unregister

    def is_registered(self, name: str) -> bool:
        """Check if an agent profile is currently registered."""
        with self._lock:
            return name.lower() in self._agents

    def get(self, name: str) -> AgentProfile | None:
        """Fetch an agent profile by name."""
        with self._lock:
            return self._agents.get(name.lower())

    def list_agents(self) -> list[AgentProfile]:
        """Return all registered agent profiles."""
        with self._lock:
            return list(self._agents.values())

    def get_scoped_tools(self, agent_name: str, tool_registry: Any) -> list[str]:
        """Return the subset of tools in the registry authorized for this agent."""
        profile = self.get(agent_name)
        if not profile:
            return []
        from core.orchestrator.tools import _TIER_RANK
        max_allowed_rank = max((_TIER_RANK.get(t, 0) for t in profile.allowed_tiers), default=0)
        scoped = []
        for tool_name in profile.allowed_tools:
            tool = tool_registry.get(tool_name)
            if tool:
                tool_rank = _TIER_RANK.get(tool.capability_tier, 99)
                if tool_rank <= max_allowed_rank:
                    scoped.append(tool_name)
        return scoped

    def load_from_markdown(self, path: Path | str) -> AgentProfile:
        """Parse a markdown agent profile (compatible with Mark-LVII agents/*.md format)."""
        file_path = Path(path)
        text = file_path.read_text(encoding="utf-8").lstrip("\ufeff")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
        if m:
            head, body = m.group(1), m.group(2).strip()
        else:
            lines = text.splitlines()
            head_lines = []
            body_lines = []
            in_head = True
            for line in lines:
                stripped = line.strip()
                if in_head and (":" in stripped or stripped.startswith("#") or not stripped):
                    head_lines.append(stripped)
                else:
                    in_head = False
                    body_lines.append(line)
            head = "\n".join(head_lines)
            body = "\n".join(body_lines).strip()

        meta: dict[str, str] = {}
        for line in head.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, sep, v = line.partition(":")
            if sep:
                meta[k.strip().lower()] = v.strip()

        name = meta.get("name", file_path.stem).lower()
        title = meta.get("title") or name.title()
        description = meta.get("description", "")
        raw_tools = [t.strip() for t in re.split(r"[,\s]+", meta.get("tools", "")) if t.strip()]

        # Derive tiers
        tiers = [CapabilityTier.READ_ONLY]
        if meta.get("tier"):
            tier_str = meta["tier"].strip().upper()
            if tier_str in CapabilityTier.__members__:
                tiers = [CapabilityTier[tier_str]]
        else:
            if any(t in raw_tools for t in ("write_file", "file_processor", "create_file")):
                tiers.append(CapabilityTier.WORKSPACE_WRITE)
            if any(t in raw_tools for t in ("search_web", "web_search", "fetch_page")):
                tiers.append(CapabilityTier.NETWORK_EXTERNAL)

        try:
            minutes = int(meta.get("minutes", 5))
        except (ValueError, TypeError):
            minutes = 5

        capability = AgentCapability(
            domain=meta.get("mode") or meta.get("domain") or "general",
            allowed_tools=raw_tools or ["calculator", "read_file"],
            allowed_tiers=tiers,
            max_turns=max(2, min(50, int(meta.get("rounds", 8)))),
            timeout_seconds=minutes * 60.0,
        )

        profile = AgentProfile(
            name=name,
            title=title,
            description=description,
            capability=capability,
            instructions=body or "Execute your assigned subtask accurately.",
        )
        self.register(profile)
        return profile

    def discover_directory(self, agents_dir: Path | str) -> int:
        """Scan a directory for markdown agent profiles and register them."""
        d = Path(agents_dir)
        if not d.is_dir():
            return 0
        count = 0
        for md_file in d.glob("*.md"):
            try:
                self.load_from_markdown(md_file)
                count += 1
            except Exception as e:
                print(f"[AgentRegistry] Failed loading profile '{md_file.name}': {e}")
        return count
