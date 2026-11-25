"""gui/optimus/animation/qml_loader.py — Safe QQuickWidget factory and QML loader.

Performs pre-flight capability checks, handles sceneGraphError signals,
registers context properties, and cleanly falls back if QML compilation or
graphics context creation fails.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from PyQt6.QtCore import QObject, QUrl, Qt
from PyQt6.QtWidgets import QWidget

from gui.optimus.animation.animation_config import (
    DEFAULT_WIDGET_MIN_HEIGHT,
    DEFAULT_WIDGET_MIN_WIDTH,
    is_qml_disabled,
)

logger = logging.getLogger("OPTIMUS.QMLLoader")

# Safe import of QtQuickWidgets
try:
    from PyQt6.QtQuick import QQuickWindow
    from PyQt6.QtQuickWidgets import QQuickWidget
    HAVE_QT_QUICK = True
except Exception as _err:  # pragma: no cover
    QQuickWidget = None  # type: ignore
    QQuickWindow = None  # type: ignore
    HAVE_QT_QUICK = False
    logger.warning("PyQt6.QtQuickWidgets unavailable: %s", _err)


class QMLLoader:
    """Factory for safely creating and compiling QQuickWidget instances."""

    @classmethod
    def is_available(cls) -> bool:
        """Check if QtQuick is supported and not disabled by environment."""
        if is_qml_disabled():
            logger.info("QML explicitly disabled via OPTIMUS_DISABLE_QML")
            return False
        return HAVE_QT_QUICK and QQuickWidget is not None

    @classmethod
    def load_qml_widget(
        cls,
        qml_path: Path | str,
        bridge: QObject,
        parent: QWidget | None = None,
    ) -> QQuickWidget | None:
        """Safely instantiate and compile a QQuickWidget.

        Returns None if compilation fails, file is missing, or graphics context
        cannot be created, permitting automatic fallback.
        """
        if not cls.is_available():
            return None

        path_obj = Path(qml_path).resolve()
        if not path_obj.exists():
            logger.warning("QML file not found: %s", path_obj)
            return None

        try:
            widget = QQuickWidget(parent)

            # Enforce Geometry Parity Contract
            widget.setMinimumSize(DEFAULT_WIDGET_MIN_WIDTH, DEFAULT_WIDGET_MIN_HEIGHT)
            widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

            # Windows Direct3D 11 / OpenGL transparency configuration (L-01)
            widget.setClearColor(Qt.GlobalColor.transparent)
            widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

            # Scene graph error handler (B-04)
            def _on_scene_graph_error(error, message: str) -> None:
                logger.error("Qt Quick sceneGraphError: [%s] %s", error, message)

            widget.sceneGraphError.connect(_on_scene_graph_error)

            # Register authoritative AnimationBridge context property
            engine = widget.engine()
            if engine is not None:
                engine.rootContext().setContextProperty("animBridge", bridge)

            # Set source QML
            widget.setSource(QUrl.fromLocalFile(str(path_obj)))

            # Verify compilation status
            status = widget.status()
            if status == QQuickWidget.Status.Error:
                errors = widget.errors()
                err_msgs = [e.toString() for e in errors]
                logger.error("QML compilation failed for %s: %s", path_obj.name, err_msgs)
                widget.deleteLater()
                return None

            return widget

        except Exception as exc:
            logger.error("Exception loading QML widget for %s: %s", path_obj, exc, exc_info=True)
            return None
