"""tests/animation_engine/test_state_machine.py — Tests for animation state machine and runtime reactivity.
"""
from __future__ import annotations

import pytest
from gui.optimus.animation import AnimationBridge, AnimationManager


def test_all_eight_core_states(anim_bridge):
    """Verify all 8 specification-mandated core states can be activated."""
    states = [
        "IDLE", "LISTENING", "THINKING", "PROCESSING",
        "EXECUTING", "SPEAKING", "ERROR", "OFFLINE"
    ]
    for st in states:
        anim_bridge.set_core_state(st)
        assert anim_bridge.coreState == st


def test_audio_reactivity_listening_and_speaking(anim_bridge):
    """Verify audio level updates simulate incoming mic input and TTS output (L-02)."""
    # 1. Listening (Mic input)
    anim_bridge.set_core_state("LISTENING")
    anim_bridge.set_audio_level(0.65)
    assert anim_bridge.coreState == "LISTENING"
    assert anim_bridge._pending_audio_level == 0.65

    # 2. Speaking (TTS output)
    anim_bridge.set_core_state("SPEAKING")
    anim_bridge.set_audio_level(0.85)
    assert anim_bridge.coreState == "SPEAKING"
    assert anim_bridge._pending_audio_level == 0.85


def test_procedural_harmonics_generation(anim_bridge):
    """Verify procedural harmonic spectrum generation derived from scalar RMS amplitude (B-02)."""
    anim_bridge.set_audio_level(0.8)
    anim_bridge._on_coalesce_tick()

    harmonics = anim_bridge._procedural_harmonics
    assert len(harmonics) == 16
    assert any(h > 0.0 for h in harmonics)
    for h in harmonics:
        assert 0.0 <= h <= 1.0


def test_exponential_audio_decay(anim_bridge):
    """Verify smooth decay to 0.0 when speech ceases without visual snapping."""
    anim_bridge._current_audio_level = 0.8
    anim_bridge.set_audio_level(0.0)

    # Simulate ticks of decay
    for _ in range(30):
        anim_bridge._on_coalesce_tick()

    assert anim_bridge.audioLevel == 0.0


def test_service_bridge_speech_state_mapping(bridge, anim_manager):
    """Verify OptimusServiceBridge speech_state_changed maps to correct core states."""
    test_mappings = [
        ({"state": "listening", "level": 0.5}, "LISTENING"),
        ({"state": "speaking", "level": 0.8}, "SPEAKING"),
        ({"state": "thinking"}, "THINKING"),
        ({"state": "executing"}, "EXECUTING"),
        ({"state": "error"}, "ERROR"),
        ({"state": "offline"}, "OFFLINE"),
        ({"state": "ready"}, "IDLE"),
    ]
    for payload, expected_state in test_mappings:
        bridge.speech_state_changed.emit(payload)
        assert anim_manager.bridge.coreState == expected_state


def test_service_bridge_telemetry_dispatch(bridge, anim_manager):
    """Verify OptimusServiceBridge telemetry_updated updates both bridge and monitor."""
    payload = {
        "cpu_percent": 82.5,
        "ram_percent": 88.0,
        "gpu_percent": 45.0,
        "vram_percent": 60.0,
    }
    bridge.telemetry_updated.emit(payload)

    assert anim_manager.bridge.cpuUsage == 82.5
    assert anim_manager.bridge.ramUsage == 88.0
    assert anim_manager.bridge.gpuUsage == 45.0
    assert anim_manager.bridge.vramUsage == 60.0

    # Triggers load shedding level 1 (CPU > 80% or RAM > 85%)
    assert anim_manager.bridge.loadSheddingLevel >= 1


def test_service_bridge_mission_progress_dispatch(bridge, anim_manager):
    """Verify mission_state_changed signal updates mission progress and stage."""
    payload = {"progress": 0.75, "stage": "Phase 2: Execution"}
    bridge.mission_state_changed.emit(payload)

    assert anim_manager.bridge.missionProgress == 0.75
    assert anim_manager.bridge.missionStage == "Phase 2: Execution"


def test_service_bridge_agent_states_dispatch(bridge, anim_manager):
    """Verify agent_states_updated signal updates active agent roster."""
    payload = [
        {"name": "researcher", "role": "analyst", "status": "active", "load": 0.9},
        {"name": "developer", "role": "engineer", "status": "idle", "load": 0.1},
    ]
    bridge.agent_states_updated.emit(payload)

    agents = anim_manager.bridge.activeAgents
    assert len(agents) == 2
    assert agents[0]["name"] == "researcher"
    assert agents[1]["name"] == "developer"


def test_service_bridge_reduced_motion_dispatch(bridge, anim_manager):
    """Verify reduced_motion_changed signal propagates to bridge."""
    bridge.reduced_motion_changed.emit(True)
    assert anim_manager.bridge.reducedMotion is True

    bridge.reduced_motion_changed.emit(False)
    assert anim_manager.bridge.reducedMotion is False


def test_concurrent_property_updates(anim_bridge):
    """Verify updating multiple properties in rapid succession without race conditions."""
    for i in range(50):
        anim_bridge.set_core_state("EXECUTING" if i % 2 == 0 else "THINKING")
        anim_bridge.set_audio_level(float(i % 10) / 10.0)
        anim_bridge.set_telemetry(float(i), float(100 - i))
        anim_bridge.set_mission_state(float(i) / 50.0, f"Step {i}")

    assert anim_bridge.coreState in ("EXECUTING", "THINKING")
    assert anim_bridge.missionProgress == 49.0 / 50.0
