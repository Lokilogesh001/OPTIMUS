"""core/marketplace/scanner.py — Security screening, AST analysis, Zip-Slip, and typosquatting detection.

Performs static analysis on plugin metadata, code artifacts, and package archives:
- Typosquatting detection against core tools and known standard packages using Levenshtein distance.
- License category classification (Permissive, Copyleft, Proprietary, Unknown).
- Archive inspection for Zip-Slip directory traversal attempts (.. or absolute paths).
- Build-hook restriction (enforces binary distribution, rejects dynamic setup.py execution).
- Static AST screening for dangerous imports or root-level side effects in Python plugins.
"""
from __future__ import annotations

import ast
import logging
import os
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("optimus.marketplace.scanner")

CORE_TOOL_NAMES = {
    "read_file", "write_file", "edit_file", "delete_file", "list_files",
    "run_command", "bash", "python", "git", "search_web", "inspect_desktop",
    "computer_interact", "terminal_command", "optimus", "gemini", "ollama",
    "hermes", "mcp_client", "system_monitor", "memory_store", "task_planner"
}

POPULAR_PACKAGES = {
    "requests", "urllib3", "pydantic", "fastapi", "numpy", "pandas",
    "pytest", "click", "flask", "aiohttp", "httpx", "cryptography",
    "setuptools", "wheel", "pip", "pyyaml", "jinja2", "certifi"
}

PERMISSIVE_LICENSES = {
    "mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "unlicense", "0bsd", "cc0-1.0"
}

COPYLEFT_LICENSES = {
    "gpl-2.0", "gpl-3.0", "agpl-3.0", "lgpl-2.1", "lgpl-3.0", "mpl-2.0", "epl-2.0"
}


