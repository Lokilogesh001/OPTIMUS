"""core/interaction — OPTIMUS Autonomous Interaction & Voice Intelligence Package.

Provides sovereign multi-modal conversation runtime, voice intelligence,
intent classification, session memory isolation, and orchestrator handoff.
"""

from __future__ import annotations

from core.interaction.events import (
    AgentResponseChunkEvent,
    AgentResponseCompletedEvent,
    DuplicateSpeechSuppressedEvent,
    IntentClassifiedEvent,
    InteractionEvent,
    SpeechInterruptedEvent,
    SpeechStateChangedEvent,
    TaskCancellationRequestedEvent,
    UserMessageReceivedEvent,
    VisionPolicyViolationEvent,
)
from core.interaction.router import InteractionRouter
from core.interaction.runtime import InteractionRuntime
from core.interaction.session import InteractionSessionManager, redact_secrets
from core.interaction.types import (
    ImageAttachment,
    IntentCategory,
    IntentClassificationResult,
    InteractionMessage,
    InteractionModality,
    InteractionRole,
    InteractionSessionState,
    VoiceState,
)
from core.interaction.voice_bridge import VoiceIntelligenceBridge

__all__ = [
    "AgentResponseChunkEvent",
    "AgentResponseCompletedEvent",
    "DuplicateSpeechSuppressedEvent",
    "ImageAttachment",
    "IntentCategory",
    "IntentClassificationResult",
    "IntentClassifiedEvent",
    "InteractionEvent",
    "InteractionMessage",
    "InteractionModality",
    "InteractionRole",
    "InteractionRouter",
    "InteractionRuntime",
    "InteractionSessionManager",
    "InteractionSessionState",
    "SpeechInterruptedEvent",
    "SpeechStateChangedEvent",
    "TaskCancellationRequestedEvent",
    "UserMessageReceivedEvent",
    "VisionPolicyViolationEvent",
    "VoiceIntelligenceBridge",
    "VoiceState",
    "redact_secrets",
]
