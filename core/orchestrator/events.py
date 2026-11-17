"""core/orchestrator/events.py — In-process observable event bus for OPTIMUS.

Publishes task lifecycle events, notifications, and verification outcomes.
Maintains UI decoupling by allowing synchronous and asynchronous handlers
to observe execution off the main Qt thread without blocking.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrchestratorEvent:
    """Base event model for OPTIMUS event dispatch."""
    timestamp: float = field(default_factory=time.time)


@dataclass
class TaskCreatedEvent(OrchestratorEvent):
    task_id: str = ""
    goal: str = ""


@dataclass
class PlanCreatedEvent(OrchestratorEvent):
    task_id: str = ""
    plan_id: str = ""
    goal: str = ""
    subtask_count: int = 0
    subtasks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SubtaskDelegatedEvent(OrchestratorEvent):
    task_id: str = ""
    subtask_id: str = ""
    subtask_index: int = 0
    assigned_agent: str = ""
    title: str = ""
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class SubtaskProgressEvent(OrchestratorEvent):
    task_id: str = ""
    subtask_id: str = ""
    agent_name: str = ""
    progress_text: str = ""


@dataclass
class SubtaskVerifiedEvent(OrchestratorEvent):
    task_id: str = ""
    subtask_id: str = ""
    agent_name: str = ""
    is_verified: bool = False
    verdict: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubtaskCompletedEvent(OrchestratorEvent):
    task_id: str = ""
    subtask_id: str = ""
    agent_name: str = ""
    output_summary: str = ""


@dataclass
class SubtaskFailedEvent(OrchestratorEvent):
    task_id: str = ""
    subtask_id: str = ""
    agent_name: str = ""
    error: str = ""


@dataclass
class TaskCompletedEvent(OrchestratorEvent):
    task_id: str = ""
    goal: str = ""
    result: str = ""
    duration_seconds: float = 0.0


@dataclass
class TaskFailedEvent(OrchestratorEvent):
    task_id: str = ""
    goal: str = ""
    error: str = ""
    duration_seconds: float = 0.0


@dataclass
class TaskCancelledEvent(OrchestratorEvent):
    task_id: str = ""
    goal: str = ""
    reason: str = "Cancelled by user"


@dataclass
class ToolExecutedEvent(OrchestratorEvent):
    """Audit event published whenever a tool is invoked, completed, failed, or denied."""
    tool_name: str = ""
    caller_agent: str = ""
    status: str = "done"  # done, failed, denied
    duration_ms: float = 0.0
    arguments: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    evidence_digest: str = ""


class EventBus:
    """Thread-safe, synchronous and asynchronous event bus."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[type[OrchestratorEvent], list[Callable[[Any], Any]]] = {}

    def subscribe(self, event_type: type[OrchestratorEvent], handler: Callable[[Any], Any]) -> None:
        """Register a handler (sync or async) for a given event type."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: type[OrchestratorEvent], handler: Callable[[Any], Any]) -> None:
        """Unregister a previously subscribed handler."""
        with self._lock:
            if event_type in self._subscribers and handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)

    def publish(self, event: OrchestratorEvent) -> None:
        """Publish an event to all registered subscribers.
        
        Executes synchronous handlers immediately and schedules asynchronous
        coroutines if an event loop is running. Never raises to caller.
        """
        with self._lock:
            handlers = list(self._subscribers.get(type(event), []))
            # Also notify any Base OrchestratorEvent catch-all subscribers
            if type(event) is not OrchestratorEvent:
                handlers.extend(self._subscribers.get(OrchestratorEvent, []))

        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(handler(event))
                    except RuntimeError:
                        asyncio.run(handler(event))
                else:
                    res = handler(event)
                    if inspect.iscoroutine(res):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(res)
                        except RuntimeError:
                            asyncio.run(res)
            except Exception as e:
                # Event dispatch errors should never crash orchestrator workers
                print(f"[EventBus] Error in subscriber {handler}: {e}")
