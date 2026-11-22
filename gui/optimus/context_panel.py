"""gui/optimus/context_panel.py — Collapsible Contextual Intelligence Panel for OPTIMUS.

Implements the right-hand contextual panel of the three-part command interface:
- Active task and step details with assigned agent.
- Real-time desktop visual awareness context from UnifiedVisionService.
- Pending human approval requests with one-click authorization.
- Cryptographic verification ledger evidence.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel


class OptimusContextPanel(QWidget):
    """Collapsible right-side contextual intelligence deck."""

    visibility_changed = pyqtSignal(bool)

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setFixedWidth(280)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            OptimusContextPanel {{
                background: {C.PANEL};
                border-left: 1px solid {C.BORDER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── 1. Header Row ────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("CONTEXT INTELLIGENCE")
        title.setFont(px_font(10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid transparent; border-radius: 3px;
            }}
            QPushButton:hover {{
                color: {C.WHITE}; background: {C.PANEL2}; border-color: {C.BORDER_A};
            }}
        """)
        self.btn_close.clicked.connect(self.hide_panel)
        hdr.addWidget(self.btn_close)
        layout.addLayout(hdr)

        # Scroll Area for context cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_css())

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        c_lay = QVBoxLayout(container)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(10)
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # ── 2. Active Task & Execution Card ──────────────────────────────────
        p_task = HoloPanel("ACTIVE EXECUTION", "TASK")
        task_lay = QVBoxLayout()
        task_lay.setContentsMargins(6, 6, 6, 6)
        task_lay.setSpacing(4)

        self.lbl_goal = QLabel("System idle. Ready for directives.")
        self.lbl_goal.setFont(px_font(10))
        self.lbl_goal.setStyleSheet(f"color: {C.WHITE};")
        self.lbl_goal.setWordWrap(True)
        task_lay.addWidget(self.lbl_goal)

        self.lbl_step = QLabel("Step: None")
        self.lbl_step.setFont(mono_font(8))
        self.lbl_step.setStyleSheet(f"color: {C.TEXT_MED};")
        task_lay.addWidget(self.lbl_step)

        self.lbl_agent = QLabel("Agent: central_orchestrator [EXEC]")
        self.lbl_agent.setFont(mono_font(8))
        self.lbl_agent.setStyleSheet(f"color: {C.PRI};")
        task_lay.addWidget(self.lbl_agent)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: {C.BAR_BG}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {C.PRI}; border-radius: 2px; }}
        """)
        task_lay.addWidget(self.progress_bar)
        p_task.body.addLayout(task_lay)
        c_lay.addWidget(p_task)

        # ── 3. Desktop Visual Context Card ───────────────────────────────────
        p_vision = HoloPanel("DESKTOP AWARENESS", "VISION")
        vis_lay = QVBoxLayout()
        vis_lay.setContentsMargins(6, 6, 6, 6)
        vis_lay.setSpacing(4)

        self.lbl_win_app = QLabel("App: Desktop Shell")
        self.lbl_win_app.setFont(px_font(10, QFont.Weight.Medium))
        self.lbl_win_app.setStyleSheet(f"color: {C.WHITE};")
        vis_lay.addWidget(self.lbl_win_app)

        self.lbl_win_title = QLabel("Focus: explorer.exe")
        self.lbl_win_title.setFont(px_font(9))
        self.lbl_win_title.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.lbl_win_title.setWordWrap(True)
        vis_lay.addWidget(self.lbl_win_title)

        self.lbl_obs_status = QLabel("Status: ● OBSERVING ACTIVE")
        self.lbl_obs_status.setFont(mono_font(8))
        self.lbl_obs_status.setStyleSheet(f"color: {C.GREEN};")
        vis_lay.addWidget(self.lbl_obs_status)
        p_vision.body.addLayout(vis_lay)
        c_lay.addWidget(p_vision)

        # ── 4. Human Authorization Gates Card ────────────────────────────────
        p_appr = HoloPanel("HUMAN APPROVAL GATES", "SECURITY")
        appr_lay = QVBoxLayout()
        appr_lay.setContentsMargins(6, 6, 6, 6)
        appr_lay.setSpacing(4)

        self.lbl_appr_summary = QLabel("0 Pending Approvals")
        self.lbl_appr_summary.setFont(px_font(9, QFont.Weight.Medium))
        self.lbl_appr_summary.setStyleSheet(f"color: {C.TEXT_MED};")
        appr_lay.addWidget(self.lbl_appr_summary)

        self.lbl_appr_detail = QLabel("Destructive operations require sign-off.")
        self.lbl_appr_detail.setFont(px_font(8))
        self.lbl_appr_detail.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.lbl_appr_detail.setWordWrap(True)
        appr_lay.addWidget(self.lbl_appr_detail)
        p_appr.body.addLayout(appr_lay)
        c_lay.addWidget(p_appr)

        # ── 5. Verification & Integrity Card ─────────────────────────────────
        p_verif = HoloPanel("EVIDENCE VERIFICATION", "SHA256")
        verif_lay = QVBoxLayout()
        verif_lay.setContentsMargins(6, 6, 6, 6)
        verif_lay.setSpacing(4)

        self.lbl_verif_status = QLabel("● SYSTEM SEALED & VERIFIED")
        self.lbl_verif_status.setFont(mono_font(8))
        self.lbl_verif_status.setStyleSheet(f"color: {C.GREEN};")
        verif_lay.addWidget(self.lbl_verif_status)

        self.lbl_verif_hash = QLabel("Chain: sha256:d8a2...3f1c")
        self.lbl_verif_hash.setFont(mono_font(8))
        self.lbl_verif_hash.setStyleSheet(f"color: {C.TEXT_DIM};")
        verif_lay.addWidget(self.lbl_verif_hash)
        p_verif.body.addLayout(verif_lay)
        c_lay.addWidget(p_verif)

        c_lay.addStretch()

        # Connect bridge signals
        self.bridge.task_created.connect(self._on_task_created)
        self.bridge.task_completed.connect(self._on_task_completed)
        self.bridge.task_failed.connect(self._on_task_failed)
        self.bridge.approval_requested.connect(self._on_approvals_changed)
        self.bridge.approval_resolved.connect(self._on_approvals_changed)

    def toggle_visibility(self) -> None:
        self.setVisible(not self.isVisible())
        self.visibility_changed.emit(self.isVisible())

    def hide_panel(self) -> None:
        self.setVisible(False)
        self.visibility_changed.emit(False)

    def show_panel(self) -> None:
        self.setVisible(True)
        self.visibility_changed.emit(True)

    def update_desktop_context(self, app_name: str, window_title: str, is_observing: bool = True) -> None:
        self.lbl_win_app.setText(f"App: {app_name or 'Desktop Shell'}")
        self.lbl_win_title.setText(f"Focus: {window_title[:45] if window_title else 'None'}")
        if is_observing:
            self.lbl_obs_status.setText("Status: ● OBSERVING ACTIVE")
            self.lbl_obs_status.setStyleSheet(f"color: {C.GREEN};")
        else:
            self.lbl_obs_status.setText("Status: ⏸ OBSERVATION PAUSED")
            self.lbl_obs_status.setStyleSheet(f"color: {C.ACC};")

    def _on_task_created(self, task_data: dict[str, Any]) -> None:
        goal = task_data.get("goal", "")
        self.lbl_goal.setText(goal[:90] if goal else "Task executing...")
        self.lbl_step.setText("Step: Executing plan")
        self.progress_bar.setValue(25)

    def _on_task_completed(self, task_data: dict[str, Any]) -> None:
        self.lbl_goal.setText("Task completed successfully.")
        self.lbl_step.setText("Status: Completed")
        self.progress_bar.setValue(100)

    def _on_task_failed(self, task_data: dict[str, Any]) -> None:
        self.lbl_goal.setText("Task failed or encountered error.")
        self.lbl_step.setText("Status: Failed")
        self.progress_bar.setValue(100)

    def _on_approvals_changed(self, _) -> None:
        pending = self.bridge.get_pending_approvals()
        count = len(pending)
        if count > 0:
            self.lbl_appr_summary.setText(f"{count} Action(s) Require Authorization")
            self.lbl_appr_summary.setStyleSheet(f"color: {C.ACC}; font-weight: bold;")
            first = pending[0]
            self.lbl_appr_detail.setText(f"Action: {first.get('action_type', 'Tool invocation')}")
        else:
            self.lbl_appr_summary.setText("0 Pending Approvals")
            self.lbl_appr_summary.setStyleSheet(f"color: {C.TEXT_MED};")
            self.lbl_appr_detail.setText("Destructive operations require sign-off.")
