"""core/interaction/types.py — Type definitions and data contracts for OPTIMUS Interaction Layer.

Provides immutable contracts for:
- Multi-modal interactions (text, voice, image).
- Intent classification and goal routing.
- Audio / speech states and interruption signals.
- Session state and conversation history.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InteractionModality(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    MULTIMODAL = "multimodal"


class InteractionRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class IntentCategory(str, Enum):
    CONVERSATION = "conversation"
    ACTIONABLE_TASK = "actionable_task"
    STOP_SPEAKING = "stop_speaking"
    CANCEL_TASK = "cancel_task"
    SYSTEM_QUERY = "system_query"
    UNKNOWN = "unknown"


class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class ImageAttachment:
    """Represents an image attachment provided with an interaction."""
    attachment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str | None = None
    mime_type: str = "image/png"
    data_bytes: bytes | None = None
    description: str = ""
    is_local_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "file_path": self.file_path,
            "mime_type": self.mime_type,
            "description": self.description,
            "is_local_only": self.is_local_only,
            "has_bytes": bool(self.data_bytes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageAttachment:
        return cls(
            attachment_id=data.get("attachment_id", str(uuid.uuid4())),
            file_path=data.get("file_path"),
            mime_type=data.get("mime_type", "image/png"),
            description=data.get("description", ""),
            is_local_only=data.get("is_local_only", True),
        )


@dataclass
class InteractionMessage:
    """A single message turn in an interaction session."""
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = "default"
    role: InteractionRole = InteractionRole.USER
    content: str = ""
    modality: InteractionModality = InteractionModality.TEXT
    attachments: list[ImageAttachment] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "session_id": self.session_id,
            "role": self.role.value if isinstance(self.role, InteractionRole) else str(self.role),
            "content": self.content,
            "modality": self.modality.value if isinstance(self.modality, InteractionModality) else str(self.modality),
            "attachments": [a.to_dict() for a in self.attachments],
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InteractionMessage:
        role = InteractionRole(data.get("role", "user"))
        modality = InteractionModality(data.get("modality", "text"))
        raw_att = data.get("attachments", [])
        attachments = [ImageAttachment.from_dict(a) if isinstance(a, dict) else a for a in raw_att]
        return cls(
            message_id=data.get("message_id", str(uuid.uuid4())),
            session_id=data.get("session_id", "default"),
            role=role,
            content=data.get("content", ""),
            modality=modality,
            attachments=attachments,
            timestamp=data.get("timestamp", time.time()),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class IntentClassificationResult:
    """Deterministic classification outcome for user inputs."""
    intent: IntentCategory = IntentCategory.CONVERSATION
    confidence: float = 1.0
    target_goal: str | None = None
    requires_orchestrator: bool = False
    requires_vision: bool = False
    explanation: str = ""
    raw_parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value if isinstance(self.intent, IntentCategory) else str(self.intent),
            "confidence": self.confidence,
            "target_goal": self.target_goal,
            "requires_orchestrator": self.requires_orchestrator,
            "requires_vision": self.requires_vision,
            "explanation": self.explanation,
            "raw_parameters": dict(self.raw_parameters),
        }


@dataclass
class InteractionSessionState:
    """State record for a multi-turn conversation session."""
    session_id: str = "default"
    messages: list[InteractionMessage] = field(default_factory=list)
    active_task_id: str | None = None
    is_speaking: bool = False
    is_listening: bool = False
    current_stream_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "messages": [m.to_dict() for m in self.messages],
            "active_task_id": self.active_task_id,
            "is_speaking": self.is_speaking,
            "is_listening": self.is_listening,
            "current_stream_id": self.current_stream_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
        }
