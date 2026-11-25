"""tests/animation_engine/test_resource_lifecycle.py — Tests for memory leaks, resource cleanup, and load shedding.
"""
from __future__ import annotations

import time
import pytest
from PyQt6.QtWidgets import QWidget

from gui.optimus.animation import AnimationBridge, AnimationManager
from gui.optimus.animation.resource_monitor import AdaptivePerformanceMonitor
from gui.optimus.dashboard import OptimusDashboardView


def test_repeated_initialization_and_destruction(qapp):
    """Verify initializing and destroying AnimationManager 10 times without leaks or crashes."""
    for _ in range(10):
        mgr = AnimationManager()
        w1 = mgr.create_core_widget()
        w2 = mgr.create_voice_widget()
        w1.deleteLater()
        w2.deleteLater()
        mgr.shutdown()
        qapp.processEvents()


def test_high_frequency_event_burst_coalescing(qapp, anim_bridge):
    """Verify bursting 500 audio level updates in 20ms does not choke the Qt event loop."""
    start_time = time.perf_counter()
    signal_count = []
    anim_bridge.audioLevelChanged.connect(signal_count.append)

    for i in range(500):
        anim_bridge.set_audio_level(float(i % 100) / 100.0)

    # Trigger coalesce tick
    anim_bridge._on_coalesce_tick()
    qapp.processEvents()
    elapsed = time.perf_counter() - start_time

    assert elapsed < 0.25, f"Burst processing took too long: {elapsed:.3f}s"
    # Coalescing must emit at most a handful of times, never 500
    assert len(signal_count) <= 5


def test_strong_reference_retention(anim_manager):
    """Verify AnimationManager holds strong references to bridge and monitor (M-01)."""
    assert hasattr(anim_manager, "_bridge")
    assert isinstance(anim_manager.bridge, AnimationBridge)
    assert hasattr(anim_manager, "_monitor")
    assert isinstance(anim_manager.monitor, AdaptivePerformanceMonitor)


def test_adaptive_load_shedding_thresholds(qapp):
    """Verify monitor triggers load shedding levels based on CPU and RAM strain."""
    mon = AdaptivePerformanceMonitor()
    emitted = []
    mon.loadSheddingChanged.connect(emitted.append)

    # Normal load
    mon.on_telemetry(40.0, 50.0)
    assert mon.current_level == 0

    # Light shedding (CPU > 80%)
    mon.on_telemetry(82.0, 50.0)
    assert mon.current_level == 1

    # Medium shedding (CPU > 90%)
    mon.on_telemetry(92.0, 50.0)
    assert mon.current_level == 2

    # Recovery back to normal
    mon.on_telemetry(30.0, 40.0)
    assert mon.current_level == 0

    assert len(emitted) == 3


def test_pause_all_and_resume_all(anim_manager):
    """Verify pause_all and resume_all propagate to the bridge."""
    anim_manager.pause_all()
    assert anim_manager.bridge.isPaused is True

    anim_manager.resume_all()
    assert anim_manager.bridge.isPaused is False


def test_dashboard_animation_integration(qapp, bridge):
    """Verify OptimusDashboardView embeds anim_core_widget and anim_voice_widget cleanly."""
    dash = OptimusDashboardView(bridge)
    qapp.processEvents()

    assert hasattr(dash, "animation_manager")
    assert hasattr(dash, "anim_core_widget")
    assert hasattr(dash, "anim_voice_widget")
    assert dash.anim_core_widget is not None
    assert dash.anim_voice_widget is not None

    dash.deleteLater()


def test_dashboard_pause_polling_propagation(qapp, bridge):
    """Verify navigating away triggers pause_polling and pause_all on animation_manager."""
    dash = OptimusDashboardView(bridge)
    dash.pause_polling()
    assert dash.animation_manager.bridge.isPaused is True

    dash.resume_polling()
    assert dash.animation_manager.bridge.isPaused is False

    dash.deleteLater()


def test_shutdown_cleans_active_widgets(qapp):
    """Verify shutdown releases tracked widgets."""
    mgr = AnimationManager()
    w1 = mgr.create_core_widget()
    w2 = mgr.create_voice_widget()
    assert len(mgr._active_widgets) == 2

    mgr.shutdown()
    qapp.processEvents()
    assert len(mgr._active_widgets) == 0


def test_deadband_threshold_constant(anim_bridge):
    """Verify deadband threshold prevents micro-jitter updates."""
    anim_bridge._current_audio_level = 0.5
    anim_bridge.set_audio_level(0.505)  # Delta: 0.005 < 0.01 threshold
    anim_bridge._on_coalesce_tick()

    assert anim_bridge.audioLevel == 0.5


def test_clean_shutdown_on_bridge(qapp):
    """Verify AnimationBridge shutdown cleanly halts coalescing timer."""
    bridge = AnimationBridge()
    assert bridge._coalesce_timer.isActive()
    bridge.shutdown()
    assert not bridge._coalesce_timer.isActive()
