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
    DESKTOP_WINDOW = "DESKTOP_WINDOW"    # Native window existence, visibility, focus, responsiveness
    BROWSER_PAGE = "BROWSER_PAGE"        # Browser active tab, URL, domain, loaded state
    MEDIA_PLAYBACK = "MEDIA_PLAYBACK"    # Audio/video player state, temporal time progress, ad absence
    UI_CONTROL = "UI_CONTROL"            # Specific UI Automation element role, value, enabled state


class VerificationStatus(str, Enum):
    """Verdict status of a postcondition check."""
    PASS = "PASS"
    COMPLETED_VERIFIED = "COMPLETED_VERIFIED"
    COMPLETED_WITH_LIMITATIONS = "COMPLETED_WITH_LIMITATIONS"
    ACTION_EXECUTED_UNVERIFIED = "ACTION_EXECUTED_UNVERIFIED"
    FAIL = "FAIL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
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
    is_idempotent: bool = True # Safe to retry if failure occurs (non-idempotent never auto-retries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "postcondition_type": self.postcondition_type.value,
            "spec": self.spec,
            "description": self.description,
            "critical": self.critical,
            "is_idempotent": self.is_idempotent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Postcondition:
        d = dict(data)
        d["postcondition_type"] = PostconditionType(d["postcondition_type"])
        if "is_idempotent" not in d:
            d["is_idempotent"] = True
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
        return self.status in (VerificationStatus.PASS, VerificationStatus.COMPLETED_VERIFIED)

    @property
    def is_verified(self) -> bool:
        return self.status in (VerificationStatus.PASS, VerificationStatus.COMPLETED_VERIFIED)

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
        raw_status = d.get("status")
        try:
            d["status"] = VerificationStatus(raw_status)
        except ValueError:
            if str(raw_status).upper() in ("PASS", "COMPLETED_VERIFIED"):
                d["status"] = VerificationStatus.COMPLETED_VERIFIED
            else:
                d["status"] = VerificationStatus.FAILED
        return cls(**d)
