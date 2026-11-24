"""gui/optimus/control_center.py — Central Control Center UI for OPTIMUS.

Implements the three-part command interface:
- Left Navigation Sidebar (OptimusNavBar): structured, grouped module access.
- Central Workspace: header bar + QStackedWidget with lazy tab instantiation.
- Right Contextual Panel (OptimusContextPanel): collapsible intelligence deck.
"""

from __future__ import annotations

from typing import Any, Callable

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
from gui.optimus.section_marketplace import SectionMarketplace
from gui.optimus.section_mcp_manager import SectionMCPManager
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
    """The complete, unified OPTIMUS Control Center application surface with lazy tab loading."""

    _SECTION_REGISTRY: list[tuple[Callable[..., QWidget], str]] = [
        (OptimusDashboardView, "dashboard_view"),       # 0: DASHBOARD
        (SectionCommandCenter, "command_view"),         # 1: COMMAND
        (SectionWorkflows, "workflows_view"),           # 2: WORKFLOWS
        (SectionAgents, "agents_view"),                 # 3: AGENTS
        (SectionIntelligence, "intelligence_view"),     # 4: INTELLIGENCE
        (SectionMemory, "memory_view"),                 # 5: MEMORY
        (SectionVerification, "verification_view"),     # 6: VERIFICATION
        (SectionApprovals, "approvals_view"),           # 7: APPROVALS
        (SectionSystem, "system_view"),                 # 8: SYSTEM
        (SectionSettings, "settings_view"),             # 9: SETTINGS
        (SectionPlanning, "planning_view"),             # 10: PLANNING
        (SectionDeveloper, "developer_view"),           # 11: DEVELOPER
        (SectionSkills, "skills_view"),                 # 12: SKILLS
        (SectionMissions, "missions_view"),             # 13: MISSIONS
        (SectionProactive, "proactive_view"),           # 14: PROACTIVE
        (SectionPersonal, "personal_view"),             # 15: PERSONAL
        (SectionSelfImprovement, "self_improvement_view"), # 16: IMPROVE
        (SectionVision, "vision_view"),                 # 17: VISION
        (SectionApplications, "applications_view"),     # 18: APPLICATIONS
        (SectionIntegrations, "integrations_view"),     # 19: INTEGRATIONS
        (SectionMarketplace, "marketplace_view"),       # 20: MARKETPLACE
        (SectionMCPManager, "mcp_manager_view"),         # 21: MCP SERVERS
    ]

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

        # Lazy Instantiation Storage & Placeholder Pre-population
        self._section_instances: dict[int, QWidget | None] = {i: None for i in range(len(self._SECTION_REGISTRY))}

        for i in range(len(self._SECTION_REGISTRY)):
            placeholder = QWidget()
            placeholder.setStyleSheet("background: transparent;")
            self.stack.addWidget(placeholder)

        # Pre-instantiate only the initial view (Dashboard, index 0)
        self._get_or_create_section(0)

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

    # ── Lazy Section Instantiation & Property Proxies ─────────────────────────

    def _get_or_create_section(self, index: int) -> QWidget:
        """Instantiate section on first access, swapping out the placeholder in-place."""
        if index < 0 or index >= len(self._SECTION_REGISTRY):
            index = 0

        if self._section_instances.get(index) is None:
            factory, _ = self._SECTION_REGISTRY[index]
            view = factory(self.bridge, parent=self.stack)
            old_w = self.stack.widget(index)
            self.stack.removeWidget(old_w)
            self.stack.insertWidget(index, view)
            old_w.deleteLater()
            self._section_instances[index] = view

        return self._section_instances[index]  # type: ignore

    @property
    def dashboard_view(self) -> OptimusDashboardView:
        return self._get_or_create_section(0)  # type: ignore

    @property
    def command_view(self) -> SectionCommandCenter:
        return self._get_or_create_section(1)  # type: ignore

    @property
    def workflows_view(self) -> SectionWorkflows:
        return self._get_or_create_section(2)  # type: ignore

    @property
    def agents_view(self) -> SectionAgents:
        return self._get_or_create_section(3)  # type: ignore

    @property
    def intelligence_view(self) -> SectionIntelligence:
        return self._get_or_create_section(4)  # type: ignore

    @property
    def memory_view(self) -> SectionMemory:
        return self._get_or_create_section(5)  # type: ignore

    @property
    def verification_view(self) -> SectionVerification:
        return self._get_or_create_section(6)  # type: ignore

    @property
    def approvals_view(self) -> SectionApprovals:
        return self._get_or_create_section(7)  # type: ignore

    @property
    def system_view(self) -> SectionSystem:
        return self._get_or_create_section(8)  # type: ignore

    @property
    def settings_view(self) -> SectionSettings:
        return self._get_or_create_section(9)  # type: ignore

    @property
    def planning_view(self) -> SectionPlanning:
        return self._get_or_create_section(10)  # type: ignore

    @property
    def developer_view(self) -> SectionDeveloper:
        return self._get_or_create_section(11)  # type: ignore

    @property
    def skills_view(self) -> SectionSkills:
        return self._get_or_create_section(12)  # type: ignore

    @property
    def missions_view(self) -> SectionMissions:
        return self._get_or_create_section(13)  # type: ignore

    @property
    def proactive_view(self) -> SectionProactive:
        return self._get_or_create_section(14)  # type: ignore

    @property
    def personal_view(self) -> SectionPersonal:
        return self._get_or_create_section(15)  # type: ignore

    @property
    def self_improvement_view(self) -> SectionSelfImprovement:
        return self._get_or_create_section(16)  # type: ignore

    @property
    def vision_view(self) -> SectionVision:
        return self._get_or_create_section(17)  # type: ignore

    @property
    def applications_view(self) -> SectionApplications:
        return self._get_or_create_section(18)  # type: ignore

    @property
    def integrations_view(self) -> SectionIntegrations:
        return self._get_or_create_section(19)  # type: ignore

    # ── Top Bar & Navigation ──────────────────────────────────────────────────

    def _build_top_bar(self) -> QWidget:
        bar = QWidget(self.center_container)
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

    @property
    def current_section_index(self) -> int:
        """Returns the currently active section index."""
        return self.stack.currentIndex()

    def switch_section(self, index: int, title: str = "") -> None:
        """Switch active section by index."""
        self._on_tab_selected(index, title)

    def _on_tab_selected(self, index: int, title: str) -> None:
        old_w = self.stack.currentWidget()
        if old_w and hasattr(old_w, "pause_polling"):
            try:
                old_w.pause_polling()
            except Exception:
                pass
        view = self._get_or_create_section(index)
        self.stack.setCurrentIndex(index)
        if hasattr(view, "resume_polling"):
            try:
                view.resume_polling()
            except Exception:
                pass
        if hasattr(self, "lbl_workspace_title"):
            self.lbl_workspace_title.setText(f"{title.upper()} · MISSION CONTROL")

        # Trigger refresh on view when navigated to
        w = view
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
        old_w = self.stack.currentWidget()
        if old_w and hasattr(old_w, "pause_polling"):
            try:
                old_w.pause_polling()
            except Exception:
                pass
        if isinstance(target, int):
            view = self._get_or_create_section(target)
            self.nav_bar.select_tab(target)
            self.stack.setCurrentIndex(target)
            if hasattr(view, "resume_polling"):
                try:
                    view.resume_polling()
                except Exception:
                    pass
            if hasattr(self, "lbl_workspace_title") and 0 <= target < len(self.nav_bar.SECTIONS):
                self.lbl_workspace_title.setText(f"{self.nav_bar.SECTIONS[target][0]} · MISSION CONTROL")
        elif isinstance(target, str):
            target_norm = target.strip().upper()
            for idx, (title, _) in enumerate(self.nav_bar.SECTIONS):
                if title == target_norm or title.startswith(target_norm):
                    view = self._get_or_create_section(idx)
                    self.nav_bar.select_tab(idx)
                    self.stack.setCurrentIndex(idx)
                    if hasattr(view, "resume_polling"):
                        try:
                            view.resume_polling()
                        except Exception:
                            pass
                    if hasattr(self, "lbl_workspace_title"):
                        self.lbl_workspace_title.setText(f"{title} · MISSION CONTROL")
                    break

    # ── Hierarchical Lifecycle Cascading ─────────────────────────────────────

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        curr = self.stack.currentWidget()
        if curr and hasattr(curr, "pause_polling"):
            try:
                curr.pause_polling()
            except Exception:
                pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        curr = self.stack.currentWidget()
        if curr and hasattr(curr, "resume_polling"):
            try:
                curr.resume_polling()
            except Exception:
                pass
