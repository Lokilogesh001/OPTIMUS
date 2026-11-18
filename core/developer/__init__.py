"""core/developer — Autonomous Developer Capabilities for OPTIMUS.

Modules:
- workspace: DevelopmentWorkspace with boundary confinement, backup snapshots, diffs, rollback, and symbol search.
- terminal: TerminalManager for sandboxed command execution, risk classification, timeouts, process tree cleanup, and secret redaction.
- lifecycle: DeveloperTaskPlan, LifecyclePhase, and 8-phase execution state machine.
- tools: Developer Tool Suite registered into ToolRegistry.
"""

from core.developer.lifecycle import (
    LIFECYCLE_SEQUENCE,
    DeveloperTaskPlan,
    LifecyclePhase,
    PhaseResult,
)
from core.developer.terminal import (
    CommandExecutionRecord,
    TerminalManager,
)
from core.developer.tools import (
    get_active_terminal,
    get_active_workspace,
    get_developer_tools,
    register_developer_tools,
)
from core.developer.workspace import (
    DevelopmentWorkspace,
    FileSnapshot,
    WorkspaceModification,
)

__all__ = [
    "LIFECYCLE_SEQUENCE",
    "CommandExecutionRecord",
    "DeveloperTaskPlan",
    "DevelopmentWorkspace",
    "FileSnapshot",
    "LifecyclePhase",
    "PhaseResult",
    "TerminalManager",
    "WorkspaceModification",
    "get_active_terminal",
    "get_active_workspace",
    "get_developer_tools",
    "register_developer_tools",
]
