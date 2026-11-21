"""core/agents/developer.py — Autonomous Developer Agent for OPTIMUS.

Implements the 8-Phase Lifecycle:
UNDERSTAND → INSPECT → PLAN → APPROVE → IMPLEMENT → EXECUTE → VERIFY → REPORT

Coordinates with:
- Code Intelligence Agent (AST & structure)
- Execution Agent (Sandboxed commands)
- Verification Agent (Independent postconditions)
- Central ToolRegistry & PermissionManager
"""

from __future__ import annotations

import time
from typing import Any

from core.agents.base import SpecializedAgent
from core.agents.contracts import AgentInputContract, AgentOutputContract
from core.developer.lifecycle import (
    DeveloperTaskPlan,
    LifecyclePhase,
)
from core.developer.tools import get_active_terminal, get_active_workspace
from core.orchestrator.agents import AgentCapability, AgentProfile
from core.orchestrator.tools import CapabilityTier, ToolRegistry
from core.security.permissions import PermissionManager


def create_developer_profile() -> AgentProfile:
    """Create the canonical Developer Agent profile with development capabilities."""
    return AgentProfile(
        name="developer_agent",
        title="Autonomous Developer Specialist",
        description="Autonomous coding specialist performing the 8-phase engineering lifecycle (Understand, Inspect, Plan, Approve, Implement, Execute, Verify, Report).",
        capability=AgentCapability(
            domain="development",
            allowed_tools=[
                "dev_workspace_info",
                "dev_file_read",
                "dev_file_write",
                "dev_file_diff",
                "dev_file_rollback",
                "dev_search_symbols",
                "dev_grep",
                "dev_run_command",
                "dev_run_tests",
                "dev_git_status",
                "read_file",
                "write_file",
                "list_directory",
                "open_in_explorer",
                "open_directory",
                "open_folder",
                "calculator",
                "enumerate_windows",
                "focus_window",
                "enumerate_browser_tabs",
                "switch_browser_tab",
                "capture_visual_state",
                "explain_desktop_state",
            ],
            allowed_tiers=[
                CapabilityTier.READ_ONLY,
                CapabilityTier.WORKSPACE_WRITE,
                CapabilityTier.EXECUTION,
            ],
            max_turns=12,
            timeout_seconds=300.0,
        ),
        instructions=(
            "You are the OPTIMUS Autonomous Developer Agent. "
            "Execute the 8-phase development lifecycle strictly: "
            "UNDERSTAND the task -> INSPECT existing files -> PLAN discrete edits -> "
            "APPROVE safety -> IMPLEMENT with backups -> EXECUTE tests -> "
            "VERIFY results independently -> REPORT exact changes."
        ),
    )


