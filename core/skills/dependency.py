"""core/skills/dependency.py — Explicit Dependency Resolver & Compatibility Checker.

Classifies and verifies:
1. OPTIMUS Built-in Tool capabilities (from ToolRegistry).
2. Python environment dependencies (without modifying the runtime environment).
3. External system binaries (git, pytest, node, curl, etc.).
4. System & Platform requirements (Windows, Linux, macOS).
5. Optional third-party integrations (Reticle, SkillSpector, Figma).
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
from typing import TYPE_CHECKING

from core.skills.package import (
    DependencyKind,
    DependencyStatus,
    HardwareRequirements,
    SkillDependency,
)

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry


class DependencyResolver:
    """Evaluates and validates dependencies for native OPTIMUS skill packages."""

    def __init__(self, tool_registry: ToolRegistry | None = None) -> None:
        self.tool_registry = tool_registry

    def resolve_dependencies(
        self,
        dependencies: list[SkillDependency],
        tool_registry: ToolRegistry | None = None,
    ) -> list[SkillDependency]:
        """Verify each declared dependency and return updated dependency objects with verification status."""
        active_registry = tool_registry or self.tool_registry
        resolved: list[SkillDependency] = []

        for dep in dependencies:
            checked_dep = SkillDependency(
                name=dep.name,
                kind=dep.kind,
                required=dep.required,
                version_constraint=dep.version_constraint,
            )

            if dep.kind == DependencyKind.OPTIMUS_TOOL:
                is_available = active_registry.is_registered(dep.name) if active_registry else False
                if is_available:
                    checked_dep.status = DependencyStatus.SATISFIED
                    checked_dep.details = f"OPTIMUS capability '{dep.name}' is registered and active."
                else:
                    checked_dep.status = DependencyStatus.MISSING if dep.required else DependencyStatus.OPTIONAL
                    checked_dep.details = f"Required tool '{dep.name}' is not registered in ToolRegistry."

            elif dep.kind == DependencyKind.PYTHON_PACKAGE:
                spec = importlib.util.find_spec(dep.name)
                if spec is not None:
                    checked_dep.status = DependencyStatus.SATISFIED
                    checked_dep.details = f"Python package '{dep.name}' detected in active environment."
                else:
                    checked_dep.status = DependencyStatus.MISSING if dep.required else DependencyStatus.OPTIONAL
                    checked_dep.details = f"Python package '{dep.name}' not found."

            elif dep.kind == DependencyKind.EXTERNAL_BINARY:
                bin_path = shutil.which(dep.name)
                if bin_path:
                    checked_dep.status = DependencyStatus.SATISFIED
                    checked_dep.details = f"Binary '{dep.name}' located at '{bin_path}'."
                else:
                    checked_dep.status = DependencyStatus.MISSING if dep.required else DependencyStatus.OPTIONAL
                    checked_dep.details = f"System binary '{dep.name}' not found in PATH."

            elif dep.kind == DependencyKind.SYSTEM:
                # Platform or OS check
                current_os = platform.system().lower()
                if dep.name.lower() in (current_os, "any", "all"):
                    checked_dep.status = DependencyStatus.SATISFIED
                    checked_dep.details = f"System platform '{platform.system()}' is compatible."
                else:
                    checked_dep.status = DependencyStatus.MISSING if dep.required else DependencyStatus.OPTIONAL
                    checked_dep.details = f"Target system '{dep.name}' does not match current '{platform.system()}'."

            elif dep.kind == DependencyKind.INTEGRATION:
                # Optional external integrations
                bin_path = shutil.which(dep.name)
                if bin_path:
                    checked_dep.status = DependencyStatus.SATISFIED
                    checked_dep.details = f"Integration CLI '{dep.name}' detected."
                else:
                    checked_dep.status = DependencyStatus.OPTIONAL
                    checked_dep.details = f"Optional integration '{dep.name}' not currently installed."

            else:
                checked_dep.status = DependencyStatus.UNKNOWN
                checked_dep.details = f"Unrecognized dependency kind: {dep.kind}"

            resolved.append(checked_dep)

        return resolved

    def check_hardware_compatibility(self, hw: HardwareRequirements) -> tuple[bool, list[str]]:
        """Verify hardware compatibility using HardwareDetector without requiring GPU / CUDA acceleration."""
        warnings: list[str] = []
        is_compatible = True

        try:
            from core.runtime.hardware import HardwareDetector
            rep = HardwareDetector.instance().detect()

            # Check CUDA only if explicitly declared mandatory
            if hw.cuda_required and not rep.is_cuda_available:
                warnings.append("CUDA is declared required but no active CUDA device is available.")
                is_compatible = False

            # Check RAM if declared
            if hw.min_ram_mb > 0 and rep.memory.total_ram_mb < hw.min_ram_mb:
                warnings.append(f"Requires {hw.min_ram_mb:.0f} MB RAM, but system has {rep.memory.total_ram_mb:.0f} MB.")
                is_compatible = False
        except Exception:
            pass

        return is_compatible, warnings

    def extract_inferred_dependencies(
        self,
        required_tools: list[str],
        instruction_text: str = "",
    ) -> list[SkillDependency]:
        """Infer dependencies from required tools and standard mentions in instructions."""
        deps: list[SkillDependency] = []

        # 1. OPTIMUS tools
        for tool in required_tools:
            deps.append(SkillDependency(
                name=tool,
                kind=DependencyKind.OPTIMUS_TOOL,
                required=True,
                status=DependencyStatus.UNKNOWN,
            ))

        # 2. Check for common tools mentioned in markdown
        text_lower = instruction_text.lower()
        common_binaries = ["git", "pytest", "node", "npm", "curl", "python"]
        for b in common_binaries:
            # If specifically instructed e.g. "run pytest", "run git"
            if f"run {b}" in text_lower or f"`{b} " in text_lower:
                deps.append(SkillDependency(
                    name=b,
                    kind=DependencyKind.EXTERNAL_BINARY,
                    required=False,
                    status=DependencyStatus.UNKNOWN,
                    details=f"Referenced in instructions as '{b}'",
                ))

        return deps
