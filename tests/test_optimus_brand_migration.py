"""tests/test_optimus_brand_migration.py — Brand & Identity Migration Regression Tests.

Validates that:
1. OPTIMUS brand identity is the default across UI, Engine, Config, and Prompts.
2. Complete backward compatibility aliases are preserved (JarvisUI, JarvisLive, shutdown_jarvis).
3. Theme version constants reflect OPTIMUS release identity.
4. Subtitle and window title generation functions handle both OPTIMUS and legacy JARVIS.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure Mark-LVII-main root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))



def test_ui_brand_and_compatibility_alias():
    import ui
    assert hasattr(ui, "OptimusUI"), "ui module must export OptimusUI"
    assert hasattr(ui, "JarvisUI"), "ui module must export backward-compatible JarvisUI"
    assert ui.JarvisUI is ui.OptimusUI, "JarvisUI must be an exact alias of OptimusUI"


def test_main_live_engine_and_compatibility_alias():
    import main
    assert hasattr(main, "OptimusLive"), "main module must export OptimusLive"
    assert hasattr(main, "JarvisLive"), "main module must export backward-compatible JarvisLive"
    assert main.JarvisLive is main.OptimusLive, "JarvisLive must be an exact alias of OptimusLive"


def test_theme_release_constants():
    from gui import theme
    assert theme.APP_VERSION == "OPTIMUS", f"Expected APP_VERSION == 'OPTIMUS', got '{theme.APP_VERSION}'"
    assert theme.APP_PROTOCOL == "OPTIMUS", f"Expected APP_PROTOCOL == 'OPTIMUS', got '{theme.APP_PROTOCOL}'"


def test_config_manager_default_assistant_name():
    from memory.config_manager import get_assistant_name
    # When no config exists or assistant_name is unset, default must be OPTIMUS
    name = get_assistant_name()
    assert name == "OPTIMUS", f"Expected default assistant name 'OPTIMUS', got '{name}'"


def test_tool_declarations_include_optimus_and_jarvis_shutdown():
    import main
    tool_names = [t["name"] for t in main.TOOL_DECLARATIONS]
    assert "shutdown_optimus" in tool_names, "TOOL_DECLARATIONS must declare shutdown_optimus"
    assert "shutdown_jarvis" in tool_names, "TOOL_DECLARATIONS must declare shutdown_jarvis for compatibility"


def test_default_system_prompt_branding():
    import main
    prompt = main._load_system_prompt()
    assert "OPTIMUS" in prompt, "System prompt must refer to OPTIMUS"


def test_subtitle_generation_for_both_brands():
    from gui.window import MainWindow
    sub_optimus = MainWindow._subtitle("OPTIMUS")
    assert sub_optimus == "Autonomous Intelligence & Verification System"

    sub_jarvis = MainWindow._subtitle("JARVIS")
    assert sub_jarvis == "Just A Rather Very Intelligent System"

    sub_other = MainWindow._subtitle("CustomBot")
    assert sub_other == "Personal AI Assistant"
