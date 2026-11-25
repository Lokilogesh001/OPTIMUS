"""gui/theme.py — Palette, typography and the configuration values every part of the HUD reads.

Split out of ui.py in Mark LVII; behaviour unchanged."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtGui import (
    QColor,
)

# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping JARVIS from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
    HAVE_VIDEO = True
except Exception as _e:            # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "OPTIMUS"
APP_PROTOCOL = "OPTIMUS"

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 148
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def get_system_reduced_motion() -> bool:
    """Detect whether Windows 11 / OS has animations disabled for accessibility."""
    if _OS == "Windows":
        try:
            import ctypes
            anim = ctypes.c_bool()
            # SPI_GETCLIENTAREAANIMATION = 0x1042
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(anim), 0):
                return not anim.value
        except Exception:
            pass
    return False


class C:
    BG        = "#070b14"  # Deep midnight navy primary background
    PANEL     = "#0c1322"  # Secondary surface: dark blue-gray
    PANEL2    = "#111a2e"  # Elevated surface: slightly lighter dark blue-gray
    PANEL_HEAD= "#0f172a"  # Table / card header surface
    BORDER    = "#1e293b"  # Subtle hairline border
    BORDER_B  = "#334155"  # Active / hover border
    BORDER_A  = "#1e293b"  # Subtle separator border
    PRI       = "#0084ff"  # Controlled electric blue (primary accent)
    PRI_DIM   = "#005bb5"  # Muted electric blue
    PRI_GHO   = "#0b1c33"  # Quiet dark blue surface highlight
    ACC       = "#f59e0b"  # Amber warning
    ACC2      = "#38bdf8"  # Muted cyan (secondary accent)
    GREEN     = "#10b981"  # Restrained green (success)
    GREEN_D   = "#059669"  # Dark green
    RED       = "#ef4444"  # Controlled red (error)
    MUTED_C   = "#f43f5e"  # Rose / alert
    TEXT      = "#f1f5f9"  # Cool white primary text
    TEXT_DIM  = "#64748b"  # Desaturated blue-gray secondary text
    TEXT_MED  = "#94a3b8"  # Medium blue-gray
    WHITE     = "#f8fafc"  # Crisp cool white
    DARK      = "#05080f"  # Deepest background
    BAR_BG    = "#0f1728"  # Track / progress background


class OptimusTheme:
    """Unified, hardware-accelerated design token namespace for OPTIMUS UI 2.0.
    
    Acts as a high-level design token superset while preserving 100% backward
    compatibility with legacy class C.
    """
    BG_VOID        = "#04070d"
    BG_BASE        = "#070b14"
    BG_PANEL       = "#0c1322"
    BG_ELEVATED    = "#111a2e"
    BG_HEADER      = "#0f172a"
    ACCENT_PRI     = "#0084ff"
    ACCENT_CYAN    = "#38bdf8"
    ACCENT_AMBER   = "#f59e0b"
    ACCENT_GREEN   = "#10b981"
    ACCENT_RED     = "#ef4444"
    TEXT_PRIMARY   = "#f8fafc"
    TEXT_SECONDARY = "#94a3b8"
    TEXT_MUTED     = "#64748b"
    BORDER_HAIRLINE= "#1e293b"
    BORDER_ACTIVE  = "#38bdf8"
    TRACK_BG       = "#0f1728"


# Bidirectional backward compatibility aliases (Mandate M-6)
OptimusTheme.BG = OptimusTheme.BG_BASE
OptimusTheme.PANEL = OptimusTheme.BG_PANEL
OptimusTheme.PANEL2 = OptimusTheme.BG_ELEVATED
OptimusTheme.PANEL_HEAD = OptimusTheme.BG_HEADER
OptimusTheme.PRI = OptimusTheme.ACCENT_PRI
OptimusTheme.ACC = OptimusTheme.ACCENT_AMBER
OptimusTheme.ACC2 = OptimusTheme.ACCENT_CYAN
OptimusTheme.GREEN = OptimusTheme.ACCENT_GREEN
OptimusTheme.RED = OptimusTheme.ACCENT_RED
OptimusTheme.TEXT = OptimusTheme.TEXT_PRIMARY
OptimusTheme.TEXT_MED = OptimusTheme.TEXT_SECONDARY
OptimusTheme.TEXT_DIM = OptimusTheme.TEXT_MUTED
OptimusTheme.BORDER = OptimusTheme.BORDER_HAIRLINE
OptimusTheme.BORDER_B = OptimusTheme.BORDER_ACTIVE
OptimusTheme.WHITE = OptimusTheme.TEXT_PRIMARY
OptimusTheme.BAR_BG = OptimusTheme.TRACK_BG

C.BG_VOID = OptimusTheme.BG_VOID
C.BG_BASE = C.BG
C.BG_PANEL = C.PANEL
C.BG_ELEVATED = C.PANEL2
C.BG_HEADER = C.PANEL_HEAD
C.ACCENT_PRI = C.PRI
C.ACCENT_CYAN = C.ACC2
C.ACCENT_AMBER = C.ACC
C.ACCENT_GREEN = C.GREEN
C.ACCENT_RED = C.RED
C.TEXT_PRIMARY = C.TEXT
C.TEXT_SECONDARY = C.TEXT_MED
C.TEXT_MUTED = C.TEXT_DIM
C.BORDER_HAIRLINE = C.BORDER
C.BORDER_ACTIVE = C.BORDER_B
C.TRACK_BG = C.BAR_BG



# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "PANEL_HEAD", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        new_val = f"#{int(r * 255 + 0.5):02x}{int(g * 255 + 0.5):02x}{int(b * 255 + 0.5):02x}"
        setattr(C, key, new_val)
        if key == "BG":
            OptimusTheme.BG_BASE = new_val
            OptimusTheme.BG = new_val
            C.BG_BASE = new_val
        elif key == "PANEL":
            OptimusTheme.BG_PANEL = new_val
            OptimusTheme.PANEL = new_val
            C.BG_PANEL = new_val
        elif key == "PANEL2":
            OptimusTheme.BG_ELEVATED = new_val
            OptimusTheme.PANEL2 = new_val
            C.BG_ELEVATED = new_val
        elif key == "PRI":
            OptimusTheme.ACCENT_PRI = new_val
            OptimusTheme.PRI = new_val
            C.ACCENT_PRI = new_val
        elif key == "BORDER":
            OptimusTheme.BORDER_HAIRLINE = new_val
            OptimusTheme.BORDER = new_val
            C.BORDER_HAIRLINE = new_val
        elif key == "BORDER_B":
            OptimusTheme.BORDER_ACTIVE = new_val
            OptimusTheme.BORDER_B = new_val
            C.BORDER_ACTIVE = new_val
    return True


_ORIGINAL_PALETTE_CONSTANTS: dict[str, str] = {
    "BG": "#070b14",
    "PANEL": "#0c1322",
    "PANEL2": "#111a2e",
    "PANEL_HEAD": "#0f172a",
    "BORDER": "#1e293b",
    "BORDER_B": "#334155",
    "BORDER_A": "#1e293b",
    "PRI": "#0084ff",
    "PRI_DIM": "#005bb5",
    "PRI_GHO": "#0b1c33",
    "ACC": "#f59e0b",
    "ACC2": "#38bdf8",
    "GREEN": "#10b981",
    "GREEN_D": "#059669",
    "RED": "#ef4444",
    "MUTED_C": "#f43f5e",
    "TEXT": "#f1f5f9",
    "TEXT_DIM": "#64748b",
    "TEXT_MED": "#94a3b8",
    "WHITE": "#f8fafc",
    "DARK": "#05080f",
    "BAR_BG": "#0f1728",
}


def restore_default_palette() -> None:
    """Restore all palette tokens to their original static constants."""
    for key, val in _ORIGINAL_PALETTE_CONSTANTS.items():
        setattr(C, key, val)
    OptimusTheme.BG_BASE = C.BG
    OptimusTheme.BG = C.BG
    OptimusTheme.BG_PANEL = C.PANEL
    OptimusTheme.PANEL = C.PANEL
    OptimusTheme.BG_ELEVATED = C.PANEL2
    OptimusTheme.PANEL2 = C.PANEL2
    OptimusTheme.BG_HEADER = C.PANEL_HEAD
    OptimusTheme.PANEL_HEAD = C.PANEL_HEAD
    OptimusTheme.ACCENT_PRI = C.PRI
    OptimusTheme.PRI = C.PRI
    OptimusTheme.ACCENT_CYAN = C.ACC2
    OptimusTheme.ACC2 = C.ACC2
    OptimusTheme.BORDER_HAIRLINE = C.BORDER
    OptimusTheme.BORDER = C.BORDER
    OptimusTheme.BORDER_ACTIVE = C.BORDER_B
    OptimusTheme.BORDER_B = C.BORDER_B
    OptimusTheme.TEXT_PRIMARY = C.TEXT
    OptimusTheme.TEXT = C.TEXT
    OptimusTheme.TEXT_SECONDARY = C.TEXT_MED
    OptimusTheme.TEXT_MED = C.TEXT_MED
    OptimusTheme.TEXT_MUTED = C.TEXT_DIM
    OptimusTheme.TEXT_DIM = C.TEXT_DIM
    OptimusTheme.TRACK_BG = C.BAR_BG
    OptimusTheme.BAR_BG = C.BAR_BG


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

