"""core/proactive/context.py — Context Awareness & Unfinished Work Discovery for OPTIMUS.

Implements Phase 4:
- Connects to MissionStore.find_resumable() to discover paused/interrupted missions across restarts.
- Integrates with OptimusMemoryManager to retrieve user preferences and active goals.
- Strict security sanitization: scrubs passwords, API keys, tokens, and credentials from telemetry and context.
- Analyzes unfinished work and failure patterns to provide actionable inputs to ProactiveIntelligenceEngine.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from core.memory.manager import OptimusMemoryManager, get_memory_manager
from core.memory.types import MemoryCategory
from core.missions.storage import MissionStore

logger = logging.getLogger(__name__)

# Patterns for sensitive credentials that must NEVER leak into proactive recommendations or logs
_SENSITIVE_REGEXES = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|auth[_-]?token|bearer)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"(?i)ghp_[a-zA-Z0-9]{36}"),                             # GitHub Personal Access Token
    re.compile(r"(?i)xox[baprs]-[0-9a-zA-Z]{10,48}"),                   # Slack token
    re.compile(r"(?i)AIza[0-9A-Za-z\\-_]{35}"),                         # Google API key
    re.compile(r"(?i)sk-[a-zA-Z0-9]{20,48}"),                           # OpenAI secret key
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----.*?-----END [A-Z ]+ PRIVATE KEY-----", re.DOTALL),
]


def sanitize_sensitive_data(text: str) -> str:
    """Scrub sensitive credentials, passwords, and tokens from any text string."""
    if not isinstance(text, str):
        return text
    sanitized = text
    for pattern in _SENSITIVE_REGEXES:
        sanitized = pattern.sub("[REDACTED_CREDENTIAL]", sanitized)
    return sanitized


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub sensitive keys and credential patterns from a dictionary."""
    clean: dict[str, Any] = {}
    sensitive_keys = {"password", "secret", "token", "api_key", "apikey", "credential", "private_key"}
    for k, v in data.items():
        if any(sk in k.lower() for sk in sensitive_keys):
            clean[k] = "[REDACTED_KEY]"
        elif isinstance(v, str):
            clean[k] = sanitize_sensitive_data(v)
        elif isinstance(v, dict):
            clean[k] = sanitize_dict(v)
        elif isinstance(v, list):
            clean[k] = [
                sanitize_dict(item) if isinstance(item, dict)
                else sanitize_sensitive_data(item) if isinstance(item, str)
                else item
                for item in v
            ]
        else:
            clean[k] = v
    return clean


@dataclass
class ContextSummary:
    """Consolidated proactive context captured from memory, missions, and system."""
    timestamp: float = field(default_factory=time.time)
    unfinished_missions: list[dict[str, Any]] = field(default_factory=list)
    recent_failures: list[dict[str, Any]] = field(default_factory=list)
    active_goals: list[str] = field(default_factory=list)
    user_preferences: dict[str, Any] = field(default_factory=dict)
    system_telemetry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "unfinished_missions": self.unfinished_missions,
            "recent_failures": self.recent_failures,
            "active_goals": self.active_goals,
            "user_preferences": self.user_preferences,
            "system_telemetry": self.system_telemetry,
        }


class ProactiveContextManager:
    """Context discovery and tracking engine for OPTIMUS proactive capabilities."""

    def __init__(
        self,
        mission_store: MissionStore | None = None,
        memory_manager: OptimusMemoryManager | None = None,
    ) -> None:
        self.mission_store = mission_store or MissionStore()
        try:
            self.memory_manager = memory_manager or get_memory_manager()
        except Exception as e:
            logger.warning("Could not initialize full OptimusMemoryManager: %s", e)
            self.memory_manager = None

    def get_unfinished_missions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve interrupted or incomplete missions that are candidates for resumption."""
        try:
            resumable_records = self.mission_store.find_resumable()
            results = []
            for m in resumable_records[:limit]:
                # Calculate progress
                subtasks = getattr(m.plan, "subtasks", getattr(m.plan, "tasks", [])) if m.plan else []
                total_tasks = len(subtasks)
                completed_tasks = sum(
                    1 for t in subtasks
                    if str(getattr(t, "status", "")).upper().endswith("COMPLETED")
                )
                failed_tasks = sum(
                    1 for t in subtasks
                    if str(getattr(t, "status", "")).upper().endswith("FAILED")
                )

                err_val = getattr(m, "failure_reason", None) or getattr(m, "error", None)

                results.append({
                    "mission_id": m.mission_id,
                    "objective": sanitize_sensitive_data(m.objective),
                    "state": m.state.value if hasattr(m.state, "value") else str(m.state),
                    "created_at": m.created_at,
                    "updated_at": m.updated_at,
                    "total_tasks": total_tasks,
                    "completed_tasks": completed_tasks,
                    "failed_tasks": failed_tasks,
                    "error": sanitize_sensitive_data(err_val) if err_val else None,
                })
            return results
        except Exception as e:
            logger.error("Failed to query resumable missions: %s", e)
            return []

    def get_recent_failures(self, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve recent failed missions or aborted workflows to evaluate recovery suggestions."""
        try:
            all_missions = self.mission_store.list_all(limit=50)
            failed = []
            for m in all_missions:
                state_val = m.state.value if hasattr(m.state, "value") else str(m.state)
                if state_val in {"FAILED", "ABORTED", "CANCELLED"}:
                    failed.append({
                        "mission_id": m.mission_id,
                        "objective": sanitize_sensitive_data(m.objective),
                        "state": state_val,
                        "updated_at": m.updated_at,
                        "error": sanitize_sensitive_data(m.error or "Unknown failure"),
                    })
                    if len(failed) >= limit:
                        break
            return failed
        except Exception as e:
            logger.error("Failed to query recent failures: %s", e)
            return []

    def get_user_preferences(self) -> dict[str, Any]:
        """Load user preferences and persistent project context from semantic memory."""
        prefs: dict[str, Any] = {}
        if not self.memory_manager:
            return prefs

        try:
            entries = self.memory_manager.store.list_category(MemoryCategory.SEMANTIC, limit=50)
            for entry in entries:
                if "preference" in entry.tags or "config" in entry.tags:
                    prefs[entry.key] = sanitize_sensitive_data(entry.content)
        except Exception as e:
            logger.warning("Failed to load memory preferences: %s", e)

        return prefs

    def get_active_goals(self) -> list[str]:
        """Load active strategic or developer goals from episodic/working memory."""
        goals: list[str] = []
        if not self.memory_manager:
            return goals

        try:
            entries = self.memory_manager.store.list_category(MemoryCategory.WORKING, limit=20)
            for entry in entries:
                if "goal" in entry.tags or "objective" in entry.tags:
                    goals.append(sanitize_sensitive_data(entry.content))
        except Exception as e:
            logger.warning("Failed to load active goals: %s", e)

        return goals

    def capture_full_context(self) -> ContextSummary:
        """Capture a consolidated sanitized snapshot of active context."""
        return ContextSummary(
            timestamp=time.time(),
            unfinished_missions=self.get_unfinished_missions(),
            recent_failures=self.get_recent_failures(),
            active_goals=self.get_active_goals(),
            user_preferences=self.get_user_preferences(),
            system_telemetry={},
        )
