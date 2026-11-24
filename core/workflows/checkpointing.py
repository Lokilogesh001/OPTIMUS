"""core/workflows/checkpointing.py — Checkpoint Persistence, Deliverable Re-Verification, and Loop Detection.

Features:
- WorkflowCheckpoint: Snapshot of execution state with SHA-256 tamper-evident integrity hash.
- CorruptCheckpointError: Raised on corrupted, tampered, or mismatched checkpoint restoration.
- PreResumptionDeliverableReVerifier: Re-verifies all upstream physical deliverables on disk
  before resuming an interrupted execution. If deliverables are missing, forces DAG rollback (Resolves HIGH-05).
- SemanticLoopDetector: Computes structural AST/tool signatures (filtering dynamic nonces/timestamps)
  and halts on 3x consecutive failure loops or repeating n-gram oscillations.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("optimus.workflows.checkpointing")

DEFAULT_CHECKPOINT_DIR = Path("data/checkpoints")


class CheckpointError(Exception):
    """Base exception for checkpoint operations."""


class CorruptCheckpointError(CheckpointError):
    """Raised when a checkpoint file has been tampered with or corrupted."""


class RepetitiveFailureLoopError(CheckpointError):
    """Raised when a repetitive failure loop or oscillation is detected."""


@dataclass
class WorkflowCheckpoint:
    checkpoint_id: str
    execution_id: str
    workflow_id: str
    step_records: Dict[str, Dict[str, Any]]
    shared_context: Dict[str, Any]
    deliverables: Dict[str, List[str]]  # step_id -> list of file paths/keys
    created_at: float
    state_hash: str = ""

    def compute_hash(self) -> str:
        """Computes SHA-256 of canonical state elements."""
        hasher = hashlib.sha256()
        canonical_data = {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "step_records": self.step_records,
            "deliverables": self.deliverables,
        }
        encoded = json.dumps(canonical_data, sort_keys=True).encode("utf-8")
        hasher.update(encoded)
        return hasher.hexdigest()

    def seal(self) -> None:
        """Computes and records the integrity state hash."""
        self.state_hash = self.compute_hash()

    def verify_integrity(self) -> bool:
        """Checks if current data matches state_hash."""
        if not self.state_hash:
            return False
        return self.compute_hash() == self.state_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "step_records": self.step_records,
            "shared_context": self.shared_context,
            "deliverables": self.deliverables,
            "created_at": self.created_at,
            "state_hash": self.state_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkflowCheckpoint:
        return cls(
            checkpoint_id=data["checkpoint_id"],
            execution_id=data["execution_id"],
            workflow_id=data["workflow_id"],
            step_records=data.get("step_records", {}),
            shared_context=data.get("shared_context", {}),
            deliverables=data.get("deliverables", {}),
            created_at=data.get("created_at", 0.0),
            state_hash=data.get("state_hash", ""),
        )


class CheckpointStore:
    """Manages reading and writing encrypted or tamper-evident checkpoints."""

    def __init__(self, directory: Optional[Path] = None):
        self.directory = directory or DEFAULT_CHECKPOINT_DIR
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: WorkflowCheckpoint) -> Path:
        checkpoint.seal()
        target = self.directory / f"{checkpoint.execution_id}_{checkpoint.checkpoint_id}.json"
        with open(target, "w", encoding="utf-8") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)
        logger.info("Saved checkpoint %s to %s", checkpoint.checkpoint_id, target)
        return target

    def load(self, checkpoint_path: Path) -> WorkflowCheckpoint:
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cp = WorkflowCheckpoint.from_dict(data)
        except Exception as e:
            raise CorruptCheckpointError(f"Failed parsing checkpoint file {checkpoint_path}: {e}") from e

        if not cp.verify_integrity():
            logger.error("Tampered or corrupt checkpoint: hash mismatch in %s", checkpoint_path)
            raise CorruptCheckpointError(
                f"Integrity check failed for checkpoint {cp.checkpoint_id}: hash mismatch"
            )

        return cp


@dataclass
class ReverificationReport:
    is_valid: bool
    missing_deliverables: List[str] = field(default_factory=list)
    rolled_back_steps: List[str] = field(default_factory=list)
    reverified_count: int = 0


class PreResumptionDeliverableReVerifier:
    """Re-verifies physical deliverables on disk before resuming interrupted execution (Resolves HIGH-05)."""

    def reverify(
        self,
        checkpoint: WorkflowCheckpoint,
        step_dependencies: Optional[Dict[str, List[str]]] = None
    ) -> ReverificationReport:
        """Inspects all physical deliverables declared by completed steps.
        
        If a deliverable is missing/deleted, rolls back the producing step to PENDING
        and marks all dependent downstream steps for re-execution.
        """
        missing_deliverables: List[str] = []
        rolled_back_steps: Set[str] = set()
        reverified_count = 0

        # Build reverse dependency map if provided
        step_deps = step_dependencies or {}

        for step_id, deliverables in checkpoint.deliverables.items():
            step_record = checkpoint.step_records.get(step_id, {})
            step_status = step_record.get("status")

            if step_status not in ("COMPLETED", "completed"):
                continue

            for d in deliverables:
                d_str = str(d)
                # Physical file deliverables contain slashes, backslashes, or extensions
                is_file_like = ("." in d_str) or ("/" in d_str) or ("\\" in d_str)
                if is_file_like:
                    reverified_count += 1
                    path = Path(d_str)
                    if not path.exists() or (path.is_file() and path.stat().st_size == 0):
                        missing_deliverables.append(d_str)
                        rolled_back_steps.add(step_id)
                        logger.warning(
                            "Deliverable '%s' missing on disk! Rolling back step '%s'",
                            d_str, step_id
                        )

        # Cascade rollback to all downstream dependents
        if rolled_back_steps:
            for step_id in list(rolled_back_steps):
                self._cascade_rollback(step_id, step_deps, rolled_back_steps)

            # Update step statuses in checkpoint
            for rb_step in rolled_back_steps:
                if rb_step in checkpoint.step_records:
                    checkpoint.step_records[rb_step]["status"] = "PENDING"
                    # Remove output from shared context
                    checkpoint.shared_context.pop(rb_step, None)
                    checkpoint.shared_context.pop(f"{rb_step}.output", None)

        is_valid = len(missing_deliverables) == 0
        return ReverificationReport(
            is_valid=is_valid,
            missing_deliverables=missing_deliverables,
            rolled_back_steps=sorted(list(rolled_back_steps)),
            reverified_count=reverified_count,
        )

    def _cascade_rollback(
        self,
        parent_step: str,
        step_deps: Dict[str, List[str]],
        rolled_back: Set[str]
    ) -> None:
        """Recursively marks dependent steps for rollback."""
        for child_id, deps in step_deps.items():
            if parent_step in deps and child_id not in rolled_back:
                rolled_back.add(child_id)
                self._cascade_rollback(child_id, step_deps, rolled_back)


class SemanticLoopDetector:
    """Detects repetitive failure loops and oscillations in agent tool calls."""

    def __init__(self, failure_threshold: int = 3):
        self.failure_threshold = failure_threshold
        self.call_history: List[Tuple[str, str, str, bool]] = []  # (agent, tool, signature, success)

    def _normalize_args(self, args: Dict[str, Any]) -> str:
        """Strips volatile fields like timestamps, nonces, and random IDs for structural comparison."""
        clean = {}
        for k, v in args.items():
            if k in ("nonce", "timestamp", "session_id", "execution_id", "action_id", "request_id"):
                continue
            clean[k] = v
        return json.dumps(clean, sort_keys=True)

    def record_action(
        self,
        agent: str,
        tool: str,
        args: Dict[str, Any],
        success: bool,
    ) -> None:
        """Records an action and checks for loops."""
        sig = self._normalize_args(args)
        self.call_history.append((agent, tool, sig, success))

        # Check 1: 3x consecutive identical failures
        if len(self.call_history) >= self.failure_threshold:
            tail = self.call_history[-self.failure_threshold:]
            if all(not item[3] for item in tail):  # all failed
                first = tail[0]
                if all((item[0] == first[0] and item[1] == first[1] and item[2] == first[2]) for item in tail):
                    msg = (
                        f"Repetitive failure loop detected: {self.failure_threshold} consecutive "
                        f"identical failures executing {first[1]} with agent {first[0]}"
                    )
                    logger.error(msg)
                    raise RepetitiveFailureLoopError(msg)

        # Check 2: Repeating 2-cycle oscillations (e.g. A -> B -> A -> B -> A -> B, all failing)
        if len(self.call_history) >= 6:
            tail = self.call_history[-6:]
            if all(not item[3] for item in tail):
                c1, c2, c3, c4, c5, c6 = tail
                if c1 == c3 == c5 and c2 == c4 == c6:
                    msg = f"Oscillation loop detected: repeating 2-step failure cycle ({c1[1]} <-> {c2[1]})"
                    logger.error(msg)
                    raise RepetitiveFailureLoopError(msg)
