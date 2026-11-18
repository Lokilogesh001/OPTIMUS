"""gui/optimus/section_settings.py — Dedicated Settings Section.

Provides configuration for:
- Workspace roots & sandbox boundaries.
- Privacy mode (local-only vs cloud hybrid).
- UI theme accent color customization.
- Provider API key status indicators.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.security.permissions import PermissionProfile, get_permission_manager
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

        # 2. Central Permission Architecture
        self.perm_mgr = get_permission_manager()

        p_perm = HoloPanel("AUTONOMOUS PERMISSION PROFILE (CBAC ENFORCEMENT)", "PERMISSIONS")
        pm_lay = QVBoxLayout()
        self.lbl_active_perm = QLabel(f"ACTIVE PROFILE: {self.perm_mgr.get_profile().value}")
        self.lbl_active_perm.setFont(px_font(10, QFont.Weight.Bold))
        self.lbl_active_perm.setStyleSheet(f"color: {C.PRI};")
        pm_lay.addWidget(self.lbl_active_perm)

        self.lbl_perm_desc = QLabel("")
        self.lbl_perm_desc.setFont(px_font(8))
        self.lbl_perm_desc.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.lbl_perm_desc.setWordWrap(True)
        pm_lay.addWidget(self.lbl_perm_desc)

        btn_row = QHBoxLayout()
        self.perm_btns: dict[PermissionProfile, QPushButton] = {}
        for prof in (PermissionProfile.LOW, PermissionProfile.MODERATE, PermissionProfile.ADVANCED):
            b = QPushButton(prof.value)
            b.setFont(px_font(8, QFont.Weight.Bold))
            b.setFixedHeight(28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, p=prof: self._set_perm_profile(p))
            btn_row.addWidget(b)
            self.perm_btns[prof] = b
        btn_row.addStretch()
        pm_lay.addLayout(btn_row)

        p_perm.body.addLayout(pm_lay)
        layout.addWidget(p_perm)
        self._update_perm_ui()

        # 3. Privacy & Isolation
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

        # 5. API Keys & OS Secure Keyring
        from core.security.credentials import get_credential, mask_credential

        p_keys = HoloPanel("PROVIDER CREDENTIALS & SECURE OS KEYRING", "API_KEYS")
        k_lay = QVBoxLayout()

        def _make_key_row(name: str, key_val: str | None) -> QHBoxLayout:
            r = QHBoxLayout()
            n = QLabel(name)
            n.setFont(mono_font(9))
            n.setStyleSheet(f"color: {C.WHITE};")
            n.setFixedWidth(140)
            r.addWidget(n)

            has_k = bool(key_val)
            st = f"CONFIGURED ({mask_credential(key_val)})" if has_k else "NOT DETECTED"
            col = C.GREEN if has_k else C.TEXT_DIM
            s = QLabel(st)
            s.setFont(px_font(9, QFont.Weight.Bold))
            s.setStyleSheet(f"color: {col};")
            r.addWidget(s)
            r.addStretch()
            return r

        k_lay.addLayout(_make_key_row("GEMINI LIVE", get_credential("gemini")))
        k_lay.addLayout(_make_key_row("OPENAI API", get_credential("openai")))
        k_lay.addLayout(_make_key_row("ANTHROPIC API", get_credential("anthropic")))
        k_lay.addLayout(_make_key_row("GROQ API", get_credential("groq")))
        k_lay.addLayout(_make_key_row("OPENROUTER API", get_credential("openrouter")))
        k_lay.addLayout(_make_key_row("OLLAMA LOCAL", "Local / Active"))

        # Re-run onboarding button
        ob_row = QHBoxLayout()
        btn_re_onboard = QPushButton("◈  RE-RUN ONBOARDING WIZARD")
        btn_re_onboard.setFont(px_font(9, QFont.Weight.Bold))
        btn_re_onboard.setFixedHeight(30)
        btn_re_onboard.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_re_onboard.setStyleSheet(f"""
            QPushButton {{
                color: {C.PRI}; background: transparent;
                border: 1px solid {C.PRI_DIM}; border-radius: 4px; padding: 4px 14px;
            }}
            QPushButton:hover {{ background: rgba(0, 212, 255, 0.1); border-color: {C.PRI}; }}
        """)
        btn_re_onboard.clicked.connect(self._open_onboarding_wizard)
        ob_row.addWidget(btn_re_onboard)
        ob_row.addStretch()
        k_lay.addLayout(ob_row)

        p_keys.body.addLayout(k_lay)
        layout.addWidget(p_keys)

        layout.addStretch()

    def _update_perm_ui(self) -> None:
        prof = self.perm_mgr.get_profile()
        self.lbl_active_perm.setText(f"ACTIVE PROFILE: {prof.value}")
        desc_map = {
            PermissionProfile.LOW: "LOW MODE: Read-only operations by default. Asks confirmation before file writes or network calls.",
            PermissionProfile.MODERATE: "MODERATE MODE: Autonomous workflow execution & workspace file writes. Approval required for execution.",
            PermissionProfile.ADVANCED: "ADVANCED MODE: Full autonomous execution (terminal, tools, dev agents). Mandatory approval for destructive/financial actions.",
        }
        self.lbl_perm_desc.setText(desc_map.get(prof, ""))
        for p, btn in self.perm_btns.items():
            is_active = p == prof
            col = C.PRI if is_active else C.TEXT_DIM
            bg = "rgba(0, 212, 255, 0.15)" if is_active else "transparent"
            btn.setStyleSheet(f"""
                QPushButton {{
                    color: {col}; background: {bg};
                    border: 1px solid {col}; border-radius: 3px; padding: 2px 10px;
                }}
            """)

    def _set_perm_profile(self, profile: PermissionProfile) -> None:
        self.perm_mgr.set_profile(profile)
        self._update_perm_ui()

    def _open_onboarding_wizard(self) -> None:
        win = self.window()
        if hasattr(win, "_show_setup"):
            win._show_setup()

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
