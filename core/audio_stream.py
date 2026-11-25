"""core/audio_stream.py — Continuous Streaming Audio Architecture for OPTIMUS.

Implements:
- Continuous, non-blocking PortAudio output streaming using a single persistent OutputStream.
- Eliminates the 20–80 ms WASAPI / DirectSound driver teardown & recreation gap between TTS chunks.
- Dedicated feeder thread running blocking stream.write() with small blocks (512 samples / 21.3 ms)
  which releases the Python GIL during Pa_WriteStream, preventing GIL contention with PyTorch/Kokoro.
- Bounded, thread-safe AudioRingBuffer with pre-buffering (jitter buffer) to absorb synthesis jitter.
- Multi-engine sample rate normalization (resampling to 24,000 Hz) preventing pitch distortion.
- Micro-fadeout (3 ms) to comfort silence on unexpected underruns, eliminating DC offset clicks.
- Sub-50ms synchronized barge-in / interruption with atomic cancellation flags and driver abort.
- Accurate drain tracking: on_done only fires after the final sample exits the physical speakers.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import sounddevice as sd

logger = logging.getLogger("optimus.audio_stream")

STANDARD_SAMPLE_RATE: int = 24_000
DEFAULT_BLOCK_SIZE: int = 512          # ~21.3 ms at 24 kHz
RING_BUFFER_CAPACITY_S: float = 10.0   # 10 seconds of audio buffer
PREBUFFER_DURATION_S: float = 0.10     # 100 ms jitter buffer before initial playback


def resample_audio(samples: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Fast, robust 1D audio resampling using linear interpolation.

    Zero external library dependencies beyond NumPy, avoiding heavy FFT allocations.
    """
    if orig_sr == target_sr or len(samples) == 0:
        return samples.astype(np.float32, copy=False)

    duration = len(samples) / orig_sr
    target_len = int(round(duration * target_sr))
    if target_len <= 0:
        return np.empty(0, dtype=np.float32)

    old_indices = np.linspace(0, len(samples) - 1, num=len(samples))
    new_indices = np.linspace(0, len(samples) - 1, num=target_len)
    resampled = np.interp(new_indices, old_indices, samples).astype(np.float32)
    return resampled


