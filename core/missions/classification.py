"""core/missions/classification.py — Mission Classification & Risk Scoring Engine for OPTIMUS.

Classifies missions along multiple operational dimensions:
1. Operational Mission Type: Email, Desktop Automation, File Mgmt, Research, Software Eng, System Admin, Compound.
2. Complexity Tier: T1 (Atomic), T2 (Deterministic), T3 (Dynamic Multi-Agent), T4 (Exploratory).
3. Risk Assessment: LOW, MEDIUM, HIGH, CRITICAL.
   * Resolves HIGH-06: Code modifications on repository files are HIGH/CRITICAL; reversible file moves are MEDIUM.
4. Authorization Level: AUTO_EXECUTE, PRE_EXECUTION_APPROVAL, MANDATORY_STEP_APPROVAL.
5. Resource Intensity Budget: Timeouts, token budgets, max parallel workers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.missions.intent import ParsedMissionIntent

logger = logging.getLogger("optimus.missions.classification")


class MissionType(str, Enum):
    EMAIL_INTELLIGENCE = "EMAIL_INTELLIGENCE"
    DESKTOP_AUTOMATION = "DESKTOP_AUTOMATION"
    FILE_DATA_MANAGEMENT = "FILE_DATA_MANAGEMENT"
    RESEARCH_SYNTHESIS = "RESEARCH_SYNTHESIS"
    SOFTWARE_ENGINEERING = "SOFTWARE_ENGINEERING"
    SYSTEM_ADMINISTRATION = "SYSTEM_ADMINISTRATION"
    COMPOUND_MISSION = "COMPOUND_MISSION"


class ComplexityTier(str, Enum):
    T1_ATOMIC = "T1_ATOMIC"
    T2_DETERMINISTIC = "T2_DETERMINISTIC"
    T3_DYNAMIC_MULTI_AGENT = "T3_DYNAMIC_MULTI_AGENT"
    T4_EXPLORATORY = "T4_EXPLORATORY"


class MissionRiskTier(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AuthorizationLevel(str, Enum):
    AUTO_EXECUTE = "AUTO_EXECUTE"
    PRE_EXECUTION_APPROVAL = "PRE_EXECUTION_APPROVAL"
    MANDATORY_STEP_APPROVAL = "MANDATORY_STEP_APPROVAL"


@dataclass
class MissionClassification:
    """Multi-dimensional operational classification and governance parameters."""
    mission_type: MissionType
    complexity_tier: ComplexityTier
    risk_tier: MissionRiskTier
    authorization_level: AuthorizationLevel
    estimated_duration_seconds: float
    max_token_budget: int
    max_parallel_workers: int
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_type": self.mission_type.value,
            "complexity_tier": self.complexity_tier.value,
            "risk_tier": self.risk_tier.value,
            "authorization_level": self.authorization_level.value,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "max_token_budget": self.max_token_budget,
            "max_parallel_workers": self.max_parallel_workers,
            "rationale": self.rationale,
            "metadata": self.metadata,
        }


class MissionClassifier:
    """Classifies structured intent into operational tiers and governance budgets."""

    def classify(self, intent: ParsedMissionIntent) -> MissionClassification:
        """Classify a parsed mission intent into operational governance dimensions."""
        primary = intent.primary_intent
        all_intents = [primary] + intent.secondary_intents
        domains = {i.target_domain for i in all_intents}

        # 1. Mission Type
        if len(domains) > 1 or intent.composition_mode in ("SEQUENTIAL", "PARALLEL"):
            mission_type = MissionType.COMPOUND_MISSION
        elif "email" in domains:
            mission_type = MissionType.EMAIL_INTELLIGENCE
        elif "desktop_app" in domains:
            mission_type = MissionType.DESKTOP_AUTOMATION
        elif "filesystem" in domains:
            mission_type = MissionType.FILE_DATA_MANAGEMENT
        elif "web" in domains:
            mission_type = MissionType.RESEARCH_SYNTHESIS
        elif "code" in domains:
            mission_type = MissionType.SOFTWARE_ENGINEERING
        elif "system" in domains:
            mission_type = MissionType.SYSTEM_ADMINISTRATION
        else:
            mission_type = MissionType.RESEARCH_SYNTHESIS

        # 2. Complexity Tier
        if len(all_intents) == 1 and not primary.is_mutating:
            complexity_tier = ComplexityTier.T1_ATOMIC
        elif mission_type in (MissionType.RESEARCH_SYNTHESIS, MissionType.FILE_DATA_MANAGEMENT):
            complexity_tier = ComplexityTier.T2_DETERMINISTIC
        elif mission_type in (MissionType.SOFTWARE_ENGINEERING, MissionType.DESKTOP_AUTOMATION, MissionType.COMPOUND_MISSION):
            complexity_tier = ComplexityTier.T3_DYNAMIC_MULTI_AGENT
        else:
            complexity_tier = ComplexityTier.T2_DETERMINISTIC

        # 3. Risk Tier & Authorization (Reconciled Policy resolving HIGH-06)
        # Rule: Code modification is HIGH or CRITICAL; file moves with Saga rollback are MEDIUM; read-only is LOW
        has_code_mutation = any(i.target_domain == "code" and i.is_mutating for i in all_intents)
        has_file_deletion = any("delete" in i.action_verb or "drop" in i.action_verb for i in all_intents)
        has_file_mutation = any(i.target_domain == "filesystem" and i.is_mutating for i in all_intents)
        has_external_comms = any(i.target_domain == "email" and i.is_mutating for i in all_intents)

        if has_code_mutation or has_file_deletion:
            risk_tier = MissionRiskTier.HIGH
            authorization_level = AuthorizationLevel.PRE_EXECUTION_APPROVAL
            rationale = "Modifying source code or deleting files carries high operational impact and requires pre-execution authorization."
        elif has_external_comms:
            risk_tier = MissionRiskTier.HIGH
            authorization_level = AuthorizationLevel.PRE_EXECUTION_APPROVAL
            rationale = "Sending external communications requires explicit user confirmation."
        elif has_file_mutation:
            risk_tier = MissionRiskTier.MEDIUM
            authorization_level = AuthorizationLevel.AUTO_EXECUTE
            rationale = "Filesystem movement operations are managed with transactional Saga rollback."
        elif any(i.is_mutating for i in all_intents):
            risk_tier = MissionRiskTier.MEDIUM
            authorization_level = AuthorizationLevel.AUTO_EXECUTE
            rationale = "Mutating desktop or workspace actions managed with verified postconditions."
        else:
            risk_tier = MissionRiskTier.LOW
            authorization_level = AuthorizationLevel.AUTO_EXECUTE
            rationale = "Read-only inspection query; safe to execute automatically."

        # 4. Resource Intensity & Token Budget
        if complexity_tier == ComplexityTier.T1_ATOMIC:
            estimated_duration = 15.0
            token_budget = 5_000
            workers = 1
        elif complexity_tier == ComplexityTier.T2_DETERMINISTIC:
            estimated_duration = 60.0
            token_budget = 25_000
            workers = 2
        elif complexity_tier == ComplexityTier.T3_DYNAMIC_MULTI_AGENT:
            estimated_duration = 180.0
            token_budget = 50_000
            workers = 4
        else:
            estimated_duration = 300.0
            token_budget = 100_000
            workers = 4

        return MissionClassification(
            mission_type=mission_type,
            complexity_tier=complexity_tier,
            risk_tier=risk_tier,
            authorization_level=authorization_level,
            estimated_duration_seconds=estimated_duration,
            max_token_budget=token_budget,
            max_parallel_workers=workers,
            rationale=rationale,
            metadata={"domains": list(domains), "composition_mode": intent.composition_mode},
        )
