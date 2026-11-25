"""Pytest root configuration for OPTIMUS test suites."""

import os
import sys

# Ensure headless execution and software RHI for headless QtQuick/QML testing
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

# Ensure repository root is always at the head of sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from PyQt6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
except Exception:
    pass

import pytest

# Keep a session-long strong reference to the active QApplication instance once
# created by any test module so module-scoped qapp teardowns cannot drop its
# Python wrapper and corrupt QtQuick/RHI state across subsequent test modules.
_QAPP_SESSION_ANCHOR = None


@pytest.fixture(autouse=True)
def cleanup_singletons_and_threads():
    global _QAPP_SESSION_ANCHOR
    yield
    try:
        from core.audio_stream import ContinuousAudioStreamPlayer
        ContinuousAudioStreamPlayer.reset_instance()
    except Exception:
        pass
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            _QAPP_SESSION_ANCHOR = app
            app.processEvents()
    except Exception:
        pass
    import gc
    gc.collect()
    try:
        from gui.optimus.service_bridge import OptimusServiceBridge
        OptimusServiceBridge.reset()
    except Exception:
        pass
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            _QAPP_SESSION_ANCHOR = app
            app.processEvents()
    except Exception:
        pass
    gc.collect()
