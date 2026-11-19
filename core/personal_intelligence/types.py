"""core/personal_intelligence/types.py — Canonical Entity Data Contracts for Personal Intelligence.

Implements normalized entity contracts for:
- Accounts (Gmail, Calendar, Outlook, WhatsApp Business, WhatsApp Personal)
- Messages & Conversations
- Extracted Tasks & Commitments
- Calendar Events & Reminders
- Source References & Evidence
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderType(str, Enum):
    """Supported external personal communication and calendar providers."""
    GMAIL = "GMAIL"
    GOOGLE_CALENDAR = "GOOGLE_CALENDAR"
    OUTLOOK = "OUTLOOK"
    WHATSAPP_BUSINESS = "WHATSAPP_BUSINESS"
    WHATSAPP_PERSONAL = "WHATSAPP_PERSONAL"


class AuthStatus(str, Enum):
    """Authentication and authorization state of a provider account."""
    CONNECTED = "CONNECTED"
    EXPIRED = "EXPIRED"
    DISCONNECTED = "DISCONNECTED"
    DISCONNECTED_LOCAL_ONLY = "DISCONNECTED_LOCAL_ONLY"
    REVOKED = "REVOKED"
    REVOKED_REMOTE_VERIFIED = "REVOKED_REMOTE_VERIFIED"
    ERROR = "ERROR"


class SyncStatus(str, Enum):
    """Synchronization lifecycle status."""
    IDLE = "IDLE"
    SYNCING = "SYNCING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RATE_LIMITED = "RATE_LIMITED"


class TaskStatus(str, Enum):
    """Status of an extracted personal task."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    OVERDUE = "OVERDUE"
    WAITING = "WAITING"
    CANCELLED = "CANCELLED"


