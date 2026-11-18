"""core/skills/models.py — Strongly Typed Data Contracts for OPTIMUS Skill Ecosystem.

Defines:
- SkillManifest: Canonical specification of a discovered or installed skill.
- SkillType: INSTRUCTION, TOOL_BASED, EXECUTABLE.
- SkillTrustLevel: TRUSTED, VERIFIED, UNVERIFIED, SUSPICIOUS, BLOCKED.
- SkillCategory: Domain categorization (ENGINEERING, QUALITY, SECURITY, UI_DESIGN, etc.).
- SkillSecurityReport: Security audit findings, risk scores, and detected issues.
- SkillExecutionRecord: Audit trail of skill usage by specialized agents.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SkillType(str, Enum):
    """Execution modality of a skill."""
    INSTRUCTION = "INSTRUCTION"      # Pure markdown behavioural guidance / prompt
    TOOL_BASED = "TOOL_BASED"        # Direct guidance backed by registered ToolRegistry tools
    EXECUTABLE = "EXECUTABLE"        # Contains helper scripts / commands


class SkillTrustLevel(str, Enum):
    """Validation and trust ranking for a skill."""
    VERIFIED = "VERIFIED"            # Formally verified by SkillSpector / signed provenance
    TRUSTED = "TRUSTED"              # Clean security scan, no malicious patterns
    UNVERIFIED = "UNVERIFIED"        # Newly discovered, not yet scanned
    SUSPICIOUS = "SUSPICIOUS"        # Minor security warnings (credentials or external commands)
    BLOCKED = "BLOCKED"              # Critical security violation (prompt injection, disk wipe)


class SkillCategory(str, Enum):
    """Domain category for intelligent skill retrieval."""
    ENGINEERING = "ENGINEERING"      # ECC, TDD, code refactoring, architecture
    QUALITY = "QUALITY"              # Anti-Slop, code reviews, regression tests
    SECURITY = "SECURITY"            # SkillSpector, threat modeling, cso
    UI_DESIGN = "UI_DESIGN"          # Better-*, unslop, CSS, typography, layout
    PLANNING = "PLANNING"            # Plan reviews, CEO/Eng/Design review, autoplan
    WORKFLOW = "WORKFLOW"            # Multi-agent workflows, gstack, shipping
    DEVOPS = "DEVOPS"                # CI/CD, canary, land-and-deploy
    GENERAL = "GENERAL"              # Broad utility and instructions


@dataclass
class SkillSecurityIssue:
    """A specific vulnerability or pattern detected during skill static analysis."""
    severity: str                    # "INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"
    category: str                    # "PROMPT_INJECTION", "DANGEROUS_COMMAND", "SECRET_ACCESS", "TRAVERSAL"
    description: str
    line_number: int | None = None
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillSecurityReport:
    """Comprehensive security evaluation for an agent skill."""
    is_safe: bool = True
    risk_score: float = 0.0          # 0.0 (safe) to 10.0 (dangerous)
    trust_level: SkillTrustLevel = SkillTrustLevel.UNVERIFIED
    issues: list[SkillSecurityIssue] = field(default_factory=list)
    scanned_at: float = field(default_factory=time.time)
    scanner_name: str = "OptimusSecurityValidator"

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "risk_score": round(self.risk_score, 1),
            "trust_level": self.trust_level.value,
            "issues": [i.to_dict() for i in self.issues],
            "scanned_at": self.scanned_at,
            "scanner_name": self.scanner_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillSecurityReport:
        issues = [
            SkillSecurityIssue(**i) if isinstance(i, dict) else i
            for i in data.get("issues", [])
        ]
        return cls(
            is_safe=bool(data.get("is_safe", True)),
            risk_score=float(data.get("risk_score", 0.0)),
            trust_level=SkillTrustLevel(data.get("trust_level", SkillTrustLevel.UNVERIFIED.value)),
            issues=issues,
            scanned_at=float(data.get("scanned_at", time.time())),
            scanner_name=str(data.get("scanner_name", "OptimusSecurityValidator")),
        )


@dataclass
class SkillManifest:
    """Canonical representation of an OPTIMUS agent skill."""
    id: str
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "Unknown"
    license: str = "Unspecified"
    source_path: str = ""
    entrypoint_file: str = "SKILL.md"
    skill_type: SkillType = SkillType.INSTRUCTION
    category: SkillCategory = SkillCategory.GENERAL
    tags: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    instructions: str = ""
    content_sha256: str = ""
    enabled: bool = True
    trust_level: SkillTrustLevel = SkillTrustLevel.UNVERIFIED
    security_report: SkillSecurityReport | None = None
    dependencies: list[str] = field(default_factory=list)
    incompatible_with: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: str = "local"         # "ecc", "gstack", "antislop", "nvidia", "builtin", "local"
    execution_count: int = 0
    last_executed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "license": self.license,
            "source_path": self.source_path,
            "entrypoint_file": self.entrypoint_file,
            "skill_type": self.skill_type.value,
            "category": self.category.value,
            "tags": self.tags,
            "required_tools": self.required_tools,
            "instructions": self.instructions,
            "content_sha256": self.content_sha256,
            "enabled": self.enabled,
            "trust_level": self.trust_level.value,
            "security_report": self.security_report.to_dict() if self.security_report else None,
            "dependencies": self.dependencies,
            "incompatible_with": self.incompatible_with,
            "metadata": self.metadata,
            "provenance": self.provenance,
            "execution_count": self.execution_count,
            "last_executed_at": self.last_executed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillManifest:
        d = dict(data)
        if "skill_type" in d and isinstance(d["skill_type"], str):
            d["skill_type"] = SkillType(d["skill_type"])
        if "category" in d and isinstance(d["category"], str):
            d["category"] = SkillCategory(d["category"])
        if "trust_level" in d and isinstance(d["trust_level"], str):
            d["trust_level"] = SkillTrustLevel(d["trust_level"])
        if "security_report" in d and isinstance(d["security_report"], dict):
            d["security_report"] = SkillSecurityReport.from_dict(d["security_report"])
        return cls(**d)


@dataclass
class SkillExecutionRecord:
    """Historical record documenting the application of a skill during task execution."""
    skill_id: str
    task_id: str
    caller_agent: str
    timestamp: float = field(default_factory=time.time)
    status: str = "success"          # "success", "failed", "blocked"
    duration_ms: float = 0.0
    summary: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
