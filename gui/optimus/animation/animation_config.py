"""gui/optimus/animation/animation_config.py — Configuration and constants for the OPTIMUS Animation Subsystem.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from gui.theme import C, OptimusTheme, get_system_reduced_motion


# Environment override to disable QML (e.g. headless CI or driver-broken environments)
ENV_DISABLE_QML: Final[str] = "OPTIMUS_DISABLE_QML"

# Target frame rates and intervals
FPS_NORMAL: Final[int] = 60
FPS_THROTTLED: Final[int] = 30
COALESCE_INTERVAL_MS: Final[int] = 1000 // FPS_NORMAL  # ~16ms (or 33ms at 30Hz)
TELEMETRY_INTERVAL_MS: Final[int] = 1000  # 1s updates to prevent UI wakeups

# Audio processing constants
AUDIO_DEADBAND_THRESHOLD: Final[float] = 0.01  # Minimum amplitude delta to trigger signal
AUDIO_DECAY_RATE: Final[float] = 0.85          # Exponential decay factor per tick when speech stops
AUDIO_SPECTRUM_BANDS: Final[int] = 16          # Number of synthetic frequency bands

# Default geometry constants for visual parity between QQuickWidget and Fallback
DEFAULT_WIDGET_MIN_WIDTH: Final[int] = 280
DEFAULT_WIDGET_MIN_HEIGHT: Final[int] = 280

# Load shedding levels
SHED_NORMAL: Final[int] = 0    # Full visual effects
SHED_LIGHT: Final[int] = 1     # Scanlines and particle bursts disabled
SHED_MEDIUM: Final[int] = 2    # Waveform canvas repaints reduced, ring rotation slowed
SHED_MAX: Final[int] = 3       # Static rendering or fallback


@dataclass(frozen=True)
class AnimationPalette:
    """Design token colors exposed to QML."""
    bg_void: str = OptimusTheme.BG_VOID
    bg_base: str = OptimusTheme.BG_BASE
    bg_panel: str = OptimusTheme.BG_PANEL
    bg_elevated: str = OptimusTheme.BG_ELEVATED
    accent_primary: str = OptimusTheme.ACCENT_PRI
    accent_cyan: str = OptimusTheme.ACCENT_CYAN
    accent_amber: str = OptimusTheme.ACCENT_AMBER
    accent_green: str = OptimusTheme.ACCENT_GREEN
    accent_red: str = OptimusTheme.ACCENT_RED
    text_primary: str = OptimusTheme.TEXT_PRIMARY
    text_secondary: str = OptimusTheme.TEXT_SECONDARY
    text_muted: str = OptimusTheme.TEXT_MUTED
    border_hairline: str = OptimusTheme.BORDER_HAIRLINE


def is_qml_disabled() -> bool:
    """Check whether QML is explicitly disabled by environment variable."""
    return os.environ.get(ENV_DISABLE_QML, "0").strip().lower() in ("1", "true", "yes")


def is_reduced_motion() -> bool:
    """Return whether the system or OS has requested reduced motion."""
    return get_system_reduced_motion()
