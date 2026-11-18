"""gui/optimus/section_missions.py — Dedicated Autonomous Missions & Multi-Agent UI.

Provides:
- Real-time monitoring of autonomous multi-step missions across the 7-stage state machine.
- Interactive DAG subtask inspection with dependency tracking and agent assignments.
- Elevated human approval controls for high-risk operations.
- Operational controls: Launch, Pause, Resume, and Cancel.
- Live timeline event stream, evidence ledger inspection, and verified deliverables viewer.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
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

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font
from gui.theme import C
from gui.widgets import HoloPanel


class SectionMissions(QWidget):
    """The Autonomous Missions & Multi-Agent Orchestration UI section."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        self._active_mission_id: str | None = None
        self._filter_mode: str = "ALL"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        title = QLabel("AUTONOMOUS MISSIONS & MULTI-AGENT ORCHESTRATION")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        title_box.addWidget(title)

        subtitle = QLabel("STRATEGIC DAG PLANNING  ·  AUTONOMOUS STATE MACHINE  ·  ADAPTIVE RECOVERY")
        subtitle.setFont(px_font(9, QFont.Weight.DemiBold, spacing=1.2))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        title_box.addWidget(subtitle)

        hdr.addLayout(title_box)
        hdr.addStretch()

        self.btn_resume_any = QPushButton("RESUME INTERRUPTED")
        self.btn_resume_any.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_resume_any.setStyleSheet(btn_css("secondary"))
        self.btn_resume_any.clicked.connect(self._resume_interrupted_missions)
        hdr.addWidget(self.btn_resume_any)

        self.btn_refresh = QPushButton("REFRESH")
        self.btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_refresh.setStyleSheet(btn_css("secondary"))
        self.btn_refresh.clicked.connect(self.refresh_missions)
        hdr.addWidget(self.btn_refresh)

        layout.addLayout(hdr)

        # ── Launch Bar ───────────────────────────────────────────────────────
        launch_box = QHBoxLayout()
        launch_lbl = QLabel("MISSION GOAL:")
        launch_lbl.setFont(px_font(10, QFont.Weight.Bold))
        launch_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        launch_box.addWidget(launch_lbl)

        self.input_objective = QLineEdit()
        self.input_objective.setPlaceholderText("Enter high-level objective (e.g. 'Research latest Python release, compare with local version, and produce markdown report')...")
        self.input_objective.setFont(px_font(10))
        self.input_objective.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL};
                color: {C.WHITE};
                border: 1px solid {C.BORDER_A};
                border-radius: 4px;
                padding: 6px 10px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
            }}
        """)
        self.input_objective.returnPressed.connect(self._launch_mission_from_input)
        launch_box.addWidget(self.input_objective, stretch=1)

        self.btn_launch = QPushButton("LAUNCH MISSION")
        self.btn_launch.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_launch.setStyleSheet(btn_css("primary"))
        self.btn_launch.clicked.connect(self._launch_mission_from_input)
        launch_box.addWidget(self.btn_launch)

        layout.addLayout(launch_box)

        # ── Main Content Splitter ────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(0, 160, 255, 0.2); }")

        # ── Left: Missions List Pane ─────────────────────────────────────────
        left_panel = HoloPanel("MISSION REPOSITORY", parent=splitter)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 28, 8, 8)
        left_layout.setSpacing(6)

        # Filter buttons
        filter_box = QHBoxLayout()
        filter_box.setSpacing(4)
        for mode in ("ALL", "ACTIVE", "COMPLETED"):
            btn = QPushButton(mode)
            btn.setFont(px_font(8, QFont.Weight.Bold))
            btn.setStyleSheet(btn_css("secondary"))
            btn.clicked.connect(lambda ch, m=mode: self._set_filter(m))
            filter_box.addWidget(btn)
        left_layout.addLayout(filter_box)

        self.table_missions = QTableWidget(0, 4)
        self.table_missions.setHorizontalHeaderLabels(["ID", "STATE", "OBJECTIVE", "UPDATED"])
        self.table_missions.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_missions.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_missions.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table_missions.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_missions.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_missions.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_missions.setStyleSheet(self._table_style())
        self.table_missions.itemSelectionChanged.connect(self._on_mission_selected)
        left_layout.addWidget(self.table_missions)

        # ── Desktop Observation Card ─────────────────────────────────────────
        dt_frame = QFrame()
        dt_frame.setStyleSheet(f"background: {C.PANEL}; border: 1px solid {C.BORDER_A}; border-radius: 4px; padding: 6px;")
        dt_layout = QVBoxLayout(dt_frame)
        dt_layout.setContentsMargins(6, 6, 6, 6)
        dt_layout.setSpacing(4)

        dt_hdr = QHBoxLayout()
        dt_title = QLabel("VISUAL COMPUTER OBSERVATION")
        dt_title.setFont(px_font(9, QFont.Weight.Bold, spacing=1.0))
        dt_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        dt_hdr.addWidget(dt_title, stretch=1)

        self.btn_scan_desktop = QPushButton("SCAN")
        self.btn_scan_desktop.setFont(px_font(8, QFont.Weight.Bold))
        self.btn_scan_desktop.setStyleSheet(btn_css("secondary"))
        self.btn_scan_desktop.clicked.connect(self._refresh_desktop_observation)
        dt_hdr.addWidget(self.btn_scan_desktop)
        dt_layout.addLayout(dt_hdr)

        self.lbl_desktop_app = QLabel("TARGET: Idle / Ready")
        self.lbl_desktop_app.setFont(mono_font(8))
        self.lbl_desktop_app.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        dt_layout.addWidget(self.lbl_desktop_app)

        self.lbl_desktop_metrics = QLabel("METRICS: Res --x-- | Scale 1.0 | Controls: 0")
        self.lbl_desktop_metrics.setFont(mono_font(8))
        self.lbl_desktop_metrics.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        dt_layout.addWidget(self.lbl_desktop_metrics)

        left_layout.addWidget(dt_frame)

        splitter.addWidget(left_panel)

        # ── Right: Mission Details & Live DAG Pane ───────────────────────────
        right_panel = HoloPanel("MISSION EXECUTION MONITOR", parent=splitter)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 28, 10, 10)
        right_layout.setSpacing(8)

        # Detail Header & State Badge
        detail_hdr = QHBoxLayout()
        self.lbl_active_title = QLabel("NO MISSION SELECTED")
        self.lbl_active_title.setFont(px_font(12, QFont.Weight.Bold))
        self.lbl_active_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        detail_hdr.addWidget(self.lbl_active_title, stretch=1)

        self.lbl_state_badge = QLabel("[IDLE]")
        self.lbl_state_badge.setFont(px_font(10, QFont.Weight.Bold))
        self.lbl_state_badge.setStyleSheet(f"color: {C.TEXT_DIM}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; border-radius: 4px; padding: 2px 8px;")
        detail_hdr.addWidget(self.lbl_state_badge)

        # Actions: Pause, Resume, Cancel
        self.btn_pause = QPushButton("PAUSE")
        self.btn_pause.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_pause.setStyleSheet(btn_css("secondary"))
        self.btn_pause.clicked.connect(self._pause_current_mission)
        detail_hdr.addWidget(self.btn_pause)

        self.btn_resume = QPushButton("RESUME")
        self.btn_resume.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_resume.setStyleSheet(btn_css("primary"))
        self.btn_resume.clicked.connect(self._resume_current_mission)
        detail_hdr.addWidget(self.btn_resume)

        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_cancel.setStyleSheet(btn_css("danger"))
        self.btn_cancel.clicked.connect(self._cancel_current_mission)
        detail_hdr.addWidget(self.btn_cancel)

        right_layout.addLayout(detail_hdr)

        # Approval Gate Banner (Initially hidden)
        self.approval_banner = QFrame()
        self.approval_banner.setStyleSheet(f"background: rgba(255, 170, 0, 0.15); border: 1px solid {C.ACC2}; border-radius: 4px; padding: 4px;")
        banner_layout = QHBoxLayout(self.approval_banner)
        banner_layout.setContentsMargins(8, 4, 8, 4)

        self.lbl_approval_alert = QLabel("⚡ ELEVATED AUTHORIZATION REQUIRED: This autonomous mission requires operator clearance.")
        self.lbl_approval_alert.setFont(px_font(9, QFont.Weight.Bold))
        self.lbl_approval_alert.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        banner_layout.addWidget(self.lbl_approval_alert, stretch=1)

        self.btn_approve = QPushButton("APPROVE MISSION")
        self.btn_approve.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_approve.setStyleSheet(btn_css("primary"))
        self.btn_approve.clicked.connect(self._approve_current_mission)
        banner_layout.addWidget(self.btn_approve)

        self.btn_reject = QPushButton("REJECT")
        self.btn_reject.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_reject.setStyleSheet(btn_css("danger"))
        self.btn_reject.clicked.connect(self._reject_current_mission)
        banner_layout.addWidget(self.btn_reject)

        self.approval_banner.hide()
        right_layout.addWidget(self.approval_banner)

        # Subtasks DAG Table
        lbl_subtasks = QLabel("MISSION DEPENDENCY GRAPH (DAG SUBTASKS)")
        lbl_subtasks.setFont(px_font(9, QFont.Weight.Bold, spacing=1.2))
        lbl_subtasks.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        right_layout.addWidget(lbl_subtasks)

        self.table_subtasks = QTableWidget(0, 5)
        self.table_subtasks.setHorizontalHeaderLabels(["STEP", "SUBTASK TITLE", "ASSIGNED AGENT", "DEPENDS ON", "STATUS"])
        self.table_subtasks.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_subtasks.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_subtasks.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_subtasks.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_subtasks.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_subtasks.setStyleSheet(self._table_style())
        right_layout.addWidget(self.table_subtasks, stretch=2)

        # Live Execution Timeline & Deliverables Tabs/Splits
        lbl_timeline = QLabel("EXECUTION TIMELINE & DELIVERABLES")
        lbl_timeline.setFont(px_font(9, QFont.Weight.Bold, spacing=1.2))
        lbl_timeline.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        right_layout.addWidget(lbl_timeline)

        self.txt_timeline = QTextEdit()
        self.txt_timeline.setReadOnly(True)
        self.txt_timeline.setFont(mono_font(9))
        self.txt_timeline.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.WHITE};
                border: 1px solid {C.BORDER_A};
                border-radius: 4px;
                padding: 6px;
            }}
        """)
        right_layout.addWidget(self.txt_timeline, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setSizes([380, 620])
        layout.addWidget(splitter, stretch=1)

        # Wire live signals
        self.bridge.mission_created.connect(self._on_mission_event)
        self.bridge.mission_state_changed.connect(self._on_mission_event)

        # Initial populate
        self.refresh_missions()

    def _table_style(self) -> str:
        return f"""
            QTableWidget {{
                background: {C.PANEL};
                color: {C.WHITE};
                border: 1px solid {C.BORDER_A};
                gridline-color: {C.BORDER_B};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background: {C.BG};
                color: {C.PRI};
                padding: 4px;
                border: 1px solid {C.BORDER_B};
                font-weight: bold;
                font-size: 11px;
            }}
            QTableWidget::item:selected {{
                background: rgba(0, 160, 255, 0.25);
                color: {C.WHITE};
            }}
        """

    # ── Desktop & Visual Observation Refresh ─────────────────────────────────

    def _refresh_desktop_observation(self) -> None:
        """Query bridge for active desktop observation metrics."""
        try:
            obs = self.bridge.observe_desktop()
            app = obs.get("active_app") or "Unknown"
            title = obs.get("window_title") or "No active window"
            bounds = obs.get("screen_bounds", [0, 0, 1920, 1080])
            scale = obs.get("scale_factor", 1.0)
            count = obs.get("elements_count", 0)
            digest = obs.get("digest", "")[:8]
            ui_state = obs.get("ui_state", {})
            dlg_cnt = len(ui_state.get("dialogs", []))

            self.lbl_desktop_app.setText(f"TARGET: {app} — {title[:35]}")
            self.lbl_desktop_metrics.setText(
                f"SCREEN: {bounds[2]}x{bounds[3]} (Scale {scale}) | Controls: {count} | Dialogs: {dlg_cnt} | Digest: {digest}"
            )
        except Exception:
            self.lbl_desktop_app.setText("TARGET: Observation unavailable")

    # ── Data Refresh & Filtering ─────────────────────────────────────────────

    def refresh_missions(self) -> None:
        """Reload missions from MissionStore into the table."""
        all_missions = self.bridge.list_missions()
        
        filtered = []
        for m in all_missions:
            st = m.get("state", "")
            if self._filter_mode == "ACTIVE" and st in ("COMPLETE", "FAILED", "CANCELLED"):
                continue
            if self._filter_mode == "COMPLETED" and st != "COMPLETE":
                continue
            filtered.append(m)

        self.table_missions.setRowCount(len(filtered))
        for row, m in enumerate(filtered):
            m_id = m.get("mission_id", "")
            state = m.get("state", "UNDERSTAND")
            obj = m.get("objective", "")
            upd = time.strftime("%H:%M:%S", time.localtime(m.get("updated_at", time.time())))

            id_item = QTableWidgetItem(m_id[:12])
            id_item.setData(Qt.ItemDataRole.UserRole, m_id)
            state_item = QTableWidgetItem(state)
            obj_item = QTableWidgetItem(obj)
            upd_item = QTableWidgetItem(upd)

            # State color
            state_color = self._get_state_color(state)
            state_item.setForeground(QColor(state_color))

            self.table_missions.setItem(row, 0, id_item)
            self.table_missions.setItem(row, 1, state_item)
            self.table_missions.setItem(row, 2, obj_item)
            self.table_missions.setItem(row, 3, upd_item)

        # Select first row if nothing selected
        if self._active_mission_id:
            self._select_mission_row(self._active_mission_id)
        elif filtered:
            self.table_missions.selectRow(0)

    def refresh_all(self) -> None:
        self.refresh_missions()

    def _set_filter(self, mode: str) -> None:
        self._filter_mode = mode
        self.refresh_missions()

    def _select_mission_row(self, mission_id: str) -> None:
        for r in range(self.table_missions.rowCount()):
            item = self.table_missions.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == mission_id:
                self.table_missions.selectRow(r)
                break

    # ── Detail View Rendering ────────────────────────────────────────────────

    def _on_mission_selected(self) -> None:
        selected_rows = self.table_missions.selectedItems()
        if not selected_rows:
            return
        mission_id = self.table_missions.item(self.table_missions.currentRow(), 0).data(Qt.ItemDataRole.UserRole)
        self._active_mission_id = mission_id
        self._render_mission_details(mission_id)

    def _render_mission_details(self, mission_id: str) -> None:
        m = self.bridge.get_mission(mission_id)
        if not m:
            return

        state = m.get("state", "UNDERSTAND")
        objective = m.get("objective", "")
        self.lbl_active_title.setText(f"MISSION: {objective[:70]}...")
        self.lbl_state_badge.setText(f"[{state}]")
        self.lbl_state_badge.setStyleSheet(f"color: {self._get_state_color(state)}; background: {C.PANEL}; border: 1px solid {self._get_state_color(state)}; border-radius: 4px; padding: 2px 8px;")

        # Toggle Approval Banner
        if state == "WAITING_FOR_APPROVAL":
            self.approval_banner.show()
        else:
            self.approval_banner.hide()

        # Render DAG Subtasks
        plan = m.get("plan", {})
        subtasks = plan.get("subtasks", [])
        self.table_subtasks.setRowCount(len(subtasks))
        for r, s in enumerate(subtasks):
            title = s.get("title", "")
            agent = s.get("assigned_agent", "general")
            deps = ", ".join(s.get("depends_on", [])) or "None (Root)"
            st_status = s.get("status", "PENDING")

            self.table_subtasks.setItem(r, 0, QTableWidgetItem(f"#{r+1}"))
            self.table_subtasks.setItem(r, 1, QTableWidgetItem(title))
            self.table_subtasks.setItem(r, 2, QTableWidgetItem(agent))
            self.table_subtasks.setItem(r, 3, QTableWidgetItem(deps))
            
            st_item = QTableWidgetItem(st_status)
            st_color = C.GREEN if st_status == "COMPLETED" else (C.PRI if st_status == "RUNNING" else (C.RED if st_status == "FAILED" else C.TEXT_DIM))
            st_item.setForeground(QColor(st_color))
            self.table_subtasks.setItem(r, 4, st_item)

        # Render Timeline & Deliverables
        timeline_events = m.get("timeline", [])
        deliverables = m.get("deliverables", [])
        
        lines = [f"=== MISSION EXECUTION LOG: {mission_id} ==="]
        lines.append(f"State: {state} | Subtasks: {len(subtasks)} | Recovery Attempts: {m.get('recovery_attempts', 0)}")
        if deliverables:
            lines.append("\n--- VERIFIED DELIVERABLES ---")
            for d in deliverables:
                lines.append(f"  ✓ {d}")
        
        lines.append("\n--- TIMELINE EVENTS ---")
        for ev in timeline_events:
            t_str = time.strftime("%H:%M:%S", time.localtime(ev.get("timestamp", time.time())))
            lines.append(f"[{t_str}] [{ev.get('event_type')}] {ev.get('details')}")

        self.txt_timeline.setPlainText("\n".join(lines))

    def _get_state_color(self, state: str) -> str:
        if state == "COMPLETE":
            return C.GREEN
        elif state in ("EXECUTE", "VERIFY", "PLAN", "UNDERSTAND"):
            return C.PRI
        elif state in ("WAITING_FOR_APPROVAL", "PAUSED", "RETRYING", "RECOVERING"):
            return C.ACC2
        elif state in ("FAILED", "CANCELLED"):
            return C.RED
        return C.TEXT_DIM

    # ── User Action Handlers ─────────────────────────────────────────────────

    def _launch_mission_from_input(self) -> None:
        obj = self.input_objective.text().strip()
        if not obj:
            return
        self.input_objective.clear()
        rec = self.bridge.create_mission(obj)
        m_id = rec.get("mission_id")
        self._active_mission_id = m_id
        self.refresh_missions()
        # Auto-start
        self.bridge.start_mission(m_id)

    def _pause_current_mission(self) -> None:
        if self._active_mission_id:
            self.bridge.pause_mission(self._active_mission_id)
            self._render_mission_details(self._active_mission_id)

    def _resume_current_mission(self) -> None:
        if self._active_mission_id:
            self.bridge.resume_mission(self._active_mission_id)
            self._render_mission_details(self._active_mission_id)

    def _cancel_current_mission(self) -> None:
        if self._active_mission_id:
            self.bridge.cancel_mission(self._active_mission_id)
            self._render_mission_details(self._active_mission_id)

    def _approve_current_mission(self) -> None:
        if self._active_mission_id:
            self.bridge.approve_mission(self._active_mission_id)
            self.approval_banner.hide()

    def _reject_current_mission(self) -> None:
        if self._active_mission_id:
            self.bridge.reject_mission(self._active_mission_id)
            self.approval_banner.hide()

    def _resume_interrupted_missions(self) -> None:
        """Find any incomplete / paused / interrupted missions and resume them."""
        engine = getattr(self.bridge, "mission_engine", None)
        if engine:
            resumables = engine.store.find_resumable()
            for r in resumables:
                engine.resume_mission(r.mission_id)
        self.refresh_missions()

    def _on_mission_event(self, data: dict) -> None:
        m_id = data.get("mission_id")
        if self._active_mission_id == m_id:
            self._render_mission_details(m_id)
        self.refresh_missions()