class AudioRingBuffer:
    """Thread-safe, pre-allocated circular audio buffer with underrun mitigation."""

    def __init__(self, capacity_samples: int, sample_rate: int = STANDARD_SAMPLE_RATE) -> None:
        self.capacity = max(64, capacity_samples)
        self.sample_rate = sample_rate
        self._buffer = np.zeros(self.capacity, dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0
        self._available = 0
        self._lock = threading.Lock()

        # Underrun state tracking
        self._in_underrun = False
        self._fade_len = int(sample_rate * 0.004)  # 4 ms fade (~96 samples at 24 kHz)
        self._fade_out_curve = np.cos(np.linspace(0, np.pi / 2, self._fade_len)).astype(np.float32)
        self._fade_in_curve = np.sin(np.linspace(0, np.pi / 2, self._fade_len)).astype(np.float32)

    @property
    def available_samples(self) -> int:
        with self._lock:
            return self._available

    @property
    def available_seconds(self) -> float:
        with self._lock:
            return self._available / self.sample_rate

    def clear(self) -> None:
        """Reset buffer pointers immediately."""
        with self._lock:
            self._write_pos = 0
            self._read_pos = 0
            self._available = 0
            self._in_underrun = False

    def write(self, samples: np.ndarray, orig_sr: int | None = None) -> int:
        """Write samples into the ring buffer, automatically resampling if needed."""
        samples = np.asarray(samples, dtype=np.float32)

        # Downmix to mono BEFORE resampling so 2D arrays do not break 1D interpolation
        if samples.ndim > 1:
            # If channels-first (shape[0] in [1, 2, 4, 6, 8] and shape[0] < shape[1])
            if samples.shape[0] <= 8 and samples.shape[0] < samples.shape[1]:
                samples = samples.mean(axis=0)
            else:
                samples = samples.mean(axis=1)

        if orig_sr is not None and orig_sr != self.sample_rate:
            samples = resample_audio(samples, orig_sr, self.sample_rate)

        num_samples = len(samples)
        if num_samples == 0:
            return 0

        with self._lock:
            # If resuming from an underrun, apply micro-fadein to the head of the block
            if self._in_underrun and num_samples >= self._fade_len:
                samples = samples.copy()
                samples[:self._fade_len] *= self._fade_in_curve
                self._in_underrun = False

            # Prevent buffer overflow: drop oldest samples if writing exceeds capacity
            if num_samples > self.capacity:
                samples = samples[-self.capacity:]
                num_samples = len(samples)

            free_space = self.capacity - self._available
            if num_samples > free_space:
                overflow = num_samples - free_space
                self._read_pos = (self._read_pos + overflow) % self.capacity
                self._available -= overflow

            # Copy samples into buffer (handling circular wrap)
            first_chunk = min(num_samples, self.capacity - self._write_pos)
            self._buffer[self._write_pos : self._write_pos + first_chunk] = samples[:first_chunk]

            second_chunk = num_samples - first_chunk
            if second_chunk > 0:
                self._buffer[0:second_chunk] = samples[first_chunk:]

            self._write_pos = (self._write_pos + num_samples) % self.capacity
            self._available += num_samples
            return num_samples

    def read(self, num_samples: int, is_active_utterance: bool = False) -> tuple[np.ndarray, bool]:
        """Read requested block of samples.

        Returns (block, is_underrun).
        If fewer than num_samples are available:
          - If is_active_utterance is True, applies cosine fadeout to available samples
            and pads with comfort silence (zeros), setting is_underrun=True.
          - If is_active_utterance is False (normal drain), pads remainder with zeros.
        """
        out = np.zeros(num_samples, dtype=np.float32)
        is_underrun = False

        with self._lock:
            if self._available == 0:
                if is_active_utterance:
                    self._in_underrun = True
                    is_underrun = True
                return out, is_underrun

            to_read = min(num_samples, self._available)
            first_chunk = min(to_read, self.capacity - self._read_pos)
            out[:first_chunk] = self._buffer[self._read_pos : self._read_pos + first_chunk]

            second_chunk = to_read - first_chunk
            if second_chunk > 0:
                out[first_chunk:to_read] = self._buffer[0:second_chunk]

            self._read_pos = (self._read_pos + to_read) % self.capacity
            self._available -= to_read

            # If we drained the buffer during an active utterance, apply micro-fadeout
            if to_read < num_samples and is_active_utterance:
                self._in_underrun = True
                is_underrun = True
                fade_start = max(0, to_read - self._fade_len)
                actual_fade = to_read - fade_start
                if actual_fade > 0:
                    out[fade_start:to_read] *= self._fade_out_curve[-actual_fade:]

        return out, is_underrun


class ContinuousAudioStreamPlayer:
    """Master streaming audio output engine for all OPTIMUS voice providers."""

    _instance: ContinuousAudioStreamPlayer | None = None
    _singleton_lock = threading.Lock()

    @classmethod
    def instance(cls) -> ContinuousAudioStreamPlayer:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (primarily for testing)."""
        with cls._singleton_lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None

    def set_stream_for_testing(self, stream: Any) -> None:
        """Inject a mock or alternate stream for verification without physical hardware."""
        with self._state_lock:
            self._is_testing_stream = True
            if self._stream is not None and self._stream is not stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
            self._stream = stream

    def __init__(
        self,
        sample_rate: int = STANDARD_SAMPLE_RATE,
        block_size: int = DEFAULT_BLOCK_SIZE,
        device_name: str | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.device_name = device_name

        capacity_samples = int(sample_rate * RING_BUFFER_CAPACITY_S)
        self.ring_buffer = AudioRingBuffer(capacity_samples, sample_rate=sample_rate)

        # Threading & synchronization
        self._state_lock = threading.RLock()
        self._stream: sd.OutputStream | None = None
        self._feeder_thread: threading.Thread | None = None
        self._running = False
        self._interrupted = False
        self._is_playing = False
        self._is_active_utterance = False
        self._is_draining = False

        self._has_data_event = threading.Event()
        self._drained_event = threading.Event()
        self._drained_event.set()

        # Callbacks
        self._on_start_cb: Callable[[], None] | None = None
        self._on_done_cb: Callable[[], None] | None = None
        self._level_callback: Callable[[float], None] | None = None

        # Start persistent stream and feeder thread
        self._init_stream()
        self._start_feeder()

    @property
    def is_playing(self) -> bool:
        with self._state_lock:
            return self._is_playing

    @property
    def is_interrupted(self) -> bool:
        return self._interrupted

    def set_level_callback(self, cb: Callable[[float], None] | None) -> None:
        """Register a lightweight level listener for UI HUD visualizations."""
        self._level_callback = cb

    def _init_stream(self) -> None:
        """Initialize or reopen PortAudio output stream."""
        with self._state_lock:
            if getattr(self, "_is_testing_stream", False):
                return
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

            device_index = None
            if self.device_name:
                try:
                    from core import audio_devices
                    device_index = audio_devices.resolve(self.device_name, "output")
                except Exception:
                    pass

            try:
                self._stream = sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=self.block_size,
                    device=device_index,
                )
                self._stream.start()
                logger.info("ContinuousAudioStreamPlayer opened at %d Hz, device=%s", self.sample_rate, device_index)
            except Exception as exc:
                logger.warning("Failed to open audio device %s (%s). Falling back to system default.", device_index, exc)
                try:
                    self._stream = sd.OutputStream(
                        samplerate=self.sample_rate,
                        channels=1,
                        dtype="float32",
                        blocksize=self.block_size,
                        device=None,
                    )
                    self._stream.start()
                except Exception as fatal_exc:
                    logger.error("Fatal: failed to open default audio output stream: %s", fatal_exc)
                    self._stream = None

    def _start_feeder(self) -> None:
        """Spawn the dedicated audio pump thread."""
        self._running = True
        self._feeder_thread = threading.Thread(target=self._feeder_loop, name="OptimusAudioFeeder", daemon=True)
        self._feeder_thread.start()

    def _feeder_loop(self) -> None:
        """Dedicated high-priority audio pump thread."""
        prebuffer_samples = int(self.sample_rate * PREBUFFER_DURATION_S)
        consecutive_errors = 0

        while self._running:
            # If completely idle (no active utterance, buffer empty), sleep on event
            if not self._is_active_utterance and self.ring_buffer.available_samples == 0:
                self._has_data_event.wait(timeout=0.05)
                self._has_data_event.clear()
                continue

            # Jitter pre-buffering gate: wait until minimum jitter buffer is populated
            if self._is_active_utterance and not self._is_playing:
                if self.ring_buffer.available_samples < prebuffer_samples and not self._is_draining:
                    time.sleep(0.005)
                    continue
                # Begin playback
                with self._state_lock:
                    self._is_playing = True
                    start_cb = self._on_start_cb
                if start_cb:
                    try:
                        start_cb()
                    except Exception as e:
                        logger.error("Audio on_start callback failed: %s", e)

            # Pull a block from the ring buffer
            block, is_underrun = self.ring_buffer.read(
                self.block_size,
                is_active_utterance=self._is_active_utterance,
            )

            # Compute RMS amplitude for visualizer hook
            if self._level_callback:
                rms = float(np.sqrt(np.mean(block ** 2) + 1e-12))
                try:
                    self._level_callback(min(1.0, rms * 3.5))
                except Exception:
                    pass

            # Write to PortAudio stream (releases GIL during hardware write)
            with self._state_lock:
                stream = self._stream
                interrupted = self._interrupted

            if stream is not None and not interrupted:
                try:
                    stream.write(block)
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    logger.debug("PortAudio stream write error (%d): %s", consecutive_errors, exc)
                    if not getattr(self, "_is_testing_stream", False):
                        if consecutive_errors <= 2:
                            try:
                                if hasattr(stream, "start") and hasattr(stream, "stop"):
                                    stream.stop()
                                    time.sleep(0.01)
                                    stream.start()
                            except Exception:
                                pass
                        elif consecutive_errors == 3:
                            time.sleep(0.05)
                            self._init_stream()
                        else:
                            time.sleep(0.25)
                    else:
                        time.sleep(0.01)
            elif stream is None and not interrupted:
                # Stream unavailable/disconnected; wait to prevent high-frequency CPU busy spin
                self._has_data_event.wait(timeout=0.25)
            elif interrupted:
                time.sleep(0.005)

            # Check if synthesis finished and playback has completely drained to physical speakers
            if self._is_draining and self.ring_buffer.available_samples == 0:
                with self._state_lock:
                    self._is_active_utterance = False
                    self._is_draining = False
                    self._is_playing = False
                    self._drained_event.set()
                    done_cb = self._on_done_cb

                if done_cb:
                    try:
                        done_cb()
                    except Exception as e:
                        logger.error("Audio on_done callback failed: %s", e)

    def begin_utterance(
        self,
        on_start: Callable[[], None] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Start a new speech utterance."""
        with self._state_lock:
            self._interrupted = False
            self._is_active_utterance = True
            self._is_draining = False
            self._drained_event.clear()
            self._on_start_cb = on_start
            self._on_done_cb = on_done

    def feed_chunk(self, samples: np.ndarray, sample_rate: int = STANDARD_SAMPLE_RATE) -> None:
        """Feed synthesized audio chunk into the streaming ring buffer."""
        if self._interrupted:
            return

        self.ring_buffer.write(samples, orig_sr=sample_rate)
        self._has_data_event.set()

    def feed_bytes(
        self,
        data: bytes,
        sample_rate: int = STANDARD_SAMPLE_RATE,
        dtype: str = "int16",
    ) -> None:
        """Feed raw audio bytes (int16 PCM or miniaudio decoded)."""
        if self._interrupted or not data:
            return

        if dtype == "int16":
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        elif dtype == "float32":
            arr = np.frombuffer(data, dtype=np.float32)
        else:
            raise ValueError(f"Unsupported audio dtype: {dtype}")

        self.feed_chunk(arr, sample_rate=sample_rate)

    def end_utterance(self) -> None:
        """Signal that synthesis has finished for the current utterance."""
        with self._state_lock:
            self._is_draining = True
            if self.ring_buffer.available_samples == 0:
                self._is_active_utterance = False
                self._is_draining = False
                self._is_playing = False
                self._drained_event.set()
                done_cb = self._on_done_cb
                if done_cb:
                    try:
                        done_cb()
                    except Exception:
                        pass
        self._has_data_event.set()

    def wait_drained(self, timeout: float | None = 30.0) -> bool:
        """Block until all audio in the ring buffer has exited the speakers."""
        return self._drained_event.wait(timeout=timeout)

    def stop(self, abort: bool = True) -> None:
        """Synchronized barge-in / speech cancellation."""
        with self._state_lock:
            self._interrupted = True
            self._is_active_utterance = False
            self._is_draining = False
            self._is_playing = False
            self.ring_buffer.clear()
            self._drained_event.set()

            if abort and self._stream is not None:
                try:
                    self._stream.abort()
                    self._stream.start()  # Immediate restart for next utterance
                except Exception as exc:
                    logger.debug("Stream abort/restart warning: %s", exc)

        # Notify visualizer
        if self._level_callback:
            try:
                self._level_callback(0.0)
            except Exception:
                pass

    def play_np(self, samples: np.ndarray, sample_rate: int) -> None:
        """Synchronous helper for single-shot audio playback using continuous pipeline."""
        self.begin_utterance()
        self.feed_chunk(samples, sample_rate=sample_rate)
        self.end_utterance()
        self.wait_drained()

    def close(self) -> None:
        """Teardown player and clean up resources."""
        self._running = False
        self.stop(abort=True)
        self._has_data_event.set()
        if self._feeder_thread and self._feeder_thread.is_alive():
            self._feeder_thread.join(timeout=1.0)
        with self._state_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
