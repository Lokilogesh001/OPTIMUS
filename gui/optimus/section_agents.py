"""gui/optimus/section_agents.py — Dedicated Agents & Tool Sandboxing Section.

Displays the specialized agent roster, capabilities, and CBAC tool permissions:
- Agent profiles (code intelligence, system diagnosis, data intelligence, research, executor).
- Allowed capability tiers and tool whitelists.
- Dynamic agent registration and revocation controls.
"""

from __future__ import annotations

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


class SectionAgents(QWidget):
    """Specialized Agent Library & Dynamic Tool Sandboxing Inspector."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("SPECIALIZED AGENT ROSTER  ·  CBAC SANDBOX")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH ROSTER")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_agents)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Agent Table
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)

        p_roster = HoloPanel("REGISTERED AGENTS", "ROSTER")
        self.table_agents = QTableWidget()
        self.table_agents.setColumnCount(4)
        self.table_agents.setHorizontalHeaderLabels(["AGENT", "DOMAIN", "STATUS", "TOOLS"])
        self.table_agents.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_agents.verticalHeader().setVisible(False)
        self.table_agents.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_agents.setStyleSheet(f"""
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
        self.table_agents.itemClicked.connect(self._on_agent_selected)
        p_roster.body.addWidget(self.table_agents)
        left_lay.addWidget(p_roster)
        split.addWidget(left_w)

        # Right Column: Profile & Capability Details
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_details = HoloPanel("AGENT PROFILE & PERMISSIONS", "CBAC")
        self.lbl_agent_name = QLabel("")
        self.lbl_agent_name.setFont(px_font(11, QFont.Weight.Bold))
        self.lbl_agent_name.setStyleSheet(f"color: {C.PRI}; background: transparent; padding-bottom: 4px;")
        p_details.body.addWidget(self.lbl_agent_name)

        self.detail_edit = QTextEdit()
        self.detail_edit.setReadOnly(True)
        self.detail_edit.setFont(mono_font(9))
        self.detail_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """ + scroll_css())
        p_details.body.addWidget(self.detail_edit)

        self.system_instructions_edit = self.detail_edit
        self.table_tools = QTableWidget()
        self.table_tools.setColumnCount(1)
        self.table_tools.setHorizontalHeaderLabels(["ALLOWED TOOL"])
        self.table_tools.horizontalHeader().setStretchLastSection(True)
        self.table_tools.verticalHeader().setVisible(False)
        self.table_tools.setFixedHeight(90)
        self.table_tools.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
            }}
        """ + scroll_css())
        p_details.body.addWidget(self.table_tools)

        right_lay.addWidget(p_details)
        split.addWidget(right_w)

        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        layout.addWidget(split)

        self._selected_agent: Any = None

        self.refresh_agents()

    def refresh_agents(self) -> None:
        agents = self.bridge.get_agents_status()
        self.table_agents.setRowCount(len(agents))
        for row, a in enumerate(agents):
            name_item = QTableWidgetItem(a.get("title", a["name"]))
            name_item.setFont(px_font(9, QFont.Weight.Bold))

            dom_item = QTableWidgetItem(a.get("domain", "general"))
            dom_item.setFont(mono_font(9))

            st_item = QTableWidgetItem(a.get("status", "IDLE"))
            st_item.setFont(px_font(9, QFont.Weight.DemiBold))
            if a["status"] == "BUSY":
                st_item.setForeground(QColor(C.ACC))
            else:
                st_item.setForeground(QColor(C.GREEN))

            tools_count = len(a.get("allowed_tools", []))
            tools_item = QTableWidgetItem(f"{tools_count} tools")
            tools_item.setFont(mono_font(9))

            self.table_agents.setItem(row, 0, name_item)
            self.table_agents.setItem(row, 1, dom_item)
            self.table_agents.setItem(row, 2, st_item)
            self.table_agents.setItem(row, 3, tools_item)

        if agents:
            self._render_agent_detail(agents[0])

    def _on_agent_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        agents = self.bridge.get_agents_status()
        if 0 <= row < len(agents):
            self._render_agent_detail(agents[row])

    def _render_agent_detail(self, a: dict[str, Any]) -> None:
        name = a["name"]
        prof = self.bridge.orchestrator.agent_registry.get(name)
        if not prof:
            self._selected_agent = None
            self.detail_edit.setPlainText("Agent profile not found.")
            return

        self._selected_agent = prof
        self.lbl_agent_name.setText(f"{prof.title} ({prof.name})")

        detail = (
            f"AGENT: {prof.title} ({prof.name})\n"
            f"DOMAIN: {prof.domain}\n"
            f"STATUS: {a.get('status', 'IDLE')}\n"
            f"CURRENT ASSIGNMENT: {a.get('assignment', 'None')}\n\n"
            f"DESCRIPTION:\n{prof.description}\n\n"
            f"ALLOWED CAPABILITY TIERS: {list(prof.allowed_tiers)}\n"
            f"ALLOWED TOOLS ({len(prof.allowed_tools)}):\n"
        )
        for t in prof.allowed_tools:
            detail += f"  - {t}\n"

        detail += f"\nSYSTEM INSTRUCTIONS:\n{prof.instructions}\n"
        self.detail_edit.setPlainText(detail)

        self.table_tools.setRowCount(len(prof.allowed_tools))
        for row, t in enumerate(prof.allowed_tools):
            self.table_tools.setItem(row, 0, QTableWidgetItem(t))
