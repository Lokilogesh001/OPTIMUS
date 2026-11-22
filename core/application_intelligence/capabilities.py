"""core/application_intelligence/capabilities.py — Application Capability Definitions & Safety Filters.

Defines standard capability descriptors, argument validation schemas,
and negative control heuristic filters to prevent destructive actions during autonomous learning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Negative control patterns: any UI element matching these is hard-blocked from automated exploration
# Reconciles CRIT-5 and FINDING-M17-03
DESTRUCTIVE_CONTROL_PATTERNS = re.compile(
    r"\b(?:delete|remove|erase|format|reset|wipe|discard|drop|truncate|clear|destroy|terminate|send|submit|pay|purchase|checkout|buy|transfer|authorize)\b",
    re.IGNORECASE,
)

# Read-only or safe navigation keywords allowed during autonomous exploration
SAFE_NAVIGATION_PATTERNS = re.compile(
    r"\b(?:file|edit|view|help|about|window|tools|options|preferences|settings|tabs|search|find|info|export|copy|zoom|inspect)\b",
    re.IGNORECASE,
)


def is_control_destructive(label: str, automation_id: str = "", role: str = "") -> bool:
    """Check if a UI control matches destructive action patterns."""
    text_to_check = f"{label} {automation_id}".strip()
    return bool(DESTRUCTIVE_CONTROL_PATTERNS.search(text_to_check))


def is_control_safe_for_exploration(label: str, automation_id: str = "", role: str = "") -> bool:
    """Verify that a UI control is safe to inspect or expand during passive/active learning."""
    if is_control_destructive(label, automation_id, role):
        return False
    # Dangerous roles to actuate blindly
    if role.lower() in ("combobox", "password", "checkbox"):
        return False
    return True


@dataclass
class CapabilityDescriptor:
    """Formal description of an application capability."""
    name: str
    description: str
    target_category: str
    required_parameters: list[str] = field(default_factory=list)
    optional_parameters: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    is_consequential: bool = False

    def validate_args(self, args: dict[str, Any]) -> tuple[bool, str]:
        for req in self.required_parameters:
            if req not in args or args[req] is None or str(args[req]).strip() == "":
                return False, f"Missing required parameter '{req}' for capability '{self.name}'."
        return True, ""


# Standard registered capabilities across OPTIMUS application intelligence
STANDARD_CAPABILITIES: dict[str, CapabilityDescriptor] = {
    # Communication
    "whatsapp_send_message": CapabilityDescriptor(
        name="whatsapp_send_message",
        description="Draft and send a message to a WhatsApp contact.",
        target_category="communication",
        required_parameters=["recipient", "message"],
        requires_approval=True,          # Reconciles CRIT-2 & FINDING-M17-02
        is_consequential=True,
    ),
    "whatsapp_search_contact": CapabilityDescriptor(
        name="whatsapp_search_contact",
        description="Search for a contact or conversation in WhatsApp.",
        target_category="communication",
        required_parameters=["query"],
        requires_approval=False,
    ),
    # Browser & Web
    "web_search": CapabilityDescriptor(
        name="web_search",
        description="Search the web across multiple sources and synthesize a compared answer.",
        target_category="browser",
        required_parameters=["query"],
        optional_parameters={"max_sources": 3},
        requires_approval=False,
    ),
    "browser_navigate": CapabilityDescriptor(
        name="browser_navigate",
        description="Open or switch to a browser tab and navigate to a URL.",
        target_category="browser",
        required_parameters=["url"],
        requires_approval=False,
    ),
    "browser_download": CapabilityDescriptor(
        name="browser_download",
        description="Download a file via browser and verify its saved location and integrity.",
        target_category="browser",
        required_parameters=["url"],
        optional_parameters={"destination_dir": None},
        requires_approval=False,
    ),
    # Documents
    "open_document": CapabilityDescriptor(
        name="open_document",
        description="Find and open a document using its associated desktop application.",
        target_category="document_viewer",
        required_parameters=["query"],
        optional_parameters={"file_extension": None, "target_folder": None},
        requires_approval=False,
    ),
    # Development
    "vscode_open_workspace": CapabilityDescriptor(
        name="vscode_open_workspace",
        description="Open a project directory or file in Visual Studio Code.",
        target_category="development",
        required_parameters=["path"],
        requires_approval=False,
    ),
    "run_development_command": CapabilityDescriptor(
        name="run_development_command",
        description="Execute an approved developer command or test runner in a workspace.",
        target_category="development",
        required_parameters=["command"],
        optional_parameters={"working_directory": None},
        requires_approval=False,
        is_consequential=True,
    ),
}
