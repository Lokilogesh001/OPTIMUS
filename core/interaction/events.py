"""core/interaction/events.py — Interaction domain events for OPTIMUS.

Dispatched through the Central Orchestrator's EventBus to notify subscribers
(GUI, audio output, memory subsystem, audit logs) of user interaction lifecycle changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.interaction.types import (
    ImageAttachment,
    IntentCategory,
    InteractionModality,
    VoiceState,
)


@dataclass(frozen=True)
class InteractionEvent:
    """Base event for interaction operations."""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class UserMessageReceivedEvent(InteractionEvent):
    """Fired when user sends input via text, voice, or image."""
    session_id: str = "default"
    message_id: str = ""
    modality: InteractionModality = InteractionModality.TEXT
    text: str = ""
    attachments: tuple[ImageAttachment, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntentClassifiedEvent(InteractionEvent):
    """Fired when natural language input is classified into an intent."""
    session_id: str = "default"
    intent: IntentCategory = IntentCategory.CONVERSATION
    confidence: float = 1.0
    requires_orchestrator: bool = False
    target_goal: str | None = None
    explanation: str = ""


@dataclass(frozen=True)
class AgentResponseChunkEvent(InteractionEvent):
    """Streaming chunk emitted by the conversational model."""
    session_id: str = "default"
    stream_id: str = ""
    chunk_text: str = ""
    is_audio: bool = False
    is_final: bool = False


@dataclass(frozen=True)
class AgentResponseCompletedEvent(InteractionEvent):
    """Fired when full response generation finishes."""
    session_id: str = "default"
    message_id: str = ""
    full_text: str = ""
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class SpeechStateChangedEvent(InteractionEvent):
    """Fired when voice intelligence changes listening/speaking states."""
    state: VoiceState = VoiceState.IDLE
    is_speaking: bool = False
    is_listening: bool = False
    device_name: str = "default"


@dataclass(frozen=True)
class SpeechInterruptedEvent(InteractionEvent):
    """Fired when speech output is interrupted via barge-in or explicit command."""
    reason: str = "barge_in"  # "stop_speaking" | "barge_in" | "task_cancel"
    was_speaking: bool = True
    interrupted_text: str = ""


@dataclass(frozen=True)
class TaskCancellationRequestedEvent(InteractionEvent):
    """Fired when user explicitly requests task cancellation through voice/text."""
    session_id: str = "default"
    task_id: str = ""
    reason: str = "user_cancel"
    source: str = "interaction"


@dataclass(frozen=True)
class DuplicateSpeechSuppressedEvent(InteractionEvent):
    """Fired when repeated speech transcript is detected and debounced."""
    raw_text: str = ""
    transcript_hash: str = ""
    elapsed_since_last: float = 0.0


@dataclass(frozen=True)
class VisionPolicyViolationEvent(InteractionEvent):
    """Fired when multimodal image cannot be processed under the active privacy policy."""
    reason: str = ""
    provider: str = ""
    policy: str = "strict_privacy"
