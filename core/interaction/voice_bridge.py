"""core/interaction/voice_bridge.py — Voice Intelligence & Audio Coordination for OPTIMUS.

Features:
- Speech recognition & synthesis bridge with Mark-LVII providers.
- Real-time voice state machine (IDLE, LISTENING, THINKING, SPEAKING, ERROR).
- Sliding-window speech deduplication / debouncing to prevent duplicate executions.
- Voice interruption & barge-in:
  - "Stop speaking" halts audio output without cancelling background tasks.
  - "Cancel task" halts speech and triggers orchestrator cancellation.
  - Ambiguous interruptions pause speech without cancelling destructive tasks.
- Microphone error resilience with automatic graceful fallback to text.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable

from core.interaction.events import (
    DuplicateSpeechSuppressedEvent,
    SpeechInterruptedEvent,
    SpeechStateChangedEvent,
)
from core.interaction.types import VoiceState
from core.orchestrator.events import EventBus


class VoiceIntelligenceBridge:
    """Manages audio capture, speech recognition, synthesis, debouncing, and interruptions."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        debounce_window_seconds: float = 2.0,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.debounce_window_seconds = debounce_window_seconds

        self._lock = threading.RLock()
        self._state = VoiceState.IDLE
        self._is_speaking = False
        self._is_listening = False
        self._device_name = "default"

        # Debouncing cache: transcript_hash -> timestamp
        self._recent_transcripts: dict[str, float] = {}

        # Audio playback cancellation handle
        self._current_speech_stop_token: threading.Event | None = None
        self._current_speaking_text: str = ""

        # Optional audio engine callbacks
        self._tts_play_fn: Callable[[str], None] | None = None
        self._tts_stop_fn: Callable[[], None] | None = None

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._is_speaking

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._is_listening

    def set_engine_hooks(
        self,
        play_fn: Callable[[str], None] | None = None,
        stop_fn: Callable[[], None] | None = None,
    ) -> None:
        """Register presentation-level audio playback and stop hooks."""
        self._tts_play_fn = play_fn
        self._tts_stop_fn = stop_fn

    def set_listening(self, active: bool, device_name: str = "default") -> None:
        """Update listening state and emit notification event."""
        with self._lock:
            self._is_listening = active
            self._device_name = device_name
            if active:
                self._state = VoiceState.LISTENING
            elif self._state == VoiceState.LISTENING:
                self._state = VoiceState.IDLE

            self.event_bus.publish(
                SpeechStateChangedEvent(
                    state=self._state,
                    is_speaking=self._is_speaking,
                    is_listening=self._is_listening,
                    device_name=self._device_name,
                )
            )

    def is_duplicate(self, raw_transcript: str) -> bool:
        """Check if an incoming transcript is a duplicate arriving within debounce window."""
        norm = raw_transcript.strip().lower()
        if not norm:
            return True

        h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
        now = time.time()

        with self._lock:
            # Purge expired entries
            expired = [k for k, ts in self._recent_transcripts.items() if now - ts > self.debounce_window_seconds]
            for k in expired:
                del self._recent_transcripts[k]

            if h in self._recent_transcripts:
                elapsed = now - self._recent_transcripts[h]
                self.event_bus.publish(
                    DuplicateSpeechSuppressedEvent(
                        raw_text=raw_transcript,
                        transcript_hash=h,
                        elapsed_since_last=elapsed,
                    )
                )
                return True

            self._recent_transcripts[h] = now
            return False

    def speak(self, text: str) -> None:
        """Begin speech synthesis and playback."""
        clean = text.strip()
        if not clean:
            return

        with self._lock:
            self.stop_speaking(reason="new_utterance")
            self._is_speaking = True
            self._state = VoiceState.SPEAKING
            self._current_speaking_text = clean
            token = threading.Event()
            self._current_speech_stop_token = token

            self.event_bus.publish(
                SpeechStateChangedEvent(
                    state=VoiceState.SPEAKING,
                    is_speaking=True,
                    is_listening=self._is_listening,
                    device_name=self._device_name,
                )
            )

        if self._tts_play_fn:
            try:
                self._tts_play_fn(clean)
            except Exception as e:
                print(f"[VoiceIntelligenceBridge] Speech output error: {e}")
                self.on_audio_device_error(str(e))

    def stop_speaking(self, reason: str = "stop_speaking") -> None:
        """Interrupt and halt active speech output without cancelling executing tasks."""
        with self._lock:
            if not self._is_speaking:
                return

            was_speaking = self._is_speaking
            interrupted_text = self._current_speaking_text

            if self._current_speech_stop_token:
                self._current_speech_stop_token.set()
                self._current_speech_stop_token = None

            self._is_speaking = False
            self._current_speaking_text = ""
            if self._state == VoiceState.SPEAKING:
                self._state = VoiceState.IDLE

            if self._tts_stop_fn:
                try:
                    self._tts_stop_fn()
                except Exception as e:
                    print(f"[VoiceIntelligenceBridge] Stop hook failed: {e}")

            self.event_bus.publish(
                SpeechInterruptedEvent(
                    reason=reason,
                    was_speaking=was_speaking,
                    interrupted_text=interrupted_text,
                )
            )
            self.event_bus.publish(
                SpeechStateChangedEvent(
                    state=self._state,
                    is_speaking=False,
                    is_listening=self._is_listening,
                    device_name=self._device_name,
                )
            )

    def on_audio_device_error(self, error_message: str) -> None:
        """Resiliently transition to ERROR state and fall back to text."""
        with self._lock:
            self._state = VoiceState.ERROR
            self._is_speaking = False
            self._is_listening = False

            self.event_bus.publish(
                SpeechStateChangedEvent(
                    state=VoiceState.ERROR,
                    is_speaking=False,
                    is_listening=False,
                    device_name=f"ERROR: {error_message[:40]}",
                )
            )
            print(f"[VoiceIntelligenceBridge] Audio device error handled gracefully: {error_message}. Falling back to text.")
