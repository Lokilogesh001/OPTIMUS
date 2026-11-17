"""core/orchestrator/desktop.py — Desktop GUI & Voice Bridge for OPTIMUS.

Binds the CentralOrchestrator runtime to Mark-LVII's PyQt presentation layer:
- Dispatches event updates to player.write_log() without blocking Qt.
- Surfaces completed deliverables and plans into player.show_content().
- Triggers conversational voice delivery through player.request_say().
"""

from __future__ import annotations

import threading
from pathlib import Path

from core.orchestrator.agents import AgentRegistry
from core.orchestrator.events import (
    EventBus,
    PlanCreatedEvent,
    SubtaskCompletedEvent,
    SubtaskDelegatedEvent,
    SubtaskFailedEvent,
    SubtaskVerifiedEvent,
    TaskCancelledEvent,
    TaskCompletedEvent,
    TaskCreatedEvent,
    TaskFailedEvent,
)
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import TaskStore
from core.orchestrator.tools import ToolRegistry

_BASE = Path(__file__).resolve().parent.parent.parent
_lock = threading.Lock()
_orchestrator: CentralOrchestrator | None = None
_player = None


def storage_dir() -> Path:
    return _BASE / "memory" / "orchestrator"


def agents_dir() -> Path:
    return _BASE / "agents"


def _call_player(method: str, *args) -> None:
    """Thread-safe invocation of JarvisUI presentation hooks."""
    global _player
    if _player is None:
        return
    fn = getattr(_player, method, None)
    if callable(fn):
        try:
            fn(*args)
        except Exception as e:
            print(f"[DesktopBridge] {method} failed: {e}")


def _bind_event_handlers(bus: EventBus) -> None:
    """Wire typed orchestrator events to UI callbacks."""

    def on_task_created(e: TaskCreatedEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Task initialized — {e.goal[:60]}")

    def on_plan_created(e: PlanCreatedEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Plan created ({e.subtask_count} subtasks)")
        plan_summary = f"GOAL: {e.goal}\n\nPLAN:\n" + "\n".join(
            f"  {st['index']}. [{st['assigned_agent'].upper()}] {st['title']}"
            for st in e.subtasks
        )
        _call_player("show_content", "OPTIMUS EXECUTION PLAN", plan_summary[:3000])

    def on_subtask_delegated(e: SubtaskDelegatedEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Subtask #{e.subtask_index} delegated to [{e.assigned_agent.upper()}] — {e.title}")

    def on_subtask_verified(e: SubtaskVerifiedEvent) -> None:
        status_lbl = "VERIFIED" if e.is_verified else "VERIFICATION FAILED"
        details = ""
        if e.evidence:
            ev_items = []
            for name, meta in e.evidence.items():
                if isinstance(meta, dict):
                    ev_id = meta.get("evidence_id")
                    sha = meta.get("sha256")
                    if ev_id:
                        ev_items.append(f"{name} [{ev_id}]")
                    elif sha:
                        ev_items.append(f"{name} (sha:{sha[:8]})")
            if ev_items:
                details = f" -> Evidence: {', '.join(ev_items)}"
        _call_player("write_log", f"OPTIMUS: Subtask [{e.agent_name.upper()}] {status_lbl} — {e.verdict[:80]}{details}")

    def on_subtask_completed(e: SubtaskCompletedEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Subtask [{e.agent_name.upper()}] completed")

    def on_subtask_failed(e: SubtaskFailedEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Subtask [{e.agent_name.upper()}] FAILED — {e.error[:80]}")

    def on_task_completed(e: TaskCompletedEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Task COMPLETED ({e.duration_seconds}s)")
        body = f"GOAL:\n{e.goal}\n\nRESULT:\n{e.result}"
        _call_player("show_content", "OPTIMUS TASK RESULT", body[:4000])
        from core import outcome
        _call_player(
            "request_say",
            f"[OPTIMUS TASK COMPLETE] The task '{e.goal[:100]}' has finished successfully. "
            f"Summarize the outcome in one concise conversational sentence in the user's language.\n"
            + outcome.untrusted("result", e.result[:1000])
        )

    def on_task_failed(e: TaskFailedEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Task FAILED — {e.error}")
        _call_player("show_content", "OPTIMUS TASK FAILED", f"Goal: {e.goal}\n\nError: {e.error}")
        _call_player("request_say", f"The task could not be completed. {e.error[:120]}")

    def on_task_cancelled(e: TaskCancelledEvent) -> None:
        _call_player("write_log", f"OPTIMUS: Task CANCELLED — {e.reason}")

    bus.subscribe(TaskCreatedEvent, on_task_created)
    bus.subscribe(PlanCreatedEvent, on_plan_created)
    bus.subscribe(SubtaskDelegatedEvent, on_subtask_delegated)
    bus.subscribe(SubtaskVerifiedEvent, on_subtask_verified)
    bus.subscribe(SubtaskCompletedEvent, on_subtask_completed)
    bus.subscribe(SubtaskFailedEvent, on_subtask_failed)
    bus.subscribe(TaskCompletedEvent, on_task_completed)
    bus.subscribe(TaskFailedEvent, on_task_failed)
    bus.subscribe(TaskCancelledEvent, on_task_cancelled)


def ensure(player=None) -> CentralOrchestrator:
    """Initialize or retrieve the CentralOrchestrator singleton."""
    global _orchestrator, _player
    with _lock:
        if player is not None:
            _player = player

        if _orchestrator is None:
            agent_reg = AgentRegistry()
            agent_reg.discover_directory(agents_dir())

            tool_reg = ToolRegistry()
            store = TaskStore(storage_dir())
            bus = EventBus()

            _bind_event_handlers(bus)

            _orchestrator = CentralOrchestrator(
                agent_registry=agent_reg,
                tool_registry=tool_reg,
                task_store=store,
                event_bus=bus,
                allowed_roots=[str(_BASE)],
            )

        if _orchestrator.workflow_engine is None:
            try:
                from core.workflows.engine import WorkflowEngine
                _orchestrator.workflow_engine = WorkflowEngine(
                    orchestrator=_orchestrator,
                    allowed_roots=_orchestrator.allowed_roots,
                    event_bus=_orchestrator.event_bus,
                )
            except Exception as _wf_err:
                print(f"[DesktopBridge] WorkflowEngine attach deferred: {_wf_err}")

        if _orchestrator.intelligence_gateway is None:
            try:
                from core.intelligence import get_intelligence_gateway
                _orchestrator.intelligence_gateway = get_intelligence_gateway()
                if not _orchestrator.brain_fn:
                    _orchestrator.brain_fn = _orchestrator.intelligence_gateway.get_brain_fn()
            except Exception as _ig_err:
                print(f"[DesktopBridge] IntelligenceGateway attach deferred: {_ig_err}")

        return _orchestrator


def attach(player) -> CentralOrchestrator:
    """Attach the desktop UI player to the orchestrator."""
    return ensure(player)


def orchestrator() -> CentralOrchestrator | None:
    """Return the active CentralOrchestrator instance if initialized."""
    return _orchestrator
