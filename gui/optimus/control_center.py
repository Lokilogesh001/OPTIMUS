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
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, px_font
from gui.theme import C


def _lazy_section(module_path: str, class_name: str) -> Callable[..., QWidget]:
    """Return a lazy factory that imports a Control Center section only on first access."""
    def _factory(bridge: OptimusServiceBridge, parent: QWidget | None = None) -> QWidget:
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls(bridge, parent=parent)
    return _factory


class OptimusControlCenterWidget(QWidget):
    """The complete, unified OPTIMUS Control Center application surface with lazy tab loading."""

    _SECTION_REGISTRY: list[tuple[Callable[..., QWidget], str]] = [
        (OptimusDashboardView, "dashboard_view"),                                                         # 0: DASHBOARD
        (_lazy_section("gui.optimus.section_command", "SectionCommandCenter"), "command_view"),            # 1: COMMAND
        (_lazy_section("gui.optimus.section_workflows", "SectionWorkflows"), "workflows_view"),            # 2: WORKFLOWS
        (_lazy_section("gui.optimus.section_agents", "SectionAgents"), "agents_view"),                     # 3: AGENTS
        (_lazy_section("gui.optimus.section_intelligence", "SectionIntelligence"), "intelligence_view"),   # 4: INTELLIGENCE
        (_lazy_section("gui.optimus.section_memory", "SectionMemory"), "memory_view"),                     # 5: MEMORY
        (_lazy_section("gui.optimus.section_verification", "SectionVerification"), "verification_view"),   # 6: VERIFICATION
        (_lazy_section("gui.optimus.section_approvals", "SectionApprovals"), "approvals_view"),            # 7: APPROVALS
        (_lazy_section("gui.optimus.section_system", "SectionSystem"), "system_view"),                     # 8: SYSTEM
        (_lazy_section("gui.optimus.section_settings", "SectionSettings"), "settings_view"),               # 9: SETTINGS
        (_lazy_section("gui.optimus.section_planning", "SectionPlanning"), "planning_view"),               # 10: PLANNING
        (_lazy_section("gui.optimus.section_developer", "SectionDeveloper"), "developer_view"),            # 11: DEVELOPER
        (_lazy_section("gui.optimus.section_skills", "SectionSkills"), "skills_view"),                     # 12: SKILLS
        (_lazy_section("gui.optimus.section_missions", "SectionMissions"), "missions_view"),               # 13: MISSIONS
        (_lazy_section("gui.optimus.section_proactive", "SectionProactive"), "proactive_view"),            # 14: PROACTIVE
        (_lazy_section("gui.optimus.section_personal", "SectionPersonal"), "personal_view"),               # 15: PERSONAL
        (_lazy_section("gui.optimus.section_self_improvement", "SectionSelfImprovement"), "self_improvement_view"), # 16: IMPROVE
        (_lazy_section("gui.optimus.section_vision", "SectionVision"), "vision_view"),                     # 17: VISION
        (_lazy_section("gui.optimus.section_applications", "SectionApplications"), "applications_view"),   # 18: APPLICATIONS
        (_lazy_section("gui.optimus.section_integrations", "SectionIntegrations"), "integrations_view"),   # 19: INTEGRATIONS
        (_lazy_section("gui.optimus.section_marketplace", "SectionMarketplace"), "marketplace_view"),      # 20: MARKETPLACE
        (_lazy_section("gui.optimus.section_mcp_manager", "SectionMCPManager"), "mcp_manager_view"),       # 21: MCP SERVERS
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
            if index == 0 and hasattr(view, "open_execution_graph_requested"):
                view.open_execution_graph_requested.connect(self._open_execution_graph)

        return self._section_instances[index]  # type: ignore

    def _open_execution_graph(self) -> None:
        """Navigate from Home active-mission preview to the full Missions Execution Graph."""
        self.show_section("missions")
        if hasattr(self.missions_view, "show_execution_graph"):
            self.missions_view.show_execution_graph()

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

    def closeEvent(self, event) -> None:
        for view in list(self._section_instances.values()):
            if view and hasattr(view, "closeEvent"):
                try:
                    view.closeEvent(event)
                except Exception:
                    pass
        self._section_instances.clear()

        # Cascade clean shutdown to background audio and bridge services
        try:
            from gui.optimus.service_bridge import OptimusServiceBridge
            OptimusServiceBridge.reset()
        except Exception:
            pass
        try:
            from core.audio_stream import ContinuousAudioStreamPlayer
            ContinuousAudioStreamPlayer.reset_instance()
        except Exception:
            pass
        super().closeEvent(event)
