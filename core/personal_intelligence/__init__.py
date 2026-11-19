"""core/personal_intelligence — Local-First Personal Life Intelligence Subsystem for OPTIMUS.

Milestone 12: Unified Personal Life Intelligence covering:
- Connected accounts (Gmail, Google Calendar, Outlook, WhatsApp Business & Personal).
- Unified Inbox and Message Triage.
- Actionable Task, Deadline, and Commitment Extraction with deterministic deduplication.
- Calendar aggregation and conflict detection.
- Proactive follow-ups and action preparation under PolicyEngine governance.
"""

from __future__ import annotations

from core.personal_intelligence.types import (
    Account,
    AuthStatus,
    Commitment,
    CommitmentStatus,
    Conversation,
    Message,
    PersonalEvent,
    PersonalTask,
    PersonContact,
    ProviderType,
    SourceReference,
    SyncStatus,
    TaskPriority,
    TaskStatus,
)

__all__ = [
    "Account",
    "AuthStatus",
    "Commitment",
    "CommitmentStatus",
    "Conversation",
    "Message",
    "PersonContact",
    "PersonalEvent",
    "PersonalTask",
    "ProviderType",
    "SourceReference",
    "SyncStatus",
    "TaskPriority",
    "TaskStatus",
]
