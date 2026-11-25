"""gui/optimus/animation/animation_manager.py — Master animation coordinator.

Manages the lifecycle of QML widgets and fallback renderers, retains strong references
to prevent GC collection issues, coordinates service bridge subscriptions, and
provides a clean API for embedding HUD animations.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

from PyQt6.QtCore import QObject, pyqtSlot
from PyQt6.QtWidgets import QWidget

from gui.optimus.animation.animation_bridge import AnimationBridge
from gui.optimus.animation.animation_config import is_reduced_motion
from gui.optimus.animation.fallback_renderer import AnimationFallbackWidget
from gui.optimus.animation.qml_loader import QMLLoader
from gui.optimus.animation.resource_monitor import AdaptivePerformanceMonitor

logger = logging.getLogger("OPTIMUS.AnimationManager")


class AnimationManager(QObject):
    """Central manager for cinematic animations across the OPTIMUS application."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Retain strong reference to prevent GC segfault (M-01)
        self._bridge: Final[AnimationBridge] = AnimationBridge(self)
        self._monitor: Final[AdaptivePerformanceMonitor] = AdaptivePerformanceMonitor(self)

        # Wire monitor to bridge
        self._monitor.loadSheddingChanged.connect(self._bridge.set_load_shedding_level)
        self._monitor.reducedMotionChanged.connect(self._bridge.set_reduced_motion)

        self._qml_dir: Final[Path] = Path(__file__).parent / "qml"
        self._active_widgets: list[QWidget] = []
        self._connected_bridge: Any = None

    @property
    def bridge(self) -> AnimationBridge:
        """Authoritative typed bridge exposed for state updates."""
        return self._bridge

    @property
    def monitor(self) -> AdaptivePerformanceMonitor:
        return self._monitor

    # -------------------------------------------------------------------------
    # Widget Factories
    # -------------------------------------------------------------------------
    def create_core_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the central OPTIMUS Core animation widget (or fallback)."""
        qml_path = self._qml_dir / "OptimusCore.qml"
        widget = QMLLoader.load_qml_widget(qml_path, self._bridge, parent)
        if widget is None:
            logger.info("Using AnimationFallbackWidget for OptimusCore")
            fallback = AnimationFallbackWidget(parent)
            self._connect_fallback(fallback)
            self._track_widget(fallback)
            return fallback

        self._track_widget(widget)
        return widget

    def create_voice_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the Voice Visualizer waveform widget (or fallback)."""
        qml_path = self._qml_dir / "VoiceVisualizer.qml"
        widget = QMLLoader.load_qml_widget(qml_path, self._bridge, parent)
        if widget is None:
            fallback = AnimationFallbackWidget(parent)
            self._connect_fallback(fallback)
            self._track_widget(fallback)
            return fallback

        self._track_widget(widget)
        return widget

    def create_agent_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the Multi-Agent Network visualizer widget."""
        qml_path = self._qml_dir / "AgentNetwork.qml"
        widget = QMLLoader.load_qml_widget(qml_path, self._bridge, parent)
        if widget is None:
            fallback = AnimationFallbackWidget(parent)
            self._connect_fallback(fallback)
            self._track_widget(fallback)
            return fallback

        self._track_widget(widget)
        return widget

    def create_mission_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the Mission DAG and Progress visualizer widget."""
        qml_path = self._qml_dir / "MissionVisualizer.qml"
        widget = QMLLoader.load_qml_widget(qml_path, self._bridge, parent)
        if widget is None:
            fallback = AnimationFallbackWidget(parent)
            self._connect_fallback(fallback)
            self._track_widget(fallback)
            return fallback

        self._track_widget(widget)
        return widget

    def create_telemetry_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the Telemetry Visualizer gauge widget."""
        qml_path = self._qml_dir / "TelemetryVisualizer.qml"
        widget = QMLLoader.load_qml_widget(qml_path, self._bridge, parent)
        if widget is None:
            fallback = AnimationFallbackWidget(parent)
            self._connect_fallback(fallback)
            self._track_widget(fallback)
            return fallback

        self._track_widget(widget)
        return widget

    def create_main_animation_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the composite Main Animation widget for primary dashboard embedding."""
        qml_path = self._qml_dir / "MainAnimation.qml"
        widget = QMLLoader.load_qml_widget(qml_path, self._bridge, parent)
        if widget is None:
            fallback = AnimationFallbackWidget(parent)
            self._connect_fallback(fallback)
            self._track_widget(fallback)
            return fallback

        self._track_widget(widget)
        return widget

    def _connect_fallback(self, fallback: AnimationFallbackWidget) -> None:
        """Bind fallback widget directly to bridge property signals."""
        self._bridge.coreStateChanged.connect(fallback.set_core_state)
        self._bridge.audioLevelChanged.connect(fallback.set_audio_level)
        self._bridge.telemetryChanged.connect(
            lambda: fallback.set_telemetry(self._bridge.cpuUsage, self._bridge.ramUsage)
        )
        self._bridge.reducedMotionChanged.connect(fallback.set_reduced_motion)
        self._bridge.pauseStateChanged.connect(fallback.set_paused)

        # Initialize current state
        fallback.set_core_state(self._bridge.coreState)
        fallback.set_audio_level(self._bridge.audioLevel)
        fallback.set_telemetry(self._bridge.cpuUsage, self._bridge.ramUsage)

    def _track_widget(self, widget: QWidget) -> None:
        self._active_widgets.append(widget)

    # -------------------------------------------------------------------------
    # OptimusServiceBridge Integration
    # -------------------------------------------------------------------------
    def connect_service_bridge(self, service_bridge: Any) -> None:
        """Connect to OptimusServiceBridge signals safely without leaking."""
        if service_bridge is None or self._connected_bridge is service_bridge:
            return
        self._connected_bridge = service_bridge

        # 1. Speech state updates
        if hasattr(service_bridge, "speech_state_changed"):
            service_bridge.speech_state_changed.connect(self._on_speech_state_changed)

        # 2. Telemetry updates (CPU, RAM, GPU)
        if hasattr(service_bridge, "telemetry_updated"):
            service_bridge.telemetry_updated.connect(self._on_telemetry_updated)

        # 3. Mission / Task progress
        if hasattr(service_bridge, "mission_state_changed"):
            service_bridge.mission_state_changed.connect(self._on_mission_state_changed)

        # 4. Agent states
        if hasattr(service_bridge, "agent_states_updated"):
            service_bridge.agent_states_updated.connect(self._on_agent_states_updated)

        # 5. Reduced motion
        if hasattr(service_bridge, "reduced_motion_changed"):
            service_bridge.reduced_motion_changed.connect(self._on_reduced_motion_changed)

        # 6. Real-time audio stream output level hook
        try:
            from core.audio_stream import ContinuousAudioStreamPlayer
            ContinuousAudioStreamPlayer.instance().set_level_callback(self._bridge.set_audio_level)
        except Exception:
            pass

    @pyqtSlot(dict)
    def _on_speech_state_changed(self, data: dict) -> None:
        raw_state = str(data.get("state", data.get("status", "IDLE"))).upper()
        level = float(data.get("audio_level", data.get("level", 0.0)))
        if level > 0:
            self._bridge.set_audio_level(level)

        if "LISTEN" in raw_state:
            self._bridge.set_core_state("LISTENING")
        elif "SPEAK" in raw_state:
            self._bridge.set_core_state("SPEAKING")
        elif "THINK" in raw_state or "PROCESS" in raw_state:
            self._bridge.set_core_state("THINKING")
        elif "EXEC" in raw_state:
            self._bridge.set_core_state("EXECUTING")
        elif "ERR" in raw_state:
            self._bridge.set_core_state("ERROR")
        elif "OFF" in raw_state:
            self._bridge.set_core_state("OFFLINE")
        else:
            self._bridge.set_core_state("IDLE")

    @pyqtSlot(dict)
    def _on_telemetry_updated(self, metrics: dict) -> None:
        cpu = float(metrics.get("cpu_percent", metrics.get("cpu", 0.0)))
        ram = float(metrics.get("ram_percent", metrics.get("ram", 0.0)))
        gpu = float(metrics.get("gpu_percent", metrics.get("gpu", 0.0)))
        vram = float(metrics.get("vram_percent", metrics.get("vram", 0.0)))

        self._bridge.set_telemetry(cpu, ram, gpu, vram)
        self._monitor.on_telemetry(cpu, ram)

    @pyqtSlot(dict)
    def _on_mission_state_changed(self, data: dict) -> None:
        progress = float(data.get("progress", 0.0))
        stage = str(data.get("stage", data.get("state", "Running")))
        self._bridge.set_mission_state(progress, stage)

    @pyqtSlot(list)
    def _on_agent_states_updated(self, agents: list) -> None:
        self._bridge.set_active_agents(agents)

    @pyqtSlot(bool)
    def _on_reduced_motion_changed(self, reduced: bool) -> None:
        self._bridge.set_reduced_motion(reduced)

    # -------------------------------------------------------------------------
    # Lifecycle & Cleanup
    # -------------------------------------------------------------------------
    def pause_all(self) -> None:
        """Pause all animations (called when window minimized or section hidden)."""
        self._bridge.set_paused(True)

    def resume_all(self) -> None:
        """Resume all animations."""
        self._bridge.set_paused(False)

    def shutdown(self) -> None:
        """Clean shutdown releasing timers, QQuickWidgets, and references."""
        from PyQt6.QtCore import QUrl

        # Detach audio level callback and ServiceBridge signal connections first
        try:
            from core.audio_stream import ContinuousAudioStreamPlayer
            ContinuousAudioStreamPlayer.instance().set_level_callback(None)
        except Exception:
            pass

        if self._connected_bridge is not None:
            for sig_name, slot in (
                ("speech_state_changed", self._on_speech_state_changed),
                ("telemetry_updated", self._on_telemetry_updated),
                ("mission_state_changed", self._on_mission_state_changed),
                ("agent_states_updated", self._on_agent_states_updated),
                ("reduced_motion_changed", self._on_reduced_motion_changed),
            ):
                try:
                    sig = getattr(self._connected_bridge, sig_name, None)
                    if sig is not None:
                        sig.disconnect(slot)
                except Exception:
                    pass

        self._bridge.shutdown()
        for w in self._active_widgets:
            try:
                if hasattr(w, "engine") and callable(w.engine):
                    eng = w.engine()
                    if eng is not None:
                        eng.rootContext().setContextProperty("animBridge", None)
                if hasattr(w, "setSource"):
                    w.setSource(QUrl())
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        self._active_widgets.clear()
        self._connected_bridge = None
