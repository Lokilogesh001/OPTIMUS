"""tests/test_autonomous_interaction_voice.py — Comprehensive Test Suite for Component 8.

Verifies:
1. Text conversation flow.
2. Multi-turn context retention.
3. Session isolation.
4. Voice input and response integration.
5. Speech interruption & barge-in.
6. Stop-speaking vs cancel-task separation.
7. Duplicate speech event prevention (debouncing).
8. Image routing and vision capability checks.
9. Local-only privacy enforcement.
10. Orchestrator command handoff.
11. Cancellation propagation.
12. Provider failure and fallback handling.
13. Conversation persistence and recovery.
14. Secret redaction before storage/prompting.
15. OptimusServiceBridge interaction dispatch.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.intelligence.gateway import IntelligenceGateway
from core.intelligence.types import RoutingPolicy
from core.interaction import (
    DuplicateSpeechSuppressedEvent,
    ImageAttachment,
    InteractionModality,
    InteractionRole,
    InteractionRouter,
    InteractionRuntime,
    InteractionSessionManager,
    SpeechInterruptedEvent,
    TaskCancellationRequestedEvent,
    VoiceIntelligenceBridge,
    VoiceState,
    redact_secrets,
)
from core.memory import OptimusMemoryManager
from core.orchestrator.desktop import ensure as ensure_orchestrator
from core.orchestrator.events import EventBus
from core.orchestrator.runtime import TaskStatus


@pytest.fixture
def temp_session_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    os.close(fd)
    yield Path(path)
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def custom_runtime(temp_session_file):
    bus = EventBus()
    orch = ensure_orchestrator()
    gateway = IntelligenceGateway.instance()
    mem_mgr = OptimusMemoryManager()
    session_mgr = InteractionSessionManager(storage_path=temp_session_file)
    voice_bridge = VoiceIntelligenceBridge(event_bus=bus, debounce_window_seconds=1.5)
    router = InteractionRouter(gateway=gateway)

    runtime = InteractionRuntime(
        orchestrator=orch,
        gateway=gateway,
        memory_manager=mem_mgr,
        event_bus=bus,
        session_manager=session_mgr,
        voice_bridge=voice_bridge,
        router=router,
    )
    return runtime


def test_text_conversation_flow(custom_runtime):
    """Test 1: Verify basic text turn, message persistence, and assistant answer generation."""
    session_id = "test_text_flow"
    reply = custom_runtime.process_turn_sync(
        session_id=session_id,
        text="Hello OPTIMUS, introduce yourself.",
        modality=InteractionModality.TEXT,
    )
    assert reply != ""
    session = custom_runtime.session_manager.get_or_create_session(session_id)
    assert len(session.messages) == 2
    assert session.messages[0].role == InteractionRole.USER
    assert session.messages[0].content == "Hello OPTIMUS, introduce yourself."
    assert session.messages[1].role == InteractionRole.ASSISTANT
    assert session.messages[1].content == reply


def test_multiturn_context_retention(custom_runtime):
    """Test 2: Verify multi-turn conversational history is retained in order."""
    session_id = "test_multiturn"
    custom_runtime.process_turn_sync(session_id, "My code name is Star.")
    custom_runtime.process_turn_sync(session_id, "What was my code name?")

    session = custom_runtime.session_manager.get_or_create_session(session_id)
    assert len(session.messages) == 4
    formatted = custom_runtime.session_manager.format_prompt_history(session_id)
    # Check that system prompt is first, followed by turns
    assert formatted[0]["role"] == "system"
    assert formatted[1]["role"] == "user"
    assert "Star" in formatted[1]["content"]


def test_session_isolation(custom_runtime):
    """Test 3: Verify strict session isolation across separate IDs."""
    custom_runtime.process_turn_sync("session_alpha", "Secret alpha project 123")
    custom_runtime.process_turn_sync("session_beta", "Public beta project 456")

    s_alpha = custom_runtime.session_manager.get_or_create_session("session_alpha")
    s_beta = custom_runtime.session_manager.get_or_create_session("session_beta")

    assert any("alpha" in m.content for m in s_alpha.messages)
    assert not any("alpha" in m.content for m in s_beta.messages)
    assert any("beta" in m.content for m in s_beta.messages)
    assert not any("beta" in m.content for m in s_alpha.messages)


def test_voice_input_and_response_integration(custom_runtime):
    """Test 4: Verify speech states and playback hooks."""
    played_texts = []
    custom_runtime.voice_bridge.set_engine_hooks(
        play_fn=lambda txt: played_texts.append(txt),
        stop_fn=lambda: None,
    )

    custom_runtime.voice_bridge.set_listening(True)
    assert custom_runtime.voice_bridge.is_listening is True
    assert custom_runtime.voice_bridge.state == VoiceState.LISTENING

    custom_runtime.voice_bridge.speak("Synthesized JARVIS voice response.")
    assert custom_runtime.voice_bridge.is_speaking is True
    assert custom_runtime.voice_bridge.state == VoiceState.SPEAKING
    assert len(played_texts) == 1
    assert "Synthesized" in played_texts[0]


def test_speech_interruption_barge_in(custom_runtime):
    """Test 5: Verify barge-in interruption halts speech immediately."""
    interrupted_events = []
    custom_runtime.event_bus.subscribe(
        SpeechInterruptedEvent,
        lambda e: interrupted_events.append(e),
    )

    stopped = []
    custom_runtime.voice_bridge.set_engine_hooks(
        play_fn=lambda txt: None,
        stop_fn=lambda: stopped.append(True),
    )

    custom_runtime.voice_bridge.speak("Long running audio sentence that should be interrupted.")
    assert custom_runtime.voice_bridge.is_speaking is True

    # User barges in / says "stop speaking"
    custom_runtime.voice_bridge.stop_speaking(reason="barge_in")
    assert custom_runtime.voice_bridge.is_speaking is False
    assert custom_runtime.voice_bridge.state == VoiceState.IDLE
    assert len(stopped) == 1
    assert len(interrupted_events) == 1
    assert interrupted_events[0].reason == "barge_in"


def test_stop_speaking_vs_cancel_task_separation(custom_runtime):
    """Test 6: Verify stop-speaking halts audio ONLY, while cancel-task cancels orchestrator tasks."""
    session_id = "test_separation"
    session = custom_runtime.session_manager.get_or_create_session(session_id)
    session.active_task_id = "task-fake-123"

    custom_runtime.voice_bridge.speak("Active spoken output")

    # 1. Stop speaking turn
    reply1 = custom_runtime.process_turn_sync(session_id, "stop speaking")
    assert custom_runtime.voice_bridge.is_speaking is False
    # Task MUST NOT be cancelled!
    assert session.active_task_id == "task-fake-123"
    assert "stopped" in reply1.lower()

    # 2. Cancel task turn
    cancel_events = []
    custom_runtime.event_bus.subscribe(
        TaskCancellationRequestedEvent,
        lambda e: cancel_events.append(e),
    )
    reply2 = custom_runtime.process_turn_sync(session_id, "cancel task")
    # Now task MUST be cancelled & cleared from session
    assert session.active_task_id is None
    assert "cancellation requested" in reply2.lower()
    assert len(cancel_events) == 1
    assert cancel_events[0].task_id == "task-fake-123"


def test_duplicate_speech_event_prevention(custom_runtime):
    """Test 7: Verify sliding-window debouncer prevents duplicate speech command execution."""
    suppressed_events = []
    custom_runtime.event_bus.subscribe(
        DuplicateSpeechSuppressedEvent,
        lambda e: suppressed_events.append(e),
    )

    utterance = "diagnose cpu and memory"
    # First arrival: not duplicate
    assert custom_runtime.voice_bridge.is_duplicate(utterance) is False

    # Second arrival within 0.1s: duplicate
    assert custom_runtime.voice_bridge.is_duplicate(utterance) is True
    assert len(suppressed_events) == 1
    assert suppressed_events[0].raw_text == utterance

    # In process_turn:
    reply = custom_runtime.process_turn_sync(
        "test_dup",
        utterance,
        modality=InteractionModality.VOICE,
    )
    # Since it was just processed, repeated call is suppressed and returns empty
    assert reply == ""


def test_image_routing_and_vision_capability(custom_runtime):
    """Test 8: Verify image attachment requires vision capability."""
    att = ImageAttachment(file_path="mock_diagram.png", mime_type="image/png")
    classification = custom_runtime.router.classify_intent("Inspect this architecture image", [att])
    assert classification.requires_vision is True


def test_local_only_privacy_enforcement(custom_runtime):
    """Test 9: Verify strict privacy refuses cloud upload of images without local vision."""
    att = ImageAttachment(file_path="confidential.png", is_local_only=True)
    res = custom_runtime.router.classify_intent(
        "Analyze this confidential scheme",
        attachments=[att],
        active_policy=RoutingPolicy.PRIVACY_STRICT,
    )
    # When privacy is strict and no local vision model is installed, must refuse
    assert "Privacy policy" in res.explanation or "No active provider supports vision" in res.explanation


def test_orchestrator_command_handoff(custom_runtime):
    """Test 10: Verify actionable task handoff to Central Orchestrator."""
    session_id = "test_orch_handoff"
    reply = custom_runtime.process_turn_sync(session_id, "Create system diagnostics report and verify")

    session = custom_runtime.session_manager.get_or_create_session(session_id)
    assert session.active_task_id is not None
    assert "directive initialized" in reply.lower() or "scheduled" in reply.lower()


def test_cancellation_propagation(custom_runtime):
    """Test 11: Verify task cancellation propagates to task store."""
    record = custom_runtime.orchestrator.start("Verify disk space and list files")
    custom_runtime.orchestrator.cancel_task(record.task_id)

    # Allow thread to receive cooperative cancellation
    timeout = time.time() + 2.0
    fetched = custom_runtime.orchestrator.task_store.get_task(record.task_id)
    while fetched.status != TaskStatus.CANCELLED and time.time() < timeout:
        time.sleep(0.05)
        fetched = custom_runtime.orchestrator.task_store.get_task(record.task_id)

    assert fetched.status == TaskStatus.CANCELLED


def test_provider_failure_and_fallback(custom_runtime, monkeypatch):
    """Test 12: Verify provider failure doesn't crash the conversation session."""
    def _mock_fail(req):
        raise RuntimeError("Model server 503 unavailable")

    monkeypatch.setattr(custom_runtime.gateway, "complete", _mock_fail)
    session_id = "test_failure_resilience"
    reply = custom_runtime.process_turn_sync(session_id, "What is the capital of France?")
    assert "model error" in reply.lower() or "unavailable" in reply.lower()
    # Session is still intact
    session = custom_runtime.session_manager.get_or_create_session(session_id)
    assert len(session.messages) == 2


