"""core/skills/package.py — OPTIMUS Native Skill Package Specification & Dependencies.

Defines:
- DependencyKind: Classification of dependencies (OPTIMUS tools, Python packages, binaries, system, integrations).
- SkillDependency: Strongly-typed dependency specification and detection result.
- HardwareRequirements: Hardware requirement declaration (GPU, CUDA, RAM).
- SkillPackageManifest: Canonical metadata file (`optimus_package.json`) saved in native skill storage.
- SkillInstallAction: Action enumeration for package audit log.
- SkillInstallHistoryRecord: Installation event record for `install_history.json`.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.skills.models import (
    SkillCategory,
    SkillSecurityReport,
    SkillType,
)


class DependencyKind(str, Enum):
    """Category of dependency required by a skill."""
    OPTIMUS_TOOL = "OPTIMUS_TOOL"          # Built-in tool in OPTIMUS ToolRegistry
    PYTHON_PACKAGE = "PYTHON_PACKAGE"      # Python library / package
    EXTERNAL_BINARY = "EXTERNAL_BINARY"    # System binary (git, pytest, node, curl)
    SYSTEM = "SYSTEM"                      # OS / system library
    INTEGRATION = "INTEGRATION"            # Optional external service integration


class DependencyStatus(str, Enum):
    """Verification status of a dependency."""
    SATISFIED = "SATISFIED"
    MISSING = "MISSING"
    OPTIONAL = "OPTIONAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class SkillDependency:
    """Declared dependency for a skill package."""
    name: str
    kind: DependencyKind
    required: bool = True
    version_constraint: str = ""
    status: DependencyStatus = DependencyStatus.UNKNOWN
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "required": self.required,
            "version_constraint": self.version_constraint,
            "status": self.status.value,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillDependency:
        d = dict(data)
        if "kind" in d and isinstance(d["kind"], str):
            d["kind"] = DependencyKind(d["kind"])
        if "status" in d and isinstance(d["status"], str):
            d["status"] = DependencyStatus(d["status"])
        return cls(**d)


@dataclass
class HardwareRequirements:
    """Hardware requirements declaration for native skill packages."""
    gpu_required: bool = False
    cuda_required: bool = False
    min_ram_mb: int = 128
    notes: str = "Hardware independent instruction-based execution."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HardwareRequirements:
        return cls(**data)


@dataclass
class SkillPackageManifest:
    """The canonical packaging specification stored as `optimus_package.json`."""
    id: str
    name: str
    version: str
    description: str
    author: str = "Unknown"
    license: str = "Unspecified"
    original_source: str = ""
    installed_at: float = field(default_factory=time.time)
    entrypoint_file: str = "SKILL.md"
    skill_type: SkillType = SkillType.INSTRUCTION
    category: SkillCategory = SkillCategory.GENERAL
    tags: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    dependencies: list[SkillDependency] = field(default_factory=list)
    hardware_requirements: HardwareRequirements = field(default_factory=HardwareRequirements)
    security_report: SkillSecurityReport | None = None
    integrity_checksum: str = ""
    files_checksums: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "original_source": self.original_source,
            "installed_at": self.installed_at,
            "entrypoint_file": self.entrypoint_file,
            "skill_type": self.skill_type.value,
            "category": self.category.value,
            "tags": self.tags,
            "required_tools": self.required_tools,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "hardware_requirements": self.hardware_requirements.to_dict(),
            "security_report": self.security_report.to_dict() if self.security_report else None,
            "integrity_checksum": self.integrity_checksum,
            "files_checksums": self.files_checksums,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillPackageManifest:
        d = dict(data)
        if "skill_type" in d and isinstance(d["skill_type"], str):
            d["skill_type"] = SkillType(d["skill_type"])
        if "category" in d and isinstance(d["category"], str):
            d["category"] = SkillCategory(d["category"])
        if "dependencies" in d and isinstance(d["dependencies"], list):
            d["dependencies"] = [
                SkillDependency.from_dict(dep) if isinstance(dep, dict) else dep
                for dep in d["dependencies"]
            ]
        if "hardware_requirements" in d and isinstance(d["hardware_requirements"], dict):
            d["hardware_requirements"] = HardwareRequirements.from_dict(d["hardware_requirements"])
        if "security_report" in d and isinstance(d["security_report"], dict):
            d["security_report"] = SkillSecurityReport.from_dict(d["security_report"])
        return cls(**d)

    @staticmethod
    def compute_directory_integrity(directory: Path | str) -> tuple[str, dict[str, str]]:
        """Compute SHA-256 for all package files and combined package checksum."""
        p = Path(directory)
        if not p.is_dir():
            return "", {}

        file_digests: dict[str, str] = {}
        hasher = hashlib.sha256()

        for file_path in sorted(p.rglob("*")):
            if file_path.is_file() and file_path.name != "optimus_package.json":
                rel = str(file_path.relative_to(p)).replace("\\", "/")
                content = file_path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                file_digests[rel] = digest
                hasher.update(f"{rel}:{digest}".encode("utf-8"))

        return hasher.hexdigest(), file_digests


class SkillInstallAction(str, Enum):
    """Lifecycle actions for native skill installation history."""
    IMPORT = "IMPORT"
    INSTALL = "INSTALL"
    REINSTALL = "REINSTALL"
    UPDATE = "UPDATE"
    ROLLBACK = "ROLLBACK"
    UNINSTALL = "UNINSTALL"
    ENABLE = "ENABLE"
    DISABLE = "DISABLE"


@dataclass
class SkillInstallHistoryRecord:
    """Historical audit event for package management operations."""
    skill_id: str
    action: SkillInstallAction
    version: str
    timestamp: float = field(default_factory=time.time)
    source: str = ""
    status: str = "success"          # "success", "failed", "blocked"
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "action": self.action.value,
            "version": self.version,
            "timestamp": self.timestamp,
            "source": self.source,
            "status": self.status,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillInstallHistoryRecord:
        d = dict(data)
        if "action" in d and isinstance(d["action"], str):
            d["action"] = SkillInstallAction(d["action"])
        return cls(**d)
