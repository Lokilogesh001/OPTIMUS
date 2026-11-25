"""tests/animation_engine/test_widget_embedding.py — Tests for QQuickWidget and Fallback embedding and layout.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from gui.optimus.animation import AnimationFallbackWidget, AnimationManager
from gui.optimus.animation.animation_config import (
    DEFAULT_WIDGET_MIN_HEIGHT,
    DEFAULT_WIDGET_MIN_WIDTH,
)


def test_qquickwidget_embedding_in_layout(qapp, anim_manager):
    """Verify QQuickWidget embeds properly in a QVBoxLayout."""
    parent = QWidget()
    lay = QVBoxLayout(parent)
    widget = anim_manager.create_core_widget(parent)
    lay.addWidget(widget)
    qapp.processEvents()

    assert widget.parent() is parent
    assert lay.count() == 1
    parent.deleteLater()


def test_geometry_parity_contract(qapp, anim_manager, fallback_widget):
    """Verify QQuickWidget and AnimationFallbackWidget satisfy the Geometry Parity Contract (M-04)."""
    core_widget = anim_manager.create_core_widget()

    # Minimum size parity
    assert core_widget.minimumWidth() >= DEFAULT_WIDGET_MIN_WIDTH
    assert core_widget.minimumHeight() >= DEFAULT_WIDGET_MIN_HEIGHT
    assert fallback_widget.minimumWidth() >= DEFAULT_WIDGET_MIN_WIDTH
    assert fallback_widget.minimumHeight() >= DEFAULT_WIDGET_MIN_HEIGHT

    # Size policy parity
    assert fallback_widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert fallback_widget.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding

    core_widget.deleteLater()


def test_dynamic_resize_stability(qapp, anim_manager):
    """Verify widget resizes across multiple resolutions without crashing or throwing errors."""
    widget = anim_manager.create_core_widget()
    resolutions = [(280, 280), (400, 300), (800, 600), (1280, 720), (1920, 1080), (100, 100)]
    for w, h in resolutions:
        widget.resize(w, h)
        qapp.processEvents()
        assert widget.width() == w or widget.width() >= widget.minimumWidth()

    widget.deleteLater()


def test_visibility_toggle_stability(qapp, anim_manager):
    """Verify show/hide cycling does not cause scene graph or lifecycle errors."""
    widget = anim_manager.create_core_widget()
    for _ in range(5):
        widget.hide()
        qapp.processEvents()
        assert not widget.isVisible()

        widget.show()
        qapp.processEvents()
        assert widget.isVisible()

    widget.deleteLater()


def test_stacked_widget_embedding(qapp, anim_manager):
    """Verify embedding in QStackedWidget and switching indices."""
    stacked = QStackedWidget()
    w1 = anim_manager.create_core_widget(stacked)
    w2 = anim_manager.create_voice_widget(stacked)
    w3 = anim_manager.create_telemetry_widget(stacked)

    stacked.addWidget(w1)
    stacked.addWidget(w2)
    stacked.addWidget(w3)
    qapp.processEvents()

    for idx in [0, 1, 2, 0, 2, 1]:
        stacked.setCurrentIndex(idx)
        qapp.processEvents()
        assert stacked.currentIndex() == idx

    stacked.deleteLater()


def test_multiple_simultaneous_instances(qapp, anim_manager):
    """Verify instantiating multiple animation widgets concurrently does not cause context conflict."""
    widgets = [
        anim_manager.create_core_widget(),
        anim_manager.create_voice_widget(),
        anim_manager.create_agent_widget(),
        anim_manager.create_mission_widget(),
        anim_manager.create_telemetry_widget(),
        anim_manager.create_main_animation_widget(),
    ]
    qapp.processEvents()
    for w in widgets:
        assert w is not None
        w.deleteLater()