class DeveloperAgent(SpecializedAgent):
    """The Autonomous Developer Agent orchestrating end-to-end development tasks."""

    def __init__(self, profile: AgentProfile | None = None) -> None:
        super().__init__(profile or create_developer_profile())
        self.workspace = get_active_workspace()
        self.terminal = get_active_terminal()
        self.permission_mgr = PermissionManager.instance()

    def execute(
        self,
        contract: AgentInputContract,
        tool_registry: ToolRegistry,
    ) -> AgentOutputContract:
        """Execute a development task through the 8-phase lifecycle."""
        start_time = time.perf_counter()
        task_id = contract.task_id or f"dev_{int(time.time())}"
        goal = contract.goal

        plan = DeveloperTaskPlan(
            task_id=task_id,
            goal=goal,
        )

        deliverables: list[dict[str, Any]] = []
        side_effects: list[dict[str, Any]] = []

        try:
            # ── 1. UNDERSTAND ────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.UNDERSTAND)
            task_context = contract.context_data or {}
            target_files = task_context.get("target_files", [])
            test_target = task_context.get("test_target", "tests")
            plan.record_phase_result(
                LifecyclePhase.UNDERSTAND,
                status="success",
                summary=f"Parsed development task: '{goal}'. Identified target scope.",
                evidence={"goal": goal, "target_files": target_files, "test_target": test_target},
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )

            # ── 2. INSPECT ───────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.INSPECT)
            inspected_data: dict[str, Any] = {}
            dev_ctx = {
                "workspace": self.workspace,
                "terminal": self.terminal,
                "allowed_roots": [str(self.workspace.root)],
                "is_approved": False,
            }

            # If specific files were indicated, read them; otherwise gather workspace stats
            if target_files:
                for rel_path in target_files:
                    try:
                        read_res = self.call_tool(tool_registry, "dev_file_read", {"path": rel_path}, context=dev_ctx)
                        if read_res.is_success:
                            inspected_data[rel_path] = read_res.evidence
                            plan.inspected_files.append(rel_path)
                    except Exception:
                        pass

            if not plan.inspected_files:
                # Query workspace info
                ws_info = self.call_tool(tool_registry, "dev_workspace_info", {}, context=dev_ctx)
                inspected_data["workspace"] = ws_info.evidence if ws_info.is_success else {}

            plan.record_phase_result(
                LifecyclePhase.INSPECT,
                status="success",
                summary=f"Inspected {len(plan.inspected_files)} file(s) and workspace structure.",
                evidence=inspected_data,
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )

            # ── 3. PLAN ──────────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.PLAN)
            file_edits = task_context.get("file_edits", [])  # list of {"path": ..., "content": ...}
            commands_to_run = task_context.get("commands", [])

            for edit in file_edits:
                plan.planned_modifications.append({
                    "path": edit.get("path"),
                    "action": "write",
                })

            plan.record_phase_result(
                LifecyclePhase.PLAN,
                status="success",
                summary=f"Planned {len(plan.planned_modifications)} file modification(s) and {len(commands_to_run)} command(s).",
                evidence={"planned_edits": plan.planned_modifications, "commands": commands_to_run},
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )

            # ── 4. APPROVE ───────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.APPROVE)

            # Check if execution context provided authorization
            is_pre_approved = bool(task_context.get("is_approved", False))
            requires_approval = False
            approval_reason = ""

            for edit in file_edits:
                tool_def = tool_registry.get("dev_file_write")
                if tool_def:
                    verdict = self.permission_mgr.evaluate_tool(
                        tool=tool_def,
                        caller_agent=self.name,
                        arguments=edit,
                        workspace_roots=[str(self.workspace.root)],
                        is_pre_approved=is_pre_approved,
                    )
                    if verdict.requires_approval:
                        requires_approval = True
                        approval_reason = verdict.approval_reason
                        break

            for cmd in commands_to_run:
                tool_def = tool_registry.get("dev_run_command")
                if tool_def:
                    verdict = self.permission_mgr.evaluate_tool(
                        tool=tool_def,
                        caller_agent=self.name,
                        arguments={"command": cmd},
                        workspace_roots=[str(self.workspace.root)],
                        is_pre_approved=is_pre_approved,
                    )
                    if verdict.requires_approval:
                        requires_approval = True
                        approval_reason = verdict.approval_reason
                        break
            plan.requires_approval = requires_approval
            plan.is_approved = is_pre_approved or not requires_approval
            plan.approval_reason = approval_reason

            if requires_approval and not is_pre_approved:
                plan.record_phase_result(
                    LifecyclePhase.APPROVE,
                    status="waiting_approval",
                    summary=f"Task paused: User authorization required ({approval_reason}).",
                    evidence={"reason": approval_reason},
                    duration_ms=(time.perf_counter() - p_start) * 1000.0,
                )
                return AgentOutputContract(
                    subtask_id=contract.subtask_id,
                    agent_name=self.name,
                    status="waiting_approval",
                    summary=f"Paused for approval: {approval_reason}",
                    deliverables=[],
                    side_effects=[],
                    evidence=plan.to_dict(),
                    execution_time_ms=(time.perf_counter() - start_time) * 1000.0,
                    error=f"APPROVAL_REQUIRED: {approval_reason}",
                )

            plan.record_phase_result(
                LifecyclePhase.APPROVE,
                status="success",
                summary="Permissions validated. Authorization confirmed.",
                evidence={"profile": self.permission_mgr.profile.value, "approved": plan.is_approved},
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )

            # ── 5. IMPLEMENT ─────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.IMPLEMENT)
            exec_ctx = {
                "workspace": self.workspace,
                "terminal": self.terminal,
                "allowed_roots": [str(self.workspace.root)],
                "is_approved": plan.is_approved,
            }

            for edit in file_edits:
                edit_path = edit.get("path")
                edit_content = edit.get("content", "")
                write_res = self.call_tool(
                    tool_registry,
                    "dev_file_write",
                    {"path": edit_path, "content": edit_content, "create_backup": True},
                    context=exec_ctx,
                )
                if not write_res.is_success:
                    raise RuntimeError(f"Implementation failed writing '{edit_path}': {write_res.error}")

                plan.applied_modifications.append(write_res.evidence)
                side_effects.append({
                    "type": "file_write",
                    "path": edit_path,
                    "sha256": write_res.evidence.get("sha256"),
                    "snapshot_id": write_res.evidence.get("snapshot_id"),
                })
                deliverables.append({
                    "path": edit_path,
                    "size_bytes": write_res.evidence.get("bytes_written"),
                    "sha256": write_res.evidence.get("sha256"),
                })

            plan.record_phase_result(
                LifecyclePhase.IMPLEMENT,
                status="success",
                summary=f"Applied {len(plan.applied_modifications)} file modification(s) with automatic backups.",
                evidence={"applied": plan.applied_modifications},
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )

            # ── 6. EXECUTE ───────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.EXECUTE)

            for cmd in commands_to_run:
                cmd_res = self.call_tool(
                    tool_registry,
                    "dev_run_command",
                    {"command": cmd},
                    context=exec_ctx,
                )
                plan.executed_commands.append({
                    "command": cmd,
                    "status": cmd_res.status,
                    "output": cmd_res.output[:2000],
                    "evidence": cmd_res.evidence,
                })
                if not cmd_res.is_success:
                    raise RuntimeError(f"Command execution failed: '{cmd}' -> {cmd_res.error or cmd_res.output}")

            plan.record_phase_result(
                LifecyclePhase.EXECUTE,
                status="success",
                summary=f"Executed {len(commands_to_run)} command(s) successfully.",
                evidence={"commands": plan.executed_commands},
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )

            # ── 7. VERIFY ────────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.VERIFY)

            # Independent verification of file existence and non-zero content
            verification_checks: list[dict[str, Any]] = []
            for item in deliverables:
                f_path = item.get("path")
                v_res = self.call_tool(tool_registry, "dev_file_read", {"path": f_path}, context=exec_ctx)
                passed = v_res.is_success and v_res.evidence.get("sha256") == item.get("sha256")
                verification_checks.append({
                    "target": f_path,
                    "verified": passed,
                    "expected_sha256": item.get("sha256"),
                    "actual_sha256": v_res.evidence.get("sha256") if v_res.is_success else None,
                })
                if not passed:
                    raise RuntimeError(f"Verification failure on '{f_path}': digest mismatch or missing.")

            plan.verification_evidence = {"checks": verification_checks, "verified_count": len(verification_checks)}
            plan.record_phase_result(
                LifecyclePhase.VERIFY,
                status="success",
                summary=f"Verified {len(verification_checks)} deliverable(s) physically on disk.",
                evidence=plan.verification_evidence,
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )

            # ── 8. REPORT ────────────────────────────────────────────────────
            p_start = time.perf_counter()
            plan.advance_phase(LifecyclePhase.REPORT)

            report_lines = [
                f"### OPTIMUS Developer Execution Report: {goal}",
                f"- **Task ID:** `{task_id}`",
                "- **Status:** COMPLETED SUCCESSFULLY",
                f"- **Phases:** {' -> '.join(p.value for p in plan.phases_completed)}",
                f"- **Modified Files ({len(plan.applied_modifications)}):**",
            ]
            for m in plan.applied_modifications:
                report_lines.append(f"  • `{m.get('relative_path')}` (sha256: {m.get('sha256', '')[:8]}, {m.get('diff_summary', '')})")

            if plan.executed_commands:
                report_lines.append(f"- **Executed Commands ({len(plan.executed_commands)}):**")
                for c in plan.executed_commands:
                    report_lines.append(f"  • `{c.get('command')}` -> {c.get('status')}")

            report_lines.append(f"- **Physical Verification:** {len(verification_checks)} checks PASSED.")

            final_report = "\n".join(report_lines)
            plan.final_report = final_report
            plan.record_phase_result(
                LifecyclePhase.REPORT,
                status="success",
                summary="Final report assembled.",
                evidence={"report": final_report},
                duration_ms=(time.perf_counter() - p_start) * 1000.0,
            )
            plan.advance_phase(LifecyclePhase.COMPLETED)
            plan.end_time = time.time()

            return AgentOutputContract(
                subtask_id=contract.subtask_id,
                agent_name=self.name,
                status="success",
                summary=final_report,
                deliverables=deliverables,
                side_effects=side_effects,
                evidence=plan.to_dict(),
                execution_time_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        except Exception as e:
            plan.advance_phase(LifecyclePhase.FAILED)
            plan.error = str(e)
            plan.end_time = time.time()
            return AgentOutputContract(
                subtask_id=contract.subtask_id,
                agent_name=self.name,
                status="failed",
                summary=f"Developer task failed during phase '{plan.current_phase.value}': {e}",
                deliverables=deliverables,
                side_effects=side_effects,
                evidence=plan.to_dict(),
                execution_time_ms=(time.perf_counter() - start_time) * 1000.0,
                error=str(e),
            )
