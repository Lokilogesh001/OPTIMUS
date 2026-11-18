"""gui/optimus/control_center.py — Central Control Center UI for OPTIMUS.

Integrates the JARVIS-inspired Navigation Bar with:
- Index 0: Main Multi-Module Dashboard (Home)
- Index 1: Command Center
- Index 2: Workflows
- Index 3: Agents
- Index 4: Intelligence
- Index 5: Memory
- Index 6: Verification & Evidence
- Index 7: Approvals
- Index 8: System Monitor
- Index 9: Settings
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.dashboard import OptimusDashboardView
from gui.optimus.nav_bar import OptimusNavBar
from gui.optimus.section_agents import SectionAgents
from gui.optimus.section_approvals import SectionApprovals
from gui.optimus.section_command import SectionCommandCenter
from gui.optimus.section_developer import SectionDeveloper
from gui.optimus.section_intelligence import SectionIntelligence
from gui.optimus.section_memory import SectionMemory
from gui.optimus.section_missions import SectionMissions
from gui.optimus.section_planning import SectionPlanning
from gui.optimus.section_settings import SectionSettings
from gui.optimus.section_skills import SectionSkills
from gui.optimus.section_system import SectionSystem
from gui.optimus.section_verification import SectionVerification
from gui.optimus.section_workflows import SectionWorkflows
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.theme import C


class OptimusControlCenterWidget(QWidget):
    """The complete, unified OPTIMUS Control Center application surface."""

    def __init__(self, bridge: OptimusServiceBridge | None = None, parent=None):
        super().__init__(parent)
        self.bridge = bridge or OptimusServiceBridge.instance()
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. Top Navigation Bar
        self.nav_bar = OptimusNavBar(self)
        self.nav_bar.tab_selected[int, str].connect(self._on_tab_selected)
        layout.addWidget(self.nav_bar)

        # 2. Main Stacked Widget
        self.stack = QStackedWidget(self)
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

        # Add to stack in index order matching OptimusNavBar.SECTIONS
        self.stack.addWidget(self.dashboard_view)      # 0: DASHBOARD
        self.stack.addWidget(self.command_view)        # 1: COMMAND
        self.stack.addWidget(self.workflows_view)      # 2: WORKFLOWS
        self.stack.addWidget(self.agents_view)         # 3: AGENTS
        self.stack.addWidget(self.intelligence_view)   # 4: INTELLIGENCE
        self.stack.addWidget(self.memory_view)         # 5: MEMORY
        self.stack.addWidget(self.verification_view)   # 6: VERIFICATION
        self.stack.addWidget(self.approvals_view)      # 7: APPROVALS
        self.stack.addWidget(self.system_view)         # 8: SYSTEM
        self.stack.addWidget(self.settings_view)       # 9: SETTINGS
        self.stack.addWidget(self.planning_view)       # 10: PLANNING
        self.stack.addWidget(self.developer_view)      # 11: DEVELOPER
        self.stack.addWidget(self.skills_view)         # 12: SKILLS
        self.stack.addWidget(self.missions_view)       # 13: MISSIONS

        layout.addWidget(self.stack, stretch=1)

        # Connect bridge approval events to nav bar badge
        self.bridge.approval_requested.connect(self._update_approval_badge)
        self.bridge.approval_resolved.connect(self._update_approval_badge)
        self._update_approval_badge(None)

    def _on_tab_selected(self, index: int, title: str) -> None:
        self.stack.setCurrentIndex(index)
        # Trigger refresh on view when navigated to
        w = self.stack.currentWidget()
        if hasattr(w, "refresh_all"):
            w.refresh_all()
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

    def _update_approval_badge(self, _) -> None:
        pending = self.bridge.get_pending_approvals()
        self.nav_bar.set_approval_badge(len(pending))

    def show_section(self, target: int | str) -> None:
        """Switch section programmatically by index or section name."""
        self.navigate_to(target)

    def navigate_to(self, target: int | str) -> None:
        """Switch section programmatically by index or uppercase section title."""
        if isinstance(target, int):
            self.nav_bar.select_tab(target)
            self.stack.setCurrentIndex(target)
        elif isinstance(target, str):
            target_norm = target.strip().upper()
            for idx, (title, _) in enumerate(self.nav_bar.SECTIONS):
                if title == target_norm or title.startswith(target_norm):
                    self.nav_bar.select_tab(idx)
                    self.stack.setCurrentIndex(idx)
                    break
