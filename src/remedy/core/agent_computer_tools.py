"""Register provider-agnostic computer-use tools on the runtime."""

from __future__ import annotations

from typing import Any

from remedy.core.computer.executor import get_computer_executor
from remedy.core.computer.types import ComputerAction


def register_computer_tools(runtime: Any) -> None:
    """Always-on computer use (browser rail + full desktop). No feature gate."""

    home = None
    with __import__("contextlib").suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    ex = get_computer_executor(home)

    async def computer_screenshot(
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Capture the browser rail or full desktop. Prefer before click/type."""
        return ex.run(
            ComputerAction.SCREENSHOT,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
        )

    async def computer_click(
        x: int = 0,
        y: int = 0,
        button: str = "left",
        clicks: int = 1,
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Click at screen (desktop) or embed coordinates (browser). Use screenshot first."""
        return ex.run(
            ComputerAction.CLICK,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            button=button,
            clicks=clicks,
        )

    async def computer_type(
        text: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Type text into the focused UI (browser or desktop)."""
        return ex.run(
            ComputerAction.TYPE,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            text=text,
        )

    async def computer_key(
        key: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Press a key or combo (enter, tab, ctrl+s, alt+f4, …)."""
        return ex.run(
            ComputerAction.KEY,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            key=key,
        )

    async def computer_scroll(
        x: int = 0,
        y: int = 0,
        dy: int = -3,
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Scroll at a point. dy>0 scrolls up, dy<0 scrolls down (notches)."""
        return ex.run(
            ComputerAction.SCROLL,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            dy=dy,
        )

    async def computer_navigate(
        url: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Open a URL in the in-app browser rail (preferred) or system browser."""
        return ex.run(
            ComputerAction.NAVIGATE,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            url=url,
        )

    async def computer_windows(
        mode: str = "list",
        hwnd: int = 0,
        limit: int = 40,
        target: str = "desktop",
        hint: str = "",
    ) -> str:
        """List visible windows or focus one by hwnd (desktop)."""
        return ex.run(
            ComputerAction.WINDOWS,
            target=target or "desktop",
            hint=hint,
            runtime=runtime,
            mode=mode,
            hwnd=hwnd,
            limit=limit,
        )

    async def computer_drag(
        x: int = 0,
        y: int = 0,
        x2: int = 0,
        y2: int = 0,
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Drag from (x,y) to (x2,y2)."""
        return ex.run(
            ComputerAction.DRAG,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            x2=x2,
            y2=y2,
        )

    reg = runtime.tool_registry
    target_prop = {
        "type": "string",
        "description": "auto | browser | desktop — auto routes web→rail, else OS",
    }
    hint_prop = {
        "type": "string",
        "description": "Optional task hint for auto routing",
    }

    reg.register_builtin_handler(
        "computer_screenshot",
        "Screenshot the in-app browser or full desktop. Call before clicking when unsure.",
        computer_screenshot,
        {
            "type": "object",
            "properties": {
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_click",
        "Click at coordinates (desktop screen or browser embed). Prefer screenshot first.",
        computer_click,
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "description": "left | right | middle"},
                "clicks": {"type": "integer", "description": "1 or 2 for double-click"},
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["x", "y"],
        },
    )
    reg.register_builtin_handler(
        "computer_type",
        "Type text into the focused control (browser or desktop).",
        computer_type,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["text"],
        },
    )
    reg.register_builtin_handler(
        "computer_key",
        "Press a key or combo: enter, tab, escape, ctrl+s, alt+f4, win, …",
        computer_key,
        {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["key"],
        },
    )
    reg.register_builtin_handler(
        "computer_scroll",
        "Scroll the wheel at (x,y). dy notches (negative = down).",
        computer_scroll,
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "dy": {"type": "integer"},
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_navigate",
        "Open a URL in the in-app browser rail (or system browser if host offline).",
        computer_navigate,
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["url"],
        },
    )
    reg.register_builtin_handler(
        "computer_windows",
        "List visible OS windows or focus by hwnd (desktop target).",
        computer_windows,
        {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "list | focus",
                },
                "hwnd": {"type": "integer"},
                "limit": {"type": "integer"},
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_drag",
        "Drag from (x,y) to (x2,y2) on desktop or browser.",
        computer_drag,
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "x2": {"type": "integer"},
                "y2": {"type": "integer"},
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["x", "y", "x2", "y2"],
        },
    )
