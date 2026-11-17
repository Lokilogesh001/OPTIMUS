"""gui/optimus/section_settings.py — Dedicated Settings Section.

Provides configuration for:
- Workspace roots & sandbox boundaries.
- Privacy mode (local-only vs cloud hybrid).
- UI theme accent color customization.
- Provider API key status indicators.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import mono_font, px_font
from gui.theme import C, apply_ui_accent
from gui.widgets import HoloPanel


class SectionSettings(QWidget):
    """OPTIMUS System Configuration Panel."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("SYSTEM SETTINGS  ·  ENVIRONMENT CONFIGURATION")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        layout.addLayout(hdr)

        # 1. Workspace Boundaries
        p_work = HoloPanel("WORKSPACE BOUNDARIES & SANDBOX ROOTS", "SECURITY")
        w_lay = QVBoxLayout()
        w_lbl = QLabel(f"ALLOWED ROOTS: {', '.join(self.bridge.orchestrator.allowed_roots)}")
        w_lbl.setFont(mono_font(9))
        w_lbl.setStyleSheet(f"color: {C.TEXT};")
        w_lay.addWidget(w_lbl)
        p_work.body.addLayout(w_lay)
        layout.addWidget(p_work)

        # 2. Privacy & Isolation
        p_priv = HoloPanel("DATA PRIVACY & LOCAL ENCLAVE", "PRIVACY")
        pv_lay = QVBoxLayout()
        self.chk_local_only = QCheckBox("STRICT LOCAL-ONLY MODE (Never transmit data to cloud endpoints)")
        self.chk_local_only.setFont(px_font(10, QFont.Weight.Bold))
        self.chk_local_only.setStyleSheet(f"color: {C.GREEN};")
        self.chk_local_only.setChecked(True)
        self.chk_privacy = self.chk_local_only
        self.chk_local_only.toggled.connect(self._on_privacy_toggled)
        pv_lay.addWidget(self.chk_local_only)

        note_lbl = QLabel("When enabled, all subtasks and intelligence queries are restricted to local Ollama and on-device models.")
        note_lbl.setFont(px_font(9))
        note_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        pv_lay.addWidget(note_lbl)
        p_priv.body.addLayout(pv_lay)
        layout.addWidget(p_priv)

        # 3. Theme & UI Styling
        p_theme = HoloPanel("VISUAL ACCENT PALETTE", "HUD_THEME")
        th_lay = QHBoxLayout()
        th_lbl = QLabel("ACCENT COLOR:")
        th_lbl.setFont(px_font(10, QFont.Weight.Bold))
        th_lbl.setStyleSheet(f"color: {C.WHITE};")
        th_lay.addWidget(th_lbl)

        for col_name, hex_val in [
            ("ELECTRIC CYAN", "#00d4ff"),
            ("EMERALD GREEN", "#00ff88"),
            ("AMBER GOLD", "#ffcc00"),
            ("NEON ORANGE", "#ff6b00"),
            ("CRIMSON", "#ff3355"),
        ]:
            btn_col = QPushButton(col_name)
            btn_col.setFont(px_font(8, QFont.Weight.Bold))
            btn_col.setStyleSheet(f"""
                QPushButton {{
                    color: {hex_val}; background: transparent;
                    border: 1px solid {hex_val}; border-radius: 3px; padding: 4px 8px;
                }}
                QPushButton:hover {{ background: rgba(255, 255, 255, 0.08); }}
            """)
            btn_col.clicked.connect(lambda checked=False, h=hex_val: apply_ui_accent(h))
            th_lay.addWidget(btn_col)

        th_lay.addStretch()
        p_theme.body.addLayout(th_lay)
        layout.addWidget(p_theme)

        # 4. API Keys & Providers Status
        p_keys = HoloPanel("PROVIDER CREDENTIALS STATUS", "API_KEYS")
        k_lay = QVBoxLayout()
        cfg_file = Path.cwd() / "config" / "api_keys.json"
        has_gemini = False
        has_openai = False
        has_anthropic = False
        if cfg_file.exists():
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                has_gemini = bool(data.get("gemini_api_key"))
                has_openai = bool(data.get("openai_api_key"))
                has_anthropic = bool(data.get("anthropic_api_key"))
            except Exception:
                pass

        def _make_key_row(name: str, has_k: bool) -> QHBoxLayout:
            r = QHBoxLayout()
            n = QLabel(name)
            n.setFont(mono_font(9))
            n.setStyleSheet(f"color: {C.WHITE};")
            n.setFixedWidth(120)
            r.addWidget(n)

            st = "CONFIGURED" if has_k else "NOT DETECTED"
            col = C.GREEN if has_k else C.TEXT_DIM
            s = QLabel(st)
            s.setFont(px_font(9, QFont.Weight.Bold))
            s.setStyleSheet(f"color: {col};")
            r.addWidget(s)
            r.addStretch()
            return r

        k_lay.addLayout(_make_key_row("GEMINI LIVE", has_gemini))
        k_lay.addLayout(_make_key_row("OPENAI API", has_openai))
        k_lay.addLayout(_make_key_row("ANTHROPIC API", has_anthropic))
        k_lay.addLayout(_make_key_row("OLLAMA LOCAL", True))

        p_keys.body.addLayout(k_lay)
        layout.addWidget(p_keys)

        layout.addStretch()

    def _on_privacy_toggled(self, checked: bool) -> None:
        gateway = getattr(self.bridge.orchestrator, "intelligence_gateway", None)
        if gateway:
            from core.intelligence.types import RoutingPolicy
            if checked:
                gateway.set_policy(RoutingPolicy.PRIVACY_STRICT)
            else:
                gateway.set_policy(RoutingPolicy.LOCAL_FIRST)

    def _on_accent_selected(self, hex_val: str) -> None:
        apply_ui_accent(hex_val)
