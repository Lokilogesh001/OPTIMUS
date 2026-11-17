"""gui/optimus/section_verification.py — Dedicated Verification & Evidence Section.

Provides physical postcondition verification and cryptographic audit:
- Evidence Ledger cryptographic chain inspector.
- SHA-256 tamper verification check button.
- Verifiers suite (Filesystem, Process, Data, Command).
"""

from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
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


class SectionVerification(QWidget):
    """Verification Engine & Evidence Ledger Audit Panel."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("VERIFICATION ENGINE  ·  CRYPTOGRAPHIC EVIDENCE LEDGER")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        self.btn_verify_ledger = QPushButton("VERIFY LEDGER INTEGRITY")
        self.btn_verify_ledger.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_verify_ledger.setStyleSheet(btn_css("primary"))
        self.btn_verify_ledger.clicked.connect(self._verify_ledger_chain)
        hdr.addWidget(self.btn_verify_ledger)

        btn_refresh = QPushButton("REFRESH EVIDENCE")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_verification)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Evidence Ledger Table
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        p_ledger = HoloPanel("EVIDENCE LEDGER (SHA-256 BLOCK CHAIN)", "CHAIN")
        self.table_evidence = QTableWidget()
        self.table_evidence.setColumnCount(4)
        self.table_evidence.setHorizontalHeaderLabels(["ID", "TARGET", "STATUS", "SHA-256 HASH"])
        self.table_evidence.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_evidence.verticalHeader().setVisible(False)
        self.table_evidence.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_evidence.setStyleSheet(f"""
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
        self.table_evidence.itemClicked.connect(self._on_evidence_selected)
        p_ledger.body.addWidget(self.table_evidence)
        left_lay.addWidget(p_ledger)

        split.addWidget(left_w)

        # Right Column: Evidence Details & Verifier Suite
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_audit = HoloPanel("CRYPTOGRAPHIC AUDIT STATUS", "AUDIT")
        self.audit_lbl = QLabel("LEDGER INTEGRITY: UNVERIFIED")
        self.audit_lbl.setFont(px_font(11, QFont.Weight.Bold))
        self.audit_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(0,255,136,0.08); border: 1px solid {C.GREEN}; padding: 6px; border-radius: 3px;")
        p_audit.body.addWidget(self.audit_lbl)

        self.evidence_detail = QTextEdit()
        self.evidence_detail.setReadOnly(True)
        self.evidence_detail.setFont(mono_font(9))
        self.evidence_detail.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """ + scroll_css())
        p_audit.body.addWidget(self.evidence_detail, stretch=1)
        right_lay.addWidget(p_audit)

        split.addWidget(right_w)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        layout.addWidget(split)

        # Property aliases for testing & external APIs
        self.table_records = self.table_evidence
        self.status_lbl = self.audit_lbl
        self.refresh_ledger = self.refresh_verification
        self._verify_integrity = self._verify_ledger_chain

        self._evidence_cache: list[Any] = []
        self.refresh_verification()

    def refresh_verification(self) -> None:
        verif_engine = getattr(self.bridge.orchestrator, "verification_engine", None)
        if not verif_engine:
            return

        ledger = verif_engine.ledger
        items = ledger.list_all(limit=100)
        self._evidence_cache = items

        self.table_evidence.setRowCount(len(items))
        for row, it in enumerate(items):
            id_item = QTableWidgetItem(it.evidence_id[:12])
            id_item.setFont(mono_font(9))

            target_item = QTableWidgetItem(it.subtask_id or it.verifier_identity or "--")
            target_item.setFont(px_font(9))

            st_str = "PASS" if it.is_pass else "FAIL"
            st_item = QTableWidgetItem(st_str)
            st_item.setFont(px_font(9, QFont.Weight.Bold))
            st_item.setForeground(QColor(C.GREEN if it.is_pass else C.RED))

            hash_str = it.integrity_hash[:16] if it.integrity_hash else "--"
            h_item = QTableWidgetItem(hash_str)
            h_item.setFont(mono_font(8))

            self.table_evidence.setItem(row, 0, id_item)
            self.table_evidence.setItem(row, 1, target_item)
            self.table_evidence.setItem(row, 2, st_item)
            self.table_evidence.setItem(row, 3, h_item)

        if items:
            self._render_evidence(items[0])

    def _render_evidence(self, it: Any) -> None:
        self.evidence_detail.setPlainText(json.dumps(it.to_dict(), indent=2))

    def _on_evidence_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self._evidence_cache):
            self._render_evidence(self._evidence_cache[row])

    def _verify_ledger_chain(self) -> None:
        verif_engine = getattr(self.bridge.orchestrator, "verification_engine", None)
        if not verif_engine:
            return

        is_valid = verif_engine.ledger.verify_ledger_integrity()
        if is_valid:
            self.audit_lbl.setText("PASSED — CRYPTOGRAPHIC INTEGRITY: VALID (All SHA-256 blocks verified)")
            self.audit_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(0,255,136,0.1); border: 1px solid {C.GREEN}; padding: 6px; border-radius: 3px;")
        else:
            self.audit_lbl.setText("FAILED — LEDGER INTEGRITY: TAMPER DETECTED (Block hash mismatch)")
            self.audit_lbl.setStyleSheet(f"color: {C.RED}; background: rgba(255,51,85,0.1); border: 1px solid {C.RED}; padding: 6px; border-radius: 3px;")
