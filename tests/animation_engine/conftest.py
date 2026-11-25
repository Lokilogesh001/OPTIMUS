"""tests/animation_engine/conftest.py — Headless test environment fixtures for the Animation Engine.

Configures QT_QPA_PLATFORM=offscreen and QSG_RHI_BACKEND=software (L-03)
for reliable headless CI and test execution.
"""
from __future__ import annotations

import os
import pytest

# Ensure headless execution before PyQt6 imports
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PyQt6.QtWidgets import QApplication
from gui.optimus.animation import AnimationBridge, AnimationFallbackWidget, AnimationManager
from gui.optimus.service_bridge import OptimusServiceBridge


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication instance."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def bridge(qapp):
    """OptimusServiceBridge singleton."""
    return OptimusServiceBridge.instance()


@pytest.fixture
def anim_bridge(qapp):
    """Isolated AnimationBridge instance."""
    b = AnimationBridge()
    yield b
    b.shutdown()


@pytest.fixture
def anim_manager(qapp, bridge):
    """Isolated AnimationManager instance."""
    mgr = AnimationManager()
    mgr.connect_service_bridge(bridge)
    yield mgr
    mgr.shutdown()


@pytest.fixture
def fallback_widget(qapp):
    """Isolated AnimationFallbackWidget instance."""
    w = AnimationFallbackWidget()
    yield w
    w.deleteLater()
