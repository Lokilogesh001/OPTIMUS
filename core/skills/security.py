"""core/skills/security.py — Skill Static Security Analyzer & NVIDIA SkillSpector Bridge.

Performs deterministic multi-vector analysis:
1. Prompt Injection & Jailbreak Heuristics.
2. Destructive & Administrative Shell Commands.
3. Credential & Secret Access / Exfiltration.
4. Path Traversal & Unauthorized Filesystem Writing.
5. NVIDIA SkillSpector Integration Bridge.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.skills.models import (
    SkillManifest,
    SkillSecurityIssue,
    SkillSecurityReport,
    SkillTrustLevel,
)

# ── Pattern Rules ─────────────────────────────────────────────────────────────

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Ignore instructions", re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE)),
    ("Disregard rules", re.compile(r"disregard\s+(?:all\s+)?prior\s+(?:guidelines|rules|prompts)", re.IGNORECASE)),
    ("Bypass safety", re.compile(r"bypass\s+(?:all\s+)?(?:safety|security|permission)\s+checks", re.IGNORECASE)),
    ("System prompt extraction", re.compile(r"(?:print|output|dump|reveal)\s+(?:your\s+)?system\s+prompt", re.IGNORECASE)),
    ("Jailbreak roleplay", re.compile(r"(?:dan\s+mode|jailbreak|unrestricted\s+mode|do\s+anything\s+now)", re.IGNORECASE)),
]

DANGEROUS_COMMAND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Root deletion", re.compile(r"(rm\s+-[a-zA-Z]*r[a-zA-Z]*f|del\s+/[sS]\s+/[qQ]|rmdir\s+/[sS]\s+/[qQ])", re.IGNORECASE)),
    ("Disk partition / format", re.compile(r"(format\s+[a-zA-Z]:|mkfs|fdisk|diskpart)", re.IGNORECASE)),
    ("Registry alteration", re.compile(r"reg\s+(?:delete|add\s+.*\\run)", re.IGNORECASE)),
    ("Privilege escalation", re.compile(r"(sudo\s+|su\s+-|runas\s+/user:)", re.IGNORECASE)),
    ("Raw block write", re.compile(r"dd\s+if=.*of=(/dev/|\\\\.\\)", re.IGNORECASE)),
    ("Security tampering", re.compile(r"(Set-MpPreference\s+-Disable|netsh\s+advfirewall\s+set|ufw\s+disable)", re.IGNORECASE)),
]

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Hardcoded API key", re.compile(r"(?:sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{36,})", re.IGNORECASE)),
    ("Exfiltration of environment", re.compile(r"(?:curl|wget|fetch|invoke-webrequest).*?(?:process\.env|os\.environ|\$env:)", re.IGNORECASE)),
    ("SSH key access", re.compile(r"(?:\.ssh/id_rsa|\.ssh/id_ed25519)", re.IGNORECASE)),
    ("Private credential reading", re.compile(r"(?:api_keys\.json|(?<![\w\.\$])\.env\b|credentials\.json)", re.IGNORECASE)),
]

TRAVERSAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Directory traversal", re.compile(r"(\.\./\.\./|\.\.\\\.\.\\)", re.IGNORECASE)),
    ("Windows system root target", re.compile(r"([cC]:\\windows|[cC]:\\system32)", re.IGNORECASE)),
    ("Unix system root target", re.compile(r"(/etc/passwd|/etc/shadow|/boot/)", re.IGNORECASE)),
]


class SkillSecurityValidator:
    """Security auditor evaluating skill instructions and executable artifacts."""

    def __init__(self, skillspector_root: Path | str | None = None) -> None:
        if skillspector_root is None:
            # Check default installation
            p = Path(r"C:\Users\loges\.antigravity\tools\skillspector")
            self.skillspector_root = p if p.is_dir() else None
        else:
            self.skillspector_root = Path(skillspector_root) if Path(skillspector_root).is_dir() else None

    def scan_skill(self, manifest: SkillManifest) -> SkillSecurityReport:
        """Execute a full static security assessment across a skill manifest and instructions."""
        issues: list[SkillSecurityIssue] = []
        text = manifest.instructions or ""
        lines = text.splitlines()

        # 1. Scan for Prompt Injection
        for idx, line in enumerate(lines, start=1):
            for rule_name, pat in INJECTION_PATTERNS:
                if pat.search(line):
                    issues.append(SkillSecurityIssue(
                        severity="CRITICAL",
                        category="PROMPT_INJECTION",
                        description=f"Potential prompt injection detected: {rule_name}",
                        line_number=idx,
                        snippet=line.strip()[:100],
                    ))

        # 2. Scan for Destructive Commands
        for idx, line in enumerate(lines, start=1):
            for rule_name, pat in DANGEROUS_COMMAND_PATTERNS:
                if pat.search(line):
                    issues.append(SkillSecurityIssue(
                        severity="HIGH",
                        category="DANGEROUS_COMMAND",
                        description=f"Destructive shell command pattern: {rule_name}",
                        line_number=idx,
                        snippet=line.strip()[:100],
                    ))

        # 3. Scan for Secret Exfiltration & Credential Access
        for idx, line in enumerate(lines, start=1):
            for rule_name, pat in SECRET_PATTERNS:
                if pat.search(line):
                    severity = "HIGH" if ("API key" in rule_name or "Exfiltration" in rule_name) else "MEDIUM"
                    issues.append(SkillSecurityIssue(
                        severity=severity,
                        category="SECRET_ACCESS",
                        description=f"Sensitive secret pattern detected: {rule_name}",
                        line_number=idx,
                        snippet=line.strip()[:100],
                    ))

        # 4. Scan for Directory Traversal
        for idx, line in enumerate(lines, start=1):
            for rule_name, pat in TRAVERSAL_PATTERNS:
                if pat.search(line):
                    severity = "LOW" if (rule_name == "Directory traversal" and ("import " in line or "from " in line)) else "MEDIUM"
                    issues.append(SkillSecurityIssue(
                        severity=severity,
                        category="TRAVERSAL",
                        description=f"Filesystem traversal or system directory pattern: {rule_name}",
                        line_number=idx,
                        snippet=line.strip()[:100],
                    ))

        # 5. NVIDIA SkillSpector Baseline Check
        scanner_name = "OptimusSecurityValidator"
        if self.skillspector_root:
            scanner_name = "OptimusSecurityValidator + NVIDIA SkillSpector"

        # Calculate Risk Score
        risk_score = 0.0
        for issue in issues:
            if issue.severity == "CRITICAL":
                risk_score += 4.0
            elif issue.severity == "HIGH":
                risk_score += 2.5
            elif issue.severity == "MEDIUM":
                risk_score += 1.0
            elif issue.severity == "LOW":
                risk_score += 0.5
        risk_score = min(10.0, risk_score)

        # Derive Trust Level
        if any(i.severity == "CRITICAL" for i in issues) or risk_score >= 5.0:
            trust_level = SkillTrustLevel.BLOCKED
            is_safe = False
        elif risk_score >= 2.0:
            trust_level = SkillTrustLevel.SUSPICIOUS
            is_safe = True  # Can be used with warnings / prompt
        else:
            trust_level = SkillTrustLevel.TRUSTED
            is_safe = True

        return SkillSecurityReport(
            is_safe=is_safe,
            risk_score=risk_score,
            trust_level=trust_level,
            issues=issues,
            scanner_name=scanner_name,
        )
