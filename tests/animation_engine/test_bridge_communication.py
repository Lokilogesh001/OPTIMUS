"""tests/animation_engine/test_bridge_communication.py — Tests for AnimationBridge property bindings, bounds clamping, and sanitization.
"""
from __future__ import annotations

import time
import pytest
from PyQt6.QtCore import QObject

from gui.optimus.animation import AnimationBridge


def test_bridge_initial_properties(anim_bridge):
    """Verify default initial property values of AnimationBridge."""
    assert anim_bridge.coreState == "IDLE"
    assert anim_bridge.audioLevel == 0.0
    assert len(anim_bridge.audioWaveform) == 32
    assert anim_bridge.cpuUsage == 0.0
    assert anim_bridge.ramUsage == 0.0
    assert anim_bridge.gpuUsage == 0.0
    assert anim_bridge.vramUsage == 0.0
    assert anim_bridge.activeAgents == []
    assert anim_bridge.missionProgress == 0.0
    assert anim_bridge.missionStage == "Ready"
    assert anim_bridge.isPaused is False
    assert anim_bridge.loadSheddingLevel == 0


def test_bridge_core_state_transitions_and_signal(anim_bridge):
    """Verify setting valid core states emits coreStateChanged signal."""
    received = []
    anim_bridge.coreStateChanged.connect(received.append)

    for state in ["LISTENING", "THINKING", "PROCESSING", "EXECUTING", "SPEAKING", "ERROR", "OFFLINE", "IDLE"]:
        anim_bridge.set_core_state(state)
        assert anim_bridge.coreState == state

    assert len(received) == 8


def test_bridge_core_state_invalid_sanitization(anim_bridge):
    """Verify invalid state strings fall back safely to 'IDLE'."""
    anim_bridge.set_core_state("INVALID_STATE_XYZ")
    assert anim_bridge.coreState == "IDLE"

    anim_bridge.set_core_state("MALICIOUS; DROP TABLE;")
    assert anim_bridge.coreState == "IDLE"


def test_bridge_audio_level_clamping(anim_bridge):
    """Verify audio level bounds clamping [0.0, 1.0]."""
    anim_bridge.set_audio_level(-10.0)
    assert anim_bridge._pending_audio_level == 0.0

    anim_bridge.set_audio_level(25.5)
    assert anim_bridge._pending_audio_level == 1.0

    anim_bridge.set_audio_level(0.75)
    assert anim_bridge._pending_audio_level == 0.75


def test_bridge_audio_level_deadband_coalescing(qapp, anim_bridge):
    """Verify audio updates below deadband threshold do not flood the Qt signal."""
    emitted = []
    anim_bridge.audioLevelChanged.connect(emitted.append)

    anim_bridge.set_audio_level(0.002)  # Below 0.01 threshold
    anim_bridge._on_coalesce_tick()
    qapp.processEvents()

    # Should not emit for sub-deadband delta
    assert len(emitted) == 0

    # Large jump above deadband
    anim_bridge.set_audio_level(0.85)
    anim_bridge._on_coalesce_tick()
    qapp.processEvents()

    assert len(emitted) >= 1
    assert anim_bridge.audioLevel > 0.0


def test_bridge_audio_waveform_clamping_and_normalization(anim_bridge):
    """Verify waveform input is bounded [-1.0, 1.0] and normalized in length."""
    raw_samples = [-5.0, 0.5, 3.0]
    anim_bridge.set_audio_waveform(raw_samples)
    waveform = anim_bridge.audioWaveform

    assert len(waveform) == 32
    assert waveform[0] == -1.0
    assert waveform[1] == 0.5
    assert waveform[2] == 1.0
    assert waveform[3] == 0.0


def test_bridge_telemetry_clamping_and_signals(anim_bridge):
    """Verify CPU, RAM, GPU, VRAM clamping [0.0, 100.0] and telemetryChanged signal."""
    emitted = []
    anim_bridge.telemetryChanged.connect(lambda: emitted.append(True))

    anim_bridge.set_telemetry(-5.0, 150.0, -20.0, 200.0)
    assert anim_bridge.cpuUsage == 0.0
    assert anim_bridge.ramUsage == 100.0
    assert anim_bridge.gpuUsage == 0.0
    assert anim_bridge.vramUsage == 100.0
    assert len(emitted) == 1


def test_bridge_agent_sanitization_zero_credential_exposure(anim_bridge):
    """Verify agent payloads are sanitized and internal pointers or API keys are stripped (B-03)."""
    raw_agents = [
        {
            "name": "researcher",
            "role": "analyst",
            "status": "working",
            "load": 0.8,
            "api_key": "SECRET_KEY_12345",
            "internal_obj": object(),
        },
        "invalid_non_dict_entry",
    ]
    anim_bridge.set_active_agents(raw_agents)
    agents = anim_bridge.activeAgents

    assert len(agents) == 1
    assert agents[0]["name"] == "researcher"
    assert agents[0]["role"] == "analyst"
    assert agents[0]["status"] == "working"
    assert agents[0]["load"] == 0.8
    assert "api_key" not in agents[0]
    assert "internal_obj" not in agents[0]


def test_bridge_mission_progress_and_stage(anim_bridge):
    """Verify mission progress and stage bounds clamping and signal emission."""
    emitted = []
    anim_bridge.missionChanged.connect(lambda: emitted.append(True))

    anim_bridge.set_mission_state(-0.5, "Stage 1")
    assert anim_bridge.missionProgress == 0.0
    assert anim_bridge.missionStage == "Stage 1"

    anim_bridge.set_mission_state(1.5, "A" * 100)
    assert anim_bridge.missionProgress == 1.0
    assert len(anim_bridge.missionStage) <= 64
    assert len(emitted) == 2


def test_bridge_reduced_motion_and_pause(anim_bridge):
    """Verify reduced motion and pause state setters and notifications."""
    mot_emitted = []
    anim_bridge.reducedMotionChanged.connect(mot_emitted.append)
    anim_bridge.set_reduced_motion(True)
    assert anim_bridge.reducedMotion is True
    assert len(mot_emitted) == 1

    pause_emitted = []
    anim_bridge.pauseStateChanged.connect(pause_emitted.append)
    anim_bridge.set_paused(True)
    assert anim_bridge.isPaused is True
    assert len(pause_emitted) == 1


def test_bridge_load_shedding_levels(anim_bridge):
    """Verify load shedding levels 0 to 3 with clamping."""
    shed_emitted = []
    anim_bridge.loadSheddingChanged.connect(shed_emitted.append)

    anim_bridge.set_load_shedding_level(2)
    assert anim_bridge.loadSheddingLevel == 2

    anim_bridge.set_load_shedding_level(10)  # Clamp to 3
    assert anim_bridge.loadSheddingLevel == 3

    anim_bridge.set_load_shedding_level(-5)  # Clamp to 0
    assert anim_bridge.loadSheddingLevel == 0

    assert len(shed_emitted) == 3


def test_bridge_palette_constants_exposed(anim_bridge):
    """Verify design token colors are accessible to QML as constant properties."""
    assert anim_bridge.colorBgVoid.startswith("#")
    assert anim_bridge.colorBgBase.startswith("#")
    assert anim_bridge.colorBgPanel.startswith("#")
    assert anim_bridge.colorAccentPrimary.startswith("#")
    assert anim_bridge.colorAccentCyan.startswith("#")
    assert anim_bridge.colorTextPrimary.startswith("#")
