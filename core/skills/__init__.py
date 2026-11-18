"""core/skills — Secure, Extensible Skill Intelligence & Management System for OPTIMUS.

Provides:
- Manifest models (SkillManifest, SkillSecurityReport, SkillTrustLevel, SkillType, SkillCategory).
- Multi-root discovery engine for ECC, gstack, Anti-Slop, Chisle, UI skills, and NVIDIA SkillSpector.
- Persistent thread-safe SkillRegistry.
- Static security validator & SkillSpector bridge.
- Intelligent intent & role-based skill selector.
- In-memory skill loader and prompt injector.
- Sandboxed skill executor with central ToolRegistry routing.
- Complete lifecycle management (discovery, validation, activation, update, rollback).
"""

from core.skills.dependency import DependencyResolver
from core.skills.discovery import SkillDiscoveryEngine
from core.skills.executor import SkillExecutor
from core.skills.installer import InstallResult, NativeSkillInstaller
from core.skills.lifecycle import SkillLifecycleManager
from core.skills.loader import SkillLoader
from core.skills.models import (
    SkillCategory,
    SkillExecutionRecord,
    SkillManifest,
    SkillSecurityIssue,
    SkillSecurityReport,
    SkillTrustLevel,
    SkillType,
)
from core.skills.package import (
    DependencyKind,
    DependencyStatus,
    HardwareRequirements,
    SkillDependency,
    SkillInstallAction,
    SkillInstallHistoryRecord,
    SkillPackageManifest,
)
from core.skills.registry import SkillRegistry
from core.skills.security import SkillSecurityValidator
from core.skills.selector import SelectedSkillSet, SkillSelector
from core.skills.validator import SkillValidator

__all__ = [
    "DependencyKind",
    "DependencyResolver",
    "DependencyStatus",
    "HardwareRequirements",
    "InstallResult",
    "NativeSkillInstaller",
    "SelectedSkillSet",
    "SkillCategory",
    "SkillDependency",
    "SkillDiscoveryEngine",
    "SkillExecutionRecord",
    "SkillExecutor",
    "SkillInstallAction",
    "SkillInstallHistoryRecord",
    "SkillLifecycleManager",
    "SkillLoader",
    "SkillManifest",
    "SkillPackageManifest",
    "SkillRegistry",
    "SkillSecurityIssue",
    "SkillSecurityReport",
    "SkillSecurityValidator",
    "SkillSelector",
    "SkillTrustLevel",
    "SkillType",
    "SkillValidator",
]
