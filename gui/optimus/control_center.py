"""gui/optimus/control_center.py — Central Control Center UI for OPTIMUS.

Implements the three-part command interface:
- Left Navigation Sidebar (OptimusNavBar): structured, grouped module access.
- Central Workspace: header bar + QStackedWidget for the active operational module.
- Right Contextual Panel (OptimusContextPanel): collapsible intelligence deck.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.context_panel import OptimusContextPanel
from gui.optimus.dashboard import OptimusDashboardView
from gui.optimus.nav_bar import OptimusNavBar
from gui.optimus.section_agents import SectionAgents
from gui.optimus.section_applications import SectionApplications
from gui.optimus.section_approvals import SectionApprovals
from gui.optimus.section_command import SectionCommandCenter
from gui.optimus.section_developer import SectionDeveloper
from gui.optimus.section_integrations import SectionIntegrations
from gui.optimus.section_intelligence import SectionIntelligence
from gui.optimus.section_memory import SectionMemory
from gui.optimus.section_missions import SectionMissions
from gui.optimus.section_personal import SectionPersonal
from gui.optimus.section_planning import SectionPlanning
from gui.optimus.section_proactive import SectionProactive
from gui.optimus.section_self_improvement import SectionSelfImprovement
from gui.optimus.section_settings import SectionSettings
from gui.optimus.section_skills import SectionSkills
from gui.optimus.section_system import SectionSystem
from gui.optimus.section_verification import SectionVerification
from gui.optimus.section_vision import SectionVision
from gui.optimus.section_workflows import SectionWorkflows
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, px_font
from gui.theme import C


class OptimusControlCenterWidget(QWidget):
    """The complete, unified OPTIMUS Control Center application surface."""

    def __init__(self, bridge: OptimusServiceBridge | None = None, parent=None):
        super().__init__(parent)
        self.bridge = bridge or OptimusServiceBridge.instance()
        self.setStyleSheet(f"background: {C.BG};")

        # ── 1. Root Layout: Three-Part Command Architecture ──────────────────
        root_lay = QHBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Part A: Left Navigation Sidebar
        self.nav_bar = OptimusNavBar(self)
        self.nav_bar.tab_selected[int, str].connect(self._on_tab_selected)
        root_lay.addWidget(self.nav_bar)

        # Part B: Central Workspace (Header + Stack)
        self.center_container = QWidget(self)
        self.center_container.setStyleSheet(f"background: {C.BG};")
        center_lay = QVBoxLayout(self.center_container)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(0)

        self.top_bar = self._build_top_bar()
        center_lay.addWidget(self.top_bar)

        # Main Stacked Widget
        self.stack = QStackedWidget(self.center_container)
        self.stack.setStyleSheet("background: transparent;")

        # Instantiate all sections
        self.dashboard_view = OptimusDashboardView(self.bridge, parent=self.stack)
        self.command_view = SectionCommandCenter(self.bridge, parent=self.stack)
        self.workflows_view = SectionWorkflows(self.bridge, parent=self.stack)
        self.agents_view = SectionAgents(self.bridge, parent=self.stack)
        self.intelligence_view = SectionIntelligence(self.bridge, parent=self.stack)
        self.memory_view = SectionMemory(self.bridge, parent=self.stack)
        self.verification_view = SectionVerification(self.bridge, parent=self.stack)
        self.approvals_view = SectionApprovals(self.bridge, parent=self.stack)
        self.system_view = SectionSystem(self.bridge, parent=self.stack)
        self.settings_view = SectionSettings(self.bridge, parent=self.stack)
        self.planning_view = SectionPlanning(self.bridge, parent=self.stack)
        self.developer_view = SectionDeveloper(self.bridge, parent=self.stack)
        self.skills_view = SectionSkills(self.bridge, parent=self.stack)
        self.missions_view = SectionMissions(self.bridge, parent=self.stack)
        self.proactive_view = SectionProactive(self.bridge, parent=self.stack)
        self.personal_view = SectionPersonal(self.bridge, parent=self.stack)
        self.self_improvement_view = SectionSelfImprovement(self.bridge, parent=self.stack)
        self.vision_view = SectionVision(self.bridge, parent=self.stack)
        self.applications_view = SectionApplications(self.bridge, parent=self.stack)
        self.integrations_view = SectionIntegrations(self.bridge, parent=self.stack)

        # Add to stack in index order matching OptimusNavBar.SECTIONS
        self.stack.addWidget(self.dashboard_view)        # 0: DASHBOARD
        self.stack.addWidget(self.command_view)          # 1: COMMAND
        self.stack.addWidget(self.workflows_view)        # 2: WORKFLOWS
        self.stack.addWidget(self.agents_view)           # 3: AGENTS
        self.stack.addWidget(self.intelligence_view)     # 4: INTELLIGENCE
        self.stack.addWidget(self.memory_view)           # 5: MEMORY
        self.stack.addWidget(self.verification_view)     # 6: VERIFICATION
        self.stack.addWidget(self.approvals_view)        # 7: APPROVALS
        self.stack.addWidget(self.system_view)           # 8: SYSTEM
        self.stack.addWidget(self.settings_view)         # 9: SETTINGS
        self.stack.addWidget(self.planning_view)         # 10: PLANNING
        self.stack.addWidget(self.developer_view)        # 11: DEVELOPER
        self.stack.addWidget(self.skills_view)           # 12: SKILLS
        self.stack.addWidget(self.missions_view)         # 13: MISSIONS
        self.stack.addWidget(self.proactive_view)        # 14: PROACTIVE
        self.stack.addWidget(self.personal_view)         # 15: PERSONAL
        self.stack.addWidget(self.self_improvement_view) # 16: IMPROVE
        self.stack.addWidget(self.vision_view)           # 17: VISION
        self.stack.addWidget(self.applications_view)     # 18: APPLICATIONS
        self.stack.addWidget(self.integrations_view)     # 19: INTEGRATIONS

        center_lay.addWidget(self.stack, stretch=1)
        root_lay.addWidget(self.center_container, stretch=1)

        # Part C: Right Contextual Panel (Collapsible)
        self.context_panel = OptimusContextPanel(self.bridge, self)
        root_lay.addWidget(self.context_panel)

        # Connect bridge approval events to nav bar badge
        self.bridge.approval_requested.connect(self._update_approval_badge)
        self.bridge.approval_resolved.connect(self._update_approval_badge)
        self._update_approval_badge(None)

        # Connect bridge notification events to nav bar badge
        self.bridge.notification_received.connect(self._update_proactive_badge)
        self.bridge.recommendation_created.connect(self._update_proactive_badge)
        self._update_proactive_badge(None)

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(38)
        bar.setStyleSheet(f"""
            QWidget {{
                background: {C.PANEL};
                border-bottom: 1px solid {C.BORDER};
            }}
        """)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(8)

        self.lbl_workspace_title = QLabel("DASHBOARD · MISSION CONTROL")
        self.lbl_workspace_title.setFont(px_font(10, QFont.Weight.Bold))
        self.lbl_workspace_title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self.lbl_workspace_title)

        lay.addStretch()

        self.btn_toggle_context = QPushButton("Context Panel ◨")
        self.btn_toggle_context.setFont(px_font(9, QFont.Weight.Medium))
        self.btn_toggle_context.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_context.setStyleSheet(btn_css("dim"))
        self.btn_toggle_context.clicked.connect(self._toggle_context_panel)
        lay.addWidget(self.btn_toggle_context)

        return bar

    def _toggle_context_panel(self) -> None:
        if hasattr(self, "context_panel"):
            self.context_panel.toggle_visibility()

    def _on_tab_selected(self, index: int, title: str) -> None:
        self.stack.setCurrentIndex(index)
        if hasattr(self, "lbl_workspace_title"):
            self.lbl_workspace_title.setText(f"{title.upper()} · MISSION CONTROL")

        # Trigger refresh on view when navigated to
        w = self.stack.currentWidget()
        if hasattr(w, "refresh_all"):
            w.refresh_all()
        elif hasattr(w, "refresh_vision"):
            w.refresh_vision()
        elif hasattr(w, "refresh_missions"):
            w.refresh_missions()
        elif hasattr(w, "refresh_tasks"):
            w.refresh_tasks()
        elif hasattr(w, "refresh_workflows"):
            w.refresh_workflows()
        elif hasattr(w, "refresh_agents"):
            w.refresh_agents()
        elif hasattr(w, "refresh_intelligence"):
            w.refresh_intelligence()
        elif hasattr(w, "refresh_memory"):
            w.refresh_memory()
        elif hasattr(w, "refresh_verification"):
            w.refresh_verification()
        elif hasattr(w, "refresh_approvals"):
            w.refresh_approvals()
        elif hasattr(w, "refresh_processes"):
            w.refresh_processes()
        elif hasattr(w, "refresh_data"):
            w.refresh_data()

    def _update_approval_badge(self, _) -> None:
        pending = self.bridge.get_pending_approvals()
        self.nav_bar.set_approval_badge(len(pending))

    def _update_proactive_badge(self, _) -> None:
        count = self.bridge.get_unread_notification_count()
        self.nav_bar.set_proactive_badge(count)

    def show_section(self, target: int | str) -> None:
        """Switch section programmatically by index or section name."""
        self.navigate_to(target)

    def navigate_to(self, target: int | str) -> None:
        """Switch section programmatically by index or uppercase section title."""
        if isinstance(target, int):
            self.nav_bar.select_tab(target)
            self.stack.setCurrentIndex(target)
            if hasattr(self, "lbl_workspace_title") and 0 <= target < len(self.nav_bar.SECTIONS):
                self.lbl_workspace_title.setText(f"{self.nav_bar.SECTIONS[target][0]} · MISSION CONTROL")
        elif isinstance(target, str):
            target_norm = target.strip().upper()
            for idx, (title, _) in enumerate(self.nav_bar.SECTIONS):
                if title == target_norm or title.startswith(target_norm):
                    self.nav_bar.select_tab(idx)
                    self.stack.setCurrentIndex(idx)
                    if hasattr(self, "lbl_workspace_title"):
                        self.lbl_workspace_title.setText(f"{title} · MISSION CONTROL")
                    break
