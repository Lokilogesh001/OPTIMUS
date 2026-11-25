"""gui/optimus/animation/animation_bridge.py — Typed, thread-safe Python-to-QML bridge.

Enforces zero-credential exposure, boundary clamping, lock-free event coalescing,
and clean lifecycle management.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot

from gui.optimus.animation.animation_config import (
    AUDIO_DEADBAND_THRESHOLD,
    AUDIO_DECAY_RATE,
    AUDIO_SPECTRUM_BANDS,
    COALESCE_INTERVAL_MS,
    SHED_NORMAL,
    AnimationPalette,
    is_reduced_motion,
)

logger = logging.getLogger("OPTIMUS.AnimationBridge")


class AnimationBridge(QObject):
    """Authoritative QObject bridge exposed to QML via root context as 'animBridge'."""

    # Qt Signals for reactive QML bindings
    coreStateChanged = pyqtSignal(str)
    audioLevelChanged = pyqtSignal(float)
    audioWaveformChanged = pyqtSignal(list)
    telemetryChanged = pyqtSignal()
    agentsChanged = pyqtSignal()
    missionChanged = pyqtSignal()
    reducedMotionChanged = pyqtSignal(bool)
    pauseStateChanged = pyqtSignal(bool)
    loadSheddingChanged = pyqtSignal(int)

    VALID_STATES = {
        "IDLE", "LISTENING", "THINKING", "PROCESSING",
        "EXECUTING", "SPEAKING", "ERROR", "OFFLINE"
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._palette = AnimationPalette()

        # Core state
        self._core_state: str = "IDLE"

        # Audio state (guarded atomic scalar for background thread updates)
        self._pending_audio_level: float = 0.0
        self._current_audio_level: float = 0.0
        self._audio_waveform: list[float] = [0.0] * 32
        self._procedural_harmonics: list[float] = [0.0] * AUDIO_SPECTRUM_BANDS
        self._phase_clock: float = 0.0

        # Telemetry state
        self._cpu_usage: float = 0.0
        self._ram_usage: float = 0.0
        self._gpu_usage: float = 0.0
        self._vram_usage: float = 0.0

        # Agents & Missions
        self._active_agents: list[dict[str, Any]] = []
        self._mission_progress: float = 0.0
        self._mission_stage: str = "Ready"

        # Performance & Accessibility
        self._reduced_motion: bool = is_reduced_motion()
        self._is_paused: bool = False
        self._load_shedding_level: int = SHED_NORMAL

        # Coalescing timer on main GUI thread (30Hz/60Hz)
        self._coalesce_timer = QTimer(self)
        self._coalesce_timer.setInterval(COALESCE_INTERVAL_MS)
        self._coalesce_timer.timeout.connect(self._on_coalesce_tick)
        self._coalesce_timer.start()

    # -------------------------------------------------------------------------
    # Core State Property
    # -------------------------------------------------------------------------
    @pyqtProperty(str, notify=coreStateChanged)
    def coreState(self) -> str:
        return self._core_state

    @pyqtSlot(str)
    def set_core_state(self, state: str) -> None:
        clean_state = str(state).strip().upper()
        if clean_state not in self.VALID_STATES:
            logger.warning("Invalid core state: %s. Defaulting to IDLE", state)
            clean_state = "IDLE"
        if self._core_state != clean_state:
            self._core_state = clean_state
            self.coreStateChanged.emit(self._core_state)

    # -------------------------------------------------------------------------
    # Audio Properties & Coalescing
    # -------------------------------------------------------------------------
    @pyqtProperty(float, notify=audioLevelChanged)
    def audioLevel(self) -> float:
        return self._current_audio_level

    @pyqtProperty(list, notify=audioWaveformChanged)
    def audioWaveform(self) -> list[float]:
        return self._audio_waveform

    @pyqtSlot(float)
    def set_audio_level(self, level: float) -> None:
        """Thread-safe setter. Updates atomic float without flooding Qt event loop."""
        try:
            clamped = max(0.0, min(1.0, float(level)))
            self._pending_audio_level = clamped
        except (ValueError, TypeError):
            self._pending_audio_level = 0.0

    @pyqtSlot(list)
    def set_audio_waveform(self, samples: list) -> None:
        """Update waveform samples with bounds clamping and length normalization."""
        try:
            clamped_samples = [max(-1.0, min(1.0, float(s))) for s in samples[:64]]
            if len(clamped_samples) < 32:
                clamped_samples.extend([0.0] * (32 - len(clamped_samples)))
            self._audio_waveform = clamped_samples
            self.audioWaveformChanged.emit(self._audio_waveform)
        except Exception as exc:
            logger.debug("Failed to set audio waveform: %s", exc)

    def _on_coalesce_tick(self) -> None:
        """Runs at 60Hz/30Hz on GUI thread to coalesce audio level updates smoothly."""
        if self._is_paused:
            return

        target = self._pending_audio_level
        current = self._current_audio_level

        # If speech has stopped (target is 0), decay smoothly
        if target <= 0.001 and current > 0.001:
            new_level = current * AUDIO_DECAY_RATE
            if new_level < 0.01:
                new_level = 0.0
            is_decaying = True
        else:
            # Responsive lerp toward target
            new_level = current + (target - current) * 0.45
            is_decaying = False

        # Check deadband threshold or decay progression
        delta = abs(new_level - current)
        if delta >= AUDIO_DEADBAND_THRESHOLD or is_decaying or (new_level == 0.0 and current > 0.0):
            self._current_audio_level = round(new_level, 4)
            self.audioLevelChanged.emit(self._current_audio_level)

            # Update procedural harmonic generator clock
            self._phase_clock += 0.15
            self._update_procedural_harmonics()

    def _update_procedural_harmonics(self) -> None:
        """Synthesize 16 harmonic bands derived from scalar RMS amplitude."""
        level = self._current_audio_level
        harmonics = []
        for i in range(AUDIO_SPECTRUM_BANDS):
            freq = 1.0 + (i * 0.35)
            # Damping factor: higher frequencies have lower base energy
            damping = 1.0 - (i / (AUDIO_SPECTRUM_BANDS * 1.2))
            sine_val = (math.sin(self._phase_clock * freq + i * 0.5) + 1.0) * 0.5
            band_val = level * sine_val * damping
            harmonics.append(round(max(0.0, min(1.0, band_val)), 3))
        self._procedural_harmonics = harmonics

    # -------------------------------------------------------------------------
    # Telemetry Properties
    # -------------------------------------------------------------------------
    @pyqtProperty(float, notify=telemetryChanged)
    def cpuUsage(self) -> float:
        return self._cpu_usage

    @pyqtProperty(float, notify=telemetryChanged)
    def ramUsage(self) -> float:
        return self._ram_usage

    @pyqtProperty(float, notify=telemetryChanged)
    def gpuUsage(self) -> float:
        return self._gpu_usage

    @pyqtProperty(float, notify=telemetryChanged)
    def vramUsage(self) -> float:
        return self._vram_usage

    @pyqtSlot(float, float)
    def set_telemetry(self, cpu: float, ram: float, gpu: float = 0.0, vram: float = 0.0) -> None:
        """Update telemetry metrics with clamping."""
        self._cpu_usage = max(0.0, min(100.0, float(cpu)))
        self._ram_usage = max(0.0, min(100.0, float(ram)))
        self._gpu_usage = max(0.0, min(100.0, float(gpu)))
        self._vram_usage = max(0.0, min(100.0, float(vram)))
        self.telemetryChanged.emit()

    # -------------------------------------------------------------------------
    # Agent & Mission Properties
    # -------------------------------------------------------------------------
    @pyqtProperty(list, notify=agentsChanged)
    def activeAgents(self) -> list[dict[str, Any]]:
        return self._active_agents

    @pyqtSlot(list)
    def set_active_agents(self, agents: list) -> None:
        sanitized = []
        for a in agents:
            if isinstance(a, dict):
                # Ensure no credential or internal pointer leakage
                clean_agent = {
                    "name": str(a.get("name", "Agent")),
                    "role": str(a.get("role", "specialist")),
                    "status": str(a.get("status", "idle")),
                    "load": max(0.0, min(1.0, float(a.get("load", 0.0)))),
                }
                sanitized.append(clean_agent)
        self._active_agents = sanitized
        self.agentsChanged.emit()

    @pyqtProperty(float, notify=missionChanged)
    def missionProgress(self) -> float:
        return self._mission_progress

    @pyqtProperty(str, notify=missionChanged)
    def missionStage(self) -> str:
        return self._mission_stage

    @pyqtSlot(float, str)
    def set_mission_state(self, progress: float, stage: str) -> None:
        self._mission_progress = max(0.0, min(1.0, float(progress)))
        self._mission_stage = str(stage)[:64]
        self.missionChanged.emit()

    # -------------------------------------------------------------------------
    # Accessibility & Lifecycle Properties
    # -------------------------------------------------------------------------
    @pyqtProperty(bool, notify=reducedMotionChanged)
    def reducedMotion(self) -> bool:
        return self._reduced_motion

    @pyqtSlot(bool)
    def set_reduced_motion(self, reduced: bool) -> None:
        if self._reduced_motion != bool(reduced):
            self._reduced_motion = bool(reduced)
            self.reducedMotionChanged.emit(self._reduced_motion)

    @pyqtProperty(bool, notify=pauseStateChanged)
    def isPaused(self) -> bool:
        return self._is_paused

    @pyqtSlot(bool)
    def set_paused(self, paused: bool) -> None:
        if self._is_paused != bool(paused):
            self._is_paused = bool(paused)
            self.pauseStateChanged.emit(self._is_paused)

    @pyqtProperty(int, notify=loadSheddingChanged)
    def loadSheddingLevel(self) -> int:
        return self._load_shedding_level

    @pyqtSlot(int)
    def set_load_shedding_level(self, level: int) -> None:
        clamped = max(0, min(3, int(level)))
        if self._load_shedding_level != clamped:
            self._load_shedding_level = clamped
            self.loadSheddingChanged.emit(self._load_shedding_level)

    # -------------------------------------------------------------------------
    # Palette Color Properties for QML
    # -------------------------------------------------------------------------
    @pyqtProperty(str, constant=True)
    def colorBgVoid(self) -> str:
        return self._palette.bg_void

    @pyqtProperty(str, constant=True)
    def colorBgBase(self) -> str:
        return self._palette.bg_base

    @pyqtProperty(str, constant=True)
    def colorBgPanel(self) -> str:
        return self._palette.bg_panel

    @pyqtProperty(str, constant=True)
    def colorAccentPrimary(self) -> str:
        return self._palette.accent_primary

    @pyqtProperty(str, constant=True)
    def colorAccentCyan(self) -> str:
        return self._palette.accent_cyan

    @pyqtProperty(str, constant=True)
    def colorAccentAmber(self) -> str:
        return self._palette.accent_amber

    @pyqtProperty(str, constant=True)
    def colorAccentGreen(self) -> str:
        return self._palette.accent_green

    @pyqtProperty(str, constant=True)
    def colorAccentRed(self) -> str:
        return self._palette.accent_red

    @pyqtProperty(str, constant=True)
    def colorTextPrimary(self) -> str:
        return self._palette.text_primary

    @pyqtProperty(str, constant=True)
    def colorTextMuted(self) -> str:
        return self._palette.text_muted

    def shutdown(self) -> None:
        """Stop coalescing timer and release resources cleanly."""
        if self._coalesce_timer.isActive():
            self._coalesce_timer.stop()
