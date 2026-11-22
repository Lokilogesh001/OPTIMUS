"""core/application_intelligence/types.py — Canonical Data Models for Application Intelligence.

Defines the core domain types, interaction tiers, action contracts, execution evidence,
and workflow representations for autonomous application operation in OPTIMUS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AppInteractionTier(str, Enum):
    """The 5-tier interaction hierarchy for desktop applications."""
    TIER_1_NATIVE_API = "tier_1_native_api"          # Official API, SDK, CLI, or IPC protocol
    TIER_2_ADAPTER = "tier_2_adapter"                # Dedicated application connector
    TIER_3_UI_AUTOMATION = "tier_3_ui_automation"    # Windows UI Automation / Accessibility tree
    TIER_4_BROWSER_DOM = "tier_4_browser_dom"        # Browser automation / CDP / DOM interaction
    TIER_5_VISUAL_ACTUATION = "tier_5_visual_act"    # Visual Computer Use / Screen observation & actuation


class AppCategory(str, Enum):
    """Functional categorization of desktop applications."""
    COMMUNICATION = "communication"
    BROWSER = "browser"
    DOCUMENT_VIEWER = "document_viewer"
    OFFICE = "office"
    DEVELOPMENT = "development"
    SYSTEM_UTILITY = "system_utility"
    MEDIA = "media"
    OTHER = "other"


@dataclass
class AppDefinition:
    """Canonical descriptor of a discovered or registered Windows application."""
    app_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    executable_path: str | None = None
    package_id: str | None = None            # UWP / MSIX package family name or ID
    category: AppCategory = AppCategory.OTHER
    supported_tiers: list[AppInteractionTier] = field(default_factory=list)
    associated_extensions: list[str] = field(default_factory=list)
    is_running: bool = False
    main_hwnd: int | None = None
    pids: list[int] = field(default_factory=list)
    integrity_level: str = "Medium"          # "Low", "Medium", "High", "System"
    metadata: dict[str, Any] = field(default_factory=dict)
    last_discovered_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "name": self.name,
            "aliases": self.aliases,
            "executable_path": self.executable_path,
            "package_id": self.package_id,
            "category": self.category.value,
            "supported_tiers": [t.value for t in self.supported_tiers],
            "associated_extensions": self.associated_extensions,
            "is_running": self.is_running,
            "main_hwnd": self.main_hwnd,
            "pids": self.pids,
            "integrity_level": self.integrity_level,
            "metadata": self.metadata,
            "last_discovered_at": self.last_discovered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppDefinition:
        category = AppCategory(data.get("category", AppCategory.OTHER.value))
        tiers = [AppInteractionTier(t) for t in data.get("supported_tiers", [])]
        return cls(
            app_id=data["app_id"],
            name=data["name"],
            aliases=data.get("aliases", []),
            executable_path=data.get("executable_path"),
            package_id=data.get("package_id"),
            category=category,
            supported_tiers=tiers,
            associated_extensions=data.get("associated_extensions", []),
            is_running=data.get("is_running", False),
            main_hwnd=data.get("main_hwnd"),
            pids=data.get("pids", []),
            integrity_level=data.get("integrity_level", "Medium"),
            metadata=data.get("metadata", {}),
            last_discovered_at=data.get("last_discovered_at", time.time()),
        )


class ResolutionStatus(str, Enum):
    """Outcome status for entity resolution (contacts, files, applications)."""
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass
class ResolutionResult:
    """Structured result of resolving ambiguous entities."""
    status: ResolutionStatus
    resolved_value: Any = None
    confidence: float = 0.0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    clarification_prompt: str | None = None

    @property
    def is_matched(self) -> bool:
        return self.status == ResolutionStatus.MATCHED

    @property
    def is_ambiguous(self) -> bool:
        return self.status == ResolutionStatus.AMBIGUOUS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "resolved_value": self.resolved_value,
            "confidence": self.confidence,
            "candidates": self.candidates,
            "clarification_prompt": self.clarification_prompt,
        }


@dataclass
class ActionContract:
    """Pre-execution contract defining an operation on an application."""
    action_id: str
    target_app: str
    operation: str
    parameters: dict[str, Any] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    expected_outcome: str = ""
    requires_approval: bool = False
    verification_strategy: str = "window_state"  # "window_state", "element_visible", "file_exists", "process_running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "target_app": self.target_app,
            "operation": self.operation,
            "parameters": self.parameters,
            "preconditions": self.preconditions,
            "expected_outcome": self.expected_outcome,
            "requires_approval": self.requires_approval,
            "verification_strategy": self.verification_strategy,
        }


@dataclass
class ExecutionEvidence:
    """Physical postcondition evidence matching core.outcome convention."""
    action_id: str
    status: str = "done"                       # "done", "unverified", "failed"
    tier_used: AppInteractionTier = AppInteractionTier.TIER_2_ADAPTER
    facts: list[str] = field(default_factory=list)
    evidence_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == "done"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status,
            "tier_used": self.tier_used.value,
            "facts": self.facts,
            "evidence_data": self.evidence_data,
            "error_message": self.error_message,
        }


@dataclass
class LearnedWorkflow:
    """A synthesized application workflow created by the Learning Engine."""
    workflow_id: str
    target_app: str
    goal: str
    action_sequence: list[dict[str, Any]] = field(default_factory=list)
    validation_state: str = "UNVERIFIED"       # "DISCOVERED", "UNVERIFIED", "TESTED", "VERIFIED_SKILL"
    preconditions: list[str] = field(default_factory=list)
    recovery_hints: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    execution_count: int = 0
    success_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "target_app": self.target_app,
            "goal": self.goal,
            "action_sequence": self.action_sequence,
            "validation_state": self.validation_state,
            "preconditions": self.preconditions,
            "recovery_hints": self.recovery_hints,
            "created_at": self.created_at,
            "execution_count": self.execution_count,
            "success_count": self.success_count,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearnedWorkflow:
        return cls(
            workflow_id=data["workflow_id"],
            target_app=data["target_app"],
            goal=data["goal"],
            action_sequence=data.get("action_sequence", []),
            validation_state=data.get("validation_state", "UNVERIFIED"),
            preconditions=data.get("preconditions", []),
            recovery_hints=data.get("recovery_hints", []),
            created_at=data.get("created_at", time.time()),
            execution_count=data.get("execution_count", 0),
            success_count=data.get("success_count", 0),
            metadata=data.get("metadata", {}),
        )
