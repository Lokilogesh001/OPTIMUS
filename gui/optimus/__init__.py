"""gui/optimus — OPTIMUS Control Center UI Package.

Exported components:
- OptimusServiceBridge: thread-safe bridge connecting UI to backend services.
- OptimusNavBar: JARVIS-styled command-oriented navigation bar.
- OptimusDashboardView: live home screen with all 6 telemetry and execution modules.
- OptimusControlCenterWidget: top-level stacked workspace combining all views.
- Section views: Agents, Approvals, Command, Intelligence, Memory, Settings, System, Verification, Workflows.
"""

from __future__ import annotations

from gui.optimus.control_center import OptimusControlCenterWidget
from gui.optimus.dashboard import OptimusDashboardView
from gui.optimus.nav_bar import OptimusNavBar
from gui.optimus.section_agents import SectionAgents
from gui.optimus.section_approvals import SectionApprovals
from gui.optimus.section_command import SectionCommandCenter
from gui.optimus.section_intelligence import SectionIntelligence
from gui.optimus.section_memory import SectionMemory
from gui.optimus.section_planning import SectionPlanning
from gui.optimus.section_settings import SectionSettings
from gui.optimus.section_system import SectionSystem
from gui.optimus.section_verification import SectionVerification
from gui.optimus.section_workflows import SectionWorkflows
from gui.optimus.service_bridge import OptimusServiceBridge

# Aliases for consistent naming across tests and API consumers
OptimusAgentsSection = SectionAgents
OptimusApprovalsSection = SectionApprovals
OptimusCommandSection = SectionCommandCenter
OptimusIntelligenceSection = SectionIntelligence
OptimusMemorySection = SectionMemory
OptimusPlanningSection = SectionPlanning
OptimusSettingsSection = SectionSettings
OptimusSystemSection = SectionSystem
OptimusVerificationSection = SectionVerification
OptimusWorkflowsSection = SectionWorkflows

__all__ = [
    "OptimusAgentsSection",
    "OptimusApprovalsSection",
    "OptimusCommandSection",
    "OptimusControlCenterWidget",
    "OptimusDashboardView",
    "OptimusIntelligenceSection",
    "OptimusMemorySection",
    "OptimusNavBar",
    "OptimusPlanningSection",
    "OptimusServiceBridge",
    "OptimusSettingsSection",
    "OptimusSystemSection",
    "OptimusVerificationSection",
    "OptimusWorkflowsSection",
    "SectionAgents",
    "SectionApprovals",
    "SectionCommandCenter",
    "SectionIntelligence",
    "SectionMemory",
    "SectionPlanning",
    "SectionSettings",
    "SectionSystem",
    "SectionVerification",
    "SectionWorkflows",
]
