"""core/application_intelligence/dpi.py — High-DPI coordinate scaling and normalization.

Normalizes coordinate spaces across multi-monitor setups and varied Windows display scaling:
- 100% scaling = 96 DPI (Standard 1.0x baseline)
- 125% scaling = 120 DPI (1.25x)
- 150% scaling = 144 DPI (1.50x)
- 175% scaling = 168 DPI (1.75x)
- 200% scaling = 192 DPI (2.00x)

Provides robust conversion between logical (design/accessibility) coordinates and physical screen pixels.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from typing import Tuple

logger = logging.getLogger("optimus.application_intelligence.dpi")

STANDARD_DPI = 96


def get_window_dpi(hwnd: int) -> int:
    """Returns the DPI for a given window handle on Windows, or 96 on fallback."""
    if sys.platform != "win32" or not hwnd:
        return STANDARD_DPI

    try:
        user32 = ctypes.windll.user32
        if hasattr(user32, "GetDpiForWindow"):
            dpi = user32.GetDpiForWindow(hwnd)
            if dpi > 0:
                return int(dpi)
    except Exception as e:
        logger.debug("Failed calling GetDpiForWindow(%s): %s", hwnd, e)

    return STANDARD_DPI


def get_system_dpi() -> int:
    """Returns the primary system DPI."""
    if sys.platform != "win32":
        return STANDARD_DPI

    try:
        user32 = ctypes.windll.user32
        if hasattr(user32, "GetDpiForSystem"):
            dpi = user32.GetDpiForSystem()
            if dpi > 0:
                return int(dpi)
        hdc = user32.GetDC(0)
        if hdc:
            gdi32 = ctypes.windll.gdi32
            LOGPIXELSX = 88
            dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
            user32.ReleaseDC(0, hdc)
            if dpi > 0:
                return int(dpi)
    except Exception as e:
        logger.debug("Failed getting system DPI: %s", e)

    return STANDARD_DPI


def logical_to_physical(x: float, y: float, dpi: int = STANDARD_DPI) -> Tuple[int, int]:
    """Converts logical coordinates (at 96 DPI baseline) to physical screen coordinates."""
    scale = float(dpi) / float(STANDARD_DPI)
    return round(x * scale), round(y * scale)


def physical_to_logical(x: float, y: float, dpi: int = STANDARD_DPI) -> Tuple[int, int]:
    """Converts physical screen coordinates to logical (96 DPI baseline) coordinates."""
    scale = float(dpi) / float(STANDARD_DPI)
    if scale == 0:
        return round(x), round(y)
    return round(x / scale), round(y / scale)


def scale_rect(
    rect: Tuple[int, int, int, int], from_dpi: int, to_dpi: int
) -> Tuple[int, int, int, int]:
    """Scales a (left, top, width, height) rectangle from one DPI space to another."""
    if from_dpi == to_dpi or from_dpi == 0:
        return rect

    scale = float(to_dpi) / float(from_dpi)
    left, top, width, height = rect
    return (
        round(left * scale),
        round(top * scale),
        round(width * scale),
        round(height * scale),
    )
