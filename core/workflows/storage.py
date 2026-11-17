"""core/workflows/storage.py — Atomic Disk Persistence for OPTIMUS Workflows.

Manages persistent storage of:
- Workflow definitions.
- Execution instances and step states.
- Human approval records.
- Checkpoints and crash recovery reconciliation.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from core.workflows.types import (
    ApprovalDecision,
    StepStatus,
    WorkflowDefinition,
    WorkflowExecutionRecord,
    WorkflowStatus,
)


class WorkflowStore:
    """Thread-safe, atomic disk storage for workflows, executions, and audit records."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir or (Path.cwd() / "memory" / "workflows")).resolve()
        self.def_dir = self.base_dir / "definitions"
        self.exec_dir = self.base_dir / "executions"
        self.appr_dir = self.base_dir / "approvals"

        self.def_dir.mkdir(parents=True, exist_ok=True)
        self.exec_dir.mkdir(parents=True, exist_ok=True)
        self.appr_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _atomic_write(self, target_path: Path, data: dict[str, Any]) -> None:
        """Write JSON file atomically using a temporary file to prevent corruption on crash."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_path = tempfile.mkstemp(
            prefix="wf_atomic_",
            suffix=".tmp",
            dir=str(target_path.parent),
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, str(target_path))
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    # ── Workflow Definitions ─────────────────────────────────────────────────

    def save_definition(self, definition: WorkflowDefinition) -> None:
        """Persist a workflow definition template."""
        with self._lock:
            p = self.def_dir / f"{definition.workflow_id}.json"
            self._atomic_write(p, definition.to_dict())

    def get_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        """Load a workflow definition by identifier."""
        with self._lock:
            p = self.def_dir / f"{workflow_id}.json"
            if not p.is_file():
                return None
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return WorkflowDefinition.from_dict(data)
            except Exception:
                return None

    def list_definitions(self) -> list[WorkflowDefinition]:
        """List all persisted workflow definitions."""
        with self._lock:
            definitions = []
            for p in self.def_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    definitions.append(WorkflowDefinition.from_dict(data))
                except Exception:
                    continue
            return definitions

    def delete_definition(self, workflow_id: str) -> bool:
        """Delete a workflow definition."""
        with self._lock:
            p = self.def_dir / f"{workflow_id}.json"
            if p.is_file():
                p.unlink()
                return True
            return False

    # ── Execution Instances ──────────────────────────────────────────────────

    def save_execution(self, execution: WorkflowExecutionRecord) -> None:
        """Persist an execution instance state."""
        with self._lock:
            p = self.exec_dir / f"{execution.execution_id}.json"
            self._atomic_write(p, execution.to_dict())

    def get_execution(self, execution_id: str) -> WorkflowExecutionRecord | None:
        """Load an execution instance state."""
        with self._lock:
            p = self.exec_dir / f"{execution_id}.json"
            if not p.is_file():
                return None
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return WorkflowExecutionRecord.from_dict(data)
            except Exception:
                return None

    def list_executions(self, status: WorkflowStatus | None = None) -> list[WorkflowExecutionRecord]:
        """List all workflow executions, optionally filtered by status."""
        with self._lock:
            records = []
            for p in self.exec_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    rec = WorkflowExecutionRecord.from_dict(data)
                    if status is None or rec.status == status:
                        records.append(rec)
                except Exception:
                    continue
            return records

    # ── Approval Decisions ───────────────────────────────────────────────────

    def save_approval(self, approval: ApprovalDecision) -> None:
        """Persist a human approval decision record."""
        with self._lock:
            p = self.appr_dir / f"{approval.approval_id}.json"
            self._atomic_write(p, approval.to_dict())

    def get_approval(self, approval_id: str) -> ApprovalDecision | None:
        """Load an approval decision record."""
        with self._lock:
            p = self.appr_dir / f"{approval_id}.json"
            if not p.is_file():
                return None
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return ApprovalDecision.from_dict(data)
            except Exception:
                return None

    def list_approvals(self) -> list[ApprovalDecision]:
        """List all approval decision records."""
        with self._lock:
            approvals = []
            for p in self.appr_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    approvals.append(ApprovalDecision.from_dict(data))
                except Exception:
                    continue
            return approvals

    # ── Crash Recovery & Ambiguous Execution Detection ───────────────────────

    def reconcile_interrupted_executions(self) -> list[WorkflowExecutionRecord]:
        """Scan executions on startup and mark interrupted in-flight steps as AMBIGUOUS_EXECUTION.
        
        Ensures steps that may have had external side effects are not silently repeated.
        """
        with self._lock:
            recovered = []
            active = self.list_executions()
            for rec in active:
                if rec.status in (WorkflowStatus.RUNNING, WorkflowStatus.PENDING):
                    has_ambiguous = False
                    for step_id, step_rec in rec.step_states.items():
                        if step_rec.status == StepStatus.RUNNING:
                            step_rec.status = StepStatus.AMBIGUOUS_EXECUTION
                            step_rec.error = "Process terminated mid-execution; outcome ambiguous."
                            has_ambiguous = True

                    if has_ambiguous:
                        rec.status = WorkflowStatus.PAUSED
                        rec.recovery_metadata["requires_reconciliation"] = True
                        rec.recovery_metadata["interrupted_at"] = time.time()
                    else:
                        rec.status = WorkflowStatus.PAUSED
                        rec.recovery_metadata["recovered_at"] = time.time()

                    self.save_execution(rec)
                    recovered.append(rec)
            return recovered
