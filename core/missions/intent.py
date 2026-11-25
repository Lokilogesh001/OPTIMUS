"""core/missions/intent.py — Intent Understanding & Multi-Intent Parsing Engine for OPTIMUS.

Parses natural language objectives into structured, typed intent models:
- Discovers primary objective and ancillary/secondary goals.
- Decomposes compound sequential ("then", "after that") and parallel ("while", "simultaneously") intents.
- Extracts typed entities: paths, applications, URLs, process names, contact names, file extensions.
- Normalizes temporal expressions to absolute epoch windows anchored to local timezone.
- Identifies operational constraints (read-only vs mutating, max budget, timeout).
- Formulates formal, typed CompletionCriteria for downstream verification.
- Detects ambiguous requests requiring clarification (WAITING_FOR_CLARIFICATION state).
- Sanitizes and neutralizes prompt-injection attempts.
"""

from __future__ import annotations

import datetime
import html
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("optimus.missions.intent")


class CriterionType(str, Enum):
    """Categorization of verifiable outcome criteria."""
    FILESYSTEM = "FILESYSTEM"
    PROCESS_RUNNING = "PROCESS_RUNNING"
    MEDIA_PLAYBACK = "MEDIA_PLAYBACK"
    COMMAND_EXIT = "COMMAND_EXIT"
    DATA_SCHEMA = "DATA_SCHEMA"
    NETWORK_RESPONSE = "NETWORK_RESPONSE"


@dataclass
class CompletionCriterion:
    """A formal assertion verifying a physical real-world state or deliverable."""
    criterion_type: CriterionType
    target: str
    expected_value: Any = None
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_type": self.criterion_type.value,
            "target": self.target,
            "expected_value": self.expected_value,
            "parameters": self.parameters,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompletionCriterion:
        raw_type = data.get("criterion_type", CriterionType.FILESYSTEM.value)
        try:
            c_type = CriterionType(raw_type)
        except ValueError:
            c_type = CriterionType.FILESYSTEM
        return cls(
            criterion_type=c_type,
            target=data.get("target", ""),
            expected_value=data.get("expected_value"),
            parameters=dict(data.get("parameters", {})),
            description=data.get("description", ""),
        )


@dataclass
class IntentEntity:
    """A discrete named entity extracted from user query."""
    entity_type: str  # "file_path", "app_name", "url", "process", "person", "file_pattern", "topic"
    value: str
    normalized_value: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "value": self.value,
            "normalized_value": self.normalized_value,
            "confidence": self.confidence,
        }


@dataclass
class TemporalConstraint:
    """A normalized time window anchored to local system time."""
    raw_expression: str
    start_timestamp: float | None = None
    end_timestamp: float | None = None
    is_relative: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_expression": self.raw_expression,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "is_relative": self.is_relative,
        }


@dataclass
class SingleIntent:
    """A single actionable intent within a potentially compound mission."""
    intent_id: str
    action_verb: str  # "find", "open", "play", "organize", "research", "fix", "summarize", etc.
    target_domain: str  # "email", "desktop_app", "filesystem", "code", "web", "system", "general"
    entities: list[IntentEntity] = field(default_factory=list)
    temporal_constraints: list[TemporalConstraint] = field(default_factory=list)
    completion_criteria: list[CompletionCriterion] = field(default_factory=list)
    is_mutating: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "action_verb": self.action_verb,
            "target_domain": self.target_domain,
            "entities": [e.to_dict() for e in self.entities],
            "temporal_constraints": [t.to_dict() for t in self.temporal_constraints],
            "completion_criteria": [c.to_dict() for c in self.completion_criteria],
            "is_mutating": self.is_mutating,
            "description": self.description,
        }


@dataclass
class ParsedMissionIntent:
    """Structured representation of the complete multi-intent user objective."""
    raw_query: str
    primary_intent: SingleIntent
    secondary_intents: list[SingleIntent] = field(default_factory=list)
    composition_mode: str = "SINGLE"  # "SINGLE", "SEQUENTIAL", "PARALLEL", "CONDITIONAL"
    global_constraints: dict[str, Any] = field(default_factory=dict)
    requires_disambiguation: bool = False
    clarification_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_query": self.raw_query,
            "primary_intent": self.primary_intent.to_dict(),
            "secondary_intents": [s.to_dict() for s in self.secondary_intents],
            "composition_mode": self.composition_mode,
            "global_constraints": self.global_constraints,
            "requires_disambiguation": self.requires_disambiguation,
            "clarification_prompt": self.clarification_prompt,
        }


