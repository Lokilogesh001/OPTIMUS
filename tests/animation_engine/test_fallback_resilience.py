"""tests/animation_engine/test_fallback_resilience.py — Tests for automatic fallback renderer activation and resilience.
"""
from __future__ import annotations

import os
import pytest
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtWidgets import QWidget

from gui.optimus.animation import AnimationFallbackWidget, AnimationManager
from gui.optimus.animation.animation_config import (
    DEFAULT_WIDGET_MIN_HEIGHT,
    DEFAULT_WIDGET_MIN_WIDTH,
    ENV_DISABLE_QML,
)


def test_env_override_triggers_fallback(qapp, monkeypatch):
    """Verify OPTIMUS_DISABLE_QML=1 forces AnimationManager to return AnimationFallbackWidget."""
    monkeypatch.setenv(ENV_DISABLE_QML, "1")
    mgr = AnimationManager()
    widget = mgr.create_core_widget()

    assert isinstance(widget, AnimationFallbackWidget)
    mgr.shutdown()


def test_fallback_geometry_parity(fallback_widget):
    """Verify AnimationFallbackWidget meets the 280x280 minimum dimension parity contract."""
    assert fallback_widget.minimumWidth() >= DEFAULT_WIDGET_MIN_WIDTH
    assert fallback_widget.minimumHeight() >= DEFAULT_WIDGET_MIN_HEIGHT
    hint = fallback_widget.sizeHint()
    assert hint.width() >= DEFAULT_WIDGET_MIN_WIDTH
    assert hint.height() >= DEFAULT_WIDGET_MIN_HEIGHT


def test_fallback_state_setters(fallback_widget):
    """Verify all state setter methods on AnimationFallbackWidget update internal state cleanly."""
    fallback_widget.set_core_state("SPEAKING")
    assert fallback_widget._core_state == "SPEAKING"

    fallback_widget.set_audio_level(0.72)
    assert abs(fallback_widget._audio_level - 0.72) < 0.01

    fallback_widget.set_telemetry(45.0, 60.0)
    assert fallback_widget._cpu_usage == 45.0
    assert fallback_widget._ram_usage == 60.0

    fallback_widget.set_reduced_motion(True)
    assert fallback_widget._reduced_motion is True

    fallback_widget.set_paused(True)
    assert fallback_widget._is_paused is True


def test_fallback_paint_event_execution(qapp, fallback_widget):
    """Verify paintEvent executes across all states without exception."""
    states = ["IDLE", "LISTENING", "THINKING", "PROCESSING", "EXECUTING", "SPEAKING", "ERROR", "OFFLINE"]
    fallback_widget.resize(320, 320)

    for st in states:
        fallback_widget.set_core_state(st)
        fallback_widget.set_audio_level(0.5)
        fallback_widget.repaint()
        qapp.processEvents()


def test_fallback_reduced_motion_stops_timer(fallback_widget):
    """Verify enabling reduced motion immediately halts the internal 30 FPS animation timer."""
    fallback_widget.set_reduced_motion(False)
    assert fallback_widget._timer.isActive()

    fallback_widget.set_reduced_motion(True)
    assert not fallback_widget._timer.isActive()


def test_fallback_hide_show_pausing(qapp, fallback_widget):
    """Verify hideEvent pauses timer and showEvent resumes it (M-02)."""
    fallback_widget.set_reduced_motion(False)
    fallback_widget.set_paused(False)

    fallback_widget.show()
    qapp.processEvents()
    assert fallback_widget._timer.isActive()

    fallback_widget.hide()
    qapp.processEvents()
    assert not fallback_widget._timer.isActive()

    fallback_widget.show()
    qapp.processEvents()
    assert fallback_widget._timer.isActive()


def test_fallback_bound_to_bridge_signals(qapp, anim_manager, monkeypatch):
    """Verify fallback widget updates reactively when connected through AnimationManager."""
    monkeypatch.setenv(ENV_DISABLE_QML, "1")
    widget = anim_manager.create_core_widget()
    assert isinstance(widget, AnimationFallbackWidget)

    anim_manager.bridge.set_core_state("EXECUTING")
    assert widget._core_state == "EXECUTING"

    anim_manager.bridge.set_audio_level(0.9)
    anim_manager.bridge._on_coalesce_tick()
    qapp.processEvents()
    assert widget._audio_level > 0.0

    widget.deleteLater()


def test_fallback_close_event_cleanup(fallback_widget):
    """Verify closing widget stops the render timer cleanly."""
    fallback_widget.close()
    assert not fallback_widget._timer.isActive()
