"""core/developer/terminal.py — Sandboxed Command & Process Execution for OPTIMUS.

Enforces:
- Command risk classification (Safe / Mutating / Destructive / Admin).
- Working directory confinement to authorized workspace root.
- Execution timeouts and process tree cleanup.
- Secret redaction and output truncation.
- Audit history logging.
- Explicit user approval enforcement for destructive commands.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.orchestrator.tools import CapabilityTier, sanitize_tool_output
from core.sandbox.command_guard import check_command_safety, kill_process_tree

DESTRUCTIVE_COMMAND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Recursive disk deletion", re.compile(r"(rm\s+-[a-zA-Z]*r[a-zA-Z]*f|del\s+/[sS]\s+/[qQ]|rmdir\s+/[sS])", re.IGNORECASE)),
    ("Disk partition or format", re.compile(r"(format\s+[a-zA-Z]:|mkfs|fdisk|diskpart)", re.IGNORECASE)),
    ("System registry deletion", re.compile(r"reg\s+delete", re.IGNORECASE)),
    ("System folder modification", re.compile(r"([cC]:\\windows|[cC]:\\system32|/etc/|/boot/)", re.IGNORECASE)),
    ("Process termination force", re.compile(r"(taskkill\s+/f\s+/im\s+(?:csrss|lsass|svchost|explorer)\.exe)", re.IGNORECASE)),
    ("Raw device overwrite", re.compile(r"dd\s+if=.*of=(/dev/|\\\\.\\)", re.IGNORECASE)),
    ("Fork bomb", re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.IGNORECASE)),
]

ADMIN_COMMAND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Privilege escalation", re.compile(r"(sudo\s+|su\s+-|runas\s+/user:)", re.IGNORECASE)),
    ("Security setting alteration", re.compile(r"(Set-MpPreference|netsh\s+advfirewall|ufw\s+disable)", re.IGNORECASE)),
    ("Service creation/removal", re.compile(r"(sc\s+delete|sc\s+create|systemctl\s+stop)", re.IGNORECASE)),
]


@dataclass
class CommandExecutionRecord:
    """Audit record of a command executed in the terminal."""
    command: str
    cwd: str
    exit_code: int
    duration_ms: float
    output: str
    error: str = ""
    timestamp: float = field(default_factory=time.time)
    risk_tier: CapabilityTier = CapabilityTier.EXECUTION
    is_destructive: bool = False
    requires_approval: bool = False


class TerminalManager:
    """Manages command execution within workspace bounds with risk classification and timeouts."""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        if workspace_root is None:
            workspace_root = Path(__file__).resolve().parent.parent.parent
        self.workspace_root = Path(workspace_root).resolve()
        self._history: list[CommandExecutionRecord] = []

    def set_workspace_root(self, root: Path | str) -> None:
        """Update active workspace root for terminal operations."""
        self.workspace_root = Path(root).resolve()

    def classify_command(self, command: str) -> tuple[CapabilityTier, bool, str]:
        """Classify command risk level.

        Returns:
            (tier, requires_explicit_approval, reason)
        """
        cmd = str(command or "").strip()
        if not cmd:
            return CapabilityTier.EXECUTION, False, "Empty command"

        # 1. Prohibited check via command_guard
        is_safe, reason = check_command_safety(cmd)
        if not is_safe:
            return CapabilityTier.SYSTEM_DESTRUCTIVE, True, f"Prohibited: {reason}"

        # 2. Destructive check
        for rule_name, pat in DESTRUCTIVE_COMMAND_PATTERNS:
            if pat.search(cmd):
                return CapabilityTier.SYSTEM_DESTRUCTIVE, True, f"Destructive operation: {rule_name}"

        # 3. Administrative check
        for rule_name, pat in ADMIN_COMMAND_PATTERNS:
            if pat.search(cmd):
                return CapabilityTier.SYSTEM_MUTATING, True, f"Administrative operation: {rule_name}"

        # 4. Standard development execution (git, pytest, python, npm, pip)
        return CapabilityTier.EXECUTION, False, "Standard development command"

    def execute(
        self,
        command: str,
        cwd: Path | str | None = None,
        timeout: float = 30.0,
        env: dict[str, str] | None = None,
        is_approved: bool = False,
    ) -> CommandExecutionRecord:
        """Execute a command safely with timeout and output capture."""
        start_t = time.perf_counter()
        cmd_str = str(command or "").strip()

        # Resolve cwd
        if cwd is None:
            exec_cwd = self.workspace_root
        else:
            exec_cwd = Path(cwd).resolve()

        # Boundary check: ensure execution directory is within workspace root
        if exec_cwd != self.workspace_root and self.workspace_root not in exec_cwd.parents:
            rec = CommandExecutionRecord(
                command=cmd_str,
                cwd=str(exec_cwd),
                exit_code=-1,
                duration_ms=0.0,
                output="",
                error=f"WORKSPACE_VIOLATION: Execution directory '{exec_cwd}' escapes workspace root '{self.workspace_root}'",
                risk_tier=CapabilityTier.SYSTEM_MUTATING,
                requires_approval=True,
            )
            self._history.append(rec)
            return rec

        # Risk classification
        tier, needs_approval, risk_reason = self.classify_command(cmd_str)
        if needs_approval and not is_approved:
            rec = CommandExecutionRecord(
                command=cmd_str,
                cwd=str(exec_cwd),
                exit_code=-1,
                duration_ms=0.0,
                output="",
                error=f"APPROVAL_REQUIRED: {risk_reason}. Operation was blocked pending authorization.",
                risk_tier=tier,
                is_destructive=(tier == CapabilityTier.SYSTEM_DESTRUCTIVE),
                requires_approval=True,
            )
            self._history.append(rec)
            return rec

        # Environment preparation
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        # Execute using shell subprocess
        proc = None
        try:
            proc = subprocess.Popen(
                cmd_str,
                shell=True,
                cwd=str(exec_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=run_env,
                encoding="utf-8",
                errors="replace",
            )

            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                kill_process_tree(proc.pid)
                try:
                    stdout, stderr = proc.communicate(timeout=2.0)
                except Exception:
                    stdout, stderr = "", "Process killed due to timeout."
                exit_code = -9
                stderr = f"TIMEOUT: Command exceeded limit of {timeout}s.\n" + (stderr or "")

        except Exception as e:
            if proc and proc.pid:
                kill_process_tree(proc.pid)
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            rec = CommandExecutionRecord(
                command=cmd_str,
                cwd=str(exec_cwd),
                exit_code=-1,
                duration_ms=duration_ms,
                output="",
                error=f"Execution failed: {e}",
                risk_tier=tier,
                is_destructive=(tier == CapabilityTier.SYSTEM_DESTRUCTIVE),
            )
            self._history.append(rec)
            return rec

        duration_ms = (time.perf_counter() - start_t) * 1000.0
        clean_out = sanitize_tool_output(stdout)
        clean_err = sanitize_tool_output(stderr)

        rec = CommandExecutionRecord(
            command=cmd_str,
            cwd=str(exec_cwd),
            exit_code=exit_code,
            duration_ms=duration_ms,
            output=clean_out,
            error=clean_err,
            risk_tier=tier,
            is_destructive=(tier == CapabilityTier.SYSTEM_DESTRUCTIVE),
            requires_approval=False,
        )
        self._history.append(rec)
        return rec

    def run_python(
        self,
        code_or_file: str,
        args: list[str] | None = None,
        cwd: Path | str | None = None,
        timeout: float = 30.0,
    ) -> CommandExecutionRecord:
        """Execute a Python file or inline script using the active interpreter."""
        py_exe = sys.executable
        target = Path(code_or_file)

        if target.is_file():
            arg_str = " ".join(f'"{a}"' for a in (args or []))
            cmd = f'"{py_exe}" "{target}" {arg_str}'
        else:
            # Inline snippet
            escaped = code_or_file.replace('"', '\\"')
            cmd = f'"{py_exe}" -c "{escaped}"'

        return self.execute(cmd, cwd=cwd, timeout=timeout)

    def run_tests(
        self,
        test_path: str = "tests",
        args: list[str] | None = None,
        cwd: Path | str | None = None,
        timeout: float = 60.0,
    ) -> CommandExecutionRecord:
        """Run pytest test suite using the active environment."""
        py_exe = sys.executable
        extra_args = " ".join(args or [])
        cmd = f'"{py_exe}" -m pytest "{test_path}" {extra_args}'
        return self.execute(cmd, cwd=cwd, timeout=timeout)

    def run_git(
        self,
        args: list[str],
        cwd: Path | str | None = None,
    ) -> CommandExecutionRecord:
        """Run a git command in the workspace."""
        cmd = "git " + " ".join(f'"{a}"' if " " in a else a for a in args)
        return self.execute(cmd, cwd=cwd, timeout=30.0)

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return serialized command execution history."""
        results = []
        for r in self._history[-limit:]:
            results.append({
                "command": r.command,
                "cwd": r.cwd,
                "exit_code": r.exit_code,
                "duration_ms": round(r.duration_ms, 1),
                "output": r.output[:2000],
                "error": r.error[:1000],
                "timestamp": r.timestamp,
                "time_str": time.strftime("%H:%M:%S", time.localtime(r.timestamp)),
                "risk_tier": r.risk_tier.value,
                "is_destructive": r.is_destructive,
                "requires_approval": r.requires_approval,
            })
        return results
