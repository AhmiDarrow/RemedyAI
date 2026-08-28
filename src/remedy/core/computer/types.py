"""Shared types and tool name sets for computer use."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

# Explicit targets the model (or router) may choose.
ComputerTargetName = Literal["auto", "browser", "desktop"]

# Tool names registered on the runtime (provider-agnostic OpenAI tools).
COMPUTER_TOOL_NAMES = frozenset(
    {
        "computer_screenshot",
        "computer_snapshot",
        "computer_click",
        "computer_type",
        "computer_key",
        "computer_scroll",
        "computer_navigate",
        "computer_windows",
        "computer_monitors",
        "computer_drag",
        "computer_wait",
        "computer_app",
        "computer_apps",
        "computer_page_text",
        "computer_find",
        "computer_act",
        "computer_press_hold",
        "computer_select",
        "computer_fill",
        "computer_hover",
    }
)

# Safe enough for Plan mode research (no typing / OS click storm).
COMPUTER_PLAN_MODE_TOOLS = frozenset(
    {
        "computer_screenshot",
        "computer_snapshot",
        "computer_navigate",
        "computer_windows",
        "computer_monitors",
        "computer_page_text",
        "computer_find",
        "computer_wait",
        "computer_apps",
    }
)


class ComputerAction(StrEnum):
    SCREENSHOT = "screenshot"
    SNAPSHOT = "snapshot"
    CLICK = "click"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    WINDOWS = "windows"
    MONITORS = "monitors"
    DRAG = "drag"
    PRESS_HOLD = "press_hold"
    WAIT = "wait"
    APP = "app"
    PAGE_TEXT = "page_text"
    FIND = "find"
    ACT = "act"
    SELECT = "select"
    FILL = "fill"
    HOVER = "hover"


def action_from_tool(name: str) -> ComputerAction | None:
    mapping = {
        "computer_screenshot": ComputerAction.SCREENSHOT,
        "computer_snapshot": ComputerAction.SNAPSHOT,
        "computer_click": ComputerAction.CLICK,
        "computer_type": ComputerAction.TYPE,
        "computer_key": ComputerAction.KEY,
        "computer_scroll": ComputerAction.SCROLL,
        "computer_navigate": ComputerAction.NAVIGATE,
        "computer_windows": ComputerAction.WINDOWS,
        "computer_monitors": ComputerAction.MONITORS,
        "computer_drag": ComputerAction.DRAG,
        "computer_press_hold": ComputerAction.PRESS_HOLD,
        "computer_wait": ComputerAction.WAIT,
        "computer_app": ComputerAction.APP,
        "computer_page_text": ComputerAction.PAGE_TEXT,
        "computer_find": ComputerAction.FIND,
        "computer_act": ComputerAction.ACT,
        "computer_select": ComputerAction.SELECT,
        "computer_fill": ComputerAction.FILL,
        "computer_hover": ComputerAction.HOVER,
    }
    return mapping.get(name)


def public_result(
    *,
    ok: bool,
    target: str,
    action: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": ok,
        "target": target,
        "action": action,
        "message": message,
    }
    if extra:
        out.update(extra)
    return out
