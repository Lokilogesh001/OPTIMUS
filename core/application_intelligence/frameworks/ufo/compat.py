"""core/application_intelligence/frameworks/ufo/compat.py — UFO² Upstream Compatibility & Patching.

Provides runtime shims for Microsoft UFO²:
1. FastMCP 4+ compatibility: monkey-patches FastMCP.tool to safely pop deprecated exclude_args (reconciles HIGH-3).
2. Configuration path binding: binds ConfigLoader to upstream UFO config directory regardless of current working directory.
3. Path injection: ensures ufo_upstream is present on sys.path.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("optimus.application_intelligence.frameworks.ufo.compat")

_COMPAT_INITIALIZED = False


def patch_fastmcp() -> None:
    """Monkey-patch FastMCP to eliminate TypeError from exclude_args parameter in upstream UFO (reconciles HIGH-3)."""
    try:
        import fastmcp

        if hasattr(fastmcp.FastMCP, "_optimus_m19_patched"):
            return

        orig_tool = fastmcp.FastMCP.tool

        def _compat_tool(self: Any, *args: Any, **kwargs: Any) -> Any:
            kwargs.pop("exclude_args", None)
            return orig_tool(self, *args, **kwargs)

        fastmcp.FastMCP.tool = _compat_tool
        fastmcp.FastMCP._optimus_m19_patched = True
        logger.info("FastMCP successfully patched for UFO 2.0 tool decorator compatibility (exclude_args removed).")
    except Exception as e:
        logger.warning("Could not patch FastMCP: %s", e)


def ensure_ufo_environment(ufo_root: str | None = None) -> Path:
    """Initialize sys.path and configuration loader for Microsoft UFO² runtime."""
    global _COMPAT_INITIALIZED

    # 1. Apply FastMCP decorator patch first
    patch_fastmcp()

    # 2. Resolve UFO root path
    if not ufo_root:
        # Check standard locations
        candidates = [
            Path("D:/project/ufo_upstream"),
            Path("D:/project/UFO"),
            Path(__file__).parent.parent.parent.parent.parent / "ufo_upstream",
        ]
        for c in candidates:
            if c.exists() and (c / "ufo").exists():
                ufo_root = str(c.resolve())
                break
        if not ufo_root:
            ufo_root = "D:/project/ufo_upstream"

    ufo_path = Path(ufo_root).resolve()
    if str(ufo_path) not in sys.path:
        sys.path.insert(0, str(ufo_path))
        logger.info("Added UFO root to sys.path: %s", ufo_path)

    # 3. Initialize UFO ConfigLoader with explicit config directory
    config_dir = ufo_path / "config"
    if config_dir.exists():
        try:
            from config.config_loader import ConfigLoader, get_ufo_config
            ConfigLoader.reset()
            ConfigLoader.get_instance(str(config_dir))
            # Pre-load global config so submodules don't fail relative cwd discovery
            get_ufo_config(str(config_dir))
            logger.info("UFO ConfigLoader initialized with base_path: %s", config_dir)
        except Exception as e:
            logger.warning("Failed to initialize UFO ConfigLoader with %s: %s", config_dir, e)

    _COMPAT_INITIALIZED = True
    return ufo_path
