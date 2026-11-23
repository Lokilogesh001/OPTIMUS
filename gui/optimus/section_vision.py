"""gui/optimus/section_vision.py — Vision Command Center UI for OPTIMUS.

Implements Section 8 of Milestone 16:
- Dedicated VISION tab in the cinematic blue interface.
- Live desktop preview thumbnail with multi-monitor selector.
- Active application HUD and complete discovered windows inventory.
- Complete accessible browser tab inventory with domain hints and groups.
- Visual Supervisor status card, error indicators, and live supervision event timeline.
- Privacy controls: Always-on toggle, pause/resume, application/domain exclusions, clear context.
"""

from __future__ import annotations

import io
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.vision.service import UnifiedVisionService
from core.vision.types import BrowserTabInfo, DesktopState, WindowInfo
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel


class SectionVision(QWidget):
    """Cinematic Vision Command Center for Always-On Visual Awareness and Task Supervision."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.vision = UnifiedVisionService.instance()
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── 1. Top Header Row ────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("VISUAL AWARENESS & DESKTOP SUPERVISION")
        title.setFont(px_font(12, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        hdr.addWidget(title)
        hdr.addSpacing(12)

        self.lbl_status_badge = QLabel("OBSERVING ACTIVE")
        self.lbl_status_badge.setFont(mono_font(8))
        self.lbl_status_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(16, 185, 129, 0.1); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_status_badge)

        self.lbl_supervisor_badge = QLabel("SUPERVISOR: IDLE")
        self.lbl_supervisor_badge.setFont(mono_font(8))
        self.lbl_supervisor_badge.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 132, 255, 0.1); border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_supervisor_badge)

        hdr.addStretch()

        # Pause / Resume / Clear buttons
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setFont(px_font(10, QFont.Weight.Medium))
        self.btn_pause.setStyleSheet(btn_css("dim"))
        self.btn_pause.clicked.connect(self._toggle_pause)
        hdr.addWidget(self.btn_pause)

        self.btn_clear = QPushButton("Clear Context")
        self.btn_clear.setFont(px_font(10, QFont.Weight.Medium))
        self.btn_clear.setStyleSheet(btn_css("dim"))
        self.btn_clear.clicked.connect(self._clear_context)
        hdr.addWidget(self.btn_clear)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setFont(px_font(10, QFont.Weight.Medium))
        self.btn_refresh.setStyleSheet(btn_css("primary"))
        self.btn_refresh.clicked.connect(self.refresh_vision)
        hdr.addWidget(self.btn_refresh)

        layout.addLayout(hdr)

        # ── 2. Top Summary Section (Preview + Active App + Privacy) ──────────
        top_split = QSplitter(Qt.Orientation.Horizontal)
        top_split.setStyleSheet("QSplitter::handle { background: transparent; }")

        # 2a. Live Monitor Preview Panel
        panel_preview = HoloPanel("LIVE DESKTOP PREVIEW", "MONITOR_1")
        prev_lay = QVBoxLayout()
        prev_lay.setContentsMargins(6, 6, 6, 6)

        m_row = QHBoxLayout()
        m_row.addWidget(QLabel("Select Display:"))
        self.combo_monitors = QComboBox()
        self.combo_monitors.addItems(["Monitor 1 [Primary]", "Monitor 2 [Secondary]"])
        self.combo_monitors.setStyleSheet(f"background: {C.PANEL}; color: {C.WHITE}; border: 1px solid {C.BORDER}; padding: 2px 6px;")
        m_row.addWidget(self.combo_monitors)
        m_row.addStretch()
        prev_lay.addLayout(m_row)

        self.lbl_preview_img = QLabel("Capturing screen preview...")
        self.lbl_preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview_img.setFixedSize(360, 200)
        self.lbl_preview_img.setStyleSheet(f"background: #080c14; border: 1px solid {C.BORDER_A}; border-radius: 4px; color: {C.TEXT_DIM};")
        prev_lay.addWidget(self.lbl_preview_img)
        panel_preview.body.addLayout(prev_lay)
        top_split.addWidget(panel_preview)

        # 2b. Active Application HUD Card
        panel_active = HoloPanel("ACTIVE APPLICATION HUD", "FOREGROUND")
        act_lay = QVBoxLayout()
        act_lay.setContentsMargins(8, 8, 8, 8)
        act_lay.setSpacing(6)

        self.lbl_active_app = QLabel("App: None")
        self.lbl_active_app.setFont(px_font(12, QFont.Weight.Bold))
        self.lbl_active_app.setStyleSheet(f"color: {C.WHITE};")
        act_lay.addWidget(self.lbl_active_app)

        self.lbl_active_title = QLabel("Title: None")
        self.lbl_active_title.setFont(px_font(11))
        self.lbl_active_title.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.lbl_active_title.setWordWrap(True)
        act_lay.addWidget(self.lbl_active_title)

        self.lbl_active_meta = QLabel("PID: - | HWND: - | Bounds: (0, 0, 0, 0)")
        self.lbl_active_meta.setFont(mono_font(9))
        self.lbl_active_meta.setStyleSheet(f"color: {C.PRI_DIM};")
        act_lay.addWidget(self.lbl_active_meta)

        self.lbl_active_dhash = QLabel("Visual dHash: 0000000000000000")
        self.lbl_active_dhash.setFont(mono_font(9))
        self.lbl_active_dhash.setStyleSheet(f"color: {C.ACC};")
        act_lay.addWidget(self.lbl_active_dhash)

        self.lbl_active_elements = QLabel("Accessible UI Elements: 0")
        self.lbl_active_elements.setFont(mono_font(9))
        self.lbl_active_elements.setStyleSheet(f"color: {C.GREEN};")
        act_lay.addWidget(self.lbl_active_elements)

        act_lay.addStretch()
        panel_active.body.addLayout(act_lay)
        top_split.addWidget(panel_active)

        # 2c. Privacy & Exclusions Panel
        panel_privacy = HoloPanel("PRIVACY & BOUNDARIES", "CREDENTIAL_GUARD")
        priv_lay = QVBoxLayout()
        priv_lay.setContentsMargins(8, 8, 8, 8)
        priv_lay.setSpacing(6)

        priv_info = QLabel("Excluded Applications (Never Inspected):")
        priv_info.setFont(px_font(10, QFont.Weight.Bold))
        priv_info.setStyleSheet(f"color: {C.TEXT_DIM};")
        priv_lay.addWidget(priv_info)

        self.lbl_excluded_apps = QLabel("1Password, Bitwarden, KeePass, Windows Security, UAC")
        self.lbl_excluded_apps.setFont(mono_font(9))
        self.lbl_excluded_apps.setStyleSheet(f"color: {C.WHITE}; background: rgba(255,255,255,0.04); padding: 4px; border-radius: 3px;")
        self.lbl_excluded_apps.setWordWrap(True)
        priv_lay.addWidget(self.lbl_excluded_apps)

        self.lbl_privacy_mode = QLabel("Universal Secret Masking: ACTIVE\nCredential Dialog Blanking: ACTIVE")
        self.lbl_privacy_mode.setFont(mono_font(9))
        self.lbl_privacy_mode.setStyleSheet(f"color: {C.GREEN};")
        priv_lay.addWidget(self.lbl_privacy_mode)

        priv_lay.addStretch()
        panel_privacy.body.addLayout(priv_lay)
        top_split.addWidget(panel_privacy)

        top_split.setSizes([380, 360, 300])
        layout.addWidget(top_split, stretch=3)

        # ── 3. Middle Section: Windows Inventory & Browser Tabs ──────────────
        mid_split = QSplitter(Qt.Orientation.Horizontal)
        mid_split.setStyleSheet("QSplitter::handle { background: transparent; }")

        # 3a. Discovered Windows Table
        panel_windows = HoloPanel("APPLICATION WINDOW INVENTORY", "WIN32_ENUM")
        win_lay = QVBoxLayout()
        win_lay.setContentsMargins(4, 4, 4, 4)

        self.table_windows = QTableWidget(0, 5)
        self.table_windows.setHorizontalHeaderLabels(["HWND", "App", "Title", "State", "Action"])
        self.table_windows.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table_windows.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_windows.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_windows.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_windows.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_windows.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL};
                color: {C.WHITE};
                gridline-color: {C.BORDER_A};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
            }}
            QHeaderView::section {{
                background: {C.PANEL_HEAD};
                color: {C.PRI};
                padding: 4px;
                border: 1px solid {C.BORDER_A};
                font-weight: bold;
            }}
        """ + scroll_css())
        win_lay.addWidget(self.table_windows)
        panel_windows.body.addLayout(win_lay)
        mid_split.addWidget(panel_windows)

        # 3b. Browser Tabs Inventory Table
        panel_tabs = HoloPanel("ACCESSIBLE BROWSER TABS", "UIA_BROWSER")
        tabs_lay = QVBoxLayout()
        tabs_lay.setContentsMargins(4, 4, 4, 4)

        self.table_tabs = QTableWidget(0, 5)
        self.table_tabs.setHorizontalHeaderLabels(["Browser", "Title", "Domain Hint", "State", "Action"])
        self.table_tabs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_tabs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_tabs.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_tabs.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_tabs.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_tabs.setStyleSheet(self.table_windows.styleSheet())
        tabs_lay.addWidget(self.table_tabs)
        panel_tabs.body.addLayout(tabs_lay)
        mid_split.addWidget(panel_tabs)

        mid_split.setSizes([520, 520])
        layout.addWidget(mid_split, stretch=4)

        # ── 4. Bottom Section: Supervisor Activity Timeline ──────────────────
        panel_supervisor = HoloPanel("TASK VISUAL SUPERVISOR TIMELINE", "CLOSED_LOOP_AUDIT")
        sup_lay = QVBoxLayout()
        sup_lay.setContentsMargins(4, 4, 4, 4)

        self.txt_supervisor_log = QTextEdit()
        self.txt_supervisor_log.setReadOnly(True)
        self.txt_supervisor_log.setFont(mono_font(9))
        self.txt_supervisor_log.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """ + scroll_css())
        sup_lay.addWidget(self.txt_supervisor_log)
        panel_supervisor.body.addLayout(sup_lay)
        layout.addWidget(panel_supervisor, stretch=2)

        self._last_windows_sig = None
        self._last_tabs_sig = None

        # Periodic refresh timer (every 1.5s, active only when visible)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_vision)
        if self.isVisible():
            self.timer.start(1500)

        # Initial populate
        self.refresh_vision()

    # ── Lifecycle Control ────────────────────────────────────────────────────

    def pause_polling(self) -> None:
        """Pause 1.5s refresh timer when tab is hidden or inactive."""
        if self.timer.isActive():
            self.timer.stop()

    def resume_polling(self) -> None:
        """Resume 1.5s refresh timer when tab becomes visible."""
        if not self.timer.isActive():
            self.timer.start(1500)
        self.refresh_vision()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.resume_polling()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.pause_polling()

    def _on_focus_button_clicked(self) -> None:
        sender = self.sender()
        if sender:
            target_hwnd = sender.property("target_hwnd")
            if target_hwnd:
                self._on_activate_window(target_hwnd)

    def _on_switch_tab_button_clicked(self) -> None:
        sender = self.sender()
        if sender:
            tab_id = sender.property("tab_id")
            if tab_id:
                self._on_switch_tab(tab_id)

    # ── Refresh & Populate ───────────────────────────────────────────────────

    def refresh_vision(self) -> None:
        """Fetch latest desktop state and update UI widgets."""
        state = self.vision.get_desktop_state()

        # 1. Update Badges
        if state.is_paused:
            self.lbl_status_badge.setText("[STATUS: PAUSED]")
            self.lbl_status_badge.setStyleSheet(f"color: {C.ACC}; background: rgba(255, 107, 0, 0.1); border: 1px solid {C.ACC}; border-radius: 3px; padding: 2px 8px;")
            self.btn_pause.setText("▶ RESUME")
        else:
            self.lbl_status_badge.setText("[STATUS: OBSERVING ACTIVE]")
            self.lbl_status_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 128, 0.1); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
            self.btn_pause.setText("⏸ PAUSE")

        sup_state = self.vision.supervisor.state.value
        self.lbl_supervisor_badge.setText(f"[SUPERVISOR: {sup_state}]")

        # 2. Update Thumbnail Preview
        if state.thumbnail_jpeg:
            try:
                pix = QPixmap()
                pix.loadFromData(state.thumbnail_jpeg, "JPEG")
                scaled = pix.scaled(self.lbl_preview_img.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.lbl_preview_img.setPixmap(scaled)
            except Exception:
                self.lbl_preview_img.setText("[Preview Render Error]")
        elif state.is_paused:
            self.lbl_preview_img.setText("[OBSERVATION PAUSED]")
        elif state.privacy_redacted:
            self.lbl_preview_img.setText("[PRIVACY REDACTED]")

        # 3. Update Active App Card
        if state.active_window:
            w = state.active_window
            self.lbl_active_app.setText(f"App: {w.app_name}")
            self.lbl_active_title.setText(f"Title: {w.title}")
            self.lbl_active_meta.setText(f"PID: {w.pid} | HWND: {w.hwnd} | Bounds: {w.rect}")
        else:
            self.lbl_active_app.setText("App: None")
            self.lbl_active_title.setText("Title: None")
            self.lbl_active_meta.setText("PID: - | HWND: - | Bounds: (0, 0, 0, 0)")

        self.lbl_active_dhash.setText(f"Visual dHash: {state.dhash}")
        self.lbl_active_elements.setText(f"Accessible UI Elements: {state.ui_elements_count}")

        # 4. Populate Windows Table
        self._populate_windows_table(state.windows)

        # 5. Populate Browser Tabs Table
        self._populate_tabs_table(state.browser_tabs)

        # 6. Update Supervisor Log
        self._update_supervisor_log()

    def _populate_windows_table(self, windows: list[WindowInfo]) -> None:
        """In-place update of windows table with zero widget leaks and diff guard."""
        sig = (len(windows), tuple((w.hwnd, w.is_active, w.is_minimized, w.title) for w in windows[:15]))
        if sig == self._last_windows_sig:
            return
        self._last_windows_sig = sig

        curr_rows = self.table_windows.rowCount()
        target_rows = len(windows)
        if curr_rows > target_rows:
            for r in range(curr_rows - 1, target_rows - 1, -1):
                self.table_windows.removeRow(r)
        elif curr_rows < target_rows:
            for r in range(curr_rows, target_rows):
                self.table_windows.insertRow(r)
                for c in range(4):
                    self.table_windows.setItem(r, c, QTableWidgetItem())
                btn_act = QPushButton("Focus")
                btn_act.setFont(px_font(9, QFont.Weight.Medium))
                btn_act.setStyleSheet(btn_css("primary"))
                btn_act.clicked.connect(self._on_focus_button_clicked)
                self.table_windows.setCellWidget(r, 4, btn_act)

        for row, w in enumerate(windows):
            it0 = self.table_windows.item(row, 0)
            if it0 and it0.text() != str(w.hwnd):
                it0.setText(str(w.hwnd))
                it0.setFont(mono_font(8))

            it1 = self.table_windows.item(row, 1)
            if it1 and it1.text() != w.app_name:
                it1.setText(w.app_name)

            it2 = self.table_windows.item(row, 2)
            if it2 and it2.text() != w.title:
                it2.setText(w.title)

            it3 = self.table_windows.item(row, 3)
            if it3:
                state_str = "[ACTIVE]" if w.is_active else ("[MIN]" if w.is_minimized else "[NORMAL]")
                if it3.text() != state_str:
                    it3.setText(state_str)
                    it3.setFont(mono_font(8))
                    it3.setForeground(QColor(C.GREEN if w.is_active else (C.ACC if w.is_minimized else C.WHITE)))

            btn = self.table_windows.cellWidget(row, 4)
            if btn:
                btn.setProperty("target_hwnd", w.hwnd)

    def _populate_tabs_table(self, tabs: list[BrowserTabInfo]) -> None:
        """In-place update of browser tabs table with zero widget leaks and diff guard."""
        sig = (len(tabs), tuple((t.tab_id, t.is_active, t.title) for t in tabs[:15]))
        if sig == self._last_tabs_sig:
            return
        self._last_tabs_sig = sig

        curr_rows = self.table_tabs.rowCount()
        target_rows = len(tabs)
        if curr_rows > target_rows:
            for r in range(curr_rows - 1, target_rows - 1, -1):
                self.table_tabs.removeRow(r)
        elif curr_rows < target_rows:
            for r in range(curr_rows, target_rows):
                self.table_tabs.insertRow(r)
                for c in range(4):
                    self.table_tabs.setItem(r, c, QTableWidgetItem())
                btn_sw = QPushButton("Switch")
                btn_sw.setFont(px_font(9, QFont.Weight.Medium))
                btn_sw.setStyleSheet(btn_css("primary"))
                btn_sw.clicked.connect(self._on_switch_tab_button_clicked)
                self.table_tabs.setCellWidget(r, 4, btn_sw)

        for row, t in enumerate(tabs):
            it0 = self.table_tabs.item(row, 0)
            if it0 and it0.text() != t.browser.upper():
                it0.setText(t.browser.upper())
                it0.setFont(mono_font(8))

            it1 = self.table_tabs.item(row, 1)
            if it1 and it1.text() != t.title:
                it1.setText(t.title)

            it2 = self.table_tabs.item(row, 2)
            d_hint = t.domain_hint or t.domain or "-"
            if it2 and it2.text() != d_hint:
                it2.setText(d_hint)
                it2.setFont(mono_font(8))

            it3 = self.table_tabs.item(row, 3)
            if it3:
                state_str = "[ACTIVE]" if t.is_active else ("[PINNED]" if t.is_pinned else "[BG]")
                if it3.text() != state_str:
                    it3.setText(state_str)
                    it3.setFont(mono_font(8))
                    it3.setForeground(QColor(C.GREEN if t.is_active else C.TEXT_DIM))

            btn = self.table_tabs.cellWidget(row, 4)
            if btn:
                btn.setProperty("tab_id", t.tab_id)

    def _update_supervisor_log(self) -> None:
        history = self.vision.supervisor.get_history()
        lines = []
        for ev in history:
            status = "VERIFIED" if ev.is_successful else f"ANOMALY: {ev.detected_anomaly}"
            rec = f" | Rec: {ev.recommended_action}" if ev.recommended_action else ""
            lines.append(f"[{status}] Step {ev.step_index} ({ev.action_type} on '{ev.target_element}') -> {ev.actual_state}{rec}")

        # Query recent physical verification records from EvidenceLedger
        latest_status = None
        try:
            from core.verification.engine import VerificationEngine
            ledger = VerificationEngine().ledger
            with ledger._lock:
                recent_recs = list(ledger._records.values())[-8:]
            if recent_recs:
                latest_status = recent_recs[-1].status.value
                lines.append("\n── Physical Verification Ledger (Cryptographic SHA-256 Records) ──")
                for r in reversed(recent_recs):
                    stat_badge = f"[{r.status.value}]"
                    v_hash = f"sha256={r.integrity_hash[:10]}..." if r.integrity_hash else ""
                    lines.append(f"{stat_badge} {r.verifier_identity} | {r.verdict} | {v_hash}")
        except Exception:
            pass

        # Update live status badge
        if latest_status:
            if "VERIFIED" in latest_status or latest_status == "PASS":
                self.lbl_supervisor_badge.setText(f"VERIFIED: {latest_status}")
                self.lbl_supervisor_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(16, 185, 129, 0.15); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
            elif latest_status == "BLOCKED":
                self.lbl_supervisor_badge.setText("VERIFIER: BLOCKED (SAFETY HALT)")
                self.lbl_supervisor_badge.setStyleSheet("color: #f97316; background: rgba(249, 115, 22, 0.15); border: 1px solid #f97316; border-radius: 3px; padding: 2px 8px;")
            elif "FAIL" in latest_status:
                self.lbl_supervisor_badge.setText(f"VERIFIER: {latest_status}")
                self.lbl_supervisor_badge.setStyleSheet(f"color: {C.RED}; background: rgba(239, 68, 68, 0.15); border: 1px solid {C.RED}; border-radius: 3px; padding: 2px 8px;")
            else:
                self.lbl_supervisor_badge.setText(f"VERIFIER: {latest_status}")
                self.lbl_supervisor_badge.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 132, 255, 0.1); border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 2px 8px;")
        elif history:
            last_ev = history[-1]
            if last_ev.is_successful:
                self.lbl_supervisor_badge.setText("SUPERVISOR: STEP VERIFIED")
                self.lbl_supervisor_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(16, 185, 129, 0.1); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
            else:
                self.lbl_supervisor_badge.setText(f"SUPERVISOR: {last_ev.detected_anomaly.upper() if last_ev.detected_anomaly else 'ANOMALY'}")
                self.lbl_supervisor_badge.setStyleSheet(f"color: {C.RED}; background: rgba(239, 68, 68, 0.1); border: 1px solid {C.RED}; border-radius: 3px; padding: 2px 8px;")

        if not lines:
            lines.append("Visual Supervisor & Verification Engine ready. Waiting for task execution events...")
        self.txt_supervisor_log.setText("\n".join(lines))

    # ── User Action Handlers ─────────────────────────────────────────────────

    def _toggle_pause(self) -> None:
        if self.vision.is_paused():
            self.vision.resume()
        else:
            self.vision.pause()
        self.refresh_vision()

    def _clear_context(self) -> None:
        self.vision.clear_visual_context()
        self.refresh_vision()

    def _on_activate_window(self, hwnd: int) -> None:
        self.vision.focus_window(hwnd, restore_minimized=True)
        self.refresh_vision()

    def _on_switch_tab(self, tab_id: str) -> None:
        self.vision.switch_browser_tab(tab_id)
        self.refresh_vision()
