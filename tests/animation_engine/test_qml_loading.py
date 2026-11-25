"""tests/animation_engine/test_qml_loading.py — Tests for QML file loading and compilation.
"""
from __future__ import annotations

from pathlib import Path
import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuickWidgets import QQuickWidget

from gui.optimus.animation import AnimationBridge, QMLLoader


QML_DIR = Path("gui/optimus/animation/qml").resolve()


def test_qml_loader_availability():
    """Verify QMLLoader reports availability in test environment."""
    assert QMLLoader.is_available() is True


def test_qml_load_optimus_core(qapp, anim_bridge):
    """Verify OptimusCore.qml compiles and loads cleanly."""
    path = QML_DIR / "OptimusCore.qml"
    widget = QMLLoader.load_qml_widget(path, anim_bridge)
    assert widget is not None
    assert isinstance(widget, QQuickWidget)
    assert widget.status() == QQuickWidget.Status.Ready
    widget.deleteLater()


def test_qml_load_voice_visualizer(qapp, anim_bridge):
    """Verify VoiceVisualizer.qml compiles and loads cleanly."""
    path = QML_DIR / "VoiceVisualizer.qml"
    widget = QMLLoader.load_qml_widget(path, anim_bridge)
    assert widget is not None
    assert widget.status() == QQuickWidget.Status.Ready
    widget.deleteLater()


def test_qml_load_agent_network(qapp, anim_bridge):
    """Verify AgentNetwork.qml compiles and loads cleanly."""
    path = QML_DIR / "AgentNetwork.qml"
    widget = QMLLoader.load_qml_widget(path, anim_bridge)
    assert widget is not None
    assert widget.status() == QQuickWidget.Status.Ready
    widget.deleteLater()


def test_qml_load_mission_visualizer(qapp, anim_bridge):
    """Verify MissionVisualizer.qml compiles and loads cleanly."""
    path = QML_DIR / "MissionVisualizer.qml"
    widget = QMLLoader.load_qml_widget(path, anim_bridge)
    assert widget is not None
    assert widget.status() == QQuickWidget.Status.Ready
    widget.deleteLater()


def test_qml_load_telemetry_visualizer(qapp, anim_bridge):
    """Verify TelemetryVisualizer.qml compiles and loads cleanly."""
    path = QML_DIR / "TelemetryVisualizer.qml"
    widget = QMLLoader.load_qml_widget(path, anim_bridge)
    assert widget is not None
    assert widget.status() == QQuickWidget.Status.Ready
    widget.deleteLater()


def test_qml_load_navigation_transitions(qapp, anim_bridge):
    """Verify NavigationTransitions.qml compiles and loads cleanly."""
    path = QML_DIR / "NavigationTransitions.qml"
    widget = QMLLoader.load_qml_widget(path, anim_bridge)
    assert widget is not None
    assert widget.status() == QQuickWidget.Status.Ready
    widget.deleteLater()


def test_qml_load_main_animation(qapp, anim_bridge):
    """Verify MainAnimation.qml composite compiles and loads cleanly."""
    path = QML_DIR / "MainAnimation.qml"
    widget = QMLLoader.load_qml_widget(path, anim_bridge)
    assert widget is not None
    assert widget.status() == QQuickWidget.Status.Ready
    widget.deleteLater()


def test_qml_load_primitives_direct(qapp, anim_bridge):
    """Verify all 4 primitive QML files compile cleanly via QQmlEngine."""
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("animBridge", anim_bridge)
    primitives = ["HoloRing.qml", "PulseGlow.qml", "ScanLine.qml", "WaveformCanvas.qml"]
    for p_name in primitives:
        path = QML_DIR / "components" / p_name
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
        assert comp.status() == QQmlComponent.Status.Ready, f"Errors in {p_name}: {[e.toString() for e in comp.errors()]}"


def test_qml_missing_file_handling(qapp, anim_bridge):
    """Verify requesting a non-existent QML file returns None without crashing."""
    widget = QMLLoader.load_qml_widget("gui/non_existent.qml", anim_bridge)
    assert widget is None


def test_qml_syntax_error_handling(qapp, anim_bridge, tmp_path):
    """Verify broken QML syntax returns None, logs errors, and deletes widget."""
    bad_qml = tmp_path / "broken.qml"
    bad_qml.write_text("import QtQuick\nItem { broken syntax !!!", encoding="utf-8")
    widget = QMLLoader.load_qml_widget(bad_qml, anim_bridge)
    assert widget is None
