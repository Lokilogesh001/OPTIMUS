"""gui/optimus/section_skills.py — Dedicated Skill Management & Intelligence Section.

Displays and manages the OPTIMUS skill ecosystem:
- Installed & discovered skills across ECC, gstack, Anti-Slop, Chisle, UI skills, and NVIDIA tools.
- Skill search and filtering by Category, Trust Level, and Provenance.
- Interactive Enable / Disable toggles and security validation scan triggers.
- Detailed manifest inspector with full instructions viewer and provenance metadata.
- Security audit inspector with risk score and detected issues breakdown.
- Execution history and telemetry viewer.
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
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.skills.lifecycle import SkillLifecycleManager
from core.skills.models import SkillCategory, SkillTrustLevel
from core.skills.registry import SkillRegistry
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C


class SectionSkills(QWidget):
    """Unified Skill Management and Ecosystem Explorer for OPTIMUS."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.registry = SkillRegistry.instance()
        self.lifecycle = SkillLifecycleManager(self.registry)
        self._current_selected_id: str | None = None

        self.setStyleSheet(f"background: {C.BG};")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        # ── 1. Header Bar ─────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("SKILL INTELLIGENCE & MANAGEMENT  ·  AGENTIC ECOSYSTEM")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        self.lbl_skill_count = QLabel("0 SKILLS LOADED")
        self.lbl_skill_count.setFont(mono_font(10))
        self.lbl_skill_count.setStyleSheet(f"color: {C.PRI_DIM}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 3px 8px;")
        hdr.addWidget(self.lbl_skill_count)

        self.btn_rescan = QPushButton("RESCAN ECOSYSTEM")
        self.btn_rescan.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_rescan.setStyleSheet(btn_css("primary"))
        self.btn_rescan.clicked.connect(self._on_rescan_clicked)
        hdr.addWidget(self.btn_rescan)

        root_layout.addLayout(hdr)

        # ── 2. Search & Filter Bar ────────────────────────────────────────────
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search skills by name, tag, or description...")
        self.search_input.setFont(px_font(10))
        self.search_input.setStyleSheet(f"color: {C.WHITE}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; border-radius: 4px; padding: 6px 10px;")
        self.search_input.textChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.search_input, stretch=2)

        self.category_combo = QComboBox()
        self.category_combo.addItem("ALL CATEGORIES")
        for cat in SkillCategory:
            self.category_combo.addItem(cat.value)
        self.category_combo.setStyleSheet(f"color: {C.WHITE}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; padding: 4px 8px;")
        self.category_combo.currentIndexChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.category_combo, stretch=1)

        self.trust_combo = QComboBox()
        self.trust_combo.addItem("ALL TRUST LEVELS")
        for t in SkillTrustLevel:
            self.trust_combo.addItem(t.value)
        self.trust_combo.setStyleSheet(f"color: {C.WHITE}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; padding: 4px 8px;")
        self.trust_combo.currentIndexChanged.connect(self._apply_filters)
        filter_bar.addWidget(self.trust_combo, stretch=1)

        root_layout.addLayout(filter_bar)

        # ── 3. Main Splitter ──────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(0, 212, 255, 0.15); width: 2px; }")

        # Left Table: Discovered Skills
        self.table_skills = QTableWidget(0, 5)
        self.table_skills.setHorizontalHeaderLabels(["ID / NAME", "CATEGORY", "SOURCE", "TRUST", "STATUS"])
        self.table_skills.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_skills.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_skills.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_skills.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_skills.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_skills.setFont(mono_font(10))
        self.table_skills.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER_A};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background: {C.BG};
                color: {C.PRI};
                border: none;
                border-bottom: 1px solid {C.BORDER_B};
                padding: 4px;
                font-weight: bold;
            }}
            {scroll_css()}
        """)
        self.table_skills.itemSelectionChanged.connect(self._on_skill_selected)
        splitter.addWidget(self.table_skills)

        # Right Panel: Inspector Tabs
        right_tabs = QTabWidget()
        right_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {C.BORDER_A}; background: {C.PANEL}; border-radius: 4px; }}
            QTabBar::tab {{ color: {C.TEXT_DIM}; background: {C.BG}; border: 1px solid {C.BORDER_A}; padding: 6px 12px; margin-right: 2px; }}
            QTabBar::tab:selected {{ color: {C.PRI}; background: {C.PANEL}; border-bottom: 2px solid {C.PRI}; font-weight: bold; }}
        """)

        # Tab 1: Details & Instructions
        tab_details = QWidget()
        det_lay = QVBoxLayout(tab_details)
        det_lay.setContentsMargins(8, 8, 8, 8)
        det_lay.setSpacing(6)

        self.lbl_detail_title = QLabel("SELECT A SKILL")
        self.lbl_detail_title.setFont(px_font(11, QFont.Weight.Bold))
        self.lbl_detail_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        det_lay.addWidget(self.lbl_detail_title)

        self.lbl_detail_meta = QLabel("Provenance: - | Version: - | Category: -")
        self.lbl_detail_meta.setFont(mono_font(9))
        self.lbl_detail_meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        det_lay.addWidget(self.lbl_detail_meta)

        self.text_instructions = QTextEdit()
        self.text_instructions.setReadOnly(True)
        self.text_instructions.setFont(mono_font(10))
        self.text_instructions.setStyleSheet(f"color: {C.WHITE}; background: #020617; border: 1px solid {C.BORDER_A}; border-radius: 3px; {scroll_css()}")
        det_lay.addWidget(self.text_instructions, stretch=1)

        det_btn_row = QHBoxLayout()
        det_btn_row.setSpacing(6)

        self.btn_toggle_enable = QPushButton("TOGGLE ENABLE")
        self.btn_toggle_enable.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_toggle_enable.setStyleSheet(btn_css("primary"))
        self.btn_toggle_enable.clicked.connect(self._on_toggle_enable)
        det_btn_row.addWidget(self.btn_toggle_enable)

        self.btn_validate = QPushButton("RUN SECURITY AUDIT")
        self.btn_validate.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_validate.setStyleSheet(btn_css("dim"))
        self.btn_validate.clicked.connect(self._on_run_audit)
        det_btn_row.addWidget(self.btn_validate)

        det_lay.addLayout(det_btn_row)
        right_tabs.addTab(tab_details, "INSPECTOR & PROMPT")

        # Tab 2: Security & Trust Report
        tab_sec = QWidget()
        sec_lay = QVBoxLayout(tab_sec)
        sec_lay.setContentsMargins(8, 8, 8, 8)
        sec_lay.setSpacing(6)

        self.lbl_sec_trust = QLabel("TRUST STATUS: UNVERIFIED")
        self.lbl_sec_trust.setFont(px_font(11, QFont.Weight.Bold))
        self.lbl_sec_trust.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        sec_lay.addWidget(self.lbl_sec_trust)

        self.text_sec_report = QTextEdit()
        self.text_sec_report.setReadOnly(True)
        self.text_sec_report.setFont(mono_font(10))
        self.text_sec_report.setStyleSheet(f"color: {C.TEXT}; background: #020617; border: 1px solid {C.BORDER_A}; border-radius: 3px; {scroll_css()}")
        sec_lay.addWidget(self.text_sec_report, stretch=1)

        right_tabs.addTab(tab_sec, "SECURITY & AUDIT")

        # Tab 3: Execution History
        tab_hist = QWidget()
        hist_lay = QVBoxLayout(tab_hist)
        hist_lay.setContentsMargins(8, 8, 8, 8)
        hist_lay.setSpacing(6)

        self.table_history = QTableWidget(0, 4)
        self.table_history.setHorizontalHeaderLabels(["TIME", "AGENT", "STATUS", "DURATION"])
        self.table_history.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_history.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_history.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_history.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_history.setFont(mono_font(10))
        self.table_history.setStyleSheet(f"color: {C.TEXT}; background: #020617; border: 1px solid {C.BORDER_A}; {scroll_css()}")
        hist_lay.addWidget(self.table_history)

        right_tabs.addTab(tab_hist, "USAGE HISTORY")

        splitter.addWidget(right_tabs)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 5)
        root_layout.addWidget(splitter, stretch=1)

        # Initial Rescan or Load (deferred if registry empty so UI construction is instantaneous)
        if not self.registry.list_skills():
            QTimer.singleShot(100, self._deferred_initial_rescan)
        else:
            self.refresh_skills()

    def _deferred_initial_rescan(self) -> None:
        self.lifecycle.rescan()
        self.refresh_skills()

    # ── UI Actions & Event Handlers ──────────────────────────────────────────

    def refresh_skills(self) -> None:
        """Populate the skill table based on current search & filter state."""
        q = self.search_input.text().strip()
        cat_text = self.category_combo.currentText()
        trust_text = self.trust_combo.currentText()

        selected_cat = None
        if cat_text != "ALL CATEGORIES":
            try:
                selected_cat = SkillCategory(cat_text)
            except ValueError:
                pass

        selected_trust = None
        if trust_text != "ALL TRUST LEVELS":
            try:
                selected_trust = SkillTrustLevel(trust_text)
            except ValueError:
                pass

        skills = self.registry.list_skills(
            enabled_only=False,
            category=selected_cat,
            trust_level=selected_trust,
            search_query=q,
        )

        all_skills_count = len(self.registry.list_skills())
        enabled_count = len(self.registry.list_skills(enabled_only=True))
        self.lbl_skill_count.setText(f"{enabled_count}/{all_skills_count} SKILLS ACTIVE")

        self.table_skills.setRowCount(len(skills))
        for row, s in enumerate(skills):
            id_item = QTableWidgetItem(f"{s.name} ({s.id})")
            id_item.setData(Qt.ItemDataRole.UserRole, s.id)

            cat_item = QTableWidgetItem(s.category.value)
            prov_item = QTableWidgetItem(s.provenance.upper())

            trust_item = QTableWidgetItem(s.trust_level.value)
            if s.trust_level in (SkillTrustLevel.VERIFIED, SkillTrustLevel.TRUSTED):
                trust_item.setForeground(QColor(C.GREEN))
            elif s.trust_level == SkillTrustLevel.BLOCKED:
                trust_item.setForeground(QColor("#ff3366"))
            else:
                trust_item.setForeground(QColor(C.ACC))

            status_item = QTableWidgetItem("ENABLED" if s.enabled else "DISABLED")
            status_item.setForeground(QColor(C.GREEN if s.enabled else C.TEXT_DIM))

            self.table_skills.setItem(row, 0, id_item)
            self.table_skills.setItem(row, 1, cat_item)
            self.table_skills.setItem(row, 2, prov_item)
            self.table_skills.setItem(row, 3, trust_item)
            self.table_skills.setItem(row, 4, status_item)

    def _apply_filters(self) -> None:
        self.refresh_skills()

    def _on_skill_selected(self) -> None:
        """Update detail inspector and security tabs when a skill is selected."""
        row = self.table_skills.currentRow()
        if row < 0:
            return

        item = self.table_skills.item(row, 0)
        if not item:
            return

        skill_id = item.data(Qt.ItemDataRole.UserRole)
        manifest = self.registry.get(skill_id)
        if not manifest:
            return

        self._current_selected_id = manifest.id
        self.lbl_detail_title.setText(f"{manifest.name.upper()}  [{manifest.id}]")
        self.lbl_detail_meta.setText(
            f"Provenance: {manifest.provenance.upper()} | Category: {manifest.category.value} | "
            f"Type: {manifest.skill_type.value} | Version: {manifest.version} | "
            f"Executions: {manifest.execution_count}"
        )
        self.text_instructions.setPlainText(manifest.instructions or "No instructions available.")
        self.btn_toggle_enable.setText("DISABLE SKILL" if manifest.enabled else "ENABLE SKILL")

        # Security Report
        report = manifest.security_report
        if report:
            self.lbl_sec_trust.setText(f"TRUST LEVEL: {report.trust_level.value}  ·  RISK SCORE: {report.risk_score:.1f}/10")
            sec_lines = [
                f"=== OPTIMUS Security Assessment: {manifest.id} ===",
                f"Scanner: {report.scanner_name}",
                f"Scanned At: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report.scanned_at))}",
                f"Verdict: {'SAFE FOR EXECUTION' if report.is_safe else 'SECURITY CONCERNS DETECTED'}",
                f"Issues Count: {len(report.issues)}",
                "",
            ]
            for issue in report.issues:
                sec_lines.append(f"• [{issue.severity}] {issue.category}: {issue.description}")
                if issue.line_number:
                    sec_lines.append(f"  Line {issue.line_number}: '{issue.snippet}'")
            self.text_sec_report.setPlainText("\n".join(sec_lines))
        else:
            self.lbl_sec_trust.setText("TRUST LEVEL: UNVERIFIED")
            self.text_sec_report.setPlainText("No static security scan on record. Click 'RUN SECURITY AUDIT'.")

        # Execution History
        history = self.registry.get_execution_history(skill_id=manifest.id, limit=25)
        self.table_history.setRowCount(len(history))
        for h_row, h in enumerate(reversed(history)):
            t_str = time.strftime("%H:%M:%S", time.localtime(h.timestamp))
            self.table_history.setItem(h_row, 0, QTableWidgetItem(t_str))
            self.table_history.setItem(h_row, 1, QTableWidgetItem(h.caller_agent))
            st_item = QTableWidgetItem(h.status.upper())
            st_item.setForeground(QColor(C.GREEN if h.status == "success" else "#ff3366"))
            self.table_history.setItem(h_row, 2, st_item)
            self.table_history.setItem(h_row, 3, QTableWidgetItem(f"{h.duration_ms:.1f}ms — {h.summary[:60]}"))

    def _on_toggle_enable(self) -> None:
        """Toggle active/disabled status for selected skill."""
        if not self._current_selected_id:
            return
        manifest = self.registry.get(self._current_selected_id)
        if not manifest:
            return

        new_state = not manifest.enabled
        if new_state and manifest.trust_level == SkillTrustLevel.BLOCKED:
            QMessageBox.critical(self, "Action Blocked", f"Skill '{manifest.id}' is BLOCKED by security policy and cannot be enabled.")
            return

        self.lifecycle.enable_skill(manifest.id) if new_state else self.lifecycle.disable_skill(manifest.id)
        self.refresh_skills()
        self._on_skill_selected()

    def _on_run_audit(self) -> None:
        """Trigger on-demand static security scan on selected skill."""
        if not self._current_selected_id:
            return
        is_valid, errors = self.lifecycle.validate_skill(self._current_selected_id)
        self.refresh_skills()
        self._on_skill_selected()
        if is_valid:
            QMessageBox.information(self, "Audit Complete", f"Skill '{self._current_selected_id}' passed security validation.")
        else:
            QMessageBox.warning(self, "Security Concerns", "Security issues detected:\n" + "\n".join(errors[:5]))

    def _on_rescan_clicked(self) -> None:
        """Trigger full discovery rescan across all ecosystem roots."""
        summary = self.lifecycle.rescan()
        self.refresh_skills()
        QMessageBox.information(
            self,
            "Rescan Completed",
            f"Discovered {summary['total_discovered']} skills across ecosystem roots.\n"
            f"Newly Registered: {summary['newly_registered']}\n"
            f"Refreshed: {summary['refreshed']}\n"
            f"Security Blocked: {summary['blocked_by_security']}"
        )
