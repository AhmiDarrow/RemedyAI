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
        monitor: str = "",
    ) -> str:
        """Capture the browser rail or full desktop. Prefer before click/type.

        monitor: empty = full virtual screen / rail; integer index for one display.
        """
        return ex.run(
            ComputerAction.SCREENSHOT,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            monitor=monitor if str(monitor).strip() != "" else None,
        )

    async def computer_snapshot(
        target: str = "auto",
        hint: str = "",
        limit: int = 40,
    ) -> str:
        """List interactive elements with refs for click-by-ref.

        Browser: e1… (DOM). Desktop: w1… (windows). Prefer snapshot before click.
        """
        return ex.run(
            ComputerAction.SNAPSHOT,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            limit=limit,
        )

    async def computer_monitors(hint: str = "") -> str:
        """List displays (index, size, primary) for multi-monitor screenshots."""
        return ex.run(
            ComputerAction.MONITORS,
            target="desktop",
            hint=hint,
            runtime=runtime,
        )

    async def computer_click(
        x: int = 0,
        y: int = 0,
        button: str = "left",
        clicks: int = 1,
        ref: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Click by coordinates or by ref from computer_snapshot (e.g. ref=e3)."""
        return ex.run(
            ComputerAction.CLICK,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            button=button,
            clicks=clicks,
            ref=ref,
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
        "Screenshot the in-app browser or full desktop. Optional monitor index for multi-display.",
        computer_screenshot,
        {
            "type": "object",
            "properties": {
                "target": target_prop,
                "hint": hint_prop,
                "monitor": {
                    "type": "string",
                    "description": "Monitor index from computer_monitors (desktop only)",
                },
            },
        },
    )
    reg.register_builtin_handler(
        "computer_snapshot",
        "Browser a11y-style list of interactive elements with refs (e1…). Then computer_click ref=eN.",
        computer_snapshot,
        {
            "type": "object",
            "properties": {
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_monitors",
        "List monitors (index, width, height, primary) for multi-monitor capture.",
        computer_monitors,
        {
            "type": "object",
            "properties": {"hint": hint_prop},
        },
    )
    reg.register_builtin_handler(
        "computer_click",
        "Click by coordinates or by ref from computer_snapshot (ref=e3). Prefer snapshot on web UIs.",
        computer_click,
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "ref": {
                    "type": "string",
                    "description": "Element ref from computer_snapshot, e.g. e1",
                },
                "button": {"type": "string", "description": "left | right | middle"},
                "clicks": {"type": "integer", "description": "1 or 2 for double-click"},
                "target": target_prop,
                "hint": hint_prop,
            },
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
