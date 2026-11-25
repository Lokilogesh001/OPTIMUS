"""gui/optimus/animation — Cinematic Animation Subsystem for OPTIMUS.

Provides Qt Quick + QML visual scenes embedded directly within PyQt6 Qt Widgets,
with automatic fallback to high-fidelity QPainter rendering.
"""
from __future__ import annotations

from gui.optimus.animation.animation_bridge import AnimationBridge
from gui.optimus.animation.animation_config import (
    AnimationPalette,
    is_qml_disabled,
    is_reduced_motion,
)
from gui.optimus.animation.animation_manager import AnimationManager
from gui.optimus.animation.fallback_renderer import AnimationFallbackWidget
from gui.optimus.animation.qml_loader import QMLLoader

__all__ = [
    "AnimationBridge",
    "AnimationConfig",
    "AnimationFallbackWidget",
    "AnimationManager",
    "AnimationPalette",
    "QMLLoader",
    "is_qml_disabled",
    "is_reduced_motion",
]
