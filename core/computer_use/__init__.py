"""core/computer_use — Computer Use & Visual Intelligence Subsystem for OPTIMUS.

Implements Milestone 9:
- Observation: Live desktop observation, window geometry, UIA element trees, credential protection.
- Understanding: Visual UI state interpretation, element matching, confidence scoring, observation diffing.
- Actuation: Bounded mouse/keyboard actuation, coordinate validation, anti-runaway tripwires.
- Lifecycle: Canonical 7-step interaction loop (OBSERVE -> INTERPRET -> PLAN -> AUTHORIZE -> ACT -> OBSERVE AGAIN -> VERIFY).
- Tools: High-level tool wrappers registered with ToolRegistry.
"""

from __future__ import annotations

from core.computer_use.actuation import (
    BoundaryViolationError,
    ComputerActuator,
    RunawayInteractionError,
    get_computer_actuator,
)
from core.computer_use.lifecycle import (
    ComputerInteractionLifecycle,
    get_computer_lifecycle,
)
from core.computer_use.observation import (
    DesktopObserver,
    attach_desktop,
    get_desktop_observer,
    get_foreground_window_info,
    get_open_windows,
)
from core.computer_use.tools import register_computer_tools
from core.computer_use.types import (
    DesktopObservation,
    InteractionAction,
    InteractionResult,
    UIElement,
)
from core.computer_use.understanding import (
    VisualUnderstandingPipeline,
    get_visual_understanding,
)

__all__ = [
    "BoundaryViolationError",
    "ComputerActuator",
    "ComputerInteractionLifecycle",
    "DesktopObservation",
    "DesktopObserver",
    "InteractionAction",
    "InteractionResult",
    "RunawayInteractionError",
    "UIElement",
    "VisualUnderstandingPipeline",
    "attach_desktop",
    "get_computer_actuator",
    "get_computer_lifecycle",
    "get_desktop_observer",
    "get_foreground_window_info",
    "get_open_windows",
    "get_visual_understanding",
    "register_computer_tools",
]
