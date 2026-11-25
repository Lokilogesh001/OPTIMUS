"""gui/optimus/animation/fallback_renderer.py — High-fidelity Qt Widgets / QPainter fallback renderer.

Provides a fluid, hardware-accelerated concentric-ring HUD widget when QML
is unavailable, disabled, or in headless environments.
Enforces the Geometry Parity Contract with QQuickWidget (280x280 minimum size,
Expanding size policy).
"""
from __future__ import annotations

import math
from typing import Final

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPaintEvent,
    QPainter,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from gui.optimus.animation.animation_config import (
    DEFAULT_WIDGET_MIN_HEIGHT,
    DEFAULT_WIDGET_MIN_WIDTH,
    FPS_THROTTLED,
    is_reduced_motion,
)
from gui.theme import OptimusTheme


class AnimationFallbackWidget(QWidget):
    """Pure QPainter fallback widget providing full visual parity with QML OptimusCore."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Enforce Geometry Parity Contract (M-04)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(DEFAULT_WIDGET_MIN_WIDTH, DEFAULT_WIDGET_MIN_HEIGHT)

        # State attributes
        self._core_state: str = "IDLE"
        self._audio_level: float = 0.0
        self._cpu_usage: float = 0.0
        self._ram_usage: float = 0.0
        self._reduced_motion: bool = is_reduced_motion()
        self._is_paused: bool = False

        # Visual animation clocks
        self._angle_outer: float = 0.0
        self._angle_mid: float = 0.0
        self._angle_inner: float = 0.0
        self._pulse_phase: float = 0.0

        # Animation render timer (30 FPS to be lightweight and CPU-friendly)
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // FPS_THROTTLED)
        self._timer.timeout.connect(self._on_tick)

        if not self._reduced_motion and not self._is_paused:
            self._timer.start()

    def sizeHint(self) -> QSize:
        return QSize(320, 320)

    def minimumSizeHint(self) -> QSize:
        return QSize(DEFAULT_WIDGET_MIN_WIDTH, DEFAULT_WIDGET_MIN_HEIGHT)

    # -------------------------------------------------------------------------
    # Public State Setters (Slot-compatible)
    # -------------------------------------------------------------------------
    def set_core_state(self, state: str) -> None:
        clean = str(state).strip().upper()
        if self._core_state != clean:
            self._core_state = clean
            self.update()

    def set_audio_level(self, level: float) -> None:
        clamped = max(0.0, min(1.0, float(level)))
        if abs(self._audio_level - clamped) > 0.01:
            self._audio_level = clamped
            self.update()

    def set_telemetry(self, cpu: float, ram: float, gpu: float = 0.0, vram: float = 0.0) -> None:
        self._cpu_usage = max(0.0, min(100.0, float(cpu)))
        self._ram_usage = max(0.0, min(100.0, float(ram)))
        self.update()

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = bool(reduced)
        if self._reduced_motion and self._timer.isActive():
            self._timer.stop()
        elif not self._reduced_motion and not self._is_paused and not self._timer.isActive():
            self._timer.start()
        self.update()

    def set_paused(self, paused: bool) -> None:
        self._is_paused = bool(paused)
        if self._is_paused and self._timer.isActive():
            self._timer.stop()
        elif not self._is_paused and not self._reduced_motion and not self._timer.isActive():
            self._timer.start()

    # -------------------------------------------------------------------------
    # Lifecycle & Visibility Hooks (M-02)
    # -------------------------------------------------------------------------
    def hideEvent(self, event) -> None:
        if self._timer.isActive():
            self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        if not self._reduced_motion and not self._is_paused and not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        if self._timer.isActive():
            self._timer.stop()
        super().closeEvent(event)

    # -------------------------------------------------------------------------
    # Animation Tick
    # -------------------------------------------------------------------------
    def _on_tick(self) -> None:
        if self._reduced_motion or self._is_paused or not self.isVisible():
            return

        # Adjust rotational speeds based on state
        speed_mult = 1.0
        if self._core_state in ("THINKING", "PROCESSING"):
            speed_mult = 3.2
        elif self._core_state == "EXECUTING":
            speed_mult = 2.0
        elif self._core_state == "OFFLINE":
            speed_mult = 0.0

        self._angle_outer = (self._angle_outer + 1.2 * speed_mult) % 360.0
        self._angle_mid = (self._angle_mid - 1.8 * speed_mult) % 360.0
        self._angle_inner = (self._angle_inner + 2.4 * speed_mult) % 360.0
        self._pulse_phase = (self._pulse_phase + 0.08) % (2.0 * math.pi)

        self.update()

    # -------------------------------------------------------------------------
    # Paint Event
    # -------------------------------------------------------------------------
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        rect = self.rect()
        w = rect.width()
        h = rect.height()
        cx = w / 2.0
        cy = h / 2.0
        center = QPointF(cx, cy)
        base_radius = min(w, h) * 0.42

        # 1. Fill background void
        painter.fillRect(rect, QColor(OptimusTheme.BG_VOID))

        # 2. State-driven palette colors
        primary_color = QColor(OptimusTheme.ACCENT_PRI)
        secondary_color = QColor(OptimusTheme.ACCENT_CYAN)

        if self._core_state == "LISTENING":
            primary_color = QColor(OptimusTheme.ACCENT_CYAN)
            secondary_color = QColor("#38bdf8")
        elif self._core_state in ("THINKING", "PROCESSING"):
            primary_color = QColor("#0084ff")
            secondary_color = QColor("#00d4ff")
        elif self._core_state == "EXECUTING":
            primary_color = QColor(OptimusTheme.ACCENT_AMBER)
            secondary_color = QColor("#f59e0b")
        elif self._core_state == "SPEAKING":
            primary_color = QColor(OptimusTheme.ACCENT_CYAN)
            secondary_color = QColor("#60a5fa")
        elif self._core_state == "ERROR":
            primary_color = QColor(OptimusTheme.ACCENT_RED)
            secondary_color = QColor("#f43f5e")
        elif self._core_state == "OFFLINE":
            primary_color = QColor(OptimusTheme.TEXT_MUTED)
            secondary_color = QColor("#475569")

        # 3. Holographic Radial Core Glow
        audio_boost = self._audio_level * (base_radius * 0.35)
        pulse_boost = 0.0 if self._reduced_motion else math.sin(self._pulse_phase) * 6.0
        core_radius = max(24.0, (base_radius * 0.30) + audio_boost + pulse_boost)

        radial_grad = QRadialGradient(center, core_radius)
        glow_alpha = 180 if self._core_state != "OFFLINE" else 40
        c_glow = QColor(primary_color)
        c_glow.setAlpha(glow_alpha)
        c_glow_outer = QColor(primary_color)
        c_glow_outer.setAlpha(0)
        radial_grad.setColorAt(0.0, c_glow)
        radial_grad.setColorAt(0.7, QColor(secondary_color.red(), secondary_color.green(), secondary_color.blue(), int(glow_alpha * 0.3)))
        radial_grad.setColorAt(1.0, c_glow_outer)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(radial_grad))
        painter.drawEllipse(center, core_radius, core_radius)

        # 4. Concentric Rings
        if base_radius > 30:
            # Outer Ring (Dashed Arc)
            r_outer = base_radius * 0.88
            pen_outer = QPen(primary_color, 2.0, Qt.PenStyle.DashLine)
            pen_outer.setDashPattern([12, 8])
            painter.setPen(pen_outer)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(self._angle_outer)
            painter.drawEllipse(QPointF(0, 0), r_outer, r_outer)
            painter.restore()

            # Middle Ring (Segmented / Dotted Arc)
            r_mid = base_radius * 0.68
            c_mid = QColor(secondary_color)
            c_mid.setAlpha(200)
            pen_mid = QPen(c_mid, 1.5, Qt.PenStyle.DotLine)
            painter.setPen(pen_mid)
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(self._angle_mid)
            painter.drawEllipse(QPointF(0, 0), r_mid, r_mid)
            painter.restore()

            # Inner Ring (Thin Precision Ring with tick marks)
            r_inner = base_radius * 0.48
            c_inner = QColor(OptimusTheme.BORDER_HAIRLINE)
            c_inner.setAlpha(220)
            painter.setPen(QPen(c_inner, 1.0))
            painter.drawEllipse(center, r_inner, r_inner)

        # 5. Central Status Typography
        painter.setPen(QColor(OptimusTheme.TEXT_PRIMARY))
        font_title = QFont("Segoe UI", 11, QFont.Weight.Bold)
        font_title.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        painter.setFont(font_title)
        painter.drawText(
            QRectF(0, cy - 22, w, 20),
            Qt.AlignmentFlag.AlignCenter,
            "OPTIMUS"
        )

        font_state = QFont("Segoe UI", 9, QFont.Weight.Medium)
        font_state.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        painter.setFont(font_state)
        painter.setPen(secondary_color)
        painter.drawText(
            QRectF(0, cy + 4, w, 18),
            Qt.AlignmentFlag.AlignCenter,
            self._core_state
        )

        # 6. Mini Telemetry Readout along bottom edge
        font_telem = QFont("Consolas", 8)
        painter.setFont(font_telem)
        painter.setPen(QColor(OptimusTheme.TEXT_MUTED))
        telem_str = f"CPU {self._cpu_usage:.0f}%  |  RAM {self._ram_usage:.0f}%"
        painter.drawText(
            QRectF(0, h - 26, w, 20),
            Qt.AlignmentFlag.AlignCenter,
            telem_str
        )
