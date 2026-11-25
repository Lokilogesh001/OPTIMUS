"""gui/optimus/animation/resource_monitor.py — Adaptive performance and load-shedding monitor.

Monitors hardware telemetry delivered via OptimusServiceBridge (zero duplicate psutil threads),
and signals load shedding levels to maintain 60 FPS UI fluid responsiveness.
"""
from __future__ import annotations

import logging
from typing import Final

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from gui.optimus.animation.animation_config import (
    SHED_LIGHT,
    SHED_MAX,
    SHED_MEDIUM,
    SHED_NORMAL,
    is_reduced_motion,
)

logger = logging.getLogger("OPTIMUS.ResourceMonitor")


class AdaptivePerformanceMonitor(QObject):
    """Monitors telemetry to trigger layered load shedding when system is under high strain."""

    loadSheddingChanged = pyqtSignal(int)
    reducedMotionChanged = pyqtSignal(bool)

    CPU_THRESHOLD_LIGHT: Final[float] = 80.0
    RAM_THRESHOLD_LIGHT: Final[float] = 85.0
    CPU_THRESHOLD_MEDIUM: Final[float] = 90.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._current_level: int = SHED_NORMAL
        self._reduced_motion: bool = is_reduced_motion()

    @property
    def current_level(self) -> int:
        return self._current_level

    @pyqtSlot(float, float)
    def on_telemetry(self, cpu: float, ram: float) -> None:
        """Evaluate incoming telemetry from OptimusServiceBridge (M-03)."""
        new_level = SHED_NORMAL

        if cpu > self.CPU_THRESHOLD_MEDIUM:
            new_level = SHED_MEDIUM
        elif cpu > self.CPU_THRESHOLD_LIGHT or ram > self.RAM_THRESHOLD_LIGHT:
            new_level = SHED_LIGHT

        if new_level != self._current_level:
            self._current_level = new_level
            logger.debug("Load shedding changed to level %d (CPU: %.1f%%, RAM: %.1f%%)", new_level, cpu, ram)
            self.loadSheddingChanged.emit(self._current_level)

    def check_reduced_motion(self) -> bool:
        """Poll OS reduced motion setting."""
        current = is_reduced_motion()
        if current != self._reduced_motion:
            self._reduced_motion = current
            self.reducedMotionChanged.emit(self._reduced_motion)
        return self._reduced_motion
