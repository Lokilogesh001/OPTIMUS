"""core/application_intelligence/frameworks/ufo/app_agent_schemas.py — Action Schemas.

Provides stateless action schemas for application-specific desktop operations
(Windows Settings, File Explorer, Microsoft Office) without internal LLM loops (reconciles HIGH-1).
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

# Windows Settings URI mappings
SETTINGS_URI_MAP: dict[str, str] = {
    "apps": "ms-settings:appsfeatures",
    "network": "ms-settings:network",
    "wifi": "ms-settings:network-wifi",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "bluetooth": "ms-settings:bluetooth",
    "update": "ms-settings:windowsupdate",
    "storage": "ms-settings:storagesense",
    "about": "ms-settings:about",
    "notifications": "ms-settings:notifications",
}


class UFOAppAgentSchemas:
    """Stateless action schema executor for Windows system and Office targets."""

    @classmethod
    def get_settings_uri(cls, category: str) -> str:
        """Resolve a settings category to its ms-settings: protocol URI."""
        clean_cat = (category or "").strip().lower()
        if not clean_cat:
            return "ms-settings:"
        return SETTINGS_URI_MAP.get(clean_cat, "ms-settings:")

    @classmethod
    def get_explorer_command(cls, path: str, select_item: bool = False) -> list[str]:
        """Generate command arguments for launching or selecting in Windows File Explorer."""
        if select_item:
            return ["explorer.exe", "/select,", str(path)]
        return ["explorer.exe", str(path)]

    @staticmethod
    def launch_settings_category(category: str) -> dict[str, Any]:
        """Launch Windows Settings directly to a target sub-page via registered protocol URI."""
        clean_cat = category.strip().lower()
        uri = SETTINGS_URI_MAP.get(clean_cat, f"ms-settings:{clean_cat}")
        try:
            os.startfile(uri)
            return {
                "success": True,
                "action": "settings_launched",
                "uri": uri,
                "category": clean_cat,
            }
        except Exception as ex:
            return {
                "success": False,
                "error": f"Failed to open settings URI '{uri}': {ex}",
            }

    @staticmethod
    def explorer_navigate_and_select(folder_path: str, file_to_select: str | None = None) -> dict[str, Any]:
        """Navigate File Explorer to a directory, optionally selecting a specific file."""
        import subprocess
        try:
            if file_to_select and os.path.exists(file_to_select):
                cmd = ["explorer.exe", f"/select,{file_to_select}"]
            else:
                cmd = ["explorer.exe", folder_path]

            proc = subprocess.Popen(cmd)
            return {
                "success": True,
                "action": "explorer_opened",
                "pid": proc.pid,
                "target_path": folder_path,
                "selected_file": file_to_select,
            }
        except Exception as ex:
            return {
                "success": False,
                "error": f"Failed to execute explorer action: {ex}",
            }
