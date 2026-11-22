"""gui/optimus/section_applications.py — Applications & Autonomous Desktop Control Section.

Presents discovered applications, active running instances, available adapters,
application capabilities, learned workflows, and interactive approval verification.
Adheres strictly to the refined de-slopped command center design tokens.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
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

    def __init__(self, bridge: OptimusServiceBridge | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge or OptimusServiceBridge.instance()
        self.registry = ApplicationRegistry.instance()
        self.workflow_store = WorkflowStore.instance()
        self.learning_engine = ApplicationLearningEngine(workflow_store=self.workflow_store)

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
        self.btn_refresh.clicked.connect(self._refresh_all)
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
        tabs = ["APPLICATIONS", "RUNNING INSTANCES", "ADAPTERS", "LEARNED SKILLS"]
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

        self._init_apps_view()       # 0
        self._init_running_view()    # 1
        self._init_adapters_view()   # 2
        self._init_skills_view()     # 3

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

    # ── Refresh Logic ─────────────────────────────────────────────────────────
    def _refresh_all(self) -> None:
        self.registry.refresh(force=True)
        self._refresh_apps_table()
        self._refresh_running_table()
        self._refresh_adapters_table()
        self._refresh_skills_table()

    def _refresh_apps_table(self) -> None:
        apps = self.registry.list_all_apps()
        self.tbl_apps.setRowCount(0)
        for row, app in enumerate(apps):
            self.tbl_apps.insertRow(row)
            self.tbl_apps.setItem(row, 0, QTableWidgetItem(app.name))
            self.tbl_apps.setItem(row, 1, QTableWidgetItem(app.app_id))
            self.tbl_apps.setItem(row, 2, QTableWidgetItem(app.category.value))

            status_text = "RUNNING" if app.is_running else "INSTALLED"
            st_item = QTableWidgetItem(status_text)
            st_item.setForeground(QColor(C.GREEN) if app.is_running else QColor(C.TEXT_DIM))
            self.tbl_apps.setItem(row, 3, st_item)

            self.tbl_apps.setItem(row, 4, QTableWidgetItem(app.integrity_level))

            act_w = QWidget()
            act_box = QHBoxLayout(act_w)
            act_box.setContentsMargins(4, 2, 4, 2)
            act_box.setSpacing(6)

            btn_launch = QPushButton("ACTIVATE" if app.is_running else "LAUNCH")
            btn_launch.setFont(px_font(8, QFont.Weight.Bold))
            btn_launch.setStyleSheet(btn_css("primary" if not app.is_running else "secondary"))
            btn_launch.setFixedHeight(24)
            btn_launch.clicked.connect(lambda checked=False, q=app.name: self._launch_app(q))
            act_box.addWidget(btn_launch)

            btn_learn = QPushButton("LEARN")
            btn_learn.setFont(px_font(8))
            btn_learn.setStyleSheet(btn_css("secondary"))
            btn_learn.setFixedHeight(24)
            btn_learn.clicked.connect(lambda checked=False, q=app.name: self._learn_app(q))
            act_box.addWidget(btn_learn)

            act_box.addStretch()
            self.tbl_apps.setCellWidget(row, 5, act_w)

    def _refresh_running_table(self) -> None:
        running = self.registry.list_running_apps()
        self.tbl_running.setRowCount(0)
        for row, app in enumerate(running):
            self.tbl_running.insertRow(row)
            self.tbl_running.setItem(row, 0, QTableWidgetItem(app.name))
            self.tbl_running.setItem(row, 1, QTableWidgetItem(str(app.pids[0]) if app.pids else "-"))
            self.tbl_running.setItem(row, 2, QTableWidgetItem(str(app.main_hwnd) if app.main_hwnd else "-"))
            self.tbl_running.setItem(row, 3, QTableWidgetItem(app.integrity_level))

            act_w = QWidget()
            act_box = QHBoxLayout(act_w)
            act_box.setContentsMargins(4, 2, 4, 2)
            btn_focus = QPushButton("FOCUS")
            btn_focus.setFont(px_font(8, QFont.Weight.Bold))
            btn_focus.setStyleSheet(btn_css("primary"))
            btn_focus.setFixedHeight(24)
            btn_focus.clicked.connect(lambda checked=False, q=app.name: self._launch_app(q))
            act_box.addWidget(btn_focus)
            act_box.addStretch()
            self.tbl_running.setCellWidget(row, 4, act_w)

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
