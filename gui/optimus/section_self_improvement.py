"""gui/optimus/section_self_improvement.py — Dedicated Self-Improvement View for OPTIMUS.

Displays:
- Capability Gaps (detection telemetry, category, frequency, confidence).
- Improvement Proposals (lifecycle states, risk classifications, author agent).
- Sandbox Test Evidence (syntax, functional, adversarial, privilege boundaries).
- Goldfish Review Findings (verdicts, critical/high issues, audit signature).
- Promotion History & Rollback Controls (active versioning, one-click rollback).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.style import px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel

logger = logging.getLogger("optimus.gui.self_improvement")


class SectionSelfImprovement(QWidget):
    """Dedicated UI section displaying verified self-improvement backend state."""

    def __init__(self, bridge=None, parent=None):
        super().__init__(parent)
        self.bridge = bridge

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        # Header Title
        title_bar = QHBoxLayout()
        header = QLabel("VERIFIED SELF-IMPROVEMENT // AUTONOMOUS CAPABILITY EXPANSION")
        header.setFont(px_font(13, QFont.Weight.Bold, spacing=1.5))
        header.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        title_bar.addWidget(header)

        title_bar.addStretch()

        self.btn_refresh = QPushButton("↻ REFRESH TELEMETRY")
        self.btn_refresh.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_refresh.setStyleSheet(f"""
            QPushButton {{
                color: {C.WHITE};
                background: rgba(0, 212, 255, 0.15);
                border: 1px solid {C.PRI};
                border-radius: 3px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{
                background: rgba(0, 212, 255, 0.3);
            }}
        """)
        self.btn_refresh.clicked.connect(self.refresh_data)
        title_bar.addWidget(self.btn_refresh)
        root_layout.addLayout(title_bar)

        # Main Scroll Area
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;" + scroll_css())

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(12)
        scroll.setWidget(container)
        root_layout.addWidget(scroll)

        # ── Panel 1: Capability Gaps ──────────────────────────────────────────
        self.panel_gaps = HoloPanel("CAPABILITY GAPS", "TELEMETRY_OBSERVED")
        self._build_gaps_panel(self.panel_gaps.body)
        c_layout.addWidget(self.panel_gaps)

        # ── Panel 2: Improvement Proposals ────────────────────────────────────
        self.panel_props = HoloPanel("IMPROVEMENT PROPOSALS & LIFECYCLE", "SPECIFICATION_GOVERNED")
        self._build_proposals_panel(self.panel_props.body)
        c_layout.addWidget(self.panel_props)

        # ── Panel 3: Active Promotions & Version Rollback ─────────────────────
        self.panel_prom = HoloPanel("PROMOTION HISTORY & ROLLBACK CONTROLS", "PRODUCTION_AUDIT")
        self._build_promotions_panel(self.panel_prom.body)
        c_layout.addWidget(self.panel_prom)

        # ── Panel 4: Test & Goldfish Evidence Logs ────────────────────────────
        self.panel_logs = HoloPanel("VERIFICATION & ADVERSARIAL AUDIT LOGS", "EVIDENCE_LEDGER")
        self._build_logs_panel(self.panel_logs.body)
        c_layout.addWidget(self.panel_logs)

        # Periodic refresh timer (every 5 seconds)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_data)
        self._timer.start(5000)

        # Initial load
        self.refresh_data()

    def _build_gaps_panel(self, body_layout: QVBoxLayout) -> None:
        self.table_gaps = QTableWidget(0, 5, self)
        self.table_gaps.setHorizontalHeaderLabels(["GAP ID", "CATEGORY", "DESCRIPTION", "FREQUENCY", "CONFIDENCE"])
        self.table_gaps.horizontalHeader().setStretchLastSection(True)
        self.table_gaps.setFixedHeight(120)
        self.table_gaps.setStyleSheet(f"""
            QTableWidget {{
                background: rgba(10, 16, 26, 0.6);
                border: 1px solid {C.BORDER_A};
                color: {C.WHITE};
                gridline-color: {C.BORDER_A};
            }}
            QHeaderView::section {{
                background: rgba(0, 212, 255, 0.08);
                color: {C.PRI};
                border: 1px solid {C.BORDER_A};
                padding: 4px;
            }}
        """)
        body_layout.addWidget(self.table_gaps)

    def _build_proposals_panel(self, body_layout: QVBoxLayout) -> None:
        self.table_props = QTableWidget(0, 6, self)
        self.table_props.setHorizontalHeaderLabels(["PROPOSAL ID", "TITLE", "SCOPE", "RISK", "STATE", "AUTHOR"])
        self.table_props.horizontalHeader().setStretchLastSection(True)
        self.table_props.setFixedHeight(130)
        self.table_props.setStyleSheet(f"""
            QTableWidget {{
                background: rgba(10, 16, 26, 0.6);
                border: 1px solid {C.BORDER_A};
                color: {C.WHITE};
                gridline-color: {C.BORDER_A};
            }}
            QHeaderView::section {{
                background: rgba(0, 212, 255, 0.08);
                color: {C.PRI};
                border: 1px solid {C.BORDER_A};
                padding: 4px;
            }}
        """)
        body_layout.addWidget(self.table_props)

    def _build_promotions_panel(self, body_layout: QVBoxLayout) -> None:
        self.table_prom = QTableWidget(0, 6, self)
        self.table_prom.setHorizontalHeaderLabels(["PROMOTION ID", "PROPOSAL ID", "VERSION", "APPROVER", "STATUS", "ACTION"])
        self.table_prom.horizontalHeader().setStretchLastSection(True)
        self.table_prom.setFixedHeight(130)
        self.table_prom.setStyleSheet(f"""
            QTableWidget {{
                background: rgba(10, 16, 26, 0.6);
                border: 1px solid {C.BORDER_A};
                color: {C.WHITE};
                gridline-color: {C.BORDER_A};
            }}
            QHeaderView::section {{
                background: rgba(0, 212, 255, 0.08);
                color: {C.PRI};
                border: 1px solid {C.BORDER_A};
                padding: 4px;
            }}
        """)
        body_layout.addWidget(self.table_prom)

    def _build_logs_panel(self, body_layout: QVBoxLayout) -> None:
        self.text_logs = QTextEdit(self)
        self.text_logs.setReadOnly(True)
        self.text_logs.setFont(px_font(10, mono=True))
        self.text_logs.setFixedHeight(140)
        self.text_logs.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(5, 10, 18, 0.85);
                border: 1px solid {C.BORDER_A};
                color: {C.PRI};
                padding: 6px;
            }}
        """)
        body_layout.addWidget(self.text_logs)

    def refresh_data(self) -> None:
        """Fetch actual backend state from store/bridge and populate UI tables."""
        try:
            from core.self_improvement.storage import SelfImprovementStore
            store = SelfImprovementStore.instance()

            # 1. Gaps
            gaps = store.list_gaps()
            self.table_gaps.setRowCount(len(gaps))
            for i, gap in enumerate(gaps):
                self.table_gaps.setItem(i, 0, QTableWidgetItem(gap.gap_id))
                self.table_gaps.setItem(i, 1, QTableWidgetItem(gap.category))
                self.table_gaps.setItem(i, 2, QTableWidgetItem(gap.description))
                self.table_gaps.setItem(i, 3, QTableWidgetItem(str(gap.frequency)))
                self.table_gaps.setItem(i, 4, QTableWidgetItem(f"{gap.confidence:.2f}"))

            # 2. Proposals
            props = store.list_proposals()
            self.table_props.setRowCount(len(props))
            for i, prop in enumerate(props):
                self.table_props.setItem(i, 0, QTableWidgetItem(prop.proposal_id))
                self.table_props.setItem(i, 1, QTableWidgetItem(prop.title))
                self.table_props.setItem(i, 2, QTableWidgetItem(prop.scope))
                self.table_props.setItem(i, 3, QTableWidgetItem(prop.risk_level))
                self.table_props.setItem(i, 4, QTableWidgetItem(prop.state.value))
                self.table_props.setItem(i, 5, QTableWidgetItem(prop.author_agent_id))

            # 3. Promotions
            proms = store.list_promotions()
            self.table_prom.setRowCount(len(proms))
            for i, prom in enumerate(proms):
                self.table_prom.setItem(i, 0, QTableWidgetItem(prom.promotion_id))
                self.table_prom.setItem(i, 1, QTableWidgetItem(prom.proposal_id))
                self.table_prom.setItem(i, 2, QTableWidgetItem(prom.promoted_version))
                self.table_prom.setItem(i, 3, QTableWidgetItem(prom.approver))
                self.table_prom.setItem(i, 4, QTableWidgetItem(prom.status))

                # Add Rollback Button if active
                if prom.status == "ACTIVE":
                    btn_rb = QPushButton("ROLLBACK")
                    btn_rb.setFont(px_font(9, QFont.Weight.Bold))
                    btn_rb.setStyleSheet(f"""
                        QPushButton {{
                            color: {C.ACC};
                            background: rgba(255, 107, 0, 0.15);
                            border: 1px solid {C.ACC};
                            border-radius: 2px;
                            padding: 2px 6px;
                        }}
                        QPushButton:hover {{
                            background: rgba(255, 107, 0, 0.35);
                        }}
                    """)
                    btn_rb.clicked.connect(lambda _, pid=prom.proposal_id: self._on_rollback(pid))
                    self.table_prom.setCellWidget(i, 5, btn_rb)
                else:
                    self.table_prom.setItem(i, 5, QTableWidgetItem(prom.status))

            # 4. Logs from recent reviews and evaluations
            reviews = list(store._reviews.values())
            evals = list(store._evaluations.values())
            lines = []
            for r in reversed(reviews[-5:]):
                lines.append(f"[{r.review_id}] GOLDFISH REVIEW: {r.verdict} (Findings: {len(r.findings)})")
                for f in r.findings:
                    lines.append(f"   -> [{f.severity}] {f.category}: {f.description}")
            for e in reversed(evals[-5:]):
                lines.append(f"[{e.evaluation_id}] SANDBOX EVALUATION: Passed={e.passed} ({e.tests_passed} passed, {e.tests_failed} failed)")

            self.text_logs.setPlainText("\n".join(lines) if lines else "No evaluation or review logs recorded yet.")

        except Exception as ex:  # noqa: BLE001  # Safe GUI guard against widget refresh errors
            logger.debug("Failed refreshing self-improvement section: %s", ex)

    def _on_rollback(self, proposal_id: str) -> None:
        """Trigger deterministic rollback via coordinator."""
        try:
            from core.self_improvement.coordinator import SelfImprovementCoordinator
            coord = SelfImprovementCoordinator.instance()
            success = coord.rollback(proposal_id)
            self.refresh_data()
            if success:
                logger.info("Rollback executed successfully for proposal '%s'", proposal_id)
        except Exception as ex:  # noqa: BLE001  # Safe GUI guard against user-action exceptions
            logger.error("Rollback failed for proposal '%s': %s", proposal_id, ex)
