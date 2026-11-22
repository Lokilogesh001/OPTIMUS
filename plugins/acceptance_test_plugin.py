"""plugins/acceptance_test_plugin.py — Canonical Harmless Acceptance-Test Plugin.

Used for verifying the full plugin lifecycle: discovery, AST validation, schema validation,
PolicyEngine authorization, execution, settings persistence, enable/disable toggling,
and secret-redacted error containment.
"""
from __future__ import annotations

from typing import Any
from memory.config_manager import get_plugin_setting

PLUGIN = {
    "name": "acceptance_test_plugin",
    "version": "1.0.0",
    "description": "Harmless acceptance-test plugin for verifying plugin lifecycle, settings, and security.",
    "tier": "READ_ONLY",
    "capabilities": ["test", "math", "settings:read"],
    "behavior": "BLOCKING",
    "scheduling": "WHEN_IDLE",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Operation to perform: 'health', 'calculate', 'get_setting', or 'trigger_error'",
                "enum": ["health", "calculate", "get_setting", "trigger_error"],
            },
            "a": {"type": "NUMBER", "description": "First number for calculation"},
            "b": {"type": "NUMBER", "description": "Second number for calculation"},
            "operator": {"type": "STRING", "description": "Arithmetic operator: '+', '-', '*', '/'", "default": "+"},
        },
        "required": ["action"],
    },
}

def _test_connection_action(values: dict[str, Any]) -> tuple[bool, str]:
    """Action callback for PluginSettingsOverlay verification."""
    threshold = values.get("test_threshold", 100)
    return True, f"Connection verified. Active threshold: {threshold}"

PLUGIN_SETTINGS = {
    "namespace": "acceptance_test_plugin",
    "title": "Acceptance Test Plugin Settings",
    "fields": [
        {"key": "test_threshold", "name": "test_threshold", "type": "int", "default": 100, "label": "Test Threshold"},
        {"key": "test_api_key", "name": "test_api_key", "type": "secret", "default": "", "label": "Simulated Secret Key"},
    ],
    "action": {
        "label": "TEST",
        "run": _test_connection_action,
    },
}

def run(parameters: dict[str, Any], player=None, session_memory=None) -> str:
    """Execute harmless plugin operations."""
    action = str(parameters.get("action") or "health").strip().lower()

    if action == "health":
        result = "Acceptance Test Plugin is healthy. Version: 1.0.0."
    elif action == "calculate":
        try:
            a = float(parameters.get("a", 0))
            b = float(parameters.get("b", 0))
            op = str(parameters.get("operator", "+")).strip()
            if op in ("+", "add"):
                ans = a + b
            elif op in ("-", "sub", "subtract"):
                ans = a - b
            elif op in ("*", "mul", "multiply"):
                ans = a * b
            elif op in ("/", "div", "divide"):
                if b == 0:
                    return "Error: Division by zero is undefined."
                ans = a / b
            else:
                return f"Error: Unsupported operator '{op}'."
            result = f"Calculation result: {ans:.2f}"
        except Exception as ex:
            return f"Calculation error: {ex}"
    elif action == "get_setting":
        val = get_plugin_setting("acceptance_test_plugin", "test_threshold", default=100)
        result = f"Configured test_threshold is {val}."
    elif action == "trigger_error":
        # Simulates an exception containing a simulated credential to verify secret redaction
        raise RuntimeError("Simulated failure with secret key sk-live-1234567890abcdef1234567890")
    else:
        result = f"Unknown action: '{action}'."

    if player and hasattr(player, "write_log"):
        try:
            player.write_log(f"JARVIS: {result}")
        except Exception:
            pass

    return result
