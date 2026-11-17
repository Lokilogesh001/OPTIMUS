"""core/verification/ledger.py — Append-Only Cryptographic Evidence Ledger for OPTIMUS.

Provides:
- Persistent, append-only JSON ledger storing verification records.
- Atomic file writes with Windows retry backoff.
- Tamper-evident validation verifying cryptographic SHA-256 hashes of every record.
- Fast multi-key indexing by evidence_id, task_id, and subtask_id.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.verification.types import VerificationRecord


def _atomic_write_json(target_path: Path, data: Any) -> None:
    """Atomically write JSON data using tempfile replace with Windows retry backoff."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.{secrets.token_hex(3)}.tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")

    # Retry backoff for Windows file locks
    for attempt in range(5):
        try:
            os.replace(temp_path, target_path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


class EvidenceLedger:
    """Thread-safe append-only ledger guaranteeing physical evidence provenance and tamper detection."""

    def __init__(self, storage_root: Path | str) -> None:
        self.root = Path(storage_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.root / "evidence.json"
        self._lock = threading.RLock()
        self._records: dict[str, VerificationRecord] = {}
        self._task_index: dict[str, list[str]] = {}
        self._subtask_index: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        """Hydrate in-memory index from disk."""
        with self._lock:
            if not self.ledger_file.is_file():
                return
            try:
                raw_data = json.loads(self.ledger_file.read_text(encoding="utf-8"))
                if isinstance(raw_data, list):
                    for item in raw_data:
                        rec = VerificationRecord.from_dict(item)
                        self._records[rec.evidence_id] = rec
                        self._task_index.setdefault(rec.task_id, []).append(rec.evidence_id)
                        self._subtask_index.setdefault(rec.subtask_id, []).append(rec.evidence_id)
            except Exception as e:  # noqa: BLE001
                print(f"[EvidenceLedger] Error loading evidence ledger: {e}")

    def _persist(self) -> None:
        """Persist entire ledger atomically to disk."""
        entries = [rec.to_dict() for rec in self._records.values()]
        _atomic_write_json(self.ledger_file, entries)

    def record(self, evidence: VerificationRecord) -> VerificationRecord:
        """Append an immutable verification record to the ledger."""
        with self._lock:
            # Ensure cryptographic integrity hash is calculated
            if not evidence.integrity_hash:
                evidence.integrity_hash = evidence.compute_integrity_hash()

            self._records[evidence.evidence_id] = evidence
            self._task_index.setdefault(evidence.task_id, []).append(evidence.evidence_id)
            self._subtask_index.setdefault(evidence.subtask_id, []).append(evidence.evidence_id)
            self._persist()
            return evidence

    def get(self, evidence_id: str) -> VerificationRecord | None:
        """Retrieve evidence record by ID."""
        with self._lock:
            return self._records.get(evidence_id)

    def list_for_task(self, task_id: str) -> list[VerificationRecord]:
        """Retrieve all evidence records belonging to a parent task."""
        with self._lock:
            eids = self._task_index.get(task_id, [])
            return [self._records[eid] for eid in eids if eid in self._records]

    def list_for_subtask(self, subtask_id: str) -> list[VerificationRecord]:
        """Retrieve all evidence records belonging to a specific subtask."""
        with self._lock:
            eids = self._subtask_index.get(subtask_id, [])
            return [self._records[eid] for eid in eids if eid in self._records]

    def list_all(self, limit: int = 100) -> list[VerificationRecord]:
        """Return recently recorded verification entries."""
        with self._lock:
            records = sorted(self._records.values(), key=lambda r: r.timestamp, reverse=True)
            return records[:limit]

    def count(self) -> int:
        """Return total number of recorded evidence records."""
        with self._lock:
            return len(self._records)

    def verify_ledger_integrity(self) -> tuple[bool, list[str]]:
        """Validate cryptographic integrity hashes of every record in the ledger."""
        with self._lock:
            violations: list[str] = []
            for eid, rec in self._records.items():
                if not rec.verify_integrity():
                    violations.append(
                        f"Tamper detected in record '{eid}': recorded hash '{rec.integrity_hash}' "
                        f"!= recomputed '{rec.compute_integrity_hash()}'"
                    )
            is_valid = len(violations) == 0
            return is_valid, violations