def levenshtein_distance(s1: str, s2: str) -> int:
    """Computes Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if len(s1) == 0:
        return len(s2)
    if len(s2) == 0:
        return len(s1)

    v0 = list(range(len(s2) + 1))
    v1 = [0] * (len(s2) + 1)

    for i in range(len(s1)):
        v1[0] = i + 1
        for j in range(len(s2)):
            cost = 0 if s1[i] == s2[j] else 1
            v1[j + 1] = min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
        v0, v1 = v1, v0

    return v0[len(s2)]


class LicenseCategory(str, Enum):
    PERMISSIVE = "permissive"
    COPYLEFT = "copyleft"
    PROPRIETARY = "proprietary"
    UNKNOWN = "unknown"


@dataclass
class SecurityAssessment:
    is_safe: bool
    score: int  # 0 to 100 (100 = cleanest)
    warnings: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    license_category: LicenseCategory = LicenseCategory.UNKNOWN
    typosquat_target: Optional[str] = None
    has_zip_slip: bool = False
    has_build_hooks: bool = False
    ast_safe: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "score": self.score,
            "warnings": self.warnings,
            "findings": self.findings,
            "license_category": self.license_category.value,
            "typosquat_target": self.typosquat_target,
            "has_zip_slip": self.has_zip_slip,
            "has_build_hooks": self.has_build_hooks,
            "ast_safe": self.ast_safe,
        }


class MarketplaceSecurityScanner:
    """Screening engine for marketplace metadata, packages, and archives."""

    def __init__(self, core_tools: Optional[Set[str]] = None):
        self.core_tools = core_tools or CORE_TOOL_NAMES
        self.popular_packages = POPULAR_PACKAGES

    def check_typosquatting(self, name: str) -> Tuple[bool, Optional[str], int]:
        """Checks if package name is dangerously close to a core tool or popular package."""
        name_clean = name.lower().replace("-", "_").strip()
        all_targets = self.core_tools | self.popular_packages

        if name_clean in all_targets:
            # Exact match is not a typosquat unless it claims to be an external package with a core tool's name
            if name_clean in self.core_tools:
                return True, name_clean, 0
            return False, None, 0

        for target in all_targets:
            dist = levenshtein_distance(name_clean, target.replace("-", "_"))
            if dist in (1, 2) and len(name_clean) >= 4:
                return True, target, dist

        return False, None, -1

    def classify_license(self, license_str: str) -> LicenseCategory:
        """Classifies a license string into Permissive, Copyleft, or Proprietary."""
        if not license_str or license_str.lower() in ("unknown", "none", ""):
            return LicenseCategory.UNKNOWN

        lic_lower = license_str.lower().strip()
        for p in PERMISSIVE_LICENSES:
            if p in lic_lower:
                return LicenseCategory.PERMISSIVE

        for c in COPYLEFT_LICENSES:
            if c in lic_lower:
                return LicenseCategory.COPYLEFT

        return LicenseCategory.PROPRIETARY

    def check_archive_path_traversal(self, archive_path: Path) -> Tuple[bool, List[str]]:
        """Inspects zip or tar archive for Zip-Slip vulnerabilities (path traversal)."""
        offending_members: List[str] = []

        if not archive_path.exists():
            return False, ["Archive file does not exist"]

        # Check Zip
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member_name in zf.namelist():
                    norm = os.path.normpath(member_name)
                    if (
                        norm.startswith("..")
                        or os.path.isabs(member_name)
                        or "/../" in member_name
                        or "\\..\\" in member_name
                        or norm.startswith("/")
                        or norm.startswith("\\")
                    ):
                        offending_members.append(member_name)

        # Check Tar
        elif tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, "r:*") as tf:
                for member in tf.getmembers():
                    norm = os.path.normpath(member.name)
                    if (
                        norm.startswith("..")
                        or os.path.isabs(member.name)
                        or "/../" in member.name
                        or "\\..\\" in member.name
                        or norm.startswith("/")
                        or norm.startswith("\\")
                    ):
                        offending_members.append(member.name)
        else:
            return False, ["Unrecognized archive format"]

        has_slip = len(offending_members) > 0
        return has_slip, offending_members

    def check_build_hooks(self, package_dir_or_archive: Path) -> Tuple[bool, List[str]]:
        """Ensures package does not execute arbitrary setup.py / build hooks.
        
        Enforces binary-only wheels or pure manifest configurations.
        """
        findings: List[str] = []
        if package_dir_or_archive.is_file():
            name_lower = package_dir_or_archive.name.lower()
            if name_lower.endswith(".whl"):
                # Wheels do not run setup.py at install time
                return False, []
            elif name_lower.endswith((".tar.gz", ".tgz", ".zip", ".tar.bz2")):
                # Inspect archive members for setup.py
                has_slip, members = self.check_archive_path_traversal(package_dir_or_archive)
                if zipfile.is_zipfile(package_dir_or_archive):
                    with zipfile.ZipFile(package_dir_or_archive, "r") as zf:
                        names = zf.namelist()
                        if any(n.endswith("setup.py") for n in names):
                            findings.append("Source distribution contains executable setup.py build hook")
                elif tarfile.is_tarfile(package_dir_or_archive):
                    with tarfile.open(package_dir_or_archive, "r:*") as tf:
                        names = tf.getnames()
                        if any(n.endswith("setup.py") for n in names):
                            findings.append("Source distribution contains executable setup.py build hook")
        elif package_dir_or_archive.is_dir():
            if (package_dir_or_archive / "setup.py").exists():
                findings.append("Directory contains executable setup.py build hook")

        has_hooks = len(findings) > 0
        return has_hooks, findings

    def scan_python_code(self, source_code: str) -> Tuple[bool, List[str]]:
        """Static AST analysis for dangerous imports and calls."""
        findings: List[str] = []
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return False, [f"SyntaxError in source code: {e}"]

        dangerous_modules = {"subprocess", "ctypes", "pty", "multiprocessing", "winreg", "socket"}
        dangerous_calls = {"eval", "exec", "__import__", "compile"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_root = alias.name.split(".")[0]
                    if mod_root in dangerous_modules:
                        findings.append(f"Forbidden import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_root = node.module.split(".")[0]
                    if mod_root in dangerous_modules:
                        findings.append(f"Forbidden from-import: {node.module}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in dangerous_calls:
                    findings.append(f"Forbidden dynamic execution call: {node.func.id}()")

        return len(findings) == 0, findings

    def assess_plugin(
        self,
        name: str,
        description: str,
        license_str: str,
        archive_path: Optional[Path] = None,
        source_code: Optional[str] = None
    ) -> SecurityAssessment:
        """Conducts full security assessment on a plugin candidate."""
        score = 100
        warnings: List[str] = []
        findings: List[str] = []

        # 1. Typosquatting
        is_typo, target, dist = self.check_typosquatting(name)
        if is_typo:
            if dist == 0:
                score -= 60
                findings.append(f"Name conflict: exact match with native tool/subsystem '{target}'")
            else:
                score -= 35
                warnings.append(f"Possible typosquatting target '{target}' (edit distance {dist})")

        # 2. License
        lic_cat = self.classify_license(license_str)
        if lic_cat == LicenseCategory.COPYLEFT:
            warnings.append(f"Copyleft license detected ({license_str}); review distribution restrictions")
            score -= 10
        elif lic_cat == LicenseCategory.UNKNOWN:
            warnings.append("Unknown or unparsed license")
            score -= 5

        # 3. Archive / Zip-Slip checks
        has_zip_slip = False
        has_build_hooks = False
        if archive_path and archive_path.exists():
            slip_detected, slip_members = self.check_archive_path_traversal(archive_path)
            if slip_detected:
                has_zip_slip = True
                score = 0
                findings.append(f"CRITICAL: Zip-Slip path traversal detected in members: {slip_members}")

            hooks_detected, hook_findings = self.check_build_hooks(archive_path)
            if hooks_detected:
                has_build_hooks = True
                score -= 25
                findings.extend(hook_findings)

        # 4. AST checks
        ast_safe = True
        if source_code:
            ast_ok, ast_findings = self.scan_python_code(source_code)
            if not ast_ok:
                ast_safe = False
                score -= 30
                findings.extend(ast_findings)

        score = max(0, min(100, score))
        is_safe = (score >= 60) and (not has_zip_slip) and (not has_build_hooks) and (not (is_typo and dist == 0))

        return SecurityAssessment(
            is_safe=is_safe,
            score=score,
            warnings=warnings,
            findings=findings,
            license_category=lic_cat,
            typosquat_target=target if is_typo else None,
            has_zip_slip=has_zip_slip,
            has_build_hooks=has_build_hooks,
            ast_safe=ast_safe,
        )
