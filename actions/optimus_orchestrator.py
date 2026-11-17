"""actions/optimus_orchestrator.py — OPTIMUS Core Hierarchical Orchestrator Action.

Connects the Mark-LVII conversational interface directly to the Central Orchestrator:
- Hierarchical task planning and subtask decomposition.
- Domain-specific agent delegation under strict anti-peer delegation rules.
- Capability-based sandboxed tool execution.
- Independent physical postcondition verification.
"""

from __future__ import annotations

from typing import Any

from core import outcome
from core.orchestrator import desktop as orch_desktop
from core.orchestrator.runtime import TaskStatus


def optimus_orchestrator(parameters: dict[str, Any], player: Any | None = None) -> str:
    orch = orch_desktop.ensure(player)
    p = dict(parameters or {})
    action = str(p.get("action") or "status").strip().lower()

    if action == "plan":
        goal = str(p.get("goal") or p.get("task") or "").strip()
        if not goal:
            return outcome.failed("optimus_orchestrator", "plan requires 'goal'")
        plan = orch.plan(goal)
        subtasks_summary = [
            f"  {st.index}. [{st.assigned_agent.upper()}] {st.title} (tools: {','.join(st.allowed_tools) or 'none'})"
            for st in plan.subtasks
        ]
        text = f"OPTIMUS Plan for '{goal}':\n" + "\n".join(subtasks_summary)
        return outcome.done(
            "optimus_orchestrator",
            goal=goal,
            subtask_count=len(plan.subtasks),
            subtasks=plan.subtasks_as_dicts(),
            summary=text,
        )

    if action in ("start", "orchestrate"):
        goal = str(p.get("goal") or p.get("task") or "").strip()
        if not goal:
            return outcome.failed("optimus_orchestrator", "start requires 'goal'")
        record = orch.start(goal)
        return outcome.started(
            "optimus_orchestrator",
            task_id=record.task_id,
            status=record.status.value,
            goal=record.goal,
            subtasks=len(record.plan.subtasks) if record.plan else 0,
            note="OPTIMUS orchestrator is executing subtasks asynchronously. Updates will stream to logs.",
        )

    if action == "execute_sync":
        goal = str(p.get("goal") or p.get("task") or "").strip()
        if not goal:
            return outcome.failed("optimus_orchestrator", "execute_sync requires 'goal'")
        record = orch.run_task(goal)
        if record.status == TaskStatus.COMPLETED:
            return outcome.done(
                "optimus_orchestrator",
                task_id=record.task_id,
                status=record.status.value,
                result=record.result,
                deliverables=record.deliverables,
                subtask_count=len(record.subtasks),
            )
        return outcome.failed(
            "optimus_orchestrator",
            record.error or "task did not complete successfully",
            task_id=record.task_id,
            status=record.status.value,
        )

    if action == "list":
        limit = int(p.get("limit") or 10)
        records = orch.task_store.list_all(limit=limit)
        if not records:
            return outcome.done("optimus_orchestrator", tasks=[], note="no tasks recorded yet")
        rows = [
            f"{r.task_id} [{r.status.value}] {len(r.subtasks)} subtasks · {r.goal[:50]}"
            for r in records
        ]
        return outcome.done(
            "optimus_orchestrator",
            tasks=[{"task_id": r.task_id, "status": r.status.value, "goal": r.goal} for r in records],
            summary="\n".join(rows),
        )

    if action == "status":
        tid = str(p.get("task_id") or "").strip()
        if not tid:
            records = orch.task_store.list_all(limit=1)
            record = records[0] if records else None
        else:
            record = orch.task_store.get(tid)
        if not record:
            return outcome.failed("optimus_orchestrator", "task not found" if tid else "no tasks available")
        return outcome.done(
            "optimus_orchestrator",
            task_id=record.task_id,
            status=record.status.value,
            goal=record.goal,
            subtask_count=len(record.subtasks),
            subtasks=[st.to_dict() for st in record.subtasks],
            error=record.error or None,
        )

    if action == "result":
        tid = str(p.get("task_id") or "").strip()
        if not tid:
            records = orch.task_store.list_all(limit=1)
            record = records[0] if records else None
        else:
            record = orch.task_store.get(tid)
        if not record:
            return outcome.failed("optimus_orchestrator", "task not found")
        if record.status != TaskStatus.COMPLETED:
            return outcome.done(
                "optimus_orchestrator",
                task_id=record.task_id,
                status=record.status.value,
                note="task has not completed yet",
                error=record.error or None,
            )
        return (
            outcome.done(
                "optimus_orchestrator",
                task_id=record.task_id,
                deliverables=record.deliverables,
            )
            + "\n"
            + outcome.untrusted("orchestrator-result", (record.result or "")[:12000])
        )

    if action == "cancel":
        tid = str(p.get("task_id") or "").strip()
        if not tid:
            return outcome.failed("optimus_orchestrator", "cancel requires task_id")
        reason = str(p.get("reason") or "user requested cancellation")
        ok = orch.cancel_task(tid, reason=reason)
        return (
            outcome.done("optimus_orchestrator", task_id=tid, cancelled=True, reason=reason)
            if ok
            else outcome.failed("optimus_orchestrator", "task not running or not found")
        )

    if action == "agents":
        agents = orch.agent_registry.list_agents()
        summary = [
            f"• {a.name}: {a.description} [tier: {a.capability_tier.name}, tools: {len(a.allowed_tools)}]"
            for a in agents
        ]
        return outcome.done(
            "optimus_orchestrator",
            agents=[{"name": a.name, "tier": a.capability_tier.name, "tools": a.allowed_tools} for a in agents],
            summary="\n".join(summary),
        )

    if action == "tools":
        tools = orch.tool_registry.list_tools()
        summary = [
            f"• {t.name}: {t.description} [tier: {t.tier.name}]"
            for t in tools
        ]
        return outcome.done(
            "optimus_orchestrator",
            tools=[{"name": t.name, "tier": t.tier.name, "description": t.description} for t in tools],
            summary="\n".join(summary),
        )

    if action == "memory_search":
        query = str(p.get("query") or p.get("goal") or "").strip()
        cat_str = str(p.get("category") or "").strip().upper()
        cat = None
        if cat_str in ("WORKING", "EPISODIC", "SEMANTIC", "PROCEDURAL"):
            from core.memory.types import MemoryCategory
            cat = MemoryCategory(cat_str)
        limit = int(p.get("limit") or 5)
        entries = orch.memory_manager.store.search(query=query, category=cat, limit=limit)
        results = [
            f"• [{e.category.value}] {e.key}: {e.content[:100]} (verified={e.is_verified})"
            for e in entries
        ]
        return outcome.done(
            "optimus_orchestrator",
            results=[e.to_dict() for e in entries],
            count=len(entries),
            summary="\n".join(results) or "No memories matched query.",
        )

    if action == "memory_store":
        key = str(p.get("key") or "").strip()
        content = str(p.get("content") or "").strip()
        cat_str = str(p.get("category") or "SEMANTIC").strip().upper()
        if not key or not content:
            return outcome.failed("optimus_orchestrator", "memory_store requires 'key' and 'content'")
        from core.memory.types import MemoryCategory, MemoryEntry, new_memory_id
        cat = MemoryCategory.SEMANTIC if cat_str not in ("WORKING", "EPISODIC", "SEMANTIC", "PROCEDURAL") else MemoryCategory(cat_str)
        tags = [t.strip() for t in str(p.get("tags") or "").split(",") if t.strip()]
        entry = orch.memory_manager.store.put(
            MemoryEntry(
                entry_id=new_memory_id("mem"),
                category=cat,
                key=key,
                content=content,
                tags=tags,
                source="user_action",
                is_verified=(cat == MemoryCategory.SEMANTIC),
            )
        )
        return outcome.done(
            "optimus_orchestrator",
            entry_id=entry.entry_id,
            category=entry.category.value,
            key=entry.key,
            note="Memory stored successfully.",
        )

    return outcome.failed("optimus_orchestrator", f"unknown action '{action}'")


