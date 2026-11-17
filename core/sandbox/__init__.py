"""core/sandbox — Sandboxed Execution & Specialized Tooling for OPTIMUS.

Binds:
- Command execution security guard and timeout enforcement.
- Code intelligence tools (code_ast_search, code_explain, code_test_runner).
- System diagnosis tools (system_info, cpu_status, memory_status, disk_status, gpu_status, process_diagnostics).
- Data intelligence tools (data_read_tabular, data_transform, data_compute_statistics).
- Research tools (search_web, fetch_page, extract_citations).
- Execution tools (execute_command, script_runner, process_status, process_kill).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.orchestrator.tools import ToolRegistry

from core.sandbox.command_guard import (
    PROHIBITED_COMMAND_PATTERNS,
    check_command_safety,
    execute_sandboxed_command,
    kill_process_tree,
)
from core.sandbox.tools_code import get_code_tools
from core.sandbox.tools_data import get_data_tools
from core.sandbox.tools_execution import get_execution_tools
from core.sandbox.tools_research import get_research_tools
from core.sandbox.tools_system import get_system_tools


def register_sandbox_tools(registry: ToolRegistry) -> None:
    """Register all specialized sandbox tools into the central ToolRegistry."""
    for tool_def in get_code_tools():
        registry.register(tool_def)

    for tool_def in get_system_tools():
        registry.register(tool_def)

    for tool_def in get_data_tools():
        registry.register(tool_def)

    for tool_def in get_research_tools():
        registry.register(tool_def)

    for tool_def in get_execution_tools():
        registry.register(tool_def)


__all__ = [
    "PROHIBITED_COMMAND_PATTERNS",
    "check_command_safety",
    "execute_sandboxed_command",
    "get_code_tools",
    "get_data_tools",
    "get_execution_tools",
    "get_research_tools",
    "get_system_tools",
    "kill_process_tree",
    "register_sandbox_tools",
]
