"""core/orchestrator/runtime.py — Task & Subtask Lifecycle Runtime for OPTIMUS.

Manages execution tracking, lifecycle state machines, thread-safe atomic disk
persistence, timeouts, and clean cascading cancellation without orphaned states.
"""

from __future__ import annotations

import builtins
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    """Lifecycle status of a top-level OPTIMUS task."""
    CREATED = "CREATED"
    UNDERSTANDING = "UNDERSTANDING"
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    EXECUTING = "EXECUTING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_RESOURCE = "WAITING_FOR_RESOURCE"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SubtaskStatus(str, Enum):
    """Lifecycle status of an individual delegated subtask."""
    PENDING = "PENDING"
    DELEGATED = "DELEGATED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def new_task_id(prefix: str = "opt") -> str:
    """Generate a unique timestamped task identifier."""
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def new_subtask_id(task_id: str = "task", index: int = 1) -> str:
    """Generate a deterministic subtask identifier."""
    return f"sub_{task_id}_{index}"


@dataclass
class Subtask:
    """Discrete subtask delegated to a specialized domain agent."""
    subtask_id: str
    task_id: str = ""
    index: int = 1
    title: str = ""
    description: str = ""
    assigned_agent: str = "general"
    allowed_tools: list[str] = field(default_factory=list)
    status: SubtaskStatus = SubtaskStatus.PENDING
    input_context: dict[str, Any] = field(default_factory=dict)
    output_summary: str = ""
    verification_verdict: str = ""
    expected_deliverables: list[str] = field(default_factory=list)
    timeout_seconds: float = 60.0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __init__(
        self,
        subtask_id: str,
        task_id: str = "",
        parent_task_id: str = "",
        index: int = 1,
        title: str = "",
        description: str = "",
        assigned_agent: str = "general",
        allowed_tools: list[str] | None = None,
        status: SubtaskStatus = SubtaskStatus.PENDING,
        input_context: dict[str, Any] | None = None,
        output_summary: str = "",
        verification_verdict: str = "",
        expected_deliverables: list[str] | None = None,
        timeout_seconds: float = 60.0,
        result: str = "",
        error: str | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
    ) -> None:
        self.subtask_id = subtask_id
        self.task_id = task_id or parent_task_id
        self.index = index
        self.title = title
        self.description = description
        self.assigned_agent = assigned_agent
        self.allowed_tools = list(allowed_tools or [])
        self.status = status if isinstance(status, SubtaskStatus) else SubtaskStatus(str(status))
        self.input_context = dict(input_context or {})
        self.output_summary = output_summary or result
        self.verification_verdict = verification_verdict
        self.expected_deliverables = list(expected_deliverables or [])
        self.timeout_seconds = float(timeout_seconds)
        self.error = error
        now = time.time()
        self.created_at = created_at or now
        self.updated_at = updated_at or now

    @property
    def result(self) -> str:
        return self.output_summary

    @result.setter
    def result(self, val: str) -> None:
        self.output_summary = val

    @property
    def required_tools(self) -> list[str]:
        return self.allowed_tools

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "task_id": self.task_id,
            "index": self.index,
            "title": self.title,
            "description": self.description,
            "assigned_agent": self.assigned_agent,
            "allowed_tools": self.allowed_tools,
            "status": self.status.value,
            "input_context": self.input_context,
            "output_summary": self.output_summary,
            "verification_verdict": self.verification_verdict,
            "expected_deliverables": self.expected_deliverables,
            "timeout_seconds": self.timeout_seconds,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subtask:
        d = dict(data)
        d["status"] = SubtaskStatus(d.get("status", "PENDING"))
        return cls(**d)


@dataclass
class ExecutionPlan:
    """Structured hierarchical execution plan comprising sequential subtasks."""
    plan_id: str
    task_id: str
    goal: str
    subtasks: list[Subtask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    is_completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "subtasks": [st.to_dict() for st in self.subtasks],
            "created_at": self.created_at,
            "is_completed": self.is_completed,
        }

    def subtasks_as_dicts(self) -> list[dict[str, Any]]:
        return [st.to_dict() for st in self.subtasks]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPlan:
        subtasks = [Subtask.from_dict(st) for st in data.get("subtasks", [])]
        return cls(
            plan_id=data["plan_id"],
            task_id=data["task_id"],
            goal=data["goal"],
            subtasks=subtasks,
            created_at=data.get("created_at", time.time()),
            is_completed=bool(data.get("is_completed", False)),
        )


