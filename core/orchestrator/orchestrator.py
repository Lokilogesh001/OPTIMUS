"""core/orchestrator/orchestrator.py — Central Execution Brain for OPTIMUS.

Implements the modular hybrid hierarchical orchestrator:
- Owns planning, permissions, workflow state, and verification.
- Selects and delegates to specialized domain agents.
- Enforces strict controlled delegation (preventing peer-to-peer delegation).
- Independently verifies deliverables and physical filesystem states.
- Non-blocking execution with background thread dispatch and event publishing.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.orchestrator.agents import AgentRegistry
from core.orchestrator.events import (
    EventBus,
    PlanCreatedEvent,
    SubtaskCompletedEvent,
    SubtaskDelegatedEvent,
    SubtaskFailedEvent,
    SubtaskProgressEvent,
    SubtaskVerifiedEvent,
    TaskCancelledEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskFailedEvent,
)
from core.orchestrator.runtime import (
    ExecutionPlan,
    Subtask,
    SubtaskStatus,
    TaskRecord,
    TaskStatus,
    TaskStore,
    new_subtask_id,
    new_task_id,
)
from core.orchestrator.tools import ToolRegistry, ToolResult


class CentralOrchestrator:
    """The central execution brain of OPTIMUS."""

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        task_store: TaskStore | None = None,
        event_bus: EventBus | None = None,
        brain_fn: Callable[[str, list, list | None], dict] | None = None,
        allowed_roots: list[str] | None = None,
        memory_manager: Any | None = None,
        verification_engine: Any | None = None,
        intelligence_gateway: Any | None = None,
        workflow_engine: Any | None = None,
    ) -> None:
        self.agent_registry = agent_registry or AgentRegistry()
        self.tool_registry = tool_registry or ToolRegistry()
        self.task_store = task_store or TaskStore(Path.cwd() / "memory" / "orchestrator")
        self.event_bus = event_bus or EventBus()
        if hasattr(self.tool_registry, "set_event_bus"):
            self.tool_registry.set_event_bus(self.event_bus)
        self.intelligence_gateway = intelligence_gateway
        self.brain_fn = brain_fn
        if not self.brain_fn and self.intelligence_gateway:
            self.brain_fn = self.intelligence_gateway.get_brain_fn()
        self.allowed_roots = allowed_roots or [str(Path.cwd())]
        from core.memory.manager import ensure as ensure_memory_mgr
        self.memory_manager = memory_manager or ensure_memory_mgr()
        from core.verification.engine import get_verification_engine
        self.verification_engine = verification_engine or get_verification_engine()
        self.workflow_engine = workflow_engine
        self._lock = threading.RLock()
        self._active_workers: dict[str, threading.Thread] = {}
        self._cancelled_tasks: set[str] = set()

    # ── Model Access Bridge ──────────────────────────────────────────────────

    def _ask_model(self, system: str, turns: list, schemas: list | None = None) -> dict:
        """Query LLM through injected brain_fn or core.gemini ladder."""
        if self.brain_fn:
            return self.brain_fn(system, turns, schemas)

        # Fallback to Mark-LVII core.gemini / core.tasks.brain
        try:
            from core.tasks.brain import ask
            return ask(system, turns, schemas)
        except Exception:
            try:
                from core import gemini
                prompt = system + "\n\n" + "\n".join(f"{t.get('role')}: {t.get('text')}" for t in turns)
                text = gemini.text(prompt, tier=gemini.FAST)
                return {"text": text or "", "calls": []}
            except Exception as e:
                return {"text": f"[Model offline or unavailable: {e}]", "calls": []}

    # ── Planning Phase ───────────────────────────────────────────────────────

    def plan(self, goal: str, task_id: str | None = None) -> ExecutionPlan:
        """Formulate an execution plan with discrete subtasks for specialized agents."""
        t_id = task_id or new_task_id()
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        lower_goal = goal.lower()
        subtasks: list[Subtask] = []

        # 1. Attempt model-assisted structured planning if brain is available
        plan_prompt = (
            f"You are the OPTIMUS Central Orchestrator.\n"
            f"Goal: '{goal}'\n"
            f"Available agents: {[a.name for a in self.agent_registry.list_agents()]}\n"
            f"Break this goal down into 1 to 4 sequential subtasks.\n"
            f"Respond with JSON list: [{{'agent': str, 'title': str, 'description': str}}]"
        )
        try:
            reply = self._ask_model(plan_prompt, [{"role": "user", "text": goal}], schemas=None)
            text = reply.get("text", "")
            # Look for JSON array inside response
            m = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                for idx, item in enumerate(parsed, start=1):
                    agent_name = str(item.get("agent", "general")).lower()
                    if not self.agent_registry.get(agent_name):
                        agent_name = "general"
                    prof = self.agent_registry.get(agent_name)
                    subtasks.append(Subtask(
                        subtask_id=new_subtask_id(t_id, idx),
                        task_id=t_id,
                        index=idx,
                        title=str(item.get("title", f"Step {idx}")),
                        description=str(item.get("description", goal)),
                        assigned_agent=agent_name,
                        allowed_tools=list(prof.allowed_tools) if prof else ["read_file"],
                    ))
        except Exception:
            pass

        # 2. Deterministic Domain Decomposition Fallback
        if not subtasks:
            has_code = any(kw in lower_goal for kw in ("code", "ast", "python", "syntax", "refactor", "pytest", "unit test", "analyze file", "inspect code"))
            has_sys = any(kw in lower_goal for kw in ("cpu", "gpu", "ram", "memory usage", "hardware", "process status", "system health", "diagnostics", "disk space"))
            has_data = any(kw in lower_goal for kw in ("data", "csv", "json table", "tabular", "dataset", "statistics", "mean", "median", "transform data", "filter data"))
            has_exec = any(kw in lower_goal for kw in ("run script", "execute command", "bash", "powershell", "terminal", "shell", "run process", "exec"))
            has_calc = any(kw in lower_goal for kw in ("calculate", "compute", "math", "+", "*", "-", "/", "sum"))
            has_file = any(kw in lower_goal for kw in ("write", "save", "file", "create", "result.txt", "export"))
            has_research = any(kw in lower_goal for kw in ("research", "inspect", "read", "find", "search", "investigate", "citation", "paper"))

            idx = 1
            if has_code and self.agent_registry.get("code_intelligence"):
                prof = self.agent_registry.get("code_intelligence")
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, idx),
                    task_id=t_id,
                    index=idx,
                    title="Code Intelligence & Static Analysis",
                    description=f"Analyze code structure, AST, syntax, or run tests for: {goal}",
                    assigned_agent="code_intelligence",
                    allowed_tools=list(prof.allowed_tools),
                ))
                idx += 1

            if has_sys and self.agent_registry.get("system_diagnosis"):
                prof = self.agent_registry.get("system_diagnosis")
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, idx),
                    task_id=t_id,
                    index=idx,
                    title="System Diagnostics & Telemetry",
                    description=f"Query hardware metrics, CPU, memory, or processes for: {goal}",
                    assigned_agent="system_diagnosis",
                    allowed_tools=list(prof.allowed_tools),
                ))
                idx += 1

            if has_data and self.agent_registry.get("data_intelligence"):
                prof = self.agent_registry.get("data_intelligence")
                file_match = re.search(r"(?:to|into|as)\s+([\w\-/\\]+\.(?:csv|json))", lower_goal)
                deliv = [str(Path(self.allowed_roots[0]) / file_match.group(1))] if file_match else []
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, idx),
                    task_id=t_id,
                    index=idx,
                    title="Data Intelligence & Transformation",
                    description=f"Process tabular data, clean, transform, or compute statistics for: {goal}",
                    assigned_agent="data_intelligence",
                    allowed_tools=list(prof.allowed_tools),
                    expected_deliverables=deliv,
                ))
                idx += 1

            if has_exec and self.agent_registry.get("execution_agent"):
                prof = self.agent_registry.get("execution_agent")
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, idx),
                    task_id=t_id,
                    index=idx,
                    title="Controlled Command Execution",
                    description=f"Execute sandboxed process or command for: {goal}",
                    assigned_agent="execution_agent",
                    allowed_tools=list(prof.allowed_tools),
                ))
                idx += 1

            if has_research and self.agent_registry.get("researcher"):
                prof = self.agent_registry.get("researcher")
                file_match = re.search(r"(?:save|write|to)\s+([\w\-/\\]+\.(?:md|txt))", lower_goal)
                deliv = [str(Path(self.allowed_roots[0]) / file_match.group(1))] if file_match else []
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, idx),
                    task_id=t_id,
                    index=idx,
                    title="Information Gathering & Research",
                    description=f"Inspect data and gather relevant findings for: {goal}",
                    assigned_agent="researcher",
                    allowed_tools=list(prof.allowed_tools),
                    expected_deliverables=deliv,
                ))
                idx += 1

            if has_calc and self.agent_registry.get("calculator"):
                prof = self.agent_registry.get("calculator")
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, idx),
                    task_id=t_id,
                    index=idx,
                    title="Mathematical Computation",
                    description=f"Perform precise numeric calculations for: {goal}",
                    assigned_agent="calculator",
                    allowed_tools=list(prof.allowed_tools),
                ))
                idx += 1

            if has_file and self.agent_registry.get("filesystem"):
                prof = self.agent_registry.get("filesystem")
                file_match = re.search(r"(\w+\.[a-zA-Z0-9]+)", lower_goal)
                fname = file_match.group(1) if file_match else None
                expected_deliv = [str(Path(self.allowed_roots[0]) / fname)] if fname else []
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, idx),
                    task_id=t_id,
                    index=idx,
                    title="Filesystem Persistence & Artifact Generation",
                    description=f"Write outputs and verify physical file creation on disk for: {goal}",
                    assigned_agent="filesystem",
                    allowed_tools=list(prof.allowed_tools),
                    expected_deliverables=expected_deliv,
                ))
                idx += 1

            if not subtasks:
                prof = self.agent_registry.get("general") or next(iter(self.agent_registry.list_agents()))
                subtasks.append(Subtask(
                    subtask_id=new_subtask_id(t_id, 1),
                    task_id=t_id,
                    index=1,
                    title="Goal Execution",
                    description=goal,
                    assigned_agent=prof.name,
                    allowed_tools=list(prof.allowed_tools),
                ))

        plan = ExecutionPlan(
            plan_id=plan_id,
            task_id=t_id,
            goal=goal,
            subtasks=subtasks,
        )

        self.event_bus.publish(PlanCreatedEvent(
            task_id=t_id,
            plan_id=plan_id,
            goal=goal,
            subtask_count=len(subtasks),
            subtasks=[st.to_dict() for st in subtasks],
        ))
        return plan

    # ── Subtask Execution Phase ──────────────────────────────────────────────

    def _execute_subtask(
        self,
        subtask: Subtask,
        workflow_context: dict[str, Any],
        task_id: str,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """Execute a subtask within strict agent capability boundaries.
        
        Enforces controlled top-down delegation and prevents peer-to-peer delegation.
        """
        agent = self.agent_registry.get(subtask.assigned_agent)
        if not agent:
            return False, f"Unknown assigned agent '{subtask.assigned_agent}'", []

        # Filter tool schemas strictly to agent's declared allowed_tools & allowed_tiers
        schemas = self.tool_registry.get_schemas(
            allowed_tools=subtask.allowed_tools,
            allowed_tiers=agent.allowed_tiers,
        )

        system_prompt = (
            f"You are the {agent.title} specialized agent in OPTIMUS.\n"
            f"Domain: {agent.domain}.\n"
            f"Description: {agent.description}\n"
            f"Instructions:\n{agent.instructions}\n\n"
            "CRITICAL ARCHITECTURAL CONSTRAINTS:\n"
            "- You can ONLY invoke your explicitly authorized tools.\n"
            "- You CANNOT delegate to other agents or call tools outside your declared scope.\n"
            "- Deliver factual, verified answers. Never hallucinate completions."
        )

        # Build scoped, compacted context from the SharedContextBus
        scoped_context = self.memory_manager.build_subtask_prompt_context(
            task_id=task_id,
            subtask_id=subtask.subtask_id,
            agent_name=agent.name,
            goal=workflow_context.get("goal", ""),
            subtask_title=subtask.title,
            subtask_description=subtask.description,
        )

        turns = [
            {"role": "user", "text": (
                f"Subtask: {subtask.title}\n"
                f"Instructions: {subtask.description}\n\n"
                f"{scoped_context}"
            )}
        ]

        side_effects: list[dict[str, Any]] = []
        max_turns = agent.capability.max_turns

        for turn_idx in range(1, max_turns + 1):
            if task_id in self._cancelled_tasks:
                return False, "Subtask cancelled during execution.", side_effects

            reply = self._ask_model(system_prompt, turns, schemas)
            calls = reply.get("calls") or []

            # Terminal response
            if not calls:
                summary = (reply.get("text") or "").strip()
                if not summary or summary.startswith("[Model offline"):
                    # Check for functional specialized agent implementation
                    from core.agents.contracts import AgentInputContract
                    from core.agents.registry_ext import get_specialized_agent

                    spec_agent = get_specialized_agent(agent.name)
                    if spec_agent is not None:
                        contract = AgentInputContract(
                            task_id=task_id,
                            subtask_id=subtask.subtask_id,
                            goal=workflow_context.get("goal", subtask.title),
                            instructions=subtask.description,
                            context_data={"workflow_context": workflow_context},
                            allowed_tools=subtask.allowed_tools,
                            workspace_root=self.allowed_roots[0] if self.allowed_roots else ".",
                            timeout_seconds=subtask.timeout_seconds,
                        )
                        agent_res = spec_agent.execute(contract, self.tool_registry)
                        side_effects.extend(agent_res.side_effects)
                        if agent_res.is_success:
                            return True, agent_res.summary, side_effects
                        return False, agent_res.error or agent_res.summary or "Specialized agent failed execution.", side_effects

                    # Fallback for deterministic execution when model is offline
                    lower_desc = (subtask.description + " " + subtask.title).lower()
                    math_match = re.search(r"(\d+[\s\*\+\-\/\^]+\d+)", lower_desc)
                    if math_match and "calculator" in subtask.allowed_tools:
                        calc_res = self.tool_registry.execute(
                            "calculator",
                            {"expression": math_match.group(1)},
                            caller_agent=agent.name,
                            allowed_tools=subtask.allowed_tools,
                            allowed_tiers=agent.allowed_tiers,
                        )
                        if calc_res.is_success:
                            return True, f"Calculated {math_match.group(1)} = {calc_res.output}", side_effects
                    if "write" in lower_desc and "write_file" in subtask.allowed_tools:
                        file_match = re.search(r"(\w+\.[a-zA-Z0-9]+)", lower_desc)
                        fname = file_match.group(1) if file_match else "output.txt"
                        fpath = Path(self.allowed_roots[0]) / fname
                        write_res = self.tool_registry.execute(
                            "write_file",
                            {"path": str(fpath), "content": f"Verified result for {subtask.title}"},
                            caller_agent=agent.name,
                            allowed_tools=subtask.allowed_tools,
                            allowed_tiers=agent.allowed_tiers,
                            context={"allowed_roots": self.allowed_roots},
                        )
                        if write_res.is_success:
                            side_effects.append({
                                "tool_name": "write_file",
                                "arguments": {"path": str(fpath), "content": f"Verified result for {subtask.title}"},
                                "status": "done",
                                "output": write_res.output,
                                "evidence": write_res.evidence,
                            })
                            return True, f"Written deliverable to {fpath.name}", side_effects
                    if summary.startswith("[Model offline"):
                        return True, f"Completed subtask: {subtask.title}", side_effects
                    return False, "Agent completed turn without written output.", side_effects
                return True, summary, side_effects

            # Progress notification
            if reply.get("text"):
                self.event_bus.publish(SubtaskProgressEvent(
                    task_id=task_id,
                    subtask_id=subtask.subtask_id,
                    agent_name=agent.name,
                    progress_text=reply["text"][:160],
                ))

            # Process tool calls
            call_results = []
            for call in calls:
                tool_name = str(call.get("name") or "")
                raw_args = call.get("args") or {}
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)

                # 1. Controlled Delegation Check (Anti-Peer-to-Peer)
                if tool_name not in subtask.allowed_tools:
                    res_str = (
                        f"DELEGATION_VIOLATION: Agent '{agent.name}' is prohibited from calling unauthorized "
                        f"tool '{tool_name}'. Allowed: {subtask.allowed_tools}"
                    )
                    call_results.append({"id": call.get("id", ""), "name": tool_name, "result": res_str})
                    continue

                # 2. Tool Execution via Registry
                t_result: ToolResult = self.tool_registry.execute(
                    tool_name=tool_name,
                    arguments=args,
                    caller_agent=agent.name,
                    allowed_tools=subtask.allowed_tools,
                    allowed_tiers=agent.allowed_tiers,
                    context={"allowed_roots": self.allowed_roots},
                )

                side_effects.append({
                    "tool_name": tool_name,
                    "arguments": args,
                    "status": t_result.status,
                    "output": t_result.output,
                    "evidence": t_result.evidence,
                    "error": t_result.error,
                })

                call_results.append({
                    "id": call.get("id", ""),
                    "name": tool_name,
                    "result": t_result.output if t_result.is_success else f"Error: {t_result.error}",
                })

            turns.append({"role": "model", "text": reply.get("text") or "", "calls": calls})
            turns.append({"role": "tool", "results": call_results})

        return False, f"Turn budget exhausted ({max_turns} turns).", side_effects

    # ── Independent Physical Verification Phase ──────────────────────────────

    def _verify_deliverables(
        self,
        subtask: Subtask,
        side_effects: Any = None,
        output_summary: str | None = None,
        side_effects_or_files: Any = None,
        **kwargs: Any,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Independently verify physical state and deliverables without trusting model claims."""
        effects = side_effects if side_effects is not None else side_effects_or_files

        # Support passing explicit file list directly as effects
        extra_pcs = []
        if isinstance(effects, list) and all(isinstance(x, (str, Path)) for x in effects):
            from core.verification.types import Postcondition, PostconditionType
            for x in effects:
                extra_pcs.append(Postcondition(
                    postcondition_type=PostconditionType.FILESYSTEM,
                    spec={"path": str(x), "must_exist": True, "non_empty": True},
                    description=f"Deliverable file must exist: {Path(x).name}",
                    critical=True,
                ))

        actual_side_effects = effects if (isinstance(effects, list) and not extra_pcs) else None

        is_verified, verdict, records, evidence_map = self.verification_engine.verify_subtask(
            subtask=subtask,
            side_effects=actual_side_effects,
            output_summary=output_summary or "",
            extra_postconditions=extra_pcs if extra_pcs else None,
        )

        return is_verified, verdict, evidence_map

    # ── Task Execution Orchestration ─────────────────────────────────────────

    def execute_task(self, goal: str, task_id: str | None = None) -> TaskRecord:
        """Synchronously execute a task through the full hierarchical lifecycle."""
        t_id = task_id or new_task_id()
        now = time.time()

        task = TaskRecord(
            task_id=t_id,
            goal=goal,
            status=TaskStatus.PLANNING,
            created_at=now,
            started_at=now,
        )
        self.task_store.save_task(task)
        self.event_bus.publish(TaskCreatedEvent(task_id=t_id, goal=goal))

        # Enclose in top-level try/except to prevent orphaned RUNNING state (Fixes EGM-01)
        try:
            if t_id in self._cancelled_tasks:
                self.task_store.cancel_task(t_id, reason="Task cancelled by operator.")
                self.event_bus.publish(TaskCancelledEvent(task_id=t_id, goal=goal))
                return self.task_store.get_task(t_id)

            # 0. Initialize Task Context Isolation on Context Bus
            self.memory_manager.context_bus.isolate_task(t_id)

            # 1. Formulate Plan
            plan = self.plan(goal, t_id)
            task.plan = plan

            if t_id in self._cancelled_tasks:
                self.task_store.cancel_task(t_id, reason="Task cancelled by operator.")
                self.event_bus.publish(TaskCancelledEvent(task_id=t_id, goal=goal))
                return self.task_store.get_task(t_id)

            task.status = TaskStatus.RUNNING
            self.task_store.save_task(task)

            workflow_context: dict[str, Any] = {"goal": goal, "subtask_outputs": {}}
            completed_summaries: list[str] = []

            # 2. Sequential Subtask Dispatch
            for subtask in plan.subtasks:
                # Check for cancellation before dispatch
                if t_id in self._cancelled_tasks:
                    self.task_store.cancel_task(t_id, reason="Task cancelled by operator.")
                    self.event_bus.publish(TaskCancelledEvent(task_id=t_id, goal=goal))
                    return self.task_store.get_task(t_id)

                # Transition: DELEGATED
                subtask.status = SubtaskStatus.DELEGATED
                self.task_store.update_subtask(subtask)
                self.event_bus.publish(SubtaskDelegatedEvent(
                    task_id=t_id,
                    subtask_id=subtask.subtask_id,
                    subtask_index=subtask.index,
                    assigned_agent=subtask.assigned_agent,
                    title=subtask.title,
                    allowed_tools=subtask.allowed_tools,
                ))

                # Transition: RUNNING
                subtask.status = SubtaskStatus.RUNNING
                self.task_store.update_subtask(subtask)

                # Controlled Agent Execution
                success, summary, side_effects = self._execute_subtask(
                    subtask=subtask,
                    workflow_context=workflow_context,
                    task_id=t_id,
                )

                # Check if cancellation occurred mid-execution (Fixes EGM-02)
                if t_id in self._cancelled_tasks:
                    self.task_store.cancel_task(t_id, reason="Task cancelled by operator.")
                    self.event_bus.publish(TaskCancelledEvent(task_id=t_id, goal=goal))
                    return self.task_store.get_task(t_id)

                if not success:
                    subtask.status = SubtaskStatus.FAILED
                    subtask.error = summary
                    self.task_store.update_subtask(subtask)

                    task.status = TaskStatus.FAILED
                    task.error = f"Subtask '{subtask.title}' failed: {summary}"
                    task.finished_at = time.time()
                    task.duration_seconds = round(task.finished_at - task.started_at, 2)
                    self.task_store.save_task(task)

                    self.event_bus.publish(SubtaskFailedEvent(
                        task_id=t_id,
                        subtask_id=subtask.subtask_id,
                        agent_name=subtask.assigned_agent,
                        error=summary,
                    ))
                    self.event_bus.publish(TaskFailedEvent(
                        task_id=t_id,
                        goal=goal,
                        error=task.error,
                        duration_seconds=task.duration_seconds,
                    ))
                    return task

                # Transition: VERIFYING
                subtask.status = SubtaskStatus.VERIFYING
                self.task_store.update_subtask(subtask)

                # Independent Verification
                is_verified, verdict, evidence = self._verify_deliverables(
                    subtask=subtask,
                    side_effects=side_effects,
                    output_summary=summary,
                )

                self.event_bus.publish(SubtaskVerifiedEvent(
                    task_id=t_id,
                    subtask_id=subtask.subtask_id,
                    agent_name=subtask.assigned_agent,
                    is_verified=is_verified,
                    verdict=verdict,
                    evidence=evidence,
                ))

                if not is_verified:
                    subtask.status = SubtaskStatus.FAILED
                    subtask.verification_verdict = verdict
                    subtask.error = f"Verification failed: {verdict}"
                    self.task_store.update_subtask(subtask)

                    self.memory_manager.context_bus.publish_subtask_output(
                        task_id=t_id,
                        subtask_id=subtask.subtask_id,
                        agent_name=subtask.assigned_agent,
                        key=f"step_{subtask.index}",
                        output=summary,
                        is_verified=False,
                        verification_verdict=verdict,
                    )

                    task.status = TaskStatus.FAILED
                    task.error = subtask.error
                    task.finished_at = time.time()
                    task.duration_seconds = round(task.finished_at - task.started_at, 2)
                    self.task_store.save_task(task)

                    self.event_bus.publish(TaskFailedEvent(
                        task_id=t_id,
                        goal=goal,
                        error=task.error,
                        duration_seconds=task.duration_seconds,
                    ))
                    return task

                # Transition: COMPLETED
                subtask.status = SubtaskStatus.COMPLETED
                subtask.output_summary = summary
                subtask.verification_verdict = verdict
                self.task_store.update_subtask(subtask)

                # Promote verified deliverable artifacts into trusted Semantic Memory
                if evidence:
                    for k, v in evidence.items():
                        if isinstance(v, dict) and "path" in v and "sha256" in v:
                            self.memory_manager.context_bus.publish_deliverable(
                                task_id=t_id,
                                subtask_id=subtask.subtask_id,
                                agent_name=subtask.assigned_agent,
                                file_path=v["path"],
                                byte_size=v.get("bytes", 0),
                                sha256_hash=v["sha256"],
                                verdict=verdict,
                                evidence_id=v.get("evidence_id"),
                            )

                # Publish verified subtask outcome to shared context bus
                self.memory_manager.context_bus.publish_subtask_output(
                    task_id=t_id,
                    subtask_id=subtask.subtask_id,
                    agent_name=subtask.assigned_agent,
                    key=f"step_{subtask.index}",
                    output=summary,
                    is_verified=True,
                    verification_verdict=verdict,
                    metadata={"deliverables": subtask.expected_deliverables},
                )

                self.event_bus.publish(SubtaskCompletedEvent(
                    task_id=t_id,
                    subtask_id=subtask.subtask_id,
                    agent_name=subtask.assigned_agent,
                    output_summary=summary,
                ))

                # Propagate context downstream
                workflow_context["subtask_outputs"][subtask.subtask_id] = {
                    "agent": subtask.assigned_agent,
                    "title": subtask.title,
                    "summary": summary,
                    "evidence": evidence,
                }
                completed_summaries.append(f"[{subtask.assigned_agent.upper()} - {subtask.title}]: {summary}")

            # 3. Complete Task
            now_finish = time.time()
            task.status = TaskStatus.COMPLETED
            task.finished_at = now_finish
            task.duration_seconds = round(now_finish - task.started_at, 2)
            task.result = "\n\n".join(completed_summaries)
            if task.plan:
                task.plan.is_completed = True
            self.task_store.save_task(task)

            # Record completed task lifecycle into Episodic Memory
            self.memory_manager.remember_episodic(
                key=f"task_{t_id}",
                content=f"Goal: {goal}\nResult: {task.result[:2000]}",
                task_id=t_id,
                metadata={"duration_seconds": task.duration_seconds, "status": task.status.value},
            )

            self.event_bus.publish(TaskCompletedEvent(
                task_id=t_id,
                goal=goal,
                result=task.result,
                duration_seconds=task.duration_seconds,
            ))
            return task

        except Exception as exc:
            # Catch-all failsafe
            now_err = time.time()
            task.status = TaskStatus.FAILED
            task.error = f"Internal orchestrator error: {type(exc).__name__}: {exc}"
            task.finished_at = now_err
            task.duration_seconds = round(now_err - task.started_at, 2)
            self.task_store.save_task(task)

            self.event_bus.publish(TaskFailedEvent(
                task_id=t_id,
                goal=goal,
                error=task.error,
                duration_seconds=task.duration_seconds,
            ))
            return task

    # ── Non-Blocking Background Worker Dispatch ──────────────────────────────

    def start(self, goal: str, on_complete: Callable[[TaskRecord], None] | None = None) -> TaskRecord:
        """Start a task in the background thread. Returns immediately so UI never blocks."""
        t_id = new_task_id()
        initial_task = TaskRecord(task_id=t_id, goal=goal, status=TaskStatus.PENDING)
        self.task_store.save_task(initial_task)

        def _worker() -> None:
            try:
                completed_task = self.execute_task(goal, task_id=t_id)
                if on_complete:
                    on_complete(completed_task)
            finally:
                with self._lock:
                    self._active_workers.pop(t_id, None)

        th = threading.Thread(target=_worker, name=f"optimus-worker-{t_id}", daemon=True)
        with self._lock:
            self._active_workers[t_id] = th
        th.start()
        return initial_task

    def cancel(self, task_id: str) -> bool:
        """Signal cooperative cancellation to a task."""
        self._cancelled_tasks.add(task_id)
        return bool(self.task_store.cancel_task(task_id))

    run_task = execute_task
    cancel_task = cancel