class TaskPriority(str, Enum):
    """Priority level of a personal task or reminder."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CommitmentStatus(str, Enum):
    """Status of a reciprocal commitment."""
    OPEN = "OPEN"
    FULFILLED = "FULFILLED"
    BREACHED = "BREACHED"
    CANCELLED = "CANCELLED"


@dataclass
class SourceReference:
    """Traceable provenance link to an originating message or event."""
    provider: ProviderType
    account_id: str
    record_id: str
    uri: str = ""
    snippet: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value if isinstance(self.provider, ProviderType) else str(self.provider),
            "account_id": self.account_id,
            "record_id": self.record_id,
            "uri": self.uri,
            "snippet": self.snippet,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceReference:
        prov_raw = data.get("provider", ProviderType.GMAIL.value)
        try:
            prov = ProviderType(prov_raw)
        except ValueError:
            prov = ProviderType.GMAIL
        return cls(
            provider=prov,
            account_id=str(data.get("account_id", "")),
            record_id=str(data.get("record_id", "")),
            uri=str(data.get("uri", "")),
            snippet=str(data.get("snippet", "")),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class Account:
    """Registered connected communication or calendar provider account."""
    account_id: str = field(default_factory=lambda: f"acc_{uuid.uuid4().hex[:10]}")
    provider: ProviderType = ProviderType.GMAIL
    email_or_id: str = ""
    display_name: str = ""
    scopes: list[str] = field(default_factory=list)
    auth_status: AuthStatus = AuthStatus.DISCONNECTED
    sync_status: SyncStatus = SyncStatus.IDLE
    last_synced_at: float = 0.0
    error_message: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, safe: bool = False) -> dict[str, Any]:
        meta = dict(self.metadata)
        if safe:
            sensitive_keys = {"access_token", "refresh_token", "token", "secret", "client_secret", "password"}
            meta = {
                k: ("***MASKED***" if (k.lower() in sensitive_keys or "token" in k.lower() or "secret" in k.lower()) else v)
                for k, v in meta.items()
            }
        return {
            "account_id": self.account_id,
            "provider": self.provider.value if isinstance(self.provider, ProviderType) else str(self.provider),
            "email_or_id": self.email_or_id,
            "display_name": self.display_name,
            "scopes": list(self.scopes),
            "auth_status": self.auth_status.value if isinstance(self.auth_status, AuthStatus) else str(self.auth_status),
            "sync_status": self.sync_status.value if isinstance(self.sync_status, SyncStatus) else str(self.sync_status),
            "last_synced_at": self.last_synced_at,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "metadata": meta,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        prov_raw = data.get("provider", ProviderType.GMAIL.value)
        try:
            prov = ProviderType(prov_raw)
        except ValueError:
            prov = ProviderType.GMAIL

        auth_raw = data.get("auth_status", AuthStatus.DISCONNECTED.value)
        try:
            auth = AuthStatus(auth_raw)
        except ValueError:
            auth = AuthStatus.DISCONNECTED

        sync_raw = data.get("sync_status", SyncStatus.IDLE.value)
        try:
            sync_st = SyncStatus(sync_raw)
        except ValueError:
            sync_st = SyncStatus.IDLE

        return cls(
            account_id=str(data.get("account_id", f"acc_{uuid.uuid4().hex[:10]}")),
            provider=prov,
            email_or_id=str(data.get("email_or_id", "")),
            display_name=str(data.get("display_name", "")),
            scopes=list(data.get("scopes", [])),
            auth_status=auth,
            sync_status=sync_st,
            last_synced_at=float(data.get("last_synced_at", 0.0)),
            error_message=str(data.get("error_message", "")),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Message:
    """Normalized personal email or chat message."""
    message_id: str
    provider: ProviderType
    account_id: str
    conversation_id: str
    sender: str
    sender_name: str = ""
    recipients: list[str] = field(default_factory=list)
    subject: str = ""
    body_snippet: str = ""
    timestamp: float = field(default_factory=time.time)
    labels: list[str] = field(default_factory=list)
    is_read: bool = False
    is_important: bool = False
    importance_score: float = 0.0
    suggested_actions: list[str] = field(default_factory=list)
    raw_reference: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "provider": self.provider.value if isinstance(self.provider, ProviderType) else str(self.provider),
            "account_id": self.account_id,
            "conversation_id": self.conversation_id,
            "sender": self.sender,
            "sender_name": self.sender_name,
            "recipients": list(self.recipients),
            "subject": self.subject,
            "body_snippet": self.body_snippet,
            "timestamp": self.timestamp,
            "labels": list(self.labels),
            "is_read": self.is_read,
            "is_important": self.is_important,
            "importance_score": self.importance_score,
            "suggested_actions": list(self.suggested_actions),
            "raw_reference": dict(self.raw_reference),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        prov_raw = data.get("provider", ProviderType.GMAIL.value)
        try:
            prov = ProviderType(prov_raw)
        except ValueError:
            prov = ProviderType.GMAIL

        return cls(
            message_id=str(data["message_id"]),
            provider=prov,
            account_id=str(data.get("account_id", "")),
            conversation_id=str(data.get("conversation_id", "")),
            sender=str(data.get("sender", "")),
            sender_name=str(data.get("sender_name", "")),
            recipients=list(data.get("recipients", [])),
            subject=str(data.get("subject", "")),
            body_snippet=str(data.get("body_snippet", "")),
            timestamp=float(data.get("timestamp", time.time())),
            labels=list(data.get("labels", [])),
            is_read=bool(data.get("is_read", False)),
            is_important=bool(data.get("is_important", False)),
            importance_score=float(data.get("importance_score", 0.0)),
            suggested_actions=list(data.get("suggested_actions", [])),
            raw_reference=dict(data.get("raw_reference", {})),
        )


@dataclass
class Conversation:
    """Grouped thread of messages between participants."""
    conversation_id: str
    provider: ProviderType
    account_id: str
    subject: str = ""
    participants: list[str] = field(default_factory=list)
    last_message_at: float = field(default_factory=time.time)
    unread_count: int = 0
    message_count: int = 0
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "provider": self.provider.value if isinstance(self.provider, ProviderType) else str(self.provider),
            "account_id": self.account_id,
            "subject": self.subject,
            "participants": list(self.participants),
            "last_message_at": self.last_message_at,
            "unread_count": self.unread_count,
            "message_count": self.message_count,
            "snippet": self.snippet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        prov_raw = data.get("provider", ProviderType.GMAIL.value)
        try:
            prov = ProviderType(prov_raw)
        except ValueError:
            prov = ProviderType.GMAIL

        return cls(
            conversation_id=str(data["conversation_id"]),
            provider=prov,
            account_id=str(data.get("account_id", "")),
            subject=str(data.get("subject", "")),
            participants=list(data.get("participants", [])),
            last_message_at=float(data.get("last_message_at", time.time())),
            unread_count=int(data.get("unread_count", 0)),
            message_count=int(data.get("message_count", 0)),
            snippet=str(data.get("snippet", "")),
        )


@dataclass
class PersonalTask:
    """Actionable obligation or task extracted from personal communications."""
    task_id: str = field(default_factory=lambda: f"ptask_{uuid.uuid4().hex[:10]}")
    title: str = ""
    description: str = ""
    source_provider: ProviderType = ProviderType.GMAIL
    source_ref: SourceReference | None = None
    created_at: float = field(default_factory=time.time)
    due_date: float | None = None
    timezone: str = "UTC"
    confidence: float = 1.0
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    is_deadline_inferred: bool = False
    evidence: list[str] = field(default_factory=list)
    counterpart: str = ""
    conversation_id: str = ""
    completed_at: float | None = None
    dedup_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "source_provider": self.source_provider.value if isinstance(self.source_provider, ProviderType) else str(self.source_provider),
            "source_ref": self.source_ref.to_dict() if self.source_ref else None,
            "created_at": self.created_at,
            "due_date": self.due_date,
            "timezone": self.timezone,
            "confidence": self.confidence,
            "status": self.status.value if isinstance(self.status, TaskStatus) else str(self.status),
            "priority": self.priority.value if isinstance(self.priority, TaskPriority) else str(self.priority),
            "is_deadline_inferred": self.is_deadline_inferred,
            "evidence": list(self.evidence),
            "counterpart": self.counterpart,
            "conversation_id": self.conversation_id,
            "completed_at": self.completed_at,
            "dedup_key": self.dedup_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonalTask:
        prov_raw = data.get("source_provider", ProviderType.GMAIL.value)
        try:
            prov = ProviderType(prov_raw)
        except ValueError:
            prov = ProviderType.GMAIL

        st_raw = data.get("status", TaskStatus.PENDING.value)
        try:
            st = TaskStatus(st_raw)
        except ValueError:
            st = TaskStatus.PENDING

        prio_raw = data.get("priority", TaskPriority.MEDIUM.value)
        try:
            prio = TaskPriority(prio_raw)
        except ValueError:
            prio = TaskPriority.MEDIUM

        s_ref = SourceReference.from_dict(data["source_ref"]) if data.get("source_ref") else None

        return cls(
            task_id=str(data.get("task_id", f"ptask_{uuid.uuid4().hex[:10]}")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            source_provider=prov,
            source_ref=s_ref,
            created_at=float(data.get("created_at", time.time())),
            due_date=float(data["due_date"]) if data.get("due_date") is not None else None,
            timezone=str(data.get("timezone", "UTC")),
            confidence=float(data.get("confidence", 1.0)),
            status=st,
            priority=prio,
            is_deadline_inferred=bool(data.get("is_deadline_inferred", False)),
            evidence=list(data.get("evidence", [])),
            counterpart=str(data.get("counterpart", "")),
            conversation_id=str(data.get("conversation_id", "")),
            completed_at=float(data["completed_at"]) if data.get("completed_at") is not None else None,
            dedup_key=str(data.get("dedup_key", "")),
        )


@dataclass
class PersonalEvent:
    """Normalized calendar event across Google Calendar, Outlook, and manual additions."""
    event_id: str
    provider: ProviderType
    account_id: str
    calendar_id: str = "primary"
    title: str = ""
    description: str = ""
    location: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float = field(default_factory=lambda: time.time() + 3600.0)
    timezone: str = "UTC"
    recurrence: list[str] = field(default_factory=list)
    attendees: list[str] = field(default_factory=list)
    reminders: list[int] = field(default_factory=list)  # minutes before event
    is_inferred: bool = False
    is_confirmed: bool = True
    conflict_detected: bool = False
    html_link: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "provider": self.provider.value if isinstance(self.provider, ProviderType) else str(self.provider),
            "account_id": self.account_id,
            "calendar_id": self.calendar_id,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "timezone": self.timezone,
            "recurrence": list(self.recurrence),
            "attendees": list(self.attendees),
            "reminders": list(self.reminders),
            "is_inferred": self.is_inferred,
            "is_confirmed": self.is_confirmed,
            "conflict_detected": self.conflict_detected,
            "html_link": self.html_link,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonalEvent:
        prov_raw = data.get("provider", ProviderType.GOOGLE_CALENDAR.value)
        try:
            prov = ProviderType(prov_raw)
        except ValueError:
            prov = ProviderType.GOOGLE_CALENDAR

        return cls(
            event_id=str(data["event_id"]),
            provider=prov,
            account_id=str(data.get("account_id", "")),
            calendar_id=str(data.get("calendar_id", "primary")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            location=str(data.get("location", "")),
            start_time=float(data.get("start_time", time.time())),
            end_time=float(data.get("end_time", time.time() + 3600.0)),
            timezone=str(data.get("timezone", "UTC")),
            recurrence=list(data.get("recurrence", [])),
            attendees=list(data.get("attendees", [])),
            reminders=[int(r) for r in data.get("reminders", [])],
            is_inferred=bool(data.get("is_inferred", False)),
            is_confirmed=bool(data.get("is_confirmed", True)),
            conflict_detected=bool(data.get("conflict_detected", False)),
            html_link=str(data.get("html_link", "")),
        )


@dataclass
class Commitment:
    """A reciprocal commitment made to or by a contact."""
    commitment_id: str = field(default_factory=lambda: f"com_{uuid.uuid4().hex[:10]}")
    title: str = ""
    counterpart: str = ""
    counterpart_email: str = ""
    is_user_promise: bool = True  # True if user promised; False if contact promised user
    deadline: float | None = None
    status: CommitmentStatus = CommitmentStatus.OPEN
    source_ref: SourceReference | None = None
    created_at: float = field(default_factory=time.time)
    dedup_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "commitment_id": self.commitment_id,
            "title": self.title,
            "counterpart": self.counterpart,
            "counterpart_email": self.counterpart_email,
            "is_user_promise": self.is_user_promise,
            "deadline": self.deadline,
            "status": self.status.value if isinstance(self.status, CommitmentStatus) else str(self.status),
            "source_ref": self.source_ref.to_dict() if self.source_ref else None,
            "created_at": self.created_at,
            "dedup_key": self.dedup_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Commitment:
        st_raw = data.get("status", CommitmentStatus.OPEN.value)
        try:
            st = CommitmentStatus(st_raw)
        except ValueError:
            st = CommitmentStatus.OPEN

        s_ref = SourceReference.from_dict(data["source_ref"]) if data.get("source_ref") else None

        return cls(
            commitment_id=str(data.get("commitment_id", f"com_{uuid.uuid4().hex[:10]}")),
            title=str(data.get("title", "")),
            counterpart=str(data.get("counterpart", "")),
            counterpart_email=str(data.get("counterpart_email", "")),
            is_user_promise=bool(data.get("is_user_promise", True)),
            deadline=float(data["deadline"]) if data.get("deadline") is not None else None,
            status=st,
            source_ref=s_ref,
            created_at=float(data.get("created_at", time.time())),
            dedup_key=str(data.get("dedup_key", "")),
        )


@dataclass
class PersonContact:
    """Consolidated contact record across communication channels."""
    contact_id: str = field(default_factory=lambda: f"con_{uuid.uuid4().hex[:10]}")
    display_name: str = ""
    primary_email: str = ""
    all_emails: list[str] = field(default_factory=list)
    phone_numbers: list[str] = field(default_factory=list)
    is_vip: bool = False
    interaction_count: int = 0
    last_interaction_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "display_name": self.display_name,
            "primary_email": self.primary_email,
            "all_emails": list(self.all_emails),
            "phone_numbers": list(self.phone_numbers),
            "is_vip": self.is_vip,
            "interaction_count": self.interaction_count,
            "last_interaction_at": self.last_interaction_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonContact:
        return cls(
            contact_id=str(data.get("contact_id", f"con_{uuid.uuid4().hex[:10]}")),
            display_name=str(data.get("display_name", "")),
            primary_email=str(data.get("primary_email", "")),
            all_emails=list(data.get("all_emails", [])),
            phone_numbers=list(data.get("phone_numbers", [])),
            is_vip=bool(data.get("is_vip", False)),
            interaction_count=int(data.get("interaction_count", 0)),
            last_interaction_at=float(data.get("last_interaction_at", 0.0)),
        )
