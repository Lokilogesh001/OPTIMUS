"""gui/optimus/section_approvals.py — Dedicated Human Authorization Gates Section.

Provides human oversight for sensitive and destructive workflow steps:
- Real-time pending approval requests table.
- Timeout countdowns and role verification.
- Human decision forms with rejection of automated LLM strings.
"""

from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
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
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel


class SectionApprovals(QWidget):
    """Human Authorization Gate Management Dashboard."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("HUMAN AUTHORIZATION GATES  ·  COMPLIANCE AUDIT")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH APPROVALS")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_approvals)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Pending Table
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        p_pending = HoloPanel("PENDING HUMAN GATES", "AUTHORIZATION")
        self.table_approvals = QTableWidget()
        self.table_approvals.setColumnCount(4)
        self.table_approvals.setHorizontalHeaderLabels(["ID", "STEP", "REQUIRED ROLE", "REMAINING"])
        self.table_approvals.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_approvals.verticalHeader().setVisible(False)
        self.table_approvals.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_approvals.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL2};
                color: {C.WHITE};
                gridline-color: {C.BORDER};
                border: 1px solid {C.BORDER};
                selection-background-color: {C.PRI_GHO};
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.PRI};
                border: 1px solid {C.BORDER};
                padding: 4px;
                font-weight: bold;
            }}
        """ + scroll_css())
        self.table_approvals.itemClicked.connect(self._on_approval_selected)
        p_pending.body.addWidget(self.table_approvals)
        left_lay.addWidget(p_pending)

        split.addWidget(left_w)

        # Right Column: Decision Form & Verification Details
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_form = HoloPanel("SIGN-OFF DIRECTIVE FORM", "DECISION")
        f_lay = QVBoxLayout()

        self.form_details_edit = QTextEdit()
        self.form_details_edit.setReadOnly(True)
        self.form_details_edit.setFont(mono_font(9))
        self.form_details_edit.setFixedHeight(120)
        self.form_details_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 4px;
            }}
        """ + scroll_css())
        f_lay.addWidget(self.form_details_edit)

        # Form fields
        self.approver_input = QLineEdit()
        self.approver_input.setFont(px_font(10))
        self.approver_input.setPlaceholderText("Human Operator Name (e.g. 'Lead Engineer'). AI strings strictly blocked.")
        self.approver_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """)
        f_lay.addWidget(self.approver_input)

        self.reason_input = QLineEdit()
        self.reason_input.setFont(px_font(10))
        self.reason_input.setPlaceholderText("Audit reason / justification for action...")
        self.reason_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """)
        f_lay.addWidget(self.reason_input)

        self.error_banner = QLabel("")
        self.error_banner.setFont(px_font(9, QFont.Weight.Bold))
        self.error_banner.setStyleSheet(f"color: {C.RED}; background: transparent;")
        self.error_banner.hide()
        f_lay.addWidget(self.error_banner)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_approve = QPushButton("APPROVE DIRECTIVE")
        self.btn_approve.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_approve.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_approve.setStyleSheet(f"""
            QPushButton {{
                color: {C.GREEN}; background: rgba(0, 255, 136, 0.12);
                border: 1px solid {C.GREEN}; border-radius: 3px; padding: 6px 12px;
            }}
            QPushButton:hover {{ background: rgba(0, 255, 136, 0.25); }}
        """)
        self.btn_approve.clicked.connect(self._do_approve)
        btn_row.addWidget(self.btn_approve)

        self.btn_reject = QPushButton("REJECT DIRECTIVE")
        self.btn_reject.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_reject.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reject.setStyleSheet(f"""
            QPushButton {{
                color: {C.RED}; background: rgba(255, 51, 85, 0.12);
                border: 1px solid {C.RED}; border-radius: 3px; padding: 6px 12px;
            }}
            QPushButton:hover {{ background: rgba(255, 51, 85, 0.25); }}
        """)
        self.btn_reject.clicked.connect(self._do_reject)
        btn_row.addWidget(self.btn_reject)
        f_lay.addLayout(btn_row)

        p_form.body.addLayout(f_lay)
        right_lay.addWidget(p_form)

        split.addWidget(right_w)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        layout.addWidget(split)

        self._pending_cache: list[dict[str, Any]] = []
        self._selected_approval_id: str | None = None

        # Wire signals
        self.bridge.approval_requested.connect(self._on_approval_event)
        self.bridge.approval_resolved.connect(self._on_approval_event)

        self.refresh_approvals()

    def _on_approval_event(self, _=None) -> None:
        self.refresh_approvals()

    def refresh_approvals(self) -> None:
        pending = self.bridge.get_pending_approvals()
        self._pending_cache = pending
        self.table_approvals.setRowCount(len(pending))

        now = time.time()
        for row, it in enumerate(pending):
            id_item = QTableWidgetItem(it.get("approval_id", "")[:12])
            id_item.setFont(mono_font(9))

            step_item = QTableWidgetItem(it.get("step_id", ""))
            step_item.setFont(px_font(9, QFont.Weight.Bold))

            role_item = QTableWidgetItem(it.get("required_role") or "general")
            role_item.setFont(mono_font(9))
            role_item.setForeground(QColor(C.ACC))

            exp = it.get("expires_at")
            if exp:
                rem = max(0, int(exp - now))
                rem_str = f"{rem}s"
            else:
                rem_str = "No limit"
            rem_item = QTableWidgetItem(rem_str)
            rem_item.setFont(mono_font(9))

            self.table_approvals.setItem(row, 0, id_item)
            self.table_approvals.setItem(row, 1, step_item)
            self.table_approvals.setItem(row, 2, role_item)
            self.table_approvals.setItem(row, 3, rem_item)

        if pending and not self._selected_approval_id:
            self._render_approval(pending[0])
        elif not pending:
            self.form_details_edit.setPlainText("No human authorization gates currently pending.")
            self._selected_approval_id = None

    def _on_approval_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self._pending_cache):
            self._render_approval(self._pending_cache[row])

    def _render_approval(self, it: dict[str, Any]) -> None:
        self._selected_approval_id = it.get("approval_id")
        self.error_banner.hide()
        self.form_details_edit.setPlainText(
            f"APPROVAL ID: {it.get('approval_id')}\n"
            f"WORKFLOW ID: {it.get('workflow_id')}\n"
            f"EXECUTION ID: {it.get('execution_id')}\n"
            f"STEP: {it.get('step_id')}\n"
            f"REQUIRED ROLE: {it.get('required_role') or 'operator'}\n"
            f"REASON: {it.get('reason')}\n"
        )

    def _do_approve(self) -> None:
        self._resolve(True)

    def _do_reject(self) -> None:
        self._resolve(False)

    def _resolve(self, approved: bool) -> None:
        if not self._selected_approval_id:
            self.error_banner.setText("Select an approval request first.")
            self.error_banner.show()
            return

        approver = self.approver_input.text().strip()
        reason = self.reason_input.text().strip()

        if not approver:
            self.error_banner.setText("Approver identity is required.")
            self.error_banner.show()
            return

        # Anti-LLM client-side validation
        if any(w in approver.lower() for w in ("llm", "ai", "model", "assistant", "bot", "agent")):
            self.error_banner.setText("REJECTED: Automated LLM strings cannot grant human authorization.")
            self.error_banner.show()
            return

        self.error_banner.hide()
        self.bridge.resolve_approval(self._selected_approval_id, approver, approved, reason)
        self.approver_input.clear()
        self.reason_input.clear()
        self.refresh_approvals()
