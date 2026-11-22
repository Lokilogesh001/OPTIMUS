"""core/application_intelligence/frameworks/pywinauto/control_patterns.py — UIA Control Pattern Invocation.

Directly invokes Windows UI Automation control patterns (ValuePattern, InvokePattern,
TogglePattern, SelectionItemPattern) without mouse movement, and traverses deep menu hierarchies.
"""

from __future__ import annotations

import logging
from typing import Any

from core.application_intelligence.frameworks.base import com_apartment

logger = logging.getLogger("optimus.application_intelligence.frameworks.pywinauto.patterns")


class UIAControlPatterns:
    """Invoker for native UI Automation patterns via pywinauto."""

    @staticmethod
    def set_control_value(window_title_or_hwnd: str | int | Any, control_name: str | dict[str, Any] = "", value: str = "", **kwargs: Any) -> dict[str, Any]:
        """Invoke ValuePattern.SetValue() to set text directly without simulated keystrokes."""
        if not value and "new_value" in kwargs:
            value = kwargs["new_value"]
        if not control_name and "search_criteria" in kwargs:
            sc = kwargs["search_criteria"]
            control_name = sc.get("auto_id") or sc.get("title") or sc.get("control_type", "")

        with com_apartment():
            try:
                from pywinauto import Application
                if hasattr(window_title_or_hwnd, "child_window"):
                    win = window_title_or_hwnd
                elif isinstance(window_title_or_hwnd, int):
                    app = Application(backend="uia").connect(handle=window_title_or_hwnd)
                    win = app.window(handle=window_title_or_hwnd)
                else:
                    app = Application(backend="uia").connect(title_re=f".*{window_title_or_hwnd}.*")
                    win = app.top_window()

                # Find child control matching control_name
                ctrl = win.child_window(title_re=f".*{control_name}.*", control_type="Edit")
                if not ctrl.exists(timeout=2.0):
                    ctrl = win.child_window(auto_id=str(control_name))

                if ctrl.exists(timeout=1.0):
                    ctrl.set_edit_text(value)
                    return {
                        "success": True,
                        "action": "value_pattern_set",
                        "pattern_used": "ValuePattern",
                        "control": str(control_name),
                        "value": value,
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Control '{control_name}' not found in window '{window_title_or_hwnd}'.",
                    }
            except Exception as ex:
                logger.debug("pywinauto set_control_value exception: %s", ex)
                return {"success": False, "error": str(ex)}

    @staticmethod
    def invoke_button(window_title_or_hwnd: str | int | Any, button_name: str | dict[str, Any] = "", **kwargs: Any) -> dict[str, Any]:
        """Invoke InvokePattern.Invoke() on a button without moving the mouse."""
        if not button_name and "search_criteria" in kwargs:
            sc = kwargs["search_criteria"]
            button_name = sc.get("auto_id") or sc.get("title") or sc.get("name", "")

        with com_apartment():
            try:
                from pywinauto import Application
                if hasattr(window_title_or_hwnd, "child_window"):
                    win = window_title_or_hwnd
                elif isinstance(window_title_or_hwnd, int):
                    app = Application(backend="uia").connect(handle=window_title_or_hwnd)
                    win = app.window(handle=window_title_or_hwnd)
                else:
                    app = Application(backend="uia").connect(title_re=f".*{window_title_or_hwnd}.*")
                    win = app.top_window()

                btn = win.child_window(title_re=f".*{button_name}.*", control_type="Button")
                if not btn.exists(timeout=2.0):
                    btn = win.child_window(auto_id=str(button_name))

                if btn.exists(timeout=1.0):
                    btn.invoke()
                    return {
                        "success": True,
                        "action": "invoke_pattern_invoked",
                        "pattern_used": "InvokePattern",
                        "button": str(button_name),
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Button '{button_name}' not found in window.",
                    }
            except Exception as ex:
                logger.debug("pywinauto invoke_button exception: %s", ex)
                return {"success": False, "error": str(ex)}

    @staticmethod
    def select_menu_path(window_title_or_hwnd: str | int | Any = None, menu_path: str = "", **kwargs: Any) -> dict[str, Any]:
        """Traverse a multi-level menu path (e.g. 'File -> Save As' or 'Edit -> Find')."""
        if not menu_path and "menu_path" in kwargs:
            menu_path = kwargs["menu_path"]
        target = window_title_or_hwnd or kwargs.get("window_spec")

        with com_apartment():
            try:
                from pywinauto import Application
                if hasattr(target, "menu_select"):
                    win = target
                elif isinstance(target, int):
                    app = Application(backend="win32").connect(handle=target)
                    win = app.window(handle=target)
                else:
                    app = Application(backend="win32").connect(title_re=f".*{target}.*")
                    win = app.top_window()

                win.menu_select(menu_path)
                return {
                    "success": True,
                    "action": "menu_path_selected",
                    "menu_path": menu_path,
                }
            except Exception as ex:
                logger.debug("pywinauto select_menu_path exception: %s", ex)
                return {"success": False, "error": str(ex)}

    # Aliases
    set_value = set_control_value
    invoke_control = invoke_button


PywinautoControlPatterns = UIAControlPatterns
