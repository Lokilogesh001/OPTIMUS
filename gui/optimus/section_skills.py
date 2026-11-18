"""gui/optimus/section_skills.py — Dedicated Skill Management & Intelligence Section.

Upgraded Milestone 5 Architecture:
- Explicit separation: INSTALLED SKILLS vs. SKILL EXPLORER (Discovered Catalog).
- Native skill library management (`data/skills/installed/`).
- 9-stage pipeline triggers: Import, Validate, Resolve Dependencies, Package, Install, Verify, Register, Enable.
- Interactive controls: Install, Uninstall (with confirmation), Rollback, Export (.zip), Toggle Enable, Audit.
- Deep Inspector: Instructions viewer, Package & Dependency matrix, Security findings, Version & Audit history.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
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

from core.skills.dependency import DependencyResolver
from core.skills.installer import NativeSkillInstaller
from core.skills.lifecycle import SkillLifecycleManager
from core.skills.models import SkillCategory, SkillTrustLevel
from core.skills.registry import SkillRegistry
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C


class SectionSkills(QWidget):
    """Unified Skill Management, Native Library Installer, and Ecosystem Explorer for OPTIMUS."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.registry = SkillRegistry.instance()
        self.lifecycle = SkillLifecycleManager(self.registry)
        self.installer = NativeSkillInstaller(registry=self.registry)
        self.dependency_resolver = DependencyResolver()

        self._current_selected_id: str | None = None
        self._view_mode: str = "installed"  # "installed" or "explorer"

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

        self.lbl_skill_count = QLabel("0 INSTALLED  ·  0 DISCOVERED")
        self.lbl_skill_count.setFont(mono_font(10))
        self.lbl_skill_count.setStyleSheet(
            f"color: {C.PRI_DIM}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 3px; padding: 3px 8px;"
        )
        hdr.addWidget(self.lbl_skill_count)

        self.btn_rescan = QPushButton("RESCAN ECOSYSTEM")
        self.btn_rescan.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_rescan.setStyleSheet(btn_css("primary"))
        self.btn_rescan.clicked.connect(self._on_rescan_clicked)
        hdr.addWidget(self.btn_rescan)

        root_layout.addLayout(hdr)

        # ── 2. Mode Selector Bar ──────────────────────────────────────────────
        mode_bar = QHBoxLayout()
        mode_bar.setSpacing(8)

        self.btn_mode_installed = QPushButton("▣  INSTALLED SKILLS")
        self.btn_mode_installed.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_mode_installed.setStyleSheet(btn_css("primary"))
        self.btn_mode_installed.clicked.connect(lambda: self._set_view_mode("installed"))
        mode_bar.addWidget(self.btn_mode_installed)

        self.btn_mode_explorer = QPushButton("◈  SKILL EXPLORER (CATALOG)")
        self.btn_mode_explorer.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_mode_explorer.setStyleSheet(btn_css("dim"))
        self.btn_mode_explorer.clicked.connect(lambda: self._set_view_mode("explorer"))
        mode_bar.addWidget(self.btn_mode_explorer)

        mode_bar.addSpacing(15)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search skills by name, tag, or description...")
        self.search_input.setFont(px_font(10))
        self.search_input.setStyleSheet(
            f"color: {C.WHITE}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 4px; padding: 6px 10px;"
        )
        self.search_input.textChanged.connect(self._apply_filters)
        mode_bar.addWidget(self.search_input, stretch=2)

        self.category_combo = QComboBox()
        self.category_combo.addItem("ALL CATEGORIES")
        for cat in SkillCategory:
            self.category_combo.addItem(cat.value)
        self.category_combo.setStyleSheet(
            f"color: {C.WHITE}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; padding: 4px 8px;"
        )
        self.category_combo.currentIndexChanged.connect(self._apply_filters)
        mode_bar.addWidget(self.category_combo, stretch=1)

        self.trust_combo = QComboBox()
        self.trust_combo.addItem("ALL TRUST LEVELS")
        for t in SkillTrustLevel:
            self.trust_combo.addItem(t.value)
        self.trust_combo.setStyleSheet(
            f"color: {C.WHITE}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; padding: 4px 8px;"
        )
        self.trust_combo.currentIndexChanged.connect(self._apply_filters)
        mode_bar.addWidget(self.trust_combo, stretch=1)

        root_layout.addLayout(mode_bar)

        # ── 3. Main Splitter ──────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(0, 212, 255, 0.15); width: 2px; }")

        # Left Table: Skills (Installed or Discovered depending on mode)
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
        self.text_instructions.setStyleSheet(
            f"color: {C.WHITE}; background: #020617; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 3px; {scroll_css()}"
        )
        det_lay.addWidget(self.text_instructions, stretch=1)

        # Action Buttons Row
        self.det_btn_row = QHBoxLayout()
        self.det_btn_row.setSpacing(6)

        self.btn_install = QPushButton("INSTALL TO OPTIMUS")
        self.btn_install.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_install.setStyleSheet(btn_css("primary"))
        self.btn_install.clicked.connect(self._on_install_clicked)
        self.det_btn_row.addWidget(self.btn_install)

        self.btn_toggle_enable = QPushButton("TOGGLE ENABLE")
        self.btn_toggle_enable.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_toggle_enable.setStyleSheet(btn_css("dim"))
        self.btn_toggle_enable.clicked.connect(self._on_toggle_enable)
        self.det_btn_row.addWidget(self.btn_toggle_enable)

        self.btn_uninstall = QPushButton("UNINSTALL")
        self.btn_uninstall.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_uninstall.setStyleSheet("QPushButton { color: #ff3366; background: rgba(255, 51, 102, 0.15); border: 1px solid #ff3366; border-radius: 3px; padding: 4px 10px; font-weight: bold; } QPushButton:hover { background: rgba(255, 51, 102, 0.3); }")
        self.btn_uninstall.clicked.connect(self._on_uninstall_clicked)
        self.det_btn_row.addWidget(self.btn_uninstall)

        self.btn_rollback = QPushButton("ROLLBACK")
        self.btn_rollback.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_rollback.setStyleSheet(btn_css("dim"))
        self.btn_rollback.clicked.connect(self._on_rollback_clicked)
        self.det_btn_row.addWidget(self.btn_rollback)

        self.btn_export = QPushButton("EXPORT PKG")
        self.btn_export.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_export.setStyleSheet(btn_css("dim"))
        self.btn_export.clicked.connect(self._on_export_clicked)
        self.det_btn_row.addWidget(self.btn_export)

        self.btn_validate = QPushButton("AUDIT")
        self.btn_validate.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_validate.setStyleSheet(btn_css("dim"))
        self.btn_validate.clicked.connect(self._on_run_audit)
        self.det_btn_row.addWidget(self.btn_validate)

        det_lay.addLayout(self.det_btn_row)
        right_tabs.addTab(tab_details, "INSPECTOR & PROMPT")

        # Tab 2: Package Manifest & Dependencies
        tab_pkg = QWidget()
        pkg_lay = QVBoxLayout(tab_pkg)
        pkg_lay.setContentsMargins(8, 8, 8, 8)
        pkg_lay.setSpacing(6)

        self.lbl_pkg_status = QLabel("PACKAGE STATUS: NOT INSTALLED")
        self.lbl_pkg_status.setFont(px_font(11, QFont.Weight.Bold))
        self.lbl_pkg_status.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        pkg_lay.addWidget(self.lbl_pkg_status)

        self.text_pkg_manifest = QTextEdit()
        self.text_pkg_manifest.setReadOnly(True)
        self.text_pkg_manifest.setFont(mono_font(10))
        self.text_pkg_manifest.setStyleSheet(
            f"color: {C.TEXT}; background: #020617; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 3px; {scroll_css()}"
        )
        pkg_lay.addWidget(self.text_pkg_manifest, stretch=1)

        right_tabs.addTab(tab_pkg, "PACKAGE & DEPENDENCIES")

        # Tab 3: Security & Trust Report
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
        self.text_sec_report.setStyleSheet(
            f"color: {C.TEXT}; background: #020617; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 3px; {scroll_css()}"
        )
        sec_lay.addWidget(self.text_sec_report, stretch=1)

        right_tabs.addTab(tab_sec, "SECURITY & AUDIT")

        # Tab 4: Execution & Install History
        tab_hist = QWidget()
        hist_lay = QVBoxLayout(tab_hist)
        hist_lay.setContentsMargins(8, 8, 8, 8)
        hist_lay.setSpacing(6)

        self.table_history = QTableWidget(0, 4)
        self.table_history.setHorizontalHeaderLabels(["TIME", "ACTION / CALLER", "STATUS", "DETAILS"])
        self.table_history.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_history.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_history.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_history.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_history.setFont(mono_font(10))
        self.table_history.setStyleSheet(
            f"color: {C.TEXT}; background: #020617; border: 1px solid {C.BORDER_A}; {scroll_css()}"
        )
        hist_lay.addWidget(self.table_history)

        right_tabs.addTab(tab_hist, "HISTORY & AUDIT")

        splitter.addWidget(right_tabs)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 5)
        root_layout.addWidget(splitter, stretch=1)

        # Initial Rescan or Load
        if not self.registry.list_skills():
            QTimer.singleShot(100, self._deferred_initial_rescan)
        else:
            self.refresh_skills()

    def _deferred_initial_rescan(self) -> None:
        self.lifecycle.rescan()
        self.refresh_skills()

    # ── UI Actions & Mode Switcher ────────────────────────────────────────────

    def _set_view_mode(self, mode: str) -> None:
        self._view_mode = mode
        if mode == "installed":
            self.btn_mode_installed.setStyleSheet(btn_css("primary"))
            self.btn_mode_explorer.setStyleSheet(btn_css("dim"))
            self.table_skills.setHorizontalHeaderLabels(["ID / NAME", "VERSION", "DEPENDENCIES", "TRUST", "STATUS"])
        else:
            self.btn_mode_installed.setStyleSheet(btn_css("dim"))
            self.btn_mode_explorer.setStyleSheet(btn_css("primary"))
            self.table_skills.setHorizontalHeaderLabels(["ID / NAME", "CATEGORY", "SOURCE", "TRUST", "INSTALL STATUS"])

        self.refresh_skills()

    def refresh_skills(self) -> None:
        """Populate the skill table based on current mode & filters."""
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

        all_skills = self.registry.list_skills(category=selected_cat, trust_level=selected_trust, search_query=q)

        installed_skills = [s for s in all_skills if s.is_installed]
        discovered_skills = [s for s in all_skills if not s.is_installed]

        self.lbl_skill_count.setText(f"{len(installed_skills)} INSTALLED  ·  {len(discovered_skills)} DISCOVERED")

        display_skills = installed_skills if self._view_mode == "installed" else discovered_skills

        self.table_skills.setRowCount(len(display_skills))
        for row, s in enumerate(display_skills):
            id_item = QTableWidgetItem(f"{s.name} ({s.id})")
            id_item.setData(Qt.ItemDataRole.UserRole, s.id)

            if self._view_mode == "installed":
                col1 = QTableWidgetItem(f"v{s.version}")
                deps_summary = f"{len(s.dependencies)} tools" if s.dependencies else "None (Pure)"
                col2 = QTableWidgetItem(deps_summary)
                col3 = QTableWidgetItem(s.trust_level.value)
                col4 = QTableWidgetItem("ENABLED" if s.enabled else "DISABLED")
                col4.setForeground(QColor(C.GREEN if s.enabled else C.TEXT_DIM))
            else:
                col1 = QTableWidgetItem(s.category.value)
                col2 = QTableWidgetItem(s.provenance.upper())
                col3 = QTableWidgetItem(s.trust_level.value)
                col4 = QTableWidgetItem("DISCOVERED")
                col4.setForeground(QColor(C.PRI_DIM))

            if s.trust_level in (SkillTrustLevel.VERIFIED, SkillTrustLevel.TRUSTED):
                col3.setForeground(QColor(C.GREEN))
            elif s.trust_level == SkillTrustLevel.BLOCKED:
                col3.setForeground(QColor("#ff3366"))
            else:
                col3.setForeground(QColor(C.ACC))

            self.table_skills.setItem(row, 0, id_item)
            self.table_skills.setItem(row, 1, col1)
            self.table_skills.setItem(row, 2, col2)
            self.table_skills.setItem(row, 3, col3)
            self.table_skills.setItem(row, 4, col4)

    def _apply_filters(self) -> None:
        self.refresh_skills()

    def _on_skill_selected(self) -> None:
        """Update detail inspector, dependencies, and audit tabs when a row is selected."""
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

        # Update Buttons based on installed status
        if manifest.is_installed:
            self.btn_install.setVisible(False)
            self.btn_toggle_enable.setVisible(True)
            self.btn_uninstall.setVisible(True)
            self.btn_rollback.setVisible(True)
            self.btn_export.setVisible(True)
            self.btn_toggle_enable.setText("DISABLE SKILL" if manifest.enabled else "ENABLE SKILL")
        else:
            self.btn_install.setVisible(True)
            self.btn_toggle_enable.setVisible(False)
            self.btn_uninstall.setVisible(False)
            self.btn_rollback.setVisible(False)
            self.btn_export.setVisible(False)

        install_badge = " [INSTALLED NATIVE]" if manifest.is_installed else " [DISCOVERED]"
        self.lbl_detail_title.setText(f"{manifest.name.upper()}{install_badge}")
        self.lbl_detail_meta.setText(
            f"Provenance: {manifest.provenance.upper()} | Category: {manifest.category.value} | "
            f"Type: {manifest.skill_type.value} | Version: {manifest.version} | "
            f"Executions: {manifest.execution_count}"
        )
        self.text_instructions.setPlainText(manifest.instructions or "No instructions available.")

        # Tab 2: Package Manifest & Dependencies
        pkg_manifest = self.installer.get_installed_package(manifest.id)
        if pkg_manifest:
            self.lbl_pkg_status.setText(f"PACKAGE STATUS: INSTALLED (v{pkg_manifest.version})")
            self.lbl_pkg_status.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
            pkg_lines = [
                f"Package ID: {pkg_manifest.id}",
                f"Version: {pkg_manifest.version}",
                f"Installed Path: {self.installer.installed_dir / pkg_manifest.id / pkg_manifest.version}",
                f"Original Source: {pkg_manifest.original_source}",
                f"Installed At: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(pkg_manifest.installed_at))}",
                f"Integrity Checksum: {pkg_manifest.integrity_checksum}",
                "",
                "── Declared Dependencies ──",
            ]
            if pkg_manifest.dependencies:
                for d in pkg_manifest.dependencies:
                    status_badge = f"[{d.status.value}]"
                    pkg_lines.append(f"• {status_badge} ({d.kind.value}) {d.name} — {d.details}")
            else:
                pkg_lines.append("• Zero dependencies (Hardware-independent instruction skill)")

            pkg_lines.append("")
            pkg_lines.append("── Hardware Requirements ──")
            pkg_lines.append(f"• GPU Required: {pkg_manifest.hardware_requirements.gpu_required}")
            pkg_lines.append(f"• CUDA Required: {pkg_manifest.hardware_requirements.cuda_required}")
            pkg_lines.append(f"• Min RAM: {pkg_manifest.hardware_requirements.min_ram_mb} MB")

            self.text_pkg_manifest.setPlainText("\n".join(pkg_lines))
        else:
            self.lbl_pkg_status.setText("PACKAGE STATUS: NOT INSTALLED IN NATIVE LIBRARY")
            self.lbl_pkg_status.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
            self.text_pkg_manifest.setPlainText(
                f"Skill '{manifest.id}' is discovered from '{manifest.source_path}'.\n\n"
                f"Click 'INSTALL TO OPTIMUS' to validate, resolve dependencies, package, "
                f"and install into the native OPTIMUS skill library (data/skills/)."
            )

        # Tab 3: Security & Trust Report
        report = manifest.security_report
        if report:
            self.lbl_sec_trust.setText(f"TRUST LEVEL: {report.trust_level.value}  (RISK SCORE: {report.risk_score}/10)")
            color = C.GREEN if report.is_safe else "#ff3366"
            self.lbl_sec_trust.setStyleSheet(f"color: {color}; background: transparent;")

            sec_lines = [
                f"Scanner: {report.scanner_name}",
                f"Risk Score: {report.risk_score} / 10.0",
                f"Trust Level: {report.trust_level.value}",
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
            self.text_sec_report.setPlainText("No static security scan on record. Click 'AUDIT'.")

        # Tab 4: History
        exec_history = self.registry.get_execution_history(skill_id=manifest.id, limit=15)
        install_history = self.installer.get_install_history(skill_id=manifest.id, limit=15)

        total_rows = len(exec_history) + len(install_history)
        self.table_history.setRowCount(total_rows)

        r_idx = 0
        for ih in reversed(install_history):
            t_str = time.strftime("%H:%M:%S", time.localtime(ih.timestamp))
            self.table_history.setItem(r_idx, 0, QTableWidgetItem(t_str))
            self.table_history.setItem(r_idx, 1, QTableWidgetItem(f"[PKG] {ih.action.value}"))
            st_item = QTableWidgetItem(ih.status.upper())
            st_item.setForeground(QColor(C.GREEN if ih.status == "success" else "#ff3366"))
            self.table_history.setItem(r_idx, 2, st_item)
            self.table_history.setItem(r_idx, 3, QTableWidgetItem(ih.details[:70]))
            r_idx += 1

        for eh in reversed(exec_history):
            t_str = time.strftime("%H:%M:%S", time.localtime(eh.timestamp))
            self.table_history.setItem(r_idx, 0, QTableWidgetItem(t_str))
            self.table_history.setItem(r_idx, 1, QTableWidgetItem(f"[RUN] {eh.caller_agent}"))
            st_item = QTableWidgetItem(eh.status.upper())
            st_item.setForeground(QColor(C.GREEN if eh.status == "success" else "#ff3366"))
            self.table_history.setItem(r_idx, 2, st_item)
            self.table_history.setItem(r_idx, 3, QTableWidgetItem(f"{eh.duration_ms:.1f}ms — {eh.summary[:60]}"))
            r_idx += 1

    # ── Installation & Lifecycle Handlers ────────────────────────────────────

    def _on_install_clicked(self) -> None:
        """Trigger the 9-stage native installation pipeline for the selected discovered skill."""
        if not self._current_selected_id:
            return

        manifest = self.registry.get(self._current_selected_id)
        if not manifest:
            return

        source = manifest.source_path or manifest.id
        res = self.installer.install_skill(
            source=source,
            skill_id_override=manifest.id,
            auto_enable=True,
            origin_provenance=manifest.provenance,
        )

        if res.success:
            QMessageBox.information(
                self,
                "Installation Successful",
                f"Skill '{res.skill_id}' (v{res.version}) successfully installed into OPTIMUS native library.\n\n"
                f"Installed Path:\n{res.installed_path}\n\n"
                f"The skill is now completely independent of its original external source.",
            )
            self._set_view_mode("installed")
        else:
            err_msg = "\n".join(res.errors[:4])
            QMessageBox.critical(
                self,
                "Installation Blocked",
                f"Failed to install skill '{manifest.id}':\n\n{res.message}\n\n{err_msg}",
            )
            self.refresh_skills()
            self._on_skill_selected()

    def _on_uninstall_clicked(self) -> None:
        """Uninstall an installed native skill with confirmation."""
        if not self._current_selected_id:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Uninstall",
            f"Are you sure you want to uninstall '{self._current_selected_id}' from the OPTIMUS native library?\n\n"
            "Only OPTIMUS-managed files in data/skills/ will be deleted. Original external sources will not be touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            ok, msg = self.installer.uninstall_skill(self._current_selected_id)
            if ok:
                QMessageBox.information(self, "Uninstalled", msg)
                self.refresh_skills()
                self._current_selected_id = None
                self.lbl_detail_title.setText("SELECT A SKILL")
                self.text_instructions.clear()
            else:
                QMessageBox.warning(self, "Uninstall Failed", msg)

    def _on_rollback_clicked(self) -> None:
        """Roll back an installed skill to its previous backup version."""
        if not self._current_selected_id:
            return

        ok, msg = self.installer.rollback_skill(self._current_selected_id)
        if ok:
            QMessageBox.information(self, "Rollback Complete", msg)
            self.refresh_skills()
            self._on_skill_selected()
        else:
            QMessageBox.warning(self, "Rollback Failed", msg)

    def _on_export_clicked(self) -> None:
        """Export an installed native skill to a zip archive."""
        if not self._current_selected_id:
            return

        default_name = f"{self._current_selected_id}.skill.zip"
        target_path, _ = QFileDialog.getSaveFileName(self, "Export Skill Package", default_name, "Zip Archives (*.zip)")
        if target_path:
            ok, msg = self.installer.export_skill(self._current_selected_id, target_path)
            if ok:
                QMessageBox.information(self, "Export Successful", msg)
            else:
                QMessageBox.warning(self, "Export Failed", msg)

    def _on_toggle_enable(self) -> None:
        """Toggle active/disabled status for selected skill."""
        if not self._current_selected_id:
            return
        manifest = self.registry.get(self._current_selected_id)
        if not manifest:
            return

        new_state = not manifest.enabled
        if new_state and manifest.trust_level == SkillTrustLevel.BLOCKED:
            QMessageBox.critical(
                self,
                "Action Blocked",
                f"Skill '{manifest.id}' is BLOCKED by security policy and cannot be enabled.",
            )
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
            QMessageBox.information(
                self,
                "Audit Complete",
                f"Skill '{self._current_selected_id}' passed security validation.",
            )
        else:
            QMessageBox.warning(
                self,
                "Security Concerns",
                "Security issues detected:\n" + "\n".join(errors[:5]),
            )

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
            f"Security Blocked: {summary['blocked_by_security']}",
        )
