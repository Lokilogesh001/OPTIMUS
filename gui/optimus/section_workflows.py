"""gui/optimus/section_workflows.py — Dedicated Workflows Section.

Provides full DAG workflow management:
- Definition catalog & step dependencies.
- Visual step execution status inspector.
- Synchronous and asynchronous workflow dispatch.
- Pause, resume, and cancellation controls.
"""

from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
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


class SectionWorkflows(QWidget):
    """Workflow DAG management and execution inspection."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("WORKFLOW ENGINE  ·  DAG COORDINATOR")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH WORKFLOWS")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_workflows)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Catalog & Runner
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        p_catalog = HoloPanel("REGISTERED DEFINITIONS", "CATALOG")
        self.table_defs = QTableWidget()
        self.table_defs.setColumnCount(4)
        self.table_defs.setHorizontalHeaderLabels(["ID", "NAME", "STEPS", "STATUS"])
        self.table_defs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_defs.verticalHeader().setVisible(False)
        self.table_defs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_defs.setStyleSheet(f"""
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
        self.table_defs.itemClicked.connect(self._on_workflow_selected)
        p_catalog.body.addWidget(self.table_defs)
        left_lay.addWidget(p_catalog, stretch=3)

        # Execution Dispatcher
        p_exec = HoloPanel("EXECUTE WORKFLOW", "RUN")
        e_lay = QVBoxLayout()
        self.param_input = QLineEdit()
        self.param_input.setFont(mono_font(9))
        self.param_input.setPlaceholderText('Parameters JSON (e.g. {"threshold": 50})')
        self.param_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """)
        e_lay.addWidget(self.param_input)

        b_row = QHBoxLayout()
        self.btn_run_wf = QPushButton("START WORKFLOW")
        self.btn_run_wf.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_run_wf.setStyleSheet(btn_css("primary"))
        self.btn_run_wf.clicked.connect(self._run_selected_workflow)
        b_row.addWidget(self.btn_run_wf)

        self.btn_pause_wf = QPushButton("PAUSE")
        self.btn_pause_wf.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_pause_wf.setStyleSheet(btn_css("dim"))
        self.btn_pause_wf.clicked.connect(self._pause_selected)
        b_row.addWidget(self.btn_pause_wf)

        self.btn_resume_wf = QPushButton("RESUME")
        self.btn_resume_wf.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_resume_wf.setStyleSheet(btn_css("dim"))
        self.btn_resume_wf.clicked.connect(self._resume_selected)
        b_row.addWidget(self.btn_resume_wf)

        e_lay.addLayout(b_row)
        p_exec.body.addLayout(e_lay)
        left_lay.addWidget(p_exec, stretch=1)

        split.addWidget(left_w)

        # Right Column: Visual DAG Step Details
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_dag = HoloPanel("WORKFLOW DAG STRUCTURE", "GRAPH")
        self.dag_scroll = QScrollArea()
        self.dag_scroll.setWidgetResizable(True)
        self.dag_scroll.setStyleSheet("QScrollArea { background: transparent; border: 1px solid " + C.BORDER + "; }" + scroll_css())
        self.dag_container = QWidget()
        self.dag_container.setStyleSheet("background: transparent;")
        self.dag_nodes_lay = QVBoxLayout(self.dag_container)
        self.dag_nodes_lay.setSpacing(6)
        self.dag_scroll.setWidget(self.dag_container)
        p_dag.body.addWidget(self.dag_scroll)
        right_lay.addWidget(p_dag, stretch=3)

        p_detail = HoloPanel("STEP EXECUTION RECORD", "INSPECT")
        self.step_detail_edit = QTextEdit()
        self.step_detail_edit.setReadOnly(True)
        self.step_detail_edit.setFont(mono_font(9))
        self.step_detail_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 4px;
            }}
        """ + scroll_css())
        p_detail.body.addWidget(self.step_detail_edit)
        right_lay.addWidget(p_detail, stretch=2)

        split.addWidget(right_w)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        layout.addWidget(split)

        self._selected_wf_id: str | None = None
        self._selected_exec_id: str | None = None

        # Property aliases for testing & external APIs
        self.list_defs = self.table_defs
        self.refresh_catalog = self.refresh_workflows
        self.dag_view = self.step_detail_edit

        self.refresh_workflows()

    @property
    def _selected_def_id(self) -> str | None:
        return self._selected_wf_id

    def refresh_workflows(self) -> None:
        wfs = self.bridge.get_workflows_status()
        self.table_defs.setRowCount(len(wfs))
        for row, wf in enumerate(wfs):
            id_item = QTableWidgetItem(wf["workflow_id"])
            id_item.setFont(mono_font(9))

            name_item = QTableWidgetItem(wf["name"])
            name_item.setFont(px_font(9, QFont.Weight.Bold))

            steps_item = QTableWidgetItem(str(wf["step_count"]))
            steps_item.setFont(mono_font(9))

            st_item = QTableWidgetItem(wf["last_status"])
            st_item.setFont(px_font(9, QFont.Weight.DemiBold))
            if wf["last_status"] == "COMPLETED":
                st_item.setForeground(QColor(C.GREEN))
            elif wf["last_status"] == "FAILED":
                st_item.setForeground(QColor(C.RED))
            elif wf["last_status"] == "RUNNING":
                st_item.setForeground(QColor(C.PRI))
            else:
                st_item.setForeground(QColor(C.TEXT_DIM))

            self.table_defs.setItem(row, 0, id_item)
            self.table_defs.setItem(row, 1, name_item)
            self.table_defs.setItem(row, 2, steps_item)
            self.table_defs.setItem(row, 3, st_item)

        if wfs and not self._selected_wf_id:
            self._render_dag_for_wf(wfs[0])

    def _on_workflow_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        wf_id = self.table_defs.item(row, 0).text()
        wfs = self.bridge.get_workflows_status()
        selected = [w for w in wfs if w["workflow_id"] == wf_id]
        if selected:
            self._render_dag_for_wf(selected[0])

    def _render_dag_for_wf(self, wf: dict[str, Any]) -> None:
        self._selected_wf_id = wf["workflow_id"]
        self._selected_exec_id = wf.get("last_execution_id")

        while self.dag_nodes_lay.count():
            item = self.dag_nodes_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        steps = wf.get("steps", [])
        for idx, s in enumerate(steps):
            box = QFrame()
            box.setStyleSheet(f"""
                QFrame {{
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 4px;
                    padding: 6px;
                }}
            """)
            b_lay = QVBoxLayout(box)
            b_lay.setContentsMargins(6, 4, 6, 4)
            b_lay.setSpacing(2)

            t_row = QHBoxLayout()
            s_name = QLabel(f"#{idx+1}  {s.get('name', s.get('step_id'))}")
            s_name.setFont(px_font(10, QFont.Weight.Bold))
            s_name.setStyleSheet(f"color: {C.WHITE};")
            t_row.addWidget(s_name)

            agent_chip = QLabel(f"[{s.get('assigned_agent', 'agent')}]")
            agent_chip.setFont(mono_font(8))
            agent_chip.setStyleSheet(f"color: {C.PRI}; background: rgba(0,212,255,0.08); padding: 1px 4px; border-radius: 2px;")
            t_row.addWidget(agent_chip)
            t_row.addStretch()

            if s.get("requires_approval"):
                appr_lbl = QLabel("⚠ APPROVAL GATE")
                appr_lbl.setFont(px_font(8, QFont.Weight.Bold))
                appr_lbl.setStyleSheet(f"color: {C.ACC};")
                t_row.addWidget(appr_lbl)
            b_lay.addLayout(t_row)

            deps = s.get("depends_on", [])
            dep_str = f"Depends on: {', '.join(deps)}" if deps else "Root Step (No dependencies)"
            d_lbl = QLabel(dep_str)
            d_lbl.setFont(mono_font(8))
            d_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
            b_lay.addWidget(d_lbl)

            self.dag_nodes_lay.addWidget(box)

        self.dag_nodes_lay.addStretch()

        # Display steps and last execution details
        summary = f"Workflow: {wf.get('name', '')} ({wf.get('workflow_id', '')})\nSteps ({len(steps)}):\n"
        for s in steps:
            summary += f"  - {s.get('name', s.get('step_id'))} [{s.get('assigned_agent', 'agent')}]\n"
        if self._selected_exec_id:
            exec_rec = self.bridge.get_workflow_execution(self._selected_exec_id)
            if exec_rec:
                summary += "\nLast Execution:\n" + json.dumps(exec_rec, indent=2)
        self.step_detail_edit.setPlainText(summary)

    def _run_selected_workflow(self) -> None:
        if not self._selected_wf_id:
            return
        params_str = self.param_input.text().strip()
        params = {}
        if params_str:
            try:
                params = json.loads(params_str)
            except Exception as e:
                self.step_detail_edit.setPlainText(f"Invalid JSON parameters: {e}")
                return
        self.step_detail_edit.setPlainText(f"Starting workflow '{self._selected_wf_id}' in background...")
        self.bridge.submit_workflow(self._selected_wf_id, params)

    def _pause_selected(self) -> None:
        if self._selected_exec_id:
            self.bridge.pause_workflow(self._selected_exec_id)
            self.refresh_workflows()

    def _resume_selected(self) -> None:
        if self._selected_exec_id:
            self.bridge.resume_workflow(self._selected_exec_id)
            self.refresh_workflows()
