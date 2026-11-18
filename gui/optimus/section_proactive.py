"""gui/optimus/section_proactive.py — Dedicated Proactive Personal Intelligence UI Section.

Implements Phases 6 & 7:
- Sub-views:
  1. Dashboard: Active recommendations across 7 categories, with Accept / Reject / Snooze controls.
  2. Scheduler: Manage one-time, interval, and recurring cron tasks with Pause / Resume / Cancel controls.
  3. Condition Monitors: Real-time system vitals, service status, edge-triggered alerting.
  4. Notification Center: Filterable notifications with priority badges and unread counters.
  5. Governance & Settings: Quiet hours, evaluation rate, proactive autonomy controls.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font
from gui.theme import C
from gui.widgets import HoloPanel


class SectionProactive(QWidget):
    """The Proactive Personal Intelligence UI section."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        self._active_rec_id: str | None = None
        self._active_sch_id: str | None = None
        self._active_mon_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        title = QLabel("PROACTIVE PERSONAL INTELLIGENCE")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        title_box.addWidget(title)

        subtitle = QLabel("AUTONOMOUS CONTEXT AWARENESS  ·  INTELLIGENT SCHEDULING  ·  GOVERNED RECOMMENDATIONS")
        subtitle.setFont(px_font(9, QFont.Weight.DemiBold, spacing=1.2))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        title_box.addWidget(subtitle)

        hdr.addLayout(title_box)
        hdr.addStretch()

        self.btn_refresh = QPushButton("REFRESH")
        self.btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_refresh.setStyleSheet(btn_css("secondary"))
        self.btn_refresh.clicked.connect(self.refresh_all)
        hdr.addWidget(self.btn_refresh)

        layout.addLayout(hdr)

        # ── Sub-Navigation Bar ───────────────────────────────────────────────
        subnav_box = QHBoxLayout()
        subnav_box.setSpacing(6)

        self.sub_buttons: list[QPushButton] = []
        sub_views = [
            ("DASHBOARD", 0),
            ("SCHEDULER", 1),
            ("MONITORS", 2),
            ("NOTIFICATIONS", 3),
            ("SETTINGS", 4),
        ]
        for name, idx in sub_views:
            btn = QPushButton(name)
            btn.setFont(px_font(10, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(self._subnav_btn_style(idx == 0))
            btn.clicked.connect(lambda checked=False, i=idx: self._switch_sub_view(i))
            subnav_box.addWidget(btn)
            self.sub_buttons.append(btn)

        subnav_box.addStretch()
        layout.addLayout(subnav_box)

        # ── Stacked Views ────────────────────────────────────────────────────
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack, stretch=1)

        self._build_dashboard_view()
        self._build_scheduler_view()
        self._build_monitors_view()
        self._build_notifications_view()
        self._build_settings_view()

        # Connect proactive bridge signals
        self.bridge.recommendation_created.connect(lambda _: self.refresh_dashboard())
        self.bridge.notification_received.connect(lambda _: self.refresh_notifications())

        # Auto-refresh timer (every 10 seconds)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_all)
        self._timer.start(10000)

        # Initial load
        self.refresh_all()

    def _subnav_btn_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    color: {C.WHITE};
                    background: rgba(0, 212, 255, 0.16);
                    border: 1px solid {C.PRI};
                    border-radius: 2px;
                    padding: 0 14px;
                }}
            """
        return f"""
            QPushButton {{
                color: {C.TEXT_DIM};
                background: {C.PANEL};
                border: 1px solid {C.BORDER_A};
                border-radius: 2px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                color: {C.PRI};
                border: 1px solid {C.BORDER_B};
            }}
        """

    def _switch_sub_view(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.sub_buttons):
            btn.setStyleSheet(self._subnav_btn_style(i == index))
        self.refresh_all()

    # ── View 0: Dashboard (Recommendations) ──────────────────────────────────

    def _build_dashboard_view(self) -> None:
        page = QWidget()
        layout_page = QVBoxLayout(page)
        layout_page.setContentsMargins(0, 4, 0, 0)
        layout_page.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Recommendations Table
        left_panel = HoloPanel("ACTIVE RECOMMENDATIONS & NEXT ACTIONS")
        ll = QVBoxLayout(left_panel)
        ll.setContentsMargins(8, 28, 8, 8)
        ll.setSpacing(6)

        self.table_recs = QTableWidget()
        self.table_recs.setColumnCount(4)
        self.table_recs.setHorizontalHeaderLabels(["PRIORITY", "CATEGORY", "TITLE", "GOVERNANCE"])
        self.table_recs.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table_recs.verticalHeader().setVisible(False)
        self.table_recs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_recs.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL};
                gridline-color: {C.BORDER_A};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.TEXT_DIM};
                font-weight: bold;
                border: none;
                border-bottom: 1px solid {C.BORDER_B};
                padding: 4px;
            }}
        """)
        self.table_recs.itemSelectionChanged.connect(self._on_rec_selected)
        ll.addWidget(self.table_recs)
        splitter.addWidget(left_panel)

        # Right: Details & Action Controls
        right_panel = HoloPanel("RECOMMENDATION DETAILS & ACTIONS")
        rl = QVBoxLayout(right_panel)
        rl.setContentsMargins(8, 28, 8, 8)
        rl.setSpacing(8)

        self.rec_detail_text = QTextEdit()
        self.rec_detail_text.setReadOnly(True)
        self.rec_detail_text.setFont(mono_font(9))
        self.rec_detail_text.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
                border-radius: 2px;
                padding: 6px;
            }}
        """)
        rl.addWidget(self.rec_detail_text, stretch=1)

        # Action Buttons
        btn_box = QHBoxLayout()
        self.btn_rec_accept = QPushButton("ACCEPT & EXECUTE")
        self.btn_rec_accept.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_rec_accept.setStyleSheet(btn_css("primary"))
        self.btn_rec_accept.clicked.connect(self._accept_active_rec)
        btn_box.addWidget(self.btn_rec_accept)

        self.btn_rec_snooze = QPushButton("SNOOZE (1h)")
        self.btn_rec_snooze.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_rec_snooze.setStyleSheet(btn_css("secondary"))
        self.btn_rec_snooze.clicked.connect(self._snooze_active_rec)
        btn_box.addWidget(self.btn_rec_snooze)

        self.btn_rec_reject = QPushButton("DISMISS")
        self.btn_rec_reject.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_rec_reject.setStyleSheet(btn_css("danger"))
        self.btn_rec_reject.clicked.connect(self._reject_active_rec)
        btn_box.addWidget(self.btn_rec_reject)

        rl.addLayout(btn_box)
        splitter.addWidget(right_panel)

        splitter.setSizes([550, 450])
        layout_page.addWidget(splitter)
        self.stack.addWidget(page)

    # ── View 1: Scheduler ────────────────────────────────────────────────────

    def _build_scheduler_view(self) -> None:
        page = QWidget()
        layout_page = QVBoxLayout(page)
        layout_page.setContentsMargins(0, 4, 0, 0)
        layout_page.setSpacing(8)

        # Quick Add Row
        add_box = QHBoxLayout()
        lbl = QLabel("NEW TASK:")
        lbl.setFont(px_font(9, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {C.PRI};")
        add_box.addWidget(lbl)

        self.sch_input_name = QLineEdit()
        self.sch_input_name.setPlaceholderText("Task Name...")
        self.sch_input_name.setFont(px_font(9))
        self.sch_input_name.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; padding: 3px;")
        add_box.addWidget(self.sch_input_name, stretch=1)

        self.sch_input_target = QLineEdit()
        self.sch_input_target.setPlaceholderText("Objective / Tool / Mission...")
        self.sch_input_target.setFont(px_font(9))
        self.sch_input_target.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; padding: 3px;")
        add_box.addWidget(self.sch_input_target, stretch=2)

        self.sch_combo_freq = QComboBox()
        self.sch_combo_freq.addItems(["ONCE", "DAILY", "INTERVAL"])
        self.sch_combo_freq.setFont(px_font(9))
        self.sch_combo_freq.setStyleSheet(f"background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER_A};")
        add_box.addWidget(self.sch_combo_freq)

        self.btn_add_sch = QPushButton("SCHEDULE TASK")
        self.btn_add_sch.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_add_sch.setStyleSheet(btn_css("primary"))
        self.btn_add_sch.clicked.connect(self._create_new_schedule)
        add_box.addWidget(self.btn_add_sch)

        layout_page.addLayout(add_box)

        # Schedules Table
        self.table_schedules = QTableWidget()
        self.table_schedules.setColumnCount(5)
        self.table_schedules.setHorizontalHeaderLabels(["STATUS", "NAME", "FREQUENCY", "NEXT RUN", "TARGET"])
        self.table_schedules.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table_schedules.verticalHeader().setVisible(False)
        self.table_schedules.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_schedules.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL};
                gridline-color: {C.BORDER_A};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.TEXT_DIM};
                font-weight: bold;
                border: none;
                border-bottom: 1px solid {C.BORDER_B};
                padding: 4px;
            }}
        """)
        self.table_schedules.itemSelectionChanged.connect(self._on_sch_selected)
        layout_page.addWidget(self.table_schedules, stretch=1)

        # Controls Row
        ctrl_box = QHBoxLayout()
        self.btn_sch_pause = QPushButton("PAUSE")
        self.btn_sch_pause.setStyleSheet(btn_css("secondary"))
        self.btn_sch_pause.clicked.connect(self._pause_selected_schedule)
        ctrl_box.addWidget(self.btn_sch_pause)

        self.btn_sch_resume = QPushButton("RESUME")
        self.btn_sch_resume.setStyleSheet(btn_css("secondary"))
        self.btn_sch_resume.clicked.connect(self._resume_selected_schedule)
        ctrl_box.addWidget(self.btn_sch_resume)

        self.btn_sch_delete = QPushButton("CANCEL / DELETE")
        self.btn_sch_delete.setStyleSheet(btn_css("danger"))
        self.btn_sch_delete.clicked.connect(self._delete_selected_schedule)
        ctrl_box.addWidget(self.btn_sch_delete)

        ctrl_box.addStretch()
        layout_page.addLayout(ctrl_box)

        self.stack.addWidget(page)

    # ── View 2: Condition Monitors ───────────────────────────────────────────

    def _build_monitors_view(self) -> None:
        page = QWidget()
        layout_page = QVBoxLayout(page)
        layout_page.setContentsMargins(0, 4, 0, 0)
        layout_page.setSpacing(8)

        # Table of active monitors
        self.table_monitors = QTableWidget()
        self.table_monitors.setColumnCount(5)
        self.table_monitors.setHorizontalHeaderLabels(["STATUS", "NAME", "CONDITION", "THRESHOLD", "TRIGGERS"])
        self.table_monitors.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_monitors.verticalHeader().setVisible(False)
        self.table_monitors.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_monitors.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL};
                gridline-color: {C.BORDER_A};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.TEXT_DIM};
                font-weight: bold;
                border: none;
                border-bottom: 1px solid {C.BORDER_B};
                padding: 4px;
            }}
        """)
        layout_page.addWidget(self.table_monitors, stretch=1)

        self.stack.addWidget(page)

    # ── View 3: Notifications Center ─────────────────────────────────────────

    def _build_notifications_view(self) -> None:
        page = QWidget()
        layout_page = QVBoxLayout(page)
        layout_page.setContentsMargins(0, 4, 0, 0)
        layout_page.setSpacing(8)

        top_bar = QHBoxLayout()
        self.btn_mark_all_read = QPushButton("MARK ALL AS READ")
        self.btn_mark_all_read.setStyleSheet(btn_css("secondary"))
        self.btn_mark_all_read.clicked.connect(self._mark_all_notifications_read)
        top_bar.addWidget(self.btn_mark_all_read)
        top_bar.addStretch()
        layout_page.addLayout(top_bar)

        self.table_notifs = QTableWidget()
        self.table_notifs.setColumnCount(4)
        self.table_notifs.setHorizontalHeaderLabels(["STATE", "PRIORITY", "TIME", "MESSAGE"])
        self.table_notifs.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_notifs.verticalHeader().setVisible(False)
        self.table_notifs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_notifs.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL};
                gridline-color: {C.BORDER_A};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.TEXT_DIM};
                font-weight: bold;
                border: none;
                border-bottom: 1px solid {C.BORDER_B};
                padding: 4px;
            }}
        """)
        layout_page.addWidget(self.table_notifs, stretch=1)

        self.stack.addWidget(page)

    # ── View 4: Settings & Governance ────────────────────────────────────────

    def _build_settings_view(self) -> None:
        page = QWidget()
        layout_page = QVBoxLayout(page)
        layout_page.setContentsMargins(8, 8, 8, 8)
        layout_page.setSpacing(12)

        panel = HoloPanel("PROACTIVE AUTONOMY & GOVERNANCE SETTINGS")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(12, 32, 12, 12)
        pl.setSpacing(8)

        info = QLabel(
            "ACTION GOVERNANCE MATRIX:\n\n"
            "• LEVEL 1 (RECOMMEND): Non-intrusive suggestion. Zero automatic execution.\n"
            "• LEVEL 2 (PREPARE): Context gathering, plan staging, dry-run without mutation.\n"
            "• LEVEL 3 (EXECUTE): Safe, low-risk execution within pre-authorized permission bounds.\n"
            "• LEVEL 4 (APPROVAL REQUIRED): Consequential, system-altering, or data-mutating actions.\n"
            "   Requires explicit operator approval before execution.\n\n"
            "Local-First & Hardware-Adaptive:\n"
            "Proactive intelligence evaluates deterministically without continuous background LLM polling."
        )
        info.setFont(mono_font(9))
        info.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        pl.addWidget(info)
        pl.addStretch()

        layout_page.addWidget(panel)
        self.stack.addWidget(page)

    # ── Data Refresh & Event Handlers ────────────────────────────────────────

    def refresh_all(self) -> None:
        """Refresh all proactive tabs and update badge counters."""
        self.refresh_dashboard()
        self.refresh_schedules()
        self.refresh_monitors()
        self.refresh_notifications()

    def refresh_dashboard(self) -> None:
        recs = self.bridge.list_recommendations()
        self.table_recs.setRowCount(len(recs))
        for row, r in enumerate(recs):
            pri_item = QTableWidgetItem(r.get("priority", "MEDIUM"))
            cat_item = QTableWidgetItem(r.get("category", "NEXT_ACTION"))
            title_item = QTableWidgetItem(r.get("title", ""))
            gov_item = QTableWidgetItem(r.get("governance_level", "LEVEL_1_RECOMMEND"))

            pri = r.get("priority", "")
            if pri == "CRITICAL":
                pri_item.setForeground(QColor(C.RED))
            elif pri == "HIGH":
                pri_item.setForeground(QColor(C.ACC))
            else:
                pri_item.setForeground(QColor(C.PRI))

            pri_item.setData(Qt.ItemDataRole.UserRole, r.get("recommendation_id"))

            self.table_recs.setItem(row, 0, pri_item)
            self.table_recs.setItem(row, 1, cat_item)
            self.table_recs.setItem(row, 2, title_item)
            self.table_recs.setItem(row, 3, gov_item)

    def _on_rec_selected(self) -> None:
        selected_rows = self.table_recs.selectedItems()
        if not selected_rows:
            return
        rec_id = selected_rows[0].data(Qt.ItemDataRole.UserRole)
        self._active_rec_id = rec_id

        recs = self.bridge.list_recommendations()
        target = next((r for r in recs if r.get("recommendation_id") == rec_id), None)
        if target:
            text = (
                f"RECOMMENDATION ID: {target.get('recommendation_id')}\n"
                f"CATEGORY:         {target.get('category')}\n"
                f"PRIORITY:         {target.get('priority')}\n"
                f"GOVERNANCE LEVEL: {target.get('governance_level')}\n"
                f"CREATED AT:       {time.ctime(target.get('created_at', 0))}\n\n"
                f"TITLE:            {target.get('title')}\n\n"
                f"DESCRIPTION:\n{target.get('description')}\n\n"
                f"REASON:\n{target.get('reason')}\n\n"
                f"SUGGESTED ACTION:\n{target.get('suggested_action')}\n\n"
                f"SOURCE EVIDENCE:\n{target.get('source_evidence')}\n"
            )
            self.rec_detail_text.setText(text)

    def _accept_active_rec(self) -> None:
        if not self._active_rec_id:
            return
        self.bridge.accept_recommendation(self._active_rec_id, operator_approved=True)
        self.refresh_dashboard()

    def _snooze_active_rec(self) -> None:
        if not self._active_rec_id:
            return
        self.bridge.snooze_recommendation(self._active_rec_id, duration_seconds=3600.0)
        self.refresh_dashboard()

    def _reject_active_rec(self) -> None:
        if not self._active_rec_id:
            return
        self.bridge.reject_recommendation(self._active_rec_id, reason="User rejected from UI")
        self.refresh_dashboard()

    def refresh_schedules(self) -> None:
        schedules = self.bridge.list_schedules()
        self.table_schedules.setRowCount(len(schedules))
        for row, s in enumerate(schedules):
            st_item = QTableWidgetItem(s.get("status", "ACTIVE"))
            name_item = QTableWidgetItem(s.get("name", ""))
            freq_item = QTableWidgetItem(s.get("frequency", "ONCE"))
            next_run = s.get("next_run_at") or s.get("run_at")
            next_str = time.strftime("%H:%M:%S", time.localtime(next_run)) if next_run else "-"
            next_item = QTableWidgetItem(next_str)
            target_item = QTableWidgetItem(s.get("target", ""))

            st_item.setData(Qt.ItemDataRole.UserRole, s.get("schedule_id"))
            self.table_schedules.setItem(row, 0, st_item)
            self.table_schedules.setItem(row, 1, name_item)
            self.table_schedules.setItem(row, 2, freq_item)
            self.table_schedules.setItem(row, 3, next_item)
            self.table_schedules.setItem(row, 4, target_item)

    def _on_sch_selected(self) -> None:
        selected_rows = self.table_schedules.selectedItems()
        if not selected_rows:
            return
        self._active_sch_id = selected_rows[0].data(Qt.ItemDataRole.UserRole)

    def _create_new_schedule(self) -> None:
        name = self.sch_input_name.text().strip()
        target = self.sch_input_target.text().strip()
        freq = self.sch_combo_freq.currentText()
        if not name or not target:
            return
        self.bridge.create_schedule(
            name=name,
            target=target,
            schedule_type="RECURRING" if freq != "ONCE" else "ONE_TIME",
            frequency=freq,
            run_at=time.time() + 60.0,
            interval_seconds=3600.0 if freq == "INTERVAL" else 0.0,
        )
        self.sch_input_name.clear()
        self.sch_input_target.clear()
        self.refresh_schedules()

    def _pause_selected_schedule(self) -> None:
        if self._active_sch_id:
            self.bridge.pause_schedule(self._active_sch_id)
            self.refresh_schedules()

    def _resume_selected_schedule(self) -> None:
        if self._active_sch_id:
            self.bridge.resume_schedule(self._active_sch_id)
            self.refresh_schedules()

    def _delete_selected_schedule(self) -> None:
        if self._active_sch_id:
            self.bridge.delete_schedule(self._active_sch_id)
            self.refresh_schedules()

    def refresh_monitors(self) -> None:
        monitors = self.bridge.list_monitors()
        self.table_monitors.setRowCount(len(monitors))
        for row, m in enumerate(monitors):
            st_item = QTableWidgetItem(m.get("status", "ACTIVE"))
            name_item = QTableWidgetItem(m.get("name", ""))
            cond_item = QTableWidgetItem(f"{m.get('condition_type')} {m.get('target')} {m.get('operator')}")
            thresh_item = QTableWidgetItem(str(m.get("threshold", "")))
            trig_item = QTableWidgetItem(str(m.get("trigger_count", 0)))

            st_item.setData(Qt.ItemDataRole.UserRole, m.get("monitor_id"))
            self.table_monitors.setItem(row, 0, st_item)
            self.table_monitors.setItem(row, 1, name_item)
            self.table_monitors.setItem(row, 2, cond_item)
            self.table_monitors.setItem(row, 3, thresh_item)
            self.table_monitors.setItem(row, 4, trig_item)

    def refresh_notifications(self) -> None:
        notifs = self.bridge.list_notifications()
        self.table_notifs.setRowCount(len(notifs))
        unread_count = 0
        for row, n in enumerate(notifs):
            is_read = n.get("read", False)
            if not is_read:
                unread_count += 1
            st_str = "READ" if is_read else "● NEW"
            st_item = QTableWidgetItem(st_str)
            if not is_read:
                st_item.setForeground(QColor(C.ACC))

            pri_item = QTableWidgetItem(n.get("priority", "MEDIUM"))
            time_str = time.strftime("%H:%M:%S", time.localtime(n.get("created_at", 0)))
            time_item = QTableWidgetItem(time_str)
            msg_item = QTableWidgetItem(f"[{n.get('title')}] {n.get('message')}")

            st_item.setData(Qt.ItemDataRole.UserRole, n.get("notification_id"))
            self.table_notifs.setItem(row, 0, st_item)
            self.table_notifs.setItem(row, 1, pri_item)
            self.table_notifs.setItem(row, 2, time_item)
            self.table_notifs.setItem(row, 3, msg_item)

        # Update bridge parent / navbar badge
        parent_cc = self.window()
        if hasattr(parent_cc, "nav_bar") and hasattr(parent_cc.nav_bar, "set_proactive_badge"):
            parent_cc.nav_bar.set_proactive_badge(unread_count)

    def _mark_all_notifications_read(self) -> None:
        self.bridge.mark_all_notifications_read()
        self.refresh_notifications()
