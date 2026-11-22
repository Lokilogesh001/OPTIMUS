"""core/application_intelligence/adapters/devtools.py — Developer Environment & Tools Adapter.

Integrates with VS Code, Windows Terminal, PowerShell, Git, and test runners.
Opens workspace projects, runs approved commands within workspace boundaries,
and diagnoses test/build outputs.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from core.application_intelligence.adapters.base import BaseApplicationAdapter
from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.registry import ApplicationRegistry
from core.application_intelligence.types import (
    ActionContract,
    AppDefinition,
    AppInteractionTier,
    ExecutionEvidence,
)
from core.outcome import untrusted

logger = logging.getLogger("optimus.application_intelligence.adapter.devtools")


class DevToolsAdapter(BaseApplicationAdapter):
    """Adapter for developer tools (VS Code, Terminal, Git, Python test runners)."""

    def __init__(self, app_def: AppDefinition | None = None) -> None:
        super().__init__(app_def)

    def discover(self) -> bool:
        """Verify developer tools are available."""
        reg = ApplicationRegistry.instance()
        return bool(reg.find_app("code") or reg.find_app("powershell") or reg.find_app("cmd"))

    def connect(self) -> bool:
        self._is_connected = True
        return True

    def open_vscode_workspace(self, workspace_path: str | Path) -> dict[str, Any]:
        """Open a project workspace in VS Code."""
        target = Path(workspace_path).resolve()
        if not target.exists():
            return {"success": False, "error": f"Workspace path '{target}' does not exist."}

        reg = ApplicationRegistry.instance()
        with DesktopInteractionLock(owner="vscode_open", timeout=10.0):
            res = reg.launch_or_activate("code", args=[str(target)])

        return {
            "success": res.get("success", False),
            "workspace": str(target),
            "message": f"Opened workspace '{target.name}' in VS Code.",
        }

    def execute_bounded_command(
        self,
        command: str,
        working_directory: str | Path | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Execute a developer command (e.g. pytest, git status) within authorized boundaries."""
        cwd = Path(working_directory).resolve() if working_directory else Path.cwd()

        # Boundary check: avoid executing commands outside workspace
        ws_root = Path.cwd().resolve()
        if not (cwd == ws_root or cwd.is_relative_to(ws_root)):
            logger.warning("Command execution restricted outside workspace root: %s", cwd)
            cwd = ws_root

        try:
            res = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "is_success": res.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "is_success": False,
            }
        except Exception as ex:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(ex),
                "is_success": False,
            }

    def execute(self, contract: ActionContract) -> ExecutionEvidence:
        """Execute development operations (open workspace, run tests, diagnose output)."""
        op = contract.operation.lower()

        # 1. Open VS Code Workspace
        if op in ("open_workspace", "vscode_open"):
            ws_path = contract.parameters.get("path") or str(Path.cwd())
            res = self.open_vscode_workspace(ws_path)
            if res.get("success"):
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="done",
                    tier_used=AppInteractionTier.TIER_2_ADAPTER,
                    facts=[
                        f"workspace_opened: path='{res['workspace']}'",
                        f"editor='Visual Studio Code'",
                    ],
                    evidence_data=res,
                )
            else:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="failed",
                    tier_used=AppInteractionTier.TIER_2_ADAPTER,
                    error_message=res.get("error", "Failed to open VS Code workspace"),
                )

        # 2. Run Test Suite / Command & Diagnose
        elif op in ("run_command", "run_tests", "diagnose_test_failure"):
            cmd = contract.parameters.get("command", "pytest tests/ -v")
            cwd = contract.parameters.get("working_directory")
            cmd_res = self.execute_bounded_command(cmd, working_directory=cwd)

            # Analyze test diagnosis
            stdout = cmd_res["stdout"]
            stderr = cmd_res["stderr"]
            passed_count = stdout.count("PASSED")
            failed_count = stdout.count("FAILED")

            diagnosis = ""
            if cmd_res["is_success"]:
                diagnosis = f"All tests passed successfully ({passed_count} passed)."
                status_str = "done"
            else:
                diagnosis = f"Execution encountered failures ({failed_count} failed, exit code {cmd_res['exit_code']})."
                status_str = "done"  # The inspection/execution completed, returning diagnostic facts

            return ExecutionEvidence(
                action_id=contract.action_id,
                status=status_str,
                tier_used=AppInteractionTier.TIER_2_ADAPTER,
                facts=[
                    f"command_executed: cmd='{cmd}'",
                    f"exit_code={cmd_res['exit_code']}",
                    f"tests_passed={passed_count}",
                    f"tests_failed={failed_count}",
                ],
                evidence_data={
                    "command": cmd,
                    "exit_code": cmd_res["exit_code"],
                    "diagnosis": diagnosis,
                    "output_preview": untrusted("terminal", stdout[:500] if stdout else stderr[:500]),
                },
            )

        return ExecutionEvidence(
            action_id=contract.action_id,
            status="failed",
            tier_used=AppInteractionTier.TIER_2_ADAPTER,
            error_message=f"Unsupported devtools operation: '{op}'",
        )
