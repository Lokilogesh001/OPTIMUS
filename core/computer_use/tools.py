"""core/computer_use/tools.py — Tool implementations for Visual Computer Use.

Exposes high-level tools registered with ToolRegistry:
- computer_observe: Capture structured UI observation of desktop or window.
- computer_find_element: Locate UI elements by name and role with confidence scoring.
- computer_interact: Execute a complete 7-step actuation lifecycle (click, hover, etc.).
- computer_type_text: Type text into a named control or currently focused window.
- computer_hotkey: Send keyboard shortcuts (e.g. Ctrl+S, Alt+Tab).
- computer_switch_window: Bring an application window to the foreground.
- computer_verify_state: Verify expected window state or presence of UI elements.
"""

from __future__ import annotations

import json
from typing import Any

from core.computer_use.actuation import get_computer_actuator
from core.computer_use.lifecycle import get_computer_lifecycle
from core.computer_use.observation import get_desktop_observer
from core.computer_use.types import InteractionAction
from core.computer_use.understanding import get_visual_understanding
from core.orchestrator.tools import CapabilityTier, ToolDefinition, ToolRegistry, ToolResult


def register_computer_tools(registry: ToolRegistry) -> None:
    """Register all computer use tools into the central ToolRegistry."""
    observer = get_desktop_observer()
    understanding = get_visual_understanding()
    actuator = get_computer_actuator()
    lifecycle = get_computer_lifecycle()

    # 1. computer_observe
    def _observe_handler(params: dict, context: dict | None = None) -> ToolResult:
        target_win = params.get("target_window_title")
        capture_shot = bool(params.get("capture_screenshot", False))
        obs = observer.observe(target_window_title=target_win, capture_screenshot=capture_shot)
        ui_state = understanding.classify_ui_state(obs)

        top_elements = [
            {"id": e.element_id, "role": e.role, "name": e.name, "rect": list(e.rect)}
            for e in obs.elements[:30]
        ]

        summary = (
            f"Active Application: {obs.active_app} (PID: {obs.pid})\n"
            f"Window Title: {obs.window_title}\n"
            f"Screen Bounds: {obs.screen_bounds} (Scale: {obs.scale_factor})\n"
            f"Elements Detected: {len(obs.elements)} (Source: {obs.source})\n"
            f"State Digest: {obs.digest}\n"
            f"UI State: Dialog={ui_state['is_dialog']}, Loading={ui_state['is_loading']}, "
            f"Buttons={len(ui_state['buttons'])}, Inputs={len(ui_state['inputs'])}\n"
            f"Top Interactive Elements:\n"
            + "\n".join(f" - [{e['role']}] '{e['name']}' at {e['rect']}" for e in top_elements[:10])
        )

        return ToolResult(
            status="done",
            output=summary,
            evidence={
                "active_app": obs.active_app,
                "window_title": obs.window_title,
                "elements_count": len(obs.elements),
                "digest": obs.digest,
                "screenshot_path": obs.screenshot_path,
                "ui_state": ui_state,
                "top_elements": top_elements,
            },
        )

    registry.register(
        ToolDefinition(
            name="computer_observe",
            description="Capture a live observation of the desktop or a specific application window, including structured UI elements and dialog states.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "target_window_title": {"type": "STRING", "description": "Optional title or process name of target window to observe"},
                    "capture_screenshot": {"type": "BOOLEAN", "description": "Whether to capture and save a PNG screenshot on disk"}
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_observe_handler,
            is_mutating=False,
        )
    )

    # 2. computer_find_element
    def _find_handler(params: dict, context: dict | None = None) -> ToolResult:
        query = str(params.get("query") or "")
        role = params.get("role")
        target_win = params.get("target_window_title")

        obs = observer.observe(target_window_title=target_win)
        el, conf, reason = understanding.find_best_element(obs, query=query, role=role)

        if not el:
            return ToolResult(
                status="failed",
                error=reason,
                output=f"Element lookup failed: {reason}",
                evidence={"query": query, "role": role, "confidence": conf, "window": obs.window_title},
            )

        output = (
            f"Element located: '{el.name}' [role={el.role}, id={el.element_id}]\n"
            f"Confidence: {conf:.2f}\n"
            f"Bounding Box: {el.rect} (Center: {el.center})\n"
            f"Interactable: {el.interactable}, Enabled: {el.enabled}, Focused: {el.focused}"
        )
        return ToolResult(
            status="done",
            output=output,
            evidence={
                "element_id": el.element_id,
                "name": el.name,
                "role": el.role,
                "rect": list(el.rect),
                "center": list(el.center),
                "confidence": conf,
            },
        )

    registry.register(
        ToolDefinition(
            name="computer_find_element",
            description="Find and resolve an interactive UI element on screen by name, text, or role with confidence scoring and ambiguity detection.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Name, label, or visible text of the UI element"},
                    "role": {"type": "STRING", "description": "Optional UI role (e.g. 'button', 'edit', 'menu_item', 'dialog', 'checkbox')"},
                    "target_window_title": {"type": "STRING", "description": "Optional target window title"}
                },
                "required": ["query"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_find_handler,
            is_mutating=False,
        )
    )

    # 3. computer_interact
    def _interact_handler(params: dict, context: dict | None = None) -> ToolResult:
        action_type = str(params.get("action_type") or "").strip().lower()
        target_name = params.get("target_name")
        target_role = params.get("target_role")
        raw_coords = params.get("coords")
        coords = tuple(raw_coords) if (raw_coords and len(raw_coords) >= 2) else None
        text = params.get("text")
        keys = params.get("keys")
        expected_postcondition = params.get("expected_postcondition")
        is_pre_approved = bool(params.get("is_pre_approved", False) or (context or {}).get("is_approved"))

        action = InteractionAction(
            action_type=action_type,
            target_name=target_name,
            target_role=target_role,
            coords=coords,
            text=text,
            keys=keys,
        )

        res = lifecycle.execute_interaction(
            action=action,
            expected_postcondition=expected_postcondition,
            is_pre_approved=is_pre_approved,
        )

        if not res.success:
            return ToolResult(
                status="failed",
                error=res.error or res.details,
                output=f"Interaction '{action_type}' failed: {res.details}",
                evidence=res.to_dict(),
            )

        output = (
            f"Interaction '{action_type}' succeeded: {res.details}\n"
            f"Confidence: {res.confidence}\n"
            f"State Changed: {res.state_changed}\n"
            f"State Digests: {res.before_digest} -> {res.after_digest}"
        )
        return ToolResult(
            status="done",
            output=output,
            evidence=res.to_dict(),
        )

    registry.register(
        ToolDefinition(
            name="computer_interact",
            description="Execute a safe 7-step computer interaction lifecycle: observe, interpret target, plan, authorize, actuate, observe again, and verify postconditions.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "action_type": {"type": "STRING", "description": "Action to perform: 'click', 'double_click', 'right_click', 'hover', 'type', 'press', 'hotkey', 'scroll', 'switch_window'"},
                    "target_name": {"type": "STRING", "description": "Name or text of target element/window to act upon"},
                    "target_role": {"type": "STRING", "description": "Optional control role (button, edit, etc.)"},
                    "coords": {"type": "ARRAY", "description": "Optional explicit [x, y] coordinates"},
                    "text": {"type": "STRING", "description": "Text to type if action_type is 'type'"},
                    "keys": {"type": "ARRAY", "description": "Keys to press if action_type is 'press' or 'hotkey'"},
                    "expected_postcondition": {"type": "OBJECT", "description": "Optional expected postconditions to verify (e.g. {'expected_title': '...', 'state_changed': True})"},
                    "is_pre_approved": {"type": "BOOLEAN", "description": "Whether action has pre-approval"}
                },
                "required": ["action_type"],
            },
            capability_tier=CapabilityTier.SYSTEM_MUTATING,
            handler=_interact_handler,
            is_mutating=True,
        )
    )

    # 4. computer_type_text
    def _type_handler(params: dict, context: dict | None = None) -> ToolResult:
        text = str(params.get("text") or "")
        target_name = params.get("target_name")
        is_pre_approved = bool((context or {}).get("is_approved"))

        if target_name:
            # Click the target input first to focus it
            click_action = InteractionAction(action_type="click", target_name=target_name, target_role="edit")
            res_click = lifecycle.execute_interaction(click_action, is_pre_approved=is_pre_approved)
            if not res_click.success:
                return ToolResult(status="failed", error=res_click.details, output=f"Failed to focus '{target_name}': {res_click.details}")

        type_action = InteractionAction(action_type="type", text=text)
        res_type = lifecycle.execute_interaction(type_action, is_pre_approved=is_pre_approved)

        if not res_type.success:
            return ToolResult(status="failed", error=res_type.details, output=f"Failed to type text: {res_type.details}")

        return ToolResult(
            status="done",
            output=f"Successfully typed text into target window/control (state_changed={res_type.state_changed}).",
            evidence=res_type.to_dict(),
        )

    registry.register(
        ToolDefinition(
            name="computer_type_text",
            description="Type text into a designated UI input control or the currently focused application window.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING", "description": "Text string to enter"},
                    "target_name": {"type": "STRING", "description": "Optional name of the target input element to focus first"}
                },
                "required": ["text"],
            },
            capability_tier=CapabilityTier.SYSTEM_MUTATING,
            handler=_type_handler,
            is_mutating=True,
        )
    )

    # 5. computer_hotkey
    def _hotkey_handler(params: dict, context: dict | None = None) -> ToolResult:
        keys = list(params.get("keys") or [])
        if not keys:
            return ToolResult(status="failed", error="No keys provided for hotkey", output="No keys provided")

        action = InteractionAction(action_type="hotkey", keys=keys)
        res = lifecycle.execute_interaction(action, is_pre_approved=bool((context or {}).get("is_approved")))
        if not res.success:
            return ToolResult(status="failed", error=res.details, output=f"Hotkey failed: {res.details}")

        return ToolResult(
            status="done",
            output=f"Executed hotkey shortcut '{'+'.join(keys)}' (state_changed={res.state_changed}).",
            evidence=res.to_dict(),
        )

    registry.register(
        ToolDefinition(
            name="computer_hotkey",
            description="Send a keyboard shortcut or key combination (e.g. ['ctrl', 's'], ['alt', 'tab'], ['escape']).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "keys": {"type": "ARRAY", "description": "List of key strings to press simultaneously"}
                },
                "required": ["keys"],
            },
            capability_tier=CapabilityTier.SYSTEM_MUTATING,
            handler=_hotkey_handler,
            is_mutating=True,
        )
    )

    # 6. computer_switch_window
    def _switch_handler(params: dict, context: dict | None = None) -> ToolResult:
        title = str(params.get("window_title") or "")
        action = InteractionAction(action_type="switch_window", target_name=title)
        res = lifecycle.execute_interaction(action, is_pre_approved=True)

        if not res.success:
            return ToolResult(status="failed", error=res.details, output=f"Window switch failed: {res.details}")

        return ToolResult(
            status="done",
            output=f"Successfully switched foreground focus to window matching '{title}'.",
            evidence=res.to_dict(),
        )

    registry.register(
        ToolDefinition(
            name="computer_switch_window",
            description="Switch foreground application focus to a specified window by title or process name.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "window_title": {"type": "STRING", "description": "Title or executable name of the target window"}
                },
                "required": ["window_title"],
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_switch_handler,
            is_mutating=False,
        )
    )

    # 7. computer_verify_state
    def _verify_handler(params: dict, context: dict | None = None) -> ToolResult:
        exp_title = params.get("expected_title")
        exp_text = params.get("expected_text")
        exp_el = params.get("expected_element")

        obs = observer.observe()
        passed = True
        failures = []

        if exp_title:
            if exp_title.lower() not in obs.window_title.lower():
                passed = False
                failures.append(f"Window title '{obs.window_title}' does not contain '{exp_title}'")

        if exp_text:
            text_match = any(exp_text.lower() in (e.name.lower() + " " + e.value.lower()) for e in obs.elements)
            if not text_match:
                passed = False
                failures.append(f"Expected text '{exp_text}' not visible on screen")

        if exp_el:
            el_match = obs.find_element(name=exp_el)
            if not el_match:
                passed = False
                failures.append(f"Expected element '{exp_el}' not found in active UI tree")

        if not passed:
            return ToolResult(
                status="failed",
                error="; ".join(failures),
                output=f"Verification failed: {'; '.join(failures)}",
                evidence={"window_title": obs.window_title, "active_app": obs.active_app, "digest": obs.digest},
            )

        return ToolResult(
            status="done",
            output=f"State verification succeeded for window '{obs.window_title}' (App: {obs.active_app}).",
            evidence={"window_title": obs.window_title, "active_app": obs.active_app, "digest": obs.digest},
        )

    registry.register(
        ToolDefinition(
            name="computer_verify_state",
            description="Verify that the desktop UI matches expected visual conditions (window title, visible text, or active elements).",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "expected_title": {"type": "STRING", "description": "Expected substring in window title"},
                    "expected_text": {"type": "STRING", "description": "Expected text anywhere in active UI elements"},
                    "expected_element": {"type": "STRING", "description": "Expected control name to be visible"}
                },
            },
            capability_tier=CapabilityTier.READ_ONLY,
            handler=_verify_handler,
            is_mutating=False,
        )
    )
