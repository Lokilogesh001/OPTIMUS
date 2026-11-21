"""core/vision/types.py — Typed Contracts & Models for M16 Visual Awareness."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


@dataclass
class WindowInfo:
    """Detailed metadata for a native desktop application window."""
    hwnd: int
    title: str
    app_name: str
    pid: int
    rect: tuple[int, int, int, int]  # (left, top, right, bottom)
    is_visible: bool
    is_minimized: bool
    is_maximized: bool
    is_active: bool
    monitor_index: int = 0
    class_name: str = ""
    last_seen_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrowserTabInfo:
    """Detailed metadata for an accessible browser tab."""
    tab_id: str
    browser: str                         # "chrome" | "msedge" | "firefox" | "other"
    window_hwnd: int
    title: str
    is_active: bool
    url: str | None = None               # Guaranteed only for active tab Omnibox
    domain: str | None = None            # Guaranteed only for active tab
    domain_hint: str = ""                # Extracted via title regex heuristics for background tabs
    is_pinned: bool = False
    is_discarded: bool = False
    group_id: str | None = None
    group_name: str | None = None
    is_group_collapsed: bool = False
    semantic_category: str = "general"   # "development" | "research" | "documentation" | "communication" | "general"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DesktopState:
    """Snapshot representation of the current multi-monitor visible desktop."""
    timestamp: float
    monitors: list[dict[str, Any]]
    active_window: WindowInfo | None
    windows: list[WindowInfo]
    browser_tabs: list[BrowserTabInfo]
    dhash: str
    has_changed: bool
    ui_elements_count: int = 0
    is_paused: bool = False
    privacy_redacted: bool = False
    thumbnail_jpeg: bytes | None = None  # Downscaled ephemeral JPEG preview (max 480x270)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("thumbnail_jpeg"):
            d["thumbnail_jpeg_len"] = len(d["thumbnail_jpeg"])
            del d["thumbnail_jpeg"]
        return d


class SupervisorState(str, Enum):
    IDLE = "IDLE"
    OBSERVING = "OBSERVING"
    SUPERVISING = "SUPERVISING"
    RECOVERING = "RECOVERING"
    PAUSED = "PAUSED"


@dataclass
class SupervisionStepResult:
    """Diagnostic outcome of supervising a discrete action step."""
    step_index: int
    action_type: str
    target_element: str
    expected_state: str
    actual_state: str
    is_successful: bool
    detected_anomaly: str | None = None      # "dead_click" | "disabled_button" | "unexpected_popup" | "loading_hang" | "crash" | "wrong_window"
    recommended_action: str | None = None    # "retry_with_offset" | "dismiss_blocking_dialog" | "refocus_window" | "wait_for_loading" | "escalate"
    retry_count: int = 0
    is_idempotent: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrivacyConfig:
    """User privacy exclusions and credential protection boundaries."""
    excluded_apps: set[str] = field(default_factory=lambda: {
        "1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe",
        "credentialui.exe", "consent.exe", "dashlane.exe",
    })
    excluded_window_patterns: list[str] = field(default_factory=lambda: [
        r"password", r"credential", r"windows security", r"user account control",
        r"secret", r"pin entry", r"two-factor", r"2fa",
    ])
    excluded_domains: set[str] = field(default_factory=lambda: {
        "vault.bitwarden.com", "my.1password.com", "accounts.google.com/signin",
    })
    mask_passwords: bool = True
    redact_sensitive_tokens: bool = True

    def is_window_excluded(self, app_name: str, title: str) -> bool:
        low_app = (app_name or "").lower().strip()
        if any(exc in low_app for exc in self.excluded_apps):
            return True
        low_title = (title or "").lower()
        import re
        return any(re.search(pat, low_title, re.IGNORECASE) for pat in self.excluded_window_patterns)
