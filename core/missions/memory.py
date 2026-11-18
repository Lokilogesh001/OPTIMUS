"""core/missions/memory.py — Mission Context Bus and Strategy Memory for OPTIMUS.

Manages:
- Cross-subtask shared context bus (data flow from upstream steps to downstream steps).
- Agent-specific isolated scratchpads.
- Structured findings ledger (facts, discoveries, deliverable locations).
- Strategy memory: records and retrieves proven mission decomposition patterns.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class MissionMemory:
    """Thread-safe context bus and execution memory for an autonomous mission."""

    def __init__(self, mission_id: str) -> None:
        self.mission_id = mission_id
        self._shared_context: dict[str, Any] = {}
        self._agent_contexts: dict[str, dict[str, Any]] = {}
        self._findings: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    # ── Shared Context Bus ───────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        """Store a variable in the shared mission context."""
        with self._lock:
            self._shared_context[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a variable from the shared mission context."""
        with self._lock:
            return self._shared_context.get(key, default)

    def get_all(self) -> dict[str, Any]:
        """Return a snapshot copy of all shared context variables."""
        with self._lock:
            return dict(self._shared_context)

    def update(self, mapping: dict[str, Any]) -> None:
        """Merge a dictionary into the shared context."""
        with self._lock:
            self._shared_context.update(mapping)

    # ── Agent Context Scratchpad ─────────────────────────────────────────────

    def set_agent_data(self, agent_name: str, key: str, value: Any) -> None:
        """Store agent-private state in its scratchpad."""
        with self._lock:
            if agent_name not in self._agent_contexts:
                self._agent_contexts[agent_name] = {}
            self._agent_contexts[agent_name][key] = value

    def get_agent_data(self, agent_name: str, key: str, default: Any = None) -> Any:
        """Retrieve agent-private state."""
        with self._lock:
            return self._agent_contexts.get(agent_name, {}).get(key, default)

    # ── Findings Ledger ──────────────────────────────────────────────────────

    def record_finding(self, topic: str, content: str, source_agent: str, metadata: dict[str, Any] | None = None) -> None:
        """Record an authoritative physical or computational finding."""
        with self._lock:
            finding = {
                "timestamp": time.time(),
                "topic": topic,
                "content": content,
                "source_agent": source_agent,
                "metadata": metadata or {},
            }
            self._findings.append(finding)

    def get_findings(self, topic: str | None = None) -> list[dict[str, Any]]:
        """Retrieve findings, optionally filtered by topic."""
        with self._lock:
            if topic:
                return [f for f in self._findings if f["topic"].lower() == topic.lower()]
            return list(self._findings)

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "shared_context": dict(self._shared_context),
                "agent_contexts": {k: dict(v) for k, v in self._agent_contexts.items()},
                "findings": list(self._findings),
            }

    def load_dict(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._shared_context = dict(data.get("shared_context", {}))
            self._agent_contexts = {k: dict(v) for k, v in data.get("agent_contexts", {}).items()}
            self._findings = list(data.get("findings", []))
