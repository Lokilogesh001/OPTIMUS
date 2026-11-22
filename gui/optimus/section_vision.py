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

        # Periodic refresh timer (every 1.5s)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_vision)
        self.timer.start(1500)

        # Initial populate
        self.refresh_vision()

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
        self.table_windows.setRowCount(len(windows))
        for row, w in enumerate(windows):
            hwnd_item = QTableWidgetItem(str(w.hwnd))
            hwnd_item.setFont(mono_font(8))
            self.table_windows.setItem(row, 0, hwnd_item)

            app_item = QTableWidgetItem(w.app_name)
            self.table_windows.setItem(row, 1, app_item)

            title_item = QTableWidgetItem(w.title)
            self.table_windows.setItem(row, 2, title_item)

            state_str = "[ACTIVE]" if w.is_active else ("[MIN]" if w.is_minimized else "[NORMAL]")
            state_item = QTableWidgetItem(state_str)
            state_item.setFont(mono_font(8))
            state_item.setForeground(QColor(C.GREEN if w.is_active else (C.ACC if w.is_minimized else C.WHITE)))
            self.table_windows.setItem(row, 3, state_item)

            btn_act = QPushButton("Focus")
            btn_act.setFont(px_font(9, QFont.Weight.Medium))
            btn_act.setStyleSheet(btn_css("primary"))
            btn_act.clicked.connect(lambda checked=False, target_hwnd=w.hwnd: self._on_activate_window(target_hwnd))
            self.table_windows.setCellWidget(row, 4, btn_act)

    def _populate_tabs_table(self, tabs: list[BrowserTabInfo]) -> None:
        self.table_tabs.setRowCount(len(tabs))
        for row, t in enumerate(tabs):
            b_item = QTableWidgetItem(t.browser.upper())
            b_item.setFont(mono_font(8))
            self.table_tabs.setItem(row, 0, b_item)

            t_item = QTableWidgetItem(t.title)
            self.table_tabs.setItem(row, 1, t_item)

            d_item = QTableWidgetItem(t.domain_hint or t.domain or "-")
            d_item.setFont(mono_font(8))
            self.table_tabs.setItem(row, 2, d_item)

            state_str = "[ACTIVE]" if t.is_active else ("[PINNED]" if t.is_pinned else "[BG]")
            state_item = QTableWidgetItem(state_str)
            state_item.setFont(mono_font(8))
            state_item.setForeground(QColor(C.GREEN if t.is_active else C.TEXT_DIM))
            self.table_tabs.setItem(row, 3, state_item)

            btn_sw = QPushButton("Switch")
            btn_sw.setFont(px_font(9, QFont.Weight.Medium))
            btn_sw.setStyleSheet(btn_css("primary"))
            btn_sw.clicked.connect(lambda checked=False, tab_id=t.tab_id: self._on_switch_tab(tab_id))
            self.table_tabs.setCellWidget(row, 4, btn_sw)

    def _update_supervisor_log(self) -> None:
        history = self.vision.supervisor.get_history()
        lines = []
        for ev in history:
            status = "VERIFIED" if ev.is_successful else f"ANOMALY: {ev.detected_anomaly}"
            rec = f" | Rec: {ev.recommended_action}" if ev.recommended_action else ""
            lines.append(f"[{status}] Step {ev.step_index} ({ev.action_type} on '{ev.target_element}') -> {ev.actual_state}{rec}")
        if not lines:
            lines.append("Visual Supervisor ready. Waiting for task execution events...")
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