class MissionIntentParser:
    """Parses natural language user objectives into structured, validated intent graphs."""

    _INJECTION_PATTERNS = [
        re.compile(r"<\s*/?\s*untrusted[_\w]*\b", re.IGNORECASE),
        re.compile(r"\b(ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt\s+override|jailbreak|disregard\s+prior)\b", re.IGNORECASE),
        re.compile(r"\b(you\s+are\s+now\s+in\s+developer\s+mode|dan\s+mode)\b", re.IGNORECASE),
    ]

    _SEQUENTIAL_SPLIT = re.compile(
        r"\b(?:and\s+then|after\s+that|subsequently|followed\s+by)\b|;\s*",
        re.IGNORECASE,
    )
    _PARALLEL_SPLIT = re.compile(
        r"\b(?:while|simultaneously|at\s+the\s+same\s+time|in\s+parallel\s+with)\b",
        re.IGNORECASE,
    )

    def parse(self, raw_query: str) -> ParsedMissionIntent:
        """Parse raw query string into a structured ParsedMissionIntent."""
        cleaned = (raw_query or "").strip()
        if not cleaned:
            return ParsedMissionIntent(
                raw_query="",
                primary_intent=SingleIntent("intent_0", "unknown", "general"),
                requires_disambiguation=True,
                clarification_prompt="Please describe the task or mission you would like me to perform.",
            )

        # Detect prompt injection attempts
        for pat in self._INJECTION_PATTERNS:
            if pat.search(cleaned):
                logger.warning("Prompt injection pattern detected in mission query: %s", cleaned[:80])
                # Neutralize injection text by escaping HTML characters
                cleaned = html.escape(cleaned)

        # Decompose compound phrases
        composition_mode = "SINGLE"
        clauses: list[str] = []

        if self._SEQUENTIAL_SPLIT.search(cleaned):
            composition_mode = "SEQUENTIAL"
            clauses = [c.strip() for c in self._SEQUENTIAL_SPLIT.split(cleaned) if c.strip()]
        elif self._PARALLEL_SPLIT.search(cleaned):
            composition_mode = "PARALLEL"
            clauses = [c.strip() for c in self._PARALLEL_SPLIT.split(cleaned) if c.strip()]
        else:
            clauses = [cleaned]

        intents: list[SingleIntent] = []
        prev_domain = None
        for idx, clause in enumerate(clauses):
            intent = self._parse_single_clause(clause, f"intent_{idx + 1}", prev_domain=prev_domain)
            if intent.target_domain != "general":
                prev_domain = intent.target_domain
            intents.append(intent)

        primary = intents[0]
        secondary = intents[1:] if len(intents) > 1 else []

        # Check for disambiguation requirements
        requires_disambiguation = False
        clarification_prompt = ""

        # Disambiguation rule: if query is excessively short or lacks a concrete verb and target
        if len(cleaned.split()) <= 2 and primary.target_domain == "general":
            requires_disambiguation = True
            clarification_prompt = f"Could you provide more details on what you would like to do regarding '{cleaned}'?"

        return ParsedMissionIntent(
            raw_query=raw_query,
            primary_intent=primary,
            secondary_intents=secondary,
            composition_mode=composition_mode,
            global_constraints=self._extract_global_constraints(cleaned),
            requires_disambiguation=requires_disambiguation,
            clarification_prompt=clarification_prompt,
        )

    def _parse_single_clause(self, clause: str, intent_id: str, prev_domain: str | None = None) -> SingleIntent:
        """Parse an individual sub-clause into a SingleIntent."""
        lower = clause.lower()

        # 1. Target Domain & Action Verb Extraction
        action_verb = "execute"
        target_domain = prev_domain or "general"
        is_mutating = False

        if any(w in lower for w in ("summarize", "summary", "synthesize")):
            action_verb = "summarize"

        if any(w in lower for w in ("email", "gmail", "inbox", "mail")):
            target_domain = "email"
            if any(w in lower for w in ("find", "search", "check", "list", "show")):
                action_verb = "find"
            elif any(w in lower for w in ("summarize", "read", "details")):
                action_verb = "summarize"
            elif any(w in lower for w in ("send", "reply", "forward")):
                action_verb = "send"
                is_mutating = True

        elif any(w in lower for w in ("spotify", "music", "play", "track", "song")):
            target_domain = "desktop_app"
            action_verb = "play"
            is_mutating = True  # launching app / starting playback

        elif any(w in lower for w in ("pdf", "files", "folder", "directory", "download", "documents", "organize", "move", "copy")):
            target_domain = "filesystem"
            if any(w in lower for w in ("organize", "move", "clean")):
                action_verb = "organize"
                is_mutating = True
            elif any(w in lower for w in ("find", "search", "locate")):
                action_verb = "find"
            elif any(w in lower for w in ("copy", "write", "create")):
                action_verb = "write"
                is_mutating = True

        elif any(re.search(r"\b" + re.escape(w) + r"\b", lower) for w in ("ram", "cpu", "gpu", "hardware", "process", "system", "vitals", "ollama")):
            target_domain = "system"
            action_verb = "diagnose"

        elif any(re.search(r"\b" + re.escape(w) + r"\b", lower) for w in ("git", "repository", "developer", "structure", "test", "bug", "failing", "fix", "code", "pytest", "calculator")):
            target_domain = "code"
            action_verb = "diagnose" if any(w in lower for w in ("inspect", "check", "status", "audit")) else "fix"
            is_mutating = action_verb == "fix"

        elif any(re.search(r"\b" + re.escape(w) + r"\b", lower) for w in ("laptop", "compare", "research", "best", "price", "specs", "specification", "specifications")):
            target_domain = "web"
            action_verb = "research"

        # 2. Extract Entities
        entities: list[IntentEntity] = []

        # Application entities
        for app in ("spotify", "notepad", "calculator", "chrome", "vscode", "explorer", "cmd"):
            if app in lower:
                entities.append(IntentEntity("app_name", app, app.capitalize()))

        # File extensions / patterns
        ext_match = re.findall(r"\b(?:\*\.)?([a-zA-Z0-9]{2,4})\b", clause)
        for ext in ("pdf", "docx", "txt", "csv", "xlsx", "pptx", "py", "json", "md"):
            if ext in ext_match or f".{ext}" in lower:
                entities.append(IntentEntity("file_pattern", f"*.{ext}", f"*.{ext}"))

        # Folder / path entities
        for folder in ("downloads", "documents", "desktop", "research", "notes"):
            if folder in lower:
                entities.append(IntentEntity("file_path", folder, folder.capitalize()))

        # Specific track / query titles in quotes or after verbs
        quoted = re.findall(r'["\']([^"\']+)["\']', clause)
        for q in quoted:
            entities.append(IntentEntity("topic", q, q))

        # Spotify track heuristic: "play Blinding Lights"
        if "play" in lower and not quoted:
            m = re.search(r"\bplay\s+([a-zA-Z0-9\s]+?)(?:\s+(?:on|in)\s+spotify|\.|$)", clause, re.IGNORECASE)
            if m:
                track = m.group(1).strip()
                if track.lower() not in ("spotify", "music"):
                    entities.append(IntentEntity("topic", track, track))

        # 3. Extract Temporal Constraints
        temporal: list[TemporalConstraint] = []
        now = time.time()
        # Anchored local timezone
        local_tz = datetime.datetime.now().astimezone().tzinfo

        if "last two days" in lower or "last 2 days" in lower:
            start_ts = now - (2 * 86400)
            temporal.append(TemporalConstraint("last two days", start_ts, now, is_relative=True))
        elif "today" in lower:
            today_start = datetime.datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            temporal.append(TemporalConstraint("today", today_start, now, is_relative=True))
        elif "yesterday" in lower:
            today_start = datetime.datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            yesterday_start = today_start - 86400
            temporal.append(TemporalConstraint("yesterday", yesterday_start, today_start, is_relative=True))

        # 4. Formulate Completion Criteria
        criteria: list[CompletionCriterion] = []

        if target_domain == "desktop_app" and "spotify" in lower:
            criteria.append(CompletionCriterion(
                criterion_type=CriterionType.PROCESS_RUNNING,
                target="Spotify.exe",
                description="Spotify process is running",
            ))
            criteria.append(CompletionCriterion(
                criterion_type=CriterionType.MEDIA_PLAYBACK,
                target="Spotify",
                expected_value="PLAYING",
                description="Active audio session playback verified via Windows Core Audio WASAPI",
            ))

        elif target_domain == "filesystem" and action_verb == "organize":
            criteria.append(CompletionCriterion(
                criterion_type=CriterionType.FILESYSTEM,
                target="Documents/Research",
                expected_value="EXISTS_AND_NON_EMPTY",
                description="Organized files present in target directory with matching file sizes",
            ))

        elif target_domain == "code" and action_verb == "fix":
            criteria.append(CompletionCriterion(
                criterion_type=CriterionType.COMMAND_EXIT,
                target="pytest",
                expected_value=0,
                description="Test suite passes with zero exit code",
            ))

        elif target_domain == "web" and action_verb == "research":
            criteria.append(CompletionCriterion(
                criterion_type=CriterionType.DATA_SCHEMA,
                target="markdown_table",
                parameters={"min_entries": 3, "required_columns": ["Model", "CPU", "RAM", "Price"]},
                description="Markdown comparison table compiled with valid specification schema",
            ))

        return SingleIntent(
            intent_id=intent_id,
            action_verb=action_verb,
            target_domain=target_domain,
            entities=entities,
            temporal_constraints=temporal,
            completion_criteria=criteria,
            is_mutating=is_mutating,
            description=clause,
        )

    def _extract_global_constraints(self, text: str) -> dict[str, Any]:
        """Extract explicit constraints such as budget, timeout, or format."""
        constraints: dict[str, Any] = {}
        lower = text.lower()

        # Timeout constraint
        m_timeout = re.search(r"\bwithin\s+(\d+)\s*(?:min|minutes|sec|seconds)\b", lower)
        if m_timeout:
            val = int(m_timeout.group(1))
            constraints["timeout_seconds"] = val * 60 if "min" in m_timeout.group(0) else val

        # Price / Budget constraint (e.g. "under $1200")
        m_budget = re.search(r"\bunder\s+\$?(\d+)\b", lower)
        if m_budget:
            constraints["max_price"] = float(m_budget.group(1))

        # Size constraint (e.g. "14-inch")
        m_size = re.search(r"\b(\d+)(?:-|\s*)inch\b", lower)
        if m_size:
            constraints["screen_size"] = int(m_size.group(1))

        return constraints