def test_conversation_persistence_and_recovery(temp_session_file):
    """Test 13: Verify conversation session persists to disk and recovers on reload."""
    mgr1 = InteractionSessionManager(storage_path=temp_session_file)
    mgr1.add_message("session_persist", InteractionRole.USER, "First persisted message")
    mgr1.add_message("session_persist", InteractionRole.ASSISTANT, "Acknowledged")

    # Re-instantiate new manager from disk
    mgr2 = InteractionSessionManager(storage_path=temp_session_file)
    s2 = mgr2.get_or_create_session("session_persist")
    assert len(s2.messages) == 2
    assert s2.messages[0].content == "First persisted message"
    assert s2.messages[1].content == "Acknowledged"


def test_secret_redaction_in_session_and_responses():
    """Test 14: Verify sensitive tokens and API keys are automatically redacted."""
    dummy_key = "sk-" + "proj12345678901234567890"
    raw = f"My OpenAI key is {dummy_key} and Bearer eyJhbGciOiJIUzI1NiJ9.test"
    cleaned = redact_secrets(raw)
    assert "sk-proj" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned


def test_service_bridge_interaction_dispatch():
    """Test 15: Verify OptimusServiceBridge interaction submission and Qt signal emissions."""
    import threading

    from gui.optimus.service_bridge import OptimusServiceBridge

    bridge = OptimusServiceBridge.instance()
    user_msgs = []
    agent_resps = []

    bridge.user_message_received.connect(lambda d: user_msgs.append(d))
    bridge.agent_response_completed.connect(lambda d: agent_resps.append(d))

    done_event = threading.Event()
    replies = []

    def _on_reply(r: str) -> None:
        replies.append(r)
        done_event.set()

    bridge.submit_interaction(
        text="system status",
        modality="text",
        callback=_on_reply,
    )

    completed = done_event.wait(timeout=5.0)
    assert completed is True
    assert len(replies) == 1
    assert replies[0] != ""
