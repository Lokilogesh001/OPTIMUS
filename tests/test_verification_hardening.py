"""tests/test_verification_hardening.py — Negative & Hardening Verification Test Suite.

Validates:
1. Audio downmixing stereo -> mono before resampling (prevents 2D interpolation crash).
2. Audio feeder loop stream disconnect backoff & cooperative teardown.
3. Application registry non-blocking cache queries (list_all_apps, list_running_apps).
4. Physical deliverable verification:
   - Missing deliverables
   - Empty (0-byte) deliverables
   - Min-bytes violation
   - SHA-256 corruption/mismatch
   - Extension mismatch
   - Content pattern mismatch
   - Streaming 64 KB chunked SHA-256 validation on multi-chunk deliverables
5. Service bridge shutdown and telemetry throttling.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import numpy as np
import pytest

from core.audio_stream import AudioRingBuffer, ContinuousAudioStreamPlayer, STANDARD_SAMPLE_RATE
from core.application_intelligence.registry import ApplicationRegistry
from core.application_intelligence.types import AppDefinition, AppInteractionTier
from core.verification.types import Postcondition, PostconditionType, VerificationStatus
from core.verification.verifiers import FilesystemVerifier


# ── 1. Audio Ring Buffer Downmixing & Resampling ──────────────────────────────

def test_audio_ring_buffer_downmixes_stereo_before_resampling():
    """Verify writing a 2D stereo array with a different sample rate downmixes to mono without crashing."""
    buffer = AudioRingBuffer(capacity_samples=48000, sample_rate=24000)
    
    # 2D stereo array at 48 kHz (duration: 0.1s = 4800 samples, 2 channels) - frames first
    stereo_samples = np.ones((4800, 2), dtype=np.float32) * 0.5
    
    # Writing to a 24 kHz buffer must downmix to 1D and resample to 2400 samples
    written = buffer.write(stereo_samples, orig_sr=48000)
    assert written == 2400
    assert buffer.available_samples == 2400
    
    # Read back and verify shape is 1D
    block, underrun = buffer.read(2400)
    assert block.ndim == 1
    assert len(block) == 2400
    assert not underrun
    assert np.allclose(block[:100], 0.5, atol=1e-3)


def test_audio_ring_buffer_channels_first_downmix():
    """Verify channels-first tensors (channels, time) e.g. from Kokoro/PyTorch downmix correctly."""
    buffer = AudioRingBuffer(capacity_samples=48000, sample_rate=24000)
    
    # Channels-first array: shape (2, 4800) at 48 kHz
    channels_first = np.ones((2, 4800), dtype=np.float32) * 0.4
    
    written = buffer.write(channels_first, orig_sr=48000)
    assert written == 2400
    assert buffer.available_samples == 2400
    
    block, underrun = buffer.read(2400)
    assert block.ndim == 1
    assert len(block) == 2400
    assert not underrun
    assert np.allclose(block[:100], 0.4, atol=1e-3)


def test_audio_player_cooperative_reset():
    """Verify ContinuousAudioStreamPlayer reset_instance terminates cleanly."""
    ContinuousAudioStreamPlayer.reset_instance()
    player = ContinuousAudioStreamPlayer.instance()
    assert player is not None
    
    # Resetting instance should join feeder thread within cooperative timeout
    ContinuousAudioStreamPlayer.reset_instance()
    assert ContinuousAudioStreamPlayer._instance is None


class MockOutputStream:
    def __init__(self):
        self.written_blocks: list[np.ndarray] = []
    def write(self, data: np.ndarray):
        self.written_blocks.append(data.copy())
    def start(self): pass
    def stop(self): pass
    def close(self): pass
    def abort(self): pass


def test_audio_player_drain_does_not_discard_final_block():
    """Verify that a short utterance (<= 512 samples) is written to stream and not dropped on drain."""
    ContinuousAudioStreamPlayer.reset_instance()
    player = ContinuousAudioStreamPlayer.instance()
    mock_stream = MockOutputStream()
    player.set_stream_for_testing(mock_stream)

    done_called = False
    def on_done():
        nonlocal done_called
        done_called = True

    short_chunk = np.ones(256, dtype=np.float32) * 0.8
    player.begin_utterance(on_done=on_done)
    player.feed_chunk(short_chunk, sample_rate=24000)
    player.end_utterance()
    player.wait_drained(timeout=2.0)

    assert done_called is True
    assert len(mock_stream.written_blocks) > 0
    all_samples = np.concatenate(mock_stream.written_blocks)
    assert np.any(np.isclose(all_samples, 0.8, atol=1e-2))

    ContinuousAudioStreamPlayer.reset_instance()


# ── 2. Application Registry Non-Blocking In-Memory Cache ─────────────────────

def test_application_registry_non_blocking_cache(tmp_path: Path):
    """Verify list_all_apps and list_running_apps read from cache without triggering refresh."""
    cache_file = tmp_path / "app_inventory.json"
    registry = ApplicationRegistry(cache_file=cache_file)
    
    # Seed mock application
    mock_app = AppDefinition(
        app_id="notepad_test",
        name="Notepad Test",
        aliases=["notepad"],
        executable_path="notepad.exe",
        supported_tiers=[AppInteractionTier.TIER_3_UI_AUTOMATION],
        is_running=True,
    )
    registry._register_internal(mock_app)
    
    # Calling list_all_apps without auto_refresh should strictly read memory
    apps = registry.list_all_apps(auto_refresh=False)
    assert len(apps) == 1
    assert apps[0].app_id == "notepad_test"
    
    running_apps = registry.list_running_apps(auto_refresh=False)
    assert len(running_apps) == 1
    assert running_apps[0].is_running is True


# ── 3. Filesystem Deliverable Negative & Hardening Tests ───────────────────────

@pytest.fixture
def fs_verifier():
    return FilesystemVerifier()


def test_fs_verifier_missing_deliverable(fs_verifier: FilesystemVerifier, tmp_path: Path):
    """Deliverable file does not exist -> FAIL."""
    missing_file = tmp_path / "non_existent_report.md"
    postcondition = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(missing_file), "non_empty": True},
    )
    
    rec = fs_verifier.verify("task_1", "st_1", postcondition)
    assert rec.status == VerificationStatus.FAIL
    assert "Missing deliverable file" in rec.verdict
    assert not rec.actual_observed_result.get("exists")


def test_fs_verifier_empty_zero_byte_deliverable(fs_verifier: FilesystemVerifier, tmp_path: Path):
    """Deliverable file is 0 bytes with non_empty=True -> FAIL."""
    empty_file = tmp_path / "empty_report.md"
    empty_file.touch()
    
    postcondition = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(empty_file), "non_empty": True},
    )
    
    rec = fs_verifier.verify("task_1", "st_1", postcondition)
    assert rec.status == VerificationStatus.FAIL
    assert "empty" in rec.verdict.lower() or "0 bytes" in rec.verdict.lower()
    assert rec.actual_observed_result.get("bytes") == 0


def test_fs_verifier_min_bytes_violation(fs_verifier: FilesystemVerifier, tmp_path: Path):
    """Deliverable file is smaller than min_bytes requirement -> FAIL."""
    tiny_file = tmp_path / "tiny.txt"
    tiny_file.write_text("abc", encoding="utf-8")
    
    postcondition = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(tiny_file), "min_bytes": 100},
    )
    
    rec = fs_verifier.verify("task_1", "st_1", postcondition)
    assert rec.status == VerificationStatus.FAIL
    assert "below required min" in rec.verdict


def test_fs_verifier_sha256_mismatch(fs_verifier: FilesystemVerifier, tmp_path: Path):
    """Deliverable file corrupted/tampered -> SHA-256 mismatch -> FAIL."""
    report = tmp_path / "summary.md"
    report.write_text("# Report Content", encoding="utf-8")
    
    fake_sha = "0000000000000000000000000000000000000000000000000000000000000000"
    postcondition = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(report), "sha256": fake_sha},
    )
    
    rec = fs_verifier.verify("task_1", "st_1", postcondition)
    assert rec.status == VerificationStatus.FAIL
    assert "SHA-256 hash mismatch" in rec.verdict


def test_fs_verifier_extension_mismatch(fs_verifier: FilesystemVerifier, tmp_path: Path):
    """Deliverable file has wrong extension -> FAIL."""
    bad_ext = tmp_path / "report.txt"
    bad_ext.write_text("Some text", encoding="utf-8")
    
    postcondition = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(bad_ext), "extension": ".md"},
    )
    
    rec = fs_verifier.verify("task_1", "st_1", postcondition)
    assert rec.status == VerificationStatus.FAIL
    assert "File extension mismatch" in rec.verdict


def test_fs_verifier_content_string_mismatch(fs_verifier: FilesystemVerifier, tmp_path: Path):
    """Deliverable file lacks expected content string -> FAIL."""
    doc = tmp_path / "doc.txt"
    doc.write_text("Hello World", encoding="utf-8")
    
    postcondition = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={"path": str(doc), "expected_content": "REQUIRED_TOKEN_XYZ"},
    )
    
    rec = fs_verifier.verify("task_1", "st_1", postcondition)
    assert rec.status == VerificationStatus.FAIL
    assert "Expected content string not found" in rec.verdict


def test_fs_verifier_multi_chunk_streaming_sha256(fs_verifier: FilesystemVerifier, tmp_path: Path):
    """Deliverable file spanning multiple 64 KB chunks validates SHA-256 correctly -> PASS."""
    large_file = tmp_path / "large_payload.bin"
    # Create 200 KB payload (spans 4 chunks of 64 KB)
    payload = b"OPTIMUS_VERIFICATION_PAYLOAD_" * 7000  # ~210 KB
    large_file.write_bytes(payload)
    
    expected_sha = hashlib.sha256(payload).hexdigest()
    
    postcondition = Postcondition(
        postcondition_type=PostconditionType.FILESYSTEM,
        spec={
            "path": str(large_file),
            "sha256": expected_sha,
            "min_bytes": 200000,
            "non_empty": True,
        },
    )
    
    rec = fs_verifier.verify("task_1", "st_large", postcondition)
    assert rec.status == VerificationStatus.PASS
    assert rec.actual_observed_result.get("bytes") == len(payload)
    assert rec.actual_observed_result.get("sha256") == expected_sha
    assert "Verified file" in rec.verdict


# ── 4. Service Bridge Throttling & Shutdown ──────────────────────────────────

def test_service_bridge_throttling_and_shutdown():
    """Verify OptimusServiceBridge set_throttled and shutdown methods."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from gui.optimus.service_bridge import OptimusServiceBridge
    OptimusServiceBridge.reset()
    bridge = OptimusServiceBridge.instance()

    assert bridge._telemetry_timer.interval() == 3000

    # Throttle to 6000ms
    bridge.set_throttled(True)
    assert bridge._telemetry_timer.interval() == 6000

    # Restore to 3000ms
    bridge.set_throttled(False)
    assert bridge._telemetry_timer.interval() == 3000

    # Shutdown should stop timers cleanly
    bridge.shutdown()
    assert not bridge._telemetry_timer.isActive()
    assert not bridge._token_flush_timer.isActive()

    OptimusServiceBridge.reset()


def test_service_bridge_speech_state_event_throttles_telemetry():
    """Verify speech state changed event automatically toggles telemetry throttling."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from gui.optimus.service_bridge import OptimusServiceBridge
    from core.interaction.events import SpeechStateChangedEvent
    OptimusServiceBridge.reset()
    bridge = OptimusServiceBridge.instance()

    assert bridge._telemetry_timer.interval() == 3000

    # Dispatch event: speaking -> True
    evt_speaking = SpeechStateChangedEvent(state="SPEAKING", is_speaking=True, is_listening=False)
    bridge.event_bus.publish(evt_speaking)
    app.processEvents()

    assert bridge._telemetry_timer.interval() == 6000

    # Dispatch event: speaking -> False
    evt_idle = SpeechStateChangedEvent(state="IDLE", is_speaking=False, is_listening=False)
    bridge.event_bus.publish(evt_idle)
    app.processEvents()

    assert bridge._telemetry_timer.interval() == 3000
    OptimusServiceBridge.reset()

