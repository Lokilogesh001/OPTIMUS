"""core/agents/verifier.py — Independent Verification Agent for OPTIMUS.

Provides:
- Independent postcondition validation without trusting model self-reports.
- Physical file existence and content SHA-256 matching.
- Command execution status and output assertion verification.
- Evidence recording into the persistent Verification Evidence Ledger.
"""

from __future__ import annotations

import time
from typing import Any

from core.agents.base import SpecializedAgent
from core.agents.contracts import AgentInputContract, AgentOutputContract
from core.orchestrator.agents import AgentCapability, AgentProfile
from core.orchestrator.tools import CapabilityTier, ToolRegistry
from core.verification.engine import VerificationEngine
from core.verification.types import (
    Postcondition,
    PostconditionType,
    VerificationStatus,
)


def create_verification_agent_profile() -> AgentProfile:
    """Create the canonical Verification Agent profile."""
    return AgentProfile(
        name="verification_agent",
        title="Independent Verification Specialist",
        description="Independently verifies task deliverables, physical files, command outcomes, and logs evidence into the verification ledger.",
        capability=AgentCapability(
            domain="verification",
            allowed_tools=[
                "read_file",
                "list_directory",
                "dev_file_read",
                "dev_file_diff",
                "dev_workspace_info",
                "calculator",
            ],
            allowed_tiers=[CapabilityTier.READ_ONLY],
            max_turns=6,
            timeout_seconds=120.0,
        ),
        instructions=(
            "You are the independent Verification Agent. "
            "Never trust text output or claims. Physically check that files exist, "
            "digests match, and expected postconditions hold. Record verifiable evidence."
        ),
    )


class VerificationAgent(SpecializedAgent):
    """Specialized agent performing independent verification and ledger logging."""

    def __init__(self, profile: AgentProfile | None = None, engine: VerificationEngine | None = None) -> None:
        super().__init__(profile or create_verification_agent_profile())
        self.engine = engine or VerificationEngine()

    def execute(
        self,
        contract: AgentInputContract,
        tool_registry: ToolRegistry,
    ) -> AgentOutputContract:
        """Independently verify deliverables and postconditions."""
        start_time = time.perf_counter()
        context = contract.context_data or {}
        expected_files: list[dict[str, Any]] = context.get("expected_files", [])
        expected_commands: list[dict[str, Any]] = context.get("expected_commands", [])

        records = []
        overall_success = True
        failures = []

        # 1. Verify Expected Files on disk
        for file_spec in expected_files:
            rel_path = file_spec.get("path")
            exp_sha = file_spec.get("sha256")
            min_size = file_spec.get("min_size", 1)

            pc = Postcondition(
                postcondition_type=PostconditionType.FILESYSTEM,
                spec={
                    "path": rel_path,
                    "must_exist": True,
                    "min_size": min_size,
                    "sha256": exp_sha,
                },
                description=f"Verify file '{rel_path}' exists with valid content.",
            )

            rec = self.engine.verify_postcondition(
                task_id=contract.task_id or "task_v",
                subtask_id=contract.subtask_id or "sub_v",
                postcondition=pc,
            )
            records.append(rec.to_dict())
            if rec.status != VerificationStatus.PASS:
                overall_success = False
                failures.append(f"File verification failed: {rel_path} ({rec.verdict or 'failed'})")

        # 2. Verify Command outcomes if present
        for cmd_spec in expected_commands:
            cmd = cmd_spec.get("command")
            exp_code = cmd_spec.get("expected_exit_code", 0)

            pc = Postcondition(
                postcondition_type=PostconditionType.COMMAND,
                spec={
                    "command": cmd,
                    "expected_exit_code": exp_code,
                },
                description=f"Verify command '{cmd}' execution outcome.",
            )
            rec = self.engine.verify_postcondition(
                task_id=contract.task_id or "task_v",
                subtask_id=contract.subtask_id or "sub_v",
                postcondition=pc,
            )
            records.append(rec.to_dict())
            if rec.status != VerificationStatus.PASS:
                overall_success = False
                failures.append(f"Command verification failed: {cmd} ({rec.verdict or 'failed'})")

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        status_str = "success" if overall_success else "failed"

        summary = (
            f"Verification Complete: {len(records) - len(failures)}/{len(records)} checks passed."
            if not failures
            else f"Verification Failed: {len(failures)} failures: " + "; ".join(failures)
        )

        return AgentOutputContract(
            subtask_id=contract.subtask_id,
            agent_name=self.name,
            status=status_str,
            summary=summary,
            deliverables=[],
            side_effects=[],
            evidence={"records": records, "verified": overall_success, "failures": failures},
            execution_time_ms=duration_ms,
            error=failures[0] if failures else None,
        )
