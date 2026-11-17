"""core/verification — Verification & Physical Postcondition Engine for OPTIMUS.

Provides:
- Independent physical verification of postconditions (Filesystem, Process, Data, Command).
- Immutable, tamper-evident cryptographic evidence ledger.
- Automated postcondition inference from subtasks and execution side effects.
- Clean integration with CentralOrchestrator and Memory & Context Bus.
"""

from core.verification.engine import (
    VerificationEngine,
    get_verification_engine,
    reset_verification_engine,
)
from core.verification.ledger import EvidenceLedger
from core.verification.types import (
    Postcondition,
    PostconditionType,
    VerificationRecord,
    VerificationStatus,
    new_evidence_id,
)
from core.verification.verifiers import (
    BaseVerifier,
    CommandVerifier,
    DataVerifier,
    FilesystemVerifier,
    ProcessVerifier,
)

__all__ = [
    "BaseVerifier",
    "CommandVerifier",
    "DataVerifier",
    "EvidenceLedger",
    "FilesystemVerifier",
    "Postcondition",
    "PostconditionType",
    "ProcessVerifier",
    "VerificationEngine",
    "VerificationRecord",
    "VerificationStatus",
    "get_verification_engine",
    "new_evidence_id",
    "reset_verification_engine",
]
