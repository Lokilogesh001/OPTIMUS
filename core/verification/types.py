"""core/verification/types.py — Type definitions and data models for OPTIMUS Verification Engine.

Defines:
- PostconditionType enum: FILESYSTEM, PROCESS, DATA, COMMAND.
- VerificationStatus enum: PASS, FAIL, ERROR.
- Postcondition dataclass: Specification of an expected physical outcome.
- VerificationRecord dataclass: Immutable, tamper-evident evidence record with cryptographic integrity hash.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PostconditionType(str, Enum):
    """Categorization of physical verification targets."""
    FILESYSTEM = "FILESYSTEM"  # Files, directories, sizes, formats, checksums
    PROCESS = "PROCESS"        # Process status, application launch/exit, local ports
    DATA = "DATA"              # JSON/schema validation, tabular/CSV integrity, numerical assertions
    COMMAND = "COMMAND"        # Exit codes, stdout/stderr patterns, execution timeouts


class VerificationStatus(str, Enum):
    """Verdict status of a postcondition check."""
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"


def new_evidence_id(prefix: str = "evd") -> str:
    """Generate a unique timestamped evidence identifier."""
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"


@dataclass
class Postcondition:
    """Declared physical postcondition that an operation must satisfy."""
    postcondition_type: PostconditionType
    spec: dict[str, Any]
    description: str = ""
    critical: bool = True     # If critical=True, failure prevents subtask/task completion

    def to_dict(self) -> dict[str, Any]:
        return {
            "postcondition_type": self.postcondition_type.value,
            "spec": self.spec,
            "description": self.description,
            "critical": self.critical,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Postcondition:
        d = dict(data)
        d["postcondition_type"] = PostconditionType(d["postcondition_type"])
        return cls(**d)


@dataclass
class VerificationRecord:
    """Immutable evidence record documenting an independent physical postcondition check."""
    evidence_id: str
    task_id: str
    subtask_id: str
    verifier_identity: str
    timestamp: float = field(default_factory=time.time)
    expected_postcondition: dict[str, Any] = field(default_factory=dict)
    actual_observed_result: dict[str, Any] = field(default_factory=dict)
    status: VerificationStatus = VerificationStatus.FAIL
    verdict: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    integrity_hash: str = ""

    def __post_init__(self) -> None:
        if not self.integrity_hash:
            self.integrity_hash = self.compute_integrity_hash()

    @property
    def is_pass(self) -> bool:
        return self.status == VerificationStatus.PASS

    def compute_integrity_hash(self) -> str:
        """Compute SHA-256 cryptographic hash over normalized core verification fields."""
        canonical_payload = {
            "evidence_id": self.evidence_id,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "verifier_identity": self.verifier_identity,
            "timestamp": round(self.timestamp, 4),
            "expected_postcondition": self.expected_postcondition,
            "actual_observed_result": self.actual_observed_result,
            "status": self.status.value if isinstance(self.status, VerificationStatus) else str(self.status),
            "verdict": self.verdict,
        }
        raw_bytes = json.dumps(canonical_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()

    def verify_integrity(self) -> bool:
        """Confirm that this record has not been tampered with or modified."""
        return self.integrity_hash == self.compute_integrity_hash()

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "verifier_identity": self.verifier_identity,
            "timestamp": self.timestamp,
            "expected_postcondition": self.expected_postcondition,
            "actual_observed_result": self.actual_observed_result,
            "status": self.status.value if isinstance(self.status, VerificationStatus) else str(self.status),
            "verdict": self.verdict,
            "metadata": self.metadata,
            "integrity_hash": self.integrity_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationRecord:
        d = dict(data)
        d["status"] = VerificationStatus(d["status"])
        return cls(**d)