TOOL = {
    "name": "optimus_orchestrator",
    "description": (
        "OPTIMUS Central Hierarchical Orchestrator: decomposes user goals into structured "
        "subtasks, delegates to specialized agents with capability-based sandboxing, and "
        "independently verifies deliverables. "
        "Actions: plan (create execution plan), start/orchestrate (run asynchronously), "
        "execute_sync (run synchronously), status (check task progress), result (retrieve "
        "verified deliverables), cancel (cascade task cancellation), agents (list agents), "
        "tools (list available sandboxed tools), memory_search (search persistent memory), "
        "memory_store (store verified knowledge or working notes)."
    ),
    "brief": "OPTIMUS hierarchical task planner, delegation engine, and verifier.",
    "tier": "core",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["plan", "start", "orchestrate", "execute_sync", "status", "result", "cancel", "list", "agents", "tools", "memory_search", "memory_store"],
                "description": "Action to perform",
            },
            "goal": {"type": "STRING", "description": "User goal or task to decompose and execute"},
            "task_id": {"type": "STRING", "description": "Unique task ID for status/result/cancel"},
            "reason": {"type": "STRING", "description": "Reason for cancellation"},
            "limit": {"type": "INTEGER", "description": "Max number of items to return"},
            "key": {"type": "STRING", "description": "Memory key for memory_store"},
            "content": {"type": "STRING", "description": "Memory content for memory_store"},
            "category": {"type": "STRING", "enum": ["WORKING", "EPISODIC", "SEMANTIC", "PROCEDURAL"], "description": "Memory classification"},
            "tags": {"type": "STRING", "description": "Comma-separated tags for memory entry"},
        },
        "required": ["action"],
    },
    "handler": optimus_orchestrator,
}
