"""gui/optimus/section_applications.py — Applications & Autonomous Desktop Control Section.

Presents discovered applications, active running instances, available adapters,
application capabilities, learned workflows, and interactive approval verification.
Adheres strictly to the refined de-slopped command center design tokens.
"""

from __future__ import annotations
import threading
import logging
import time
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.application_intelligence.adapters import get_adapter
from core.application_intelligence.capabilities import STANDARD_CAPABILITIES
from core.application_intelligence.frameworks import (
    BaseExecutionProvider,
    OmniParserBenchmark,
    ProviderHealthStatus,
    ProviderRegistry,
)
from core.application_intelligence.learning_engine import ApplicationLearningEngine
from core.application_intelligence.registry import ApplicationRegistry
from core.application_intelligence.types import LearnedWorkflow
from core.application_intelligence.workflow_store import WorkflowStore
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, px_font
from gui.theme import C
from gui.widgets import HoloPanel

logger = logging.getLogger("optimus.gui.section_applications")


class SectionApplications(QWidget):
    """Command Center workspace section for Universal Application Intelligence."""

    _scan_completed = pyqtSignal()

    def __init__(self, bridge: OptimusServiceBridge | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge or OptimusServiceBridge.instance()
        self.registry = ApplicationRegistry.instance()
        self.workflow_store = WorkflowStore.instance()
        self.learning_engine = ApplicationLearningEngine(workflow_store=self.workflow_store)
        self._last_apps_sig: tuple | None = None
        self._last_running_sig: tuple | None = None
        self._is_scanning: bool = False
        self._is_closing: bool = False
        self._scan_completed.connect(self._render_current_tab)

        self.setStyleSheet(f"background: {C.BG};")
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(12, 12, 12, 12)
        root_lay.setSpacing(10)

        # ── Header Bar ────────────────────────────────────────────────────────
        hdr_box = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        title = QLabel("UNIVERSAL APPLICATION INTELLIGENCE")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=1.5))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        title_box.addWidget(title)

        sub = QLabel("AUTONOMOUS COMPUTER OPERATION · DISCOVERY · ADAPTERS · LEARNED WORKFLOWS")
        sub.setFont(px_font(9, QFont.Weight.DemiBold, spacing=1.0))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        title_box.addWidget(sub)

        hdr_box.addLayout(title_box)
        hdr_box.addStretch()

        self.btn_refresh = QPushButton("REFRESH INVENTORY")
        self.btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_refresh.setStyleSheet(btn_css("secondary"))
        self.btn_refresh.setFixedHeight(28)
        self.btn_refresh.clicked.connect(lambda: self._refresh_all(force=True))
        hdr_box.addWidget(self.btn_refresh)

        self.btn_learn = QPushButton("+ LEARN UNFAMILIAR APP")
        self.btn_learn.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_learn.setStyleSheet(btn_css("primary"))
        self.btn_learn.setFixedHeight(28)
        self.btn_learn.clicked.connect(self._open_learning_dialog)
        hdr_box.addWidget(self.btn_learn)

        root_lay.addLayout(hdr_box)

        # ── Subnavigation Tabs ────────────────────────────────────────────────
        subnav_box = QHBoxLayout()
        subnav_box.setSpacing(6)
        self.sub_buttons: list[QPushButton] = []
        tabs = ["APPLICATIONS", "RUNNING INSTANCES", "ADAPTERS", "LEARNED SKILLS", "FRAMEWORK PROVIDERS"]
        for idx, tab_name in enumerate(tabs):
            btn = QPushButton(tab_name)
            btn.setFont(px_font(9, QFont.Weight.DemiBold))
            btn.setFixedHeight(26)
            btn.setStyleSheet(self._subnav_style(idx == 0))
            btn.clicked.connect(lambda checked=False, i=idx: self._switch_tab(i))
            subnav_box.addWidget(btn)
            self.sub_buttons.append(btn)

        subnav_box.addStretch()
        root_lay.addLayout(subnav_box)

        # ── Stacked Views ─────────────────────────────────────────────────────
        self.stack = QStackedWidget(self)
        root_lay.addWidget(self.stack, stretch=1)

        self._init_apps_view()          # 0
        self._init_running_view()       # 1
        self._init_adapters_view()      # 2
        self._init_skills_view()        # 3
        self._init_frameworks_view()    # 4

        self._refresh_all()

    def _subnav_style(self, active: bool) -> str:
        if active:
            return f"color: {C.WHITE}; background: {C.PANEL2}; border: 1px solid {C.PRI}; border-radius: 4px; padding: 0 12px;"
        return f"color: {C.TEXT_DIM}; background: transparent; border: 1px solid {C.BORDER}; border-radius: 4px; padding: 0 12px;"

    def _switch_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.sub_buttons):
            btn.setStyleSheet(self._subnav_style(i == index))
        if index == 0:
            self._refresh_apps_table()
        elif index == 1:
            self._refresh_running_table()
        elif index == 2:
            self._refresh_adapters_table()
        elif index == 3:
            self._refresh_skills_table()
        elif index == 4:
            self._refresh_frameworks_table()

    def _style_table(self, tbl: QTableWidget) -> None:
        tbl.setStyleSheet(f"""
            QTableWidget {{
                background-color: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                gridline-color: {C.BORDER};
                selection-background-color: {C.PANEL2};
                selection-color: {C.WHITE};
            }}
            QHeaderView::section {{
                background-color: {C.PANEL_HEAD};
                color: {C.TEXT_DIM};
                font-size: 8pt;
                font-weight: bold;
                border: none;
                border-bottom: 1px solid {C.BORDER};
                padding: 4px 8px;
            }}
        """)
        tbl.verticalHeader().setVisible(False)
        tbl.setShowGrid(False)
        tbl.setAlternatingRowColors(False)

    # ── View 0: Discovered Applications ───────────────────────────────────────
    def _init_apps_view(self) -> None:
        panel = HoloPanel("DISCOVERED WINDOWS APPLICATIONS")
        self.tbl_apps = QTableWidget(0, 6)
        self.tbl_apps.setHorizontalHeaderLabels(["APP NAME", "APP ID", "CATEGORY", "STATUS", "INTEGRITY", "ACTIONS"])
        self.tbl_apps.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_apps.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_apps.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_apps.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_apps.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_apps.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_apps)
        panel.body.addWidget(self.tbl_apps)
        self.stack.addWidget(panel)

    # ── View 1: Running Instances ─────────────────────────────────────────────
    def _init_running_view(self) -> None:
        panel = HoloPanel("ACTIVE RUNNING APPLICATION INSTANCES")
        self.tbl_running = QTableWidget(0, 5)
        self.tbl_running.setHorizontalHeaderLabels(["APPLICATION", "PID", "MAIN HWND", "INTEGRITY", "ACTION"])
        self.tbl_running.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_running.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_running.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_running.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_running.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_running)
        panel.body.addWidget(self.tbl_running)
        self.stack.addWidget(panel)

    # ── View 2: Adapters ──────────────────────────────────────────────────────
    def _init_adapters_view(self) -> None:
        panel = HoloPanel("APPLICATION ADAPTERS & HEALTH")
        self.tbl_adapters = QTableWidget(0, 4)
        self.tbl_adapters.setHorizontalHeaderLabels(["ADAPTER NAME", "TARGET APP", "HEALTH / STATUS", "ACTIONS"])
        self.tbl_adapters.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_adapters.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_adapters.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_adapters.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._style_table(self.tbl_adapters)
        panel.body.addWidget(self.tbl_adapters)
        self.stack.addWidget(panel)

    # ── View 3: Learned Skills ────────────────────────────────────────────────
    def _init_skills_view(self) -> None:
        panel = HoloPanel("AUTONOMOUSLY LEARNED APPLICATION WORKFLOWS")
        self.tbl_skills = QTableWidget(0, 5)
        self.tbl_skills.setHorizontalHeaderLabels(["WORKFLOW ID", "TARGET APP", "GOAL", "TRUST LEVEL", "ACTIONS"])
        self.tbl_skills.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_skills.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_skills.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl_skills.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_skills.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._style_table(self.tbl_skills)
        panel.body.addWidget(self.tbl_skills)
        self.stack.addWidget(panel)

    # ── View 4: Framework Providers ───────────────────────────────────────────
    def _init_frameworks_view(self) -> None:
        panel = HoloPanel("EXECUTION FRAMEWORK PROVIDERS & CIRCUIT BREAKERS")
        self.tbl_frameworks = QTableWidget(0, 6)
        self.tbl_frameworks.setHorizontalHeaderLabels([
            "PROVIDER NAME",
            "TYPE / DOMAIN",
            "HEALTH STATUS",
            "CIRCUIT BREAKER",
            "HARDWARE / BENCHMARK",
            "ACTIONS",
        ])
        self.tbl_frameworks.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_frameworks.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_frameworks.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_frameworks.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_frameworks.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl_frameworks.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._style_table(self.tbl_frameworks)
        panel.body.addWidget(self.tbl_frameworks)
        self.stack.addWidget(panel)

    # ── Refresh Logic ─────────────────────────────────────────────────────────
    @pyqtSlot()
    def _render_current_tab(self) -> None:
        """Render active tab from in-memory cache with zero main thread blocking."""
        if self._is_closing:
            return
        curr = self.stack.currentIndex()
        if curr == 0:
            self._refresh_apps_table()
        elif curr == 1:
            self._refresh_running_table()
        elif curr == 2:
            self._refresh_adapters_table()
        elif curr == 3:
            self._refresh_skills_table()
        elif curr == 4:
            self._refresh_frameworks_table()
        else:
            self._refresh_apps_table()

    def _refresh_all(self, force: bool = False) -> None:
        """Non-blocking refresh: renders cached data immediately and updates in background."""
        if self._is_closing:
            return
        self._render_current_tab()

        now = time.time()
        last_refresh = getattr(self.registry, "_last_static_refresh", 0.0)
        needs_refresh = force or (now - last_refresh > 60.0) or not getattr(self.registry, "_apps", None)

        if needs_refresh and not self._is_scanning:
            self._is_scanning = True

            def _bg_scan():
                try:
                    self.registry.refresh(force=force)
                    if not self._is_closing:
                        self._scan_completed.emit()
                except Exception as exc:
                    logger.debug("Background application registry refresh error: %s", exc)
                finally:
                    self._is_scanning = False

            threading.Thread(target=_bg_scan, daemon=True, name="AppRegistryBackgroundScanner").start()

    def closeEvent(self, event) -> None:
        """Mark closing so background scan callbacks do not touch destroyed widgets."""
        self._is_closing = True
        super().closeEvent(event)

    def refresh_all(self, force: bool = False) -> None:
        """Public hook called by tab selector and refresh button."""
        self._refresh_all(force=force)

    def _on_launch_app_clicked(self) -> None:
        sender = self.sender()
        if not sender:
            return
        app_name = sender.property("app_name")
        if app_name:
            self._launch_app(str(app_name))

    def _on_learn_app_clicked(self) -> None:
        sender = self.sender()
        if not sender:
            return
        app_name = sender.property("app_name")
        if app_name:
            self._learn_app(str(app_name))

    def _refresh_apps_table(self) -> None:
        all_apps = self.registry.list_all_apps()
        # Sort running apps first, then alphabetically by name
        sorted_apps = sorted(all_apps, key=lambda a: (not a.is_running, a.name.lower()))
        # Cap batch to top 64 to guarantee instantaneous UI responsiveness (<20ms)
        apps = sorted_apps[:64]
        sig = tuple((a.name, a.app_id, a.is_running, a.integrity_level) for a in apps)
        if sig == self._last_apps_sig:
            return
        self._last_apps_sig = sig

        self.tbl_apps.setUpdatesEnabled(False)
        try:
            curr_rows = self.tbl_apps.rowCount()
            target_rows = len(apps)
            while curr_rows < target_rows:
                self.tbl_apps.insertRow(curr_rows)
                for c in range(5):
                    self.tbl_apps.setItem(curr_rows, c, QTableWidgetItem())

                act_w = QWidget()
                act_box = QHBoxLayout(act_w)
                act_box.setContentsMargins(4, 2, 4, 2)
                act_box.setSpacing(6)

                btn_launch = QPushButton("LAUNCH")
                btn_launch.setObjectName("btn_launch")
                btn_launch.setFont(px_font(8, QFont.Weight.Bold))
                btn_launch.setFixedHeight(24)
                btn_launch.clicked.connect(self._on_launch_app_clicked)
                act_box.addWidget(btn_launch)

                btn_learn = QPushButton("LEARN")
                btn_learn.setObjectName("btn_learn")
                btn_learn.setFont(px_font(8))
                btn_learn.setStyleSheet(btn_css("secondary"))
                btn_learn.setFixedHeight(24)
                btn_learn.clicked.connect(self._on_learn_app_clicked)
                act_box.addWidget(btn_learn)
                act_box.addStretch()

                self.tbl_apps.setCellWidget(curr_rows, 5, act_w)
                curr_rows += 1

            while curr_rows > target_rows:
                self.tbl_apps.removeRow(curr_rows - 1)
                curr_rows -= 1

            for row, app in enumerate(apps):
                it0 = self.tbl_apps.item(row, 0)
                if it0:
                    it0.setText(app.name)
                it1 = self.tbl_apps.item(row, 1)
                if it1:
                    it1.setText(app.app_id)
                it2 = self.tbl_apps.item(row, 2)
                if it2:
                    it2.setText(app.category.value)

                status_text = "RUNNING" if app.is_running else "INSTALLED"
                it3 = self.tbl_apps.item(row, 3)
                if it3:
                    it3.setText(status_text)
                    it3.setForeground(QColor(C.GREEN) if app.is_running else QColor(C.TEXT_DIM))

                it4 = self.tbl_apps.item(row, 4)
                if it4:
                    it4.setText(app.integrity_level)

                act_w = self.tbl_apps.cellWidget(row, 5)
                if act_w:
                    btn_launch = act_w.findChild(QPushButton, "btn_launch")
                    if btn_launch:
                        btn_launch.setText("ACTIVATE" if app.is_running else "LAUNCH")
                        btn_launch.setStyleSheet(btn_css("primary" if not app.is_running else "secondary"))
                        btn_launch.setProperty("app_name", app.name)
                    btn_learn = act_w.findChild(QPushButton, "btn_learn")
                    if btn_learn:
                        btn_learn.setProperty("app_name", app.name)
        finally:
            self.tbl_apps.setUpdatesEnabled(True)

    def _refresh_running_table(self) -> None:
        running = self.registry.list_running_apps()
        sig = tuple((a.name, a.pids[0] if a.pids else 0, a.main_hwnd or 0, a.integrity_level) for a in running)
        if sig == self._last_running_sig:
            return
        self._last_running_sig = sig

        curr_rows = self.tbl_running.rowCount()
        target_rows = len(running)
        while curr_rows < target_rows:
            self.tbl_running.insertRow(curr_rows)
            for c in range(4):
                self.tbl_running.setItem(curr_rows, c, QTableWidgetItem())

            act_w = QWidget()
            act_box = QHBoxLayout(act_w)
            act_box.setContentsMargins(4, 2, 4, 2)
            btn_focus = QPushButton("FOCUS")
            btn_focus.setObjectName("btn_focus")
            btn_focus.setFont(px_font(8, QFont.Weight.Bold))
            btn_focus.setStyleSheet(btn_css("primary"))
            btn_focus.setFixedHeight(24)
            btn_focus.clicked.connect(self._on_launch_app_clicked)
            act_box.addWidget(btn_focus)
            act_box.addStretch()
            self.tbl_running.setCellWidget(curr_rows, 4, act_w)
            curr_rows += 1

        while curr_rows > target_rows:
            self.tbl_running.removeRow(curr_rows - 1)
            curr_rows -= 1

        for row, app in enumerate(running):
            it0 = self.tbl_running.item(row, 0)
            if it0:
                it0.setText(app.name)
            it1 = self.tbl_running.item(row, 1)
            if it1:
                it1.setText(str(app.pids[0]) if app.pids else "-")
            it2 = self.tbl_running.item(row, 2)
            if it2:
                it2.setText(str(app.main_hwnd) if app.main_hwnd else "-")
            it3 = self.tbl_running.item(row, 3)
            if it3:
                it3.setText(app.integrity_level)

            act_w = self.tbl_running.cellWidget(row, 4)
            if act_w:
                btn_focus = act_w.findChild(QPushButton, "btn_focus")
                if btn_focus:
                    btn_focus.setProperty("app_name", app.name)

    def _refresh_adapters_table(self) -> None:
        adapter_names = ["whatsapp", "browser", "document", "devtools"]
        self.tbl_adapters.setRowCount(0)
        for row, name in enumerate(adapter_names):
            adapter = get_adapter(name)
            is_disc = adapter.discover() if adapter else False

            self.tbl_adapters.insertRow(row)
            self.tbl_adapters.setItem(row, 0, QTableWidgetItem(name.capitalize()))
            self.tbl_adapters.setItem(row, 1, QTableWidgetItem(name))

            st_item = QTableWidgetItem("AVAILABLE" if is_disc else "NOT DETECTED")
            st_item.setForeground(QColor(C.GREEN) if is_disc else QColor(C.TEXT_DIM))
            self.tbl_adapters.setItem(row, 2, st_item)

            act_w = QWidget()
            act_box = QHBoxLayout(act_w)
            act_box.setContentsMargins(4, 2, 4, 2)
            btn_test = QPushButton("TEST ADAPTER")
            btn_test.setFont(px_font(8))
            btn_test.setStyleSheet(btn_css("secondary"))
            btn_test.setFixedHeight(24)
            btn_test.clicked.connect(lambda checked=False, a_name=name: self._test_adapter(a_name))
            act_box.addWidget(btn_test)
            act_box.addStretch()
            self.tbl_adapters.setCellWidget(row, 3, act_w)

    def _refresh_skills_table(self) -> None:
        workflows = self.workflow_store.list_workflows()
        self.tbl_skills.setRowCount(0)
        for row, wf in enumerate(workflows):
            self.tbl_skills.insertRow(row)
            self.tbl_skills.setItem(row, 0, QTableWidgetItem(wf.workflow_id))
            self.tbl_skills.setItem(row, 1, QTableWidgetItem(wf.target_app))
            self.tbl_skills.setItem(row, 2, QTableWidgetItem(wf.goal))

            trust_item = QTableWidgetItem(wf.validation_state)
            trust_item.setForeground(QColor(C.ACC) if wf.validation_state == "UNVERIFIED" else QColor(C.GREEN))
            self.tbl_skills.setItem(row, 3, trust_item)

            act_w = QWidget()
            act_box = QHBoxLayout(act_w)
            act_box.setContentsMargins(4, 2, 4, 2)
            btn_view = QPushButton("DETAILS")
            btn_view.setFont(px_font(8))
            btn_view.setStyleSheet(btn_css("secondary"))
            btn_view.setFixedHeight(24)
            btn_view.clicked.connect(lambda checked=False, w_id=wf.workflow_id: self._show_skill_details(w_id))
            act_box.addWidget(btn_view)
            act_box.addStretch()
            self.tbl_skills.setCellWidget(row, 4, act_w)

    def _launch_app(self, app_name: str) -> None:
        res = self.registry.launch_or_activate(app_name)
        if res.get("success"):
            QMessageBox.information(self, "Application Control", res.get("message", "Success"))
            self._refresh_all()
        else:
            QMessageBox.warning(self, "Application Control", res.get("error", "Launch failed"))

    def _learn_app(self, app_name: str) -> None:
        explore_data = self.learning_engine.passive_explore_app(app_name)
        cnt = explore_data.get("control_count", 0)
        QMessageBox.information(
            self,
            "Autonomous Learning: Passive Exploration",
            f"Passive Zero-Click inspection of '{app_name}' completed.\nDiscovered {cnt} accessibility controls.\nMode: Safe Read-Only.",
        )
        self._refresh_all()

    def _test_adapter(self, adapter_name: str) -> None:
        adapter = get_adapter(adapter_name)
        if not adapter:
            QMessageBox.warning(self, "Adapter Test", f"No adapter found for '{adapter_name}'")
            return
        disc = adapter.discover()
        QMessageBox.information(
            self,
            "Adapter Health Test",
            f"Adapter '{adapter_name.capitalize()}':\nDiscovered: {disc}\nStatus: {'OPERATIONAL' if disc else 'DISCONNECTED'}",
        )

    def _show_skill_details(self, workflow_id: str) -> None:
        wf = self.workflow_store.get_workflow(workflow_id)
        if not wf:
            return
        steps_str = "\n".join(f"• Step {s.get('step')}: {s.get('action')} on '{s.get('target_element')}'" for s in wf.action_sequence)
        QMessageBox.information(
            self,
            f"Workflow: {wf.workflow_id}",
            f"Target: {wf.target_app}\nGoal: {wf.goal}\nValidation: {wf.validation_state}\n\nSteps:\n{steps_str}",
        )

    def _refresh_frameworks_table(self) -> None:
        reg = ProviderRegistry.instance()
        providers = reg.list_providers()
        self.tbl_frameworks.setRowCount(0)

        domain_map = {
            "ufo_desktop": "Windows WinCOM & AppAgent (Word/Excel/Settings)",
            "ufo_desktop_agent": "Full Microsoft UFO² Multi-App Agent (HostAgent/AppAgents/FastMCP)",
            "pywinauto_uia": "Native UIA Pattern Automation (Desktop GUI)",
            "playwright_browser": "Headless Browser Automation (DOM/Locators)",
            "stagehand_intelligence": "Browser Intelligence (Natural Language/Extract)",
            "omniparser_grounding": "Visual Grounding & Icon Parsing (Screen-aware)",
        }

        for row, prov in enumerate(providers):
            self.tbl_frameworks.insertRow(row)
            self.tbl_frameworks.setItem(row, 0, QTableWidgetItem(prov.name))
            self.tbl_frameworks.setItem(row, 1, QTableWidgetItem(domain_map.get(prov.name, "Desktop Automation")))

            # Health status with color
            status_name = prov.health_status.value.upper()
            avg_lat = prov.get_average_latency_ms()
            lat_str = f" ({avg_lat:.0f}ms)" if avg_lat > 0 else ""
            st_item = QTableWidgetItem(f"{status_name}{lat_str}")
            if prov.health_status == ProviderHealthStatus.AVAILABLE:
                st_item.setForeground(QColor(C.GREEN))
            elif prov.health_status in (ProviderHealthStatus.DEGRADED, ProviderHealthStatus.PROBING):
                st_item.setForeground(QColor(C.ACC))
            else:
                st_item.setForeground(QColor("#EF4444"))
            self.tbl_frameworks.setItem(row, 2, st_item)

            # Circuit breaker state
            if prov.health_status == ProviderHealthStatus.AVAILABLE:
                cb_str = "CLOSED (0 fails)"
            elif prov.health_status == ProviderHealthStatus.UNAVAILABLE:
                cb_str = f"OPEN ({prov.consecutive_failures} fails · cooldown)"
            elif prov.health_status == ProviderHealthStatus.PROBING:
                cb_str = "HALF_OPEN (probing)"
            else:
                cb_str = f"DEGRADED ({prov.consecutive_failures} fails)"
            self.tbl_frameworks.setItem(row, 3, QTableWidgetItem(cb_str))

            # Hardware / Capability column
            if prov.name == "omniparser_grounding":
                hw = OmniParserBenchmark.inspect_hardware_environment()
                hw_desc = f"{hw.get('device_name', 'GPU')} · Free: {hw.get('free_vram_mb', 0):.0f}MB · Active: {'GPU' if hw.get('meets_3gb_threshold') else 'CPU/ONNX'}"
            elif prov.name == "playwright_browser":
                hw_desc = f"Headless: True · Desktop Lock: {prov.requires_desktop_lock} · Streamed Downloads: SHA-256"
            elif prov.name == "ufo_desktop":
                hw_desc = f"WinCOM: Active · Desktop Lock: {prov.requires_desktop_lock} · AppAgent: ms-settings/explorer"
            elif prov.name == "ufo_desktop_agent":
                hw_desc = f"UFO HostAgent/AppAgents · 8 FastMCP Servers · Desktop Lock: {prov.requires_desktop_lock} · Gateway Guarded"
            elif prov.name == "pywinauto_uia":
                hw_desc = f"COM Apartment: STA/MTA Safe · UIA Patterns: Active · Timeout: 5.0s"
            elif prov.name == "stagehand_intelligence":
                hw_desc = f"DOM Sanitizer: Active · Untrusted Output Wrapping: Active"
            else:
                hw_desc = f"Desktop Lock: {prov.requires_desktop_lock} · Successes: {prov.success_count}"
            self.tbl_frameworks.setItem(row, 4, QTableWidgetItem(hw_desc))

            # Actions widget
            act_w = QWidget()
            act_box = QHBoxLayout(act_w)
            act_box.setContentsMargins(4, 2, 4, 2)
            act_box.setSpacing(6)

            btn_probe = QPushButton("PROBE")
            btn_probe.setFont(px_font(8, QFont.Weight.Bold))
            btn_probe.setStyleSheet(btn_css("secondary"))
            btn_probe.setFixedHeight(24)
            btn_probe.clicked.connect(lambda checked=False, p_name=prov.name: self._probe_provider(p_name))
            act_box.addWidget(btn_probe)

            btn_bench = QPushButton("BENCHMARK")
            btn_bench.setFont(px_font(8))
            btn_bench.setStyleSheet(btn_css("primary" if prov.name == "omniparser_grounding" else "secondary"))
            btn_bench.setFixedHeight(24)
            btn_bench.clicked.connect(lambda checked=False, p_name=prov.name: self._benchmark_provider(p_name))
            act_box.addWidget(btn_bench)

            act_box.addStretch()
            self.tbl_frameworks.setCellWidget(row, 5, act_w)

    def _probe_provider(self, provider_name: str) -> None:
        reg = ProviderRegistry.instance()
        prov = reg.get_provider(provider_name)
        if not prov:
            QMessageBox.warning(self, "Provider Probe", f"Provider '{provider_name}' not found.")
            return

        is_healthy = prov.health_probe()
        self._refresh_frameworks_table()
        QMessageBox.information(
            self,
            "Provider Health Probe",
            f"Provider: {prov.name}\n"
            f"Result: {'PASS (Healthy)' if is_healthy else 'FAIL (Unhealthy)'}\n"
            f"Current Status: {prov.health_status.value.upper()}\n"
            f"Total Successes: {prov.success_count}\n"
            f"Consecutive Failures: {prov.consecutive_failures}",
        )

    def _benchmark_provider(self, provider_name: str) -> None:
        if provider_name == "omniparser_grounding":
            bench = OmniParserBenchmark.run_benchmark()
            hw = bench.get("hardware", {})
            QMessageBox.information(
                self,
                "OmniParser Hardware Benchmark",
                f"Device: {hw.get('device_name')}\n"
                f"Total VRAM: {hw.get('total_vram_mb', 0):.0f} MB\n"
                f"Free VRAM: {hw.get('free_vram_mb', 0):.0f} MB\n"
                f"Meets 3.0GB Threshold: {hw.get('meets_3gb_threshold')}\n"
                f"Active Grounding Mode: {bench.get('mode')}\n"
                f"Grounding Latency: {bench.get('latency_ms', 0):.1f} ms\n"
                f"Safety Guard: {'RTX 3050 Protected (<3GB diverted to CPU)' if not hw.get('meets_3gb_threshold') else 'Sufficient VRAM'}",
            )
        else:
            reg = ProviderRegistry.instance()
            prov = reg.get_provider(provider_name)
            avg_lat = prov.get_average_latency_ms() if prov else 0.0
            QMessageBox.information(
                self,
                f"Provider Benchmark: {provider_name}",
                f"Provider: {provider_name}\n"
                f"Average Execution Latency: {avg_lat:.1f} ms\n"
                f"Total Operations: {(prov.success_count + prov.consecutive_failures) if prov else 0}\n"
                f"Lock Required: {prov.requires_desktop_lock if prov else 'N/A'}",
            )

    def _open_learning_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Learn Unfamiliar Application")
        dlg.setFixedWidth(440)
        dlg.setStyleSheet(f"background: {C.PANEL}; color: {C.WHITE};")
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(10)

        lbl = QLabel("Enter application name or query to inspect:")
        lbl.setFont(px_font(9))
        vbox.addWidget(lbl)

        txt_app = QLineEdit()
        txt_app.setPlaceholderText("e.g. Notepad, Calculator, WhatsApp, VS Code")
        txt_app.setStyleSheet(f"background: {C.BG}; color: {C.WHITE}; border: 1px solid {C.BORDER}; padding: 6px;")
        vbox.addWidget(txt_app)

        lbl_goal = QLabel("Optional goal to synthesize workflow:")
        lbl_goal.setFont(px_font(9))
        vbox.addWidget(lbl_goal)

        txt_goal = QLineEdit()
        txt_goal.setPlaceholderText("e.g. Export file to PDF, Save project")
        txt_goal.setStyleSheet(f"background: {C.BG}; color: {C.WHITE}; border: 1px solid {C.BORDER}; padding: 6px;")
        vbox.addWidget(txt_goal)

        btn_box = QHBoxLayout()
        btn_cancel = QPushButton("CANCEL")
        btn_cancel.setStyleSheet(btn_css("secondary"))
        btn_cancel.clicked.connect(dlg.reject)
        btn_box.addWidget(btn_cancel)

        btn_run = QPushButton("START LEARNING")
        btn_run.setStyleSheet(btn_css("primary"))
        btn_run.clicked.connect(dlg.accept)
        btn_box.addWidget(btn_run)

        vbox.addLayout(btn_box)

        if dlg.exec():
            target = txt_app.text().strip()
            goal = txt_goal.text().strip()
            if target:
                if goal:
                    wf = self.learning_engine.learn_workflow_for_goal(target, goal)
                    QMessageBox.information(
                        self,
                        "Workflow Synthesized",
                        f"Learned workflow '{wf.workflow_id}' for '{target}' (State: {wf.validation_state}).",
                    )
                else:
                    self._learn_app(target)
                self._refresh_all()