@dataclass
class TaskRecord:
    """Operational and persistent record of an OPTIMUS task lifecycle."""
    task_id: str
    goal: str
    status: TaskStatus = TaskStatus.CREATED
    plan: ExecutionPlan | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    result: str = ""
    error: str | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    interpreted_objective: str = ""
    current_stage: str = "CREATED"
    dependencies: list[str] = field(default_factory=list)
    selected_agents: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    execution_evidence: list[dict[str, Any]] = field(default_factory=list)
    verification_criteria: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2
    recovery_history: list[dict[str, Any]] = field(default_factory=list)
    final_outcome: str = ""

    @property
    def subtasks(self) -> list[Subtask]:
        if self.plan is None:
            self.plan = ExecutionPlan(
                plan_id=f"plan_{self.task_id}",
                task_id=self.task_id,
                goal=self.goal,
            )
        return self.plan.subtasks

    @property
    def deliverables(self) -> list[str]:
        return [deliv for st in self.subtasks for deliv in st.expected_deliverables]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "metadata": self.metadata,
            "interpreted_objective": self.interpreted_objective,
            "current_stage": self.current_stage,
            "dependencies": self.dependencies,
            "selected_agents": self.selected_agents,
            "required_permissions": self.required_permissions,
            "execution_evidence": self.execution_evidence,
            "verification_criteria": self.verification_criteria,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "recovery_history": self.recovery_history,
            "final_outcome": self.final_outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        d = dict(data)
        d["status"] = TaskStatus(d.get("status", "PENDING"))
        if d.get("plan"):
            d["plan"] = ExecutionPlan.from_dict(d["plan"])
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


# ── Thread-Safe Atomic Persistence Store ─────────────────────────────────────

def _atomic_write_json(target_path: Path, data: Any) -> None:
    """Atomically write JSON data using tempfile replace with Windows retry backoff."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.{secrets.token_hex(3)}.tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")

    # Handle transient Windows indexing / file lock contention
    for attempt in range(5):
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


class TaskStore:
    """Persistent on-disk store for tasks, execution plans, and subtask tracking."""

    def __init__(self, storage_root: Path | str) -> None:
        self.root = Path(storage_root)
        self.tasks_dir = self.root / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, TaskRecord] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        """Hydrate in-memory index from disk."""
        with self._lock:
            for task_file in self.tasks_dir.glob("*.json"):
                try:
                    raw = json.loads(task_file.read_text(encoding="utf-8"))
                    task = TaskRecord.from_dict(raw)
                    self._cache[task.task_id] = task
                except Exception as e:
                    print(f"[TaskStore] Failed loading task '{task_file.name}': {e}")

    def save_task(self, task: TaskRecord) -> None:
        """Persist task record to disk and update index atomically."""
        with self._lock:
            self._cache[task.task_id] = task
            path = self.tasks_dir / f"{task.task_id}.json"
            _atomic_write_json(path, task.to_dict())

    save = save_task

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Fetch task record by ID."""
        with self._lock:
            return self._cache.get(task_id)

    get = get_task

    def list_tasks(self, limit: int = 50) -> builtins.list[TaskRecord]:
        """Return recently recorded tasks."""
        with self._lock:
            tasks = sorted(self._cache.values(), key=lambda t: t.created_at, reverse=True)
            return tasks[:limit]

    list_all = list_tasks
    list = list_tasks

    def update_subtask(self, subtask: Subtask) -> None:
        """Update a single subtask within its parent task plan."""
        with self._lock:
            task = self._cache.get(subtask.task_id)
            if not task or not task.plan:
                return

            subtask.updated_at = time.time()
            for idx, existing in enumerate(task.plan.subtasks):
                if existing.subtask_id == subtask.subtask_id:
                    task.plan.subtasks[idx] = subtask
                    break
            self.save_task(task)

    def cancel_task(self, task_id: str, reason: str = "Cancelled by user") -> TaskRecord | None:
        """Cascade cancellation cleanly across task and all active/pending subtasks."""
        with self._lock:
            task = self._cache.get(task_id)
            if not task:
                return None

            if task.status in (TaskStatus.COMPLETED, TaskStatus.PARTIAL, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return None

            now = time.time()
            task.status = TaskStatus.CANCELLED
            task.current_stage = "CANCELLED"
            task.final_outcome = "CANCELLED"
            task.error = reason
            task.finished_at = now
            if task.started_at > 0:
                task.duration_seconds = round(now - task.started_at, 2)

            # Cascade: Cancel all pending or active subtasks
            if task.plan:
                for subtask in task.plan.subtasks:
                    if subtask.status in (SubtaskStatus.PENDING, SubtaskStatus.DELEGATED, SubtaskStatus.RUNNING, SubtaskStatus.VERIFYING):
                        subtask.status = SubtaskStatus.CANCELLED
                        subtask.error = reason
                        subtask.updated_at = now

            self.save_task(task)
            return task
