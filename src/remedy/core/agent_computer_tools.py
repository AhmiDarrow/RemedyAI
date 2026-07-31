"""Register provider-agnostic computer-use tools on the runtime."""

from __future__ import annotations

from typing import Any

from remedy.core.computer.executor import get_computer_executor
from remedy.core.computer.types import ComputerAction


def _computer_approval_gate(runtime: Any, tool_name: str, summary: str) -> str | None:
    """Return APPROVAL_REQUIRED text when Ask mode blocks a mutation, else None."""
    from remedy.core.approvals import APPROVALS
    from remedy.core.turn_context import turn_session_id

    ask_reason = APPROVALS.needs_ask(summary, tool_name=tool_name)
    if not ask_reason:
        return None
    sid = turn_session_id(runtime)
    if APPROVALS.is_approved(tool_name, summary, session_id=sid):
        return None
    item = APPROVALS.create(
        tool_name=tool_name,
        command=summary,
        reason=ask_reason,
        session_id=sid,
    )
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={ask_reason}\n"
        f"command={summary[:400]}\n"
        "Do not invent success. Tell the user this needs approval in the UI "
        f"(or /approve {item.id}). After they approve, retry {tool_name}."
    )


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
        mode: str = "auto",
        hwnd: int = 0,
    ) -> str:
        """List interactive elements with refs for click-by-ref.

        Browser: e1… (DOM). Desktop: w1… windows + c1… UIA controls (mode=auto|windows|controls).
        """
        return ex.run(
            ComputerAction.SNAPSHOT,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            limit=limit,
            mode=mode,
            hwnd=hwnd or None,
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
        text: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Click by text (preferred), ref from snapshot, or coordinates.

        Prefer text=\"Membership options\" or ref=e3 over guessing pixels.
        """
        summary = (
            f"click text={text!r} ref={ref!r} x={x} y={y} "
            f"button={button} clicks={clicks} target={target or 'auto'}"
        )
        blocked = _computer_approval_gate(runtime, "computer_click", summary)
        if blocked:
            return blocked
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
            text=text,
        )

    async def computer_wait(seconds: float = 0.5, hint: str = "") -> str:
        """Pause briefly (page paint, app launch). Prefer 0.3–1.5s, max 30s."""
        return ex.run(
            ComputerAction.WAIT,
            target="desktop",
            hint=hint,
            runtime=runtime,
            seconds=seconds,
        )

    async def computer_app(app: str = "", hint: str = "") -> str:
        """Launch a desktop app (notepad, calc, explorer, chrome, path to .exe)."""
        summary = f"app launch app={app!r}"
        blocked = _computer_approval_gate(runtime, "computer_app", summary)
        if blocked:
            return blocked
        return ex.run(
            ComputerAction.APP,
            target="desktop",
            hint=hint,
            runtime=runtime,
            app=app,
        )

    async def computer_page_text(hint: str = "", target: str = "browser") -> str:
        """Extract visible text from the Browser rail page (no vision).

        ``target`` is accepted for schema parity with other computer tools; page
        text is always read from the in-app Browser rail (not full desktop).
        """
        _ = target  # browser-rail only; ignore desktop/auto extras from the model
        return ex.run(
            ComputerAction.PAGE_TEXT,
            target="browser",
            hint=hint,
            runtime=runtime,
        )

    async def computer_find(
        text: str = "",
        query: str = "",
        target: str = "auto",
        hint: str = "",
        limit: int = 8,
    ) -> str:
        """Find controls matching text/name on browser or desktop (ranked matches)."""
        return ex.run(
            ComputerAction.FIND,
            target=target or "auto",
            hint=hint or text or query,
            runtime=runtime,
            text=text or query,
            query=query or text,
            limit=limit,
        )

    async def computer_act(
        url: str = "",
        click: str = "",
        type: str = "",
        key: str = "",
        goal: str = "",
        target: str = "browser",
        hint: str = "",
    ) -> str:
        """Multi-step computer action in ONE call (fast path).

        Optional: navigate url → click label → type text → key.
        Prefer this for login/search flows instead of many tiny tool rounds.
        Example: url=https://mail.google.com click=\"Sign in\" type=\"user@gmail.com\" key=enter
        """
        # Do not put typed secrets in the approval banner; only lengths / labels.
        type_note = f"type_chars={len(type)}" if type else "type=-"
        summary = (
            f"act url={url!r} click={click!r} {type_note} key={key!r} "
            f"goal={goal!r} target={target or 'browser'}"
        )
        blocked = _computer_approval_gate(runtime, "computer_act", summary)
        if blocked:
            return blocked
        return ex.run(
            ComputerAction.ACT,
            target=target or "browser",
            hint=hint or goal,
            runtime=runtime,
            url=url,
            click=click,
            type=type,
            type_text=type,
            key=key,
            goal=goal,
            text=click,
        )

    async def computer_type(
        text: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Type text into the focused UI (browser or desktop)."""
        summary = f"type chars={len(text or '')} target={target or 'auto'}"
        blocked = _computer_approval_gate(runtime, "computer_type", summary)
        if blocked:
            return blocked
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
        summary = f"key={key!r} target={target or 'auto'}"
        blocked = _computer_approval_gate(runtime, "computer_key", summary)
        if blocked:
            return blocked
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
        target: str = "browser",
        hint: str = "",
    ) -> str:
        """Open a URL in the **in-app browser rail** (default).

        Use target=desktop / hint about system/external browser only when the
        user asks to open outside Remedy.
        """
        return ex.run(
            ComputerAction.NAVIGATE,
            target=target or "browser",
            hint=hint,
            runtime=runtime,
            url=url,
        )

    async def computer_windows(
        mode: str = "list",
        hwnd: int = 0,
        title: str = "",
        limit: int = 40,
        target: str = "desktop",
        hint: str = "",
    ) -> str:
        """List visible windows or focus by hwnd / title substring (desktop)."""
        return ex.run(
            ComputerAction.WINDOWS,
            target=target or "desktop",
            hint=hint or title,
            runtime=runtime,
            mode=mode,
            hwnd=hwnd,
            title=title,
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
        summary = f"drag ({x},{y})->({x2},{y2}) target={target or 'auto'}"
        blocked = _computer_approval_gate(runtime, "computer_drag", summary)
        if blocked:
            return blocked
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
        "List interactive elements with refs. Browser e1…; desktop w1… windows + c1… UIA controls. Then computer_click ref=…",
        computer_snapshot,
        {
            "type": "object",
            "properties": {
                "target": target_prop,
                "hint": hint_prop,
                "mode": {
                    "type": "string",
                    "description": "desktop: auto | windows | controls (UIA deep tree)",
                },
                "hwnd": {
                    "type": "integer",
                    "description": "Optional window handle to scope UIA walk",
                },
                "limit": {"type": "integer"},
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
        "Click by text= (preferred), ref= from snapshot, or x/y. Example: text=\"Membership options\".",
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
                "text": {
                    "type": "string",
                    "description": "Visible label/name to click (preferred over coords)",
                },
                "button": {"type": "string", "description": "left | right | middle"},
                "clicks": {"type": "integer", "description": "1 or 2 for double-click"},
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_wait",
        "Wait seconds for UI paint / app launch (default 0.5, max 30).",
        computer_wait,
        {
            "type": "object",
            "properties": {
                "seconds": {"type": "number"},
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_app",
        "Launch a Windows app: notepad, calc, explorer, chrome, edge, or path to .exe.",
        computer_app,
        {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App name or path"},
                "hint": hint_prop,
            },
            "required": ["app"],
        },
    )
    reg.register_builtin_handler(
        "computer_page_text",
        "Read visible text from the in-app Browser rail page (no vision/screenshot).",
        computer_page_text,
        {
            "type": "object",
            "properties": {
                "hint": hint_prop,
                # Accepted for parity; always routes to browser rail.
                "target": target_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_find",
        "Find ranked controls matching text on browser or desktop (then computer_click ref=…).",
        computer_find,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_act",
        "ONE-CALL multi-step: optional url + click label + type + key. Prefer for login/search (fast, accurate).",
        computer_act,
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Optional navigate URL first"},
                "click": {
                    "type": "string",
                    "description": "Visible control label to click (e.g. Sign in)",
                },
                "type": {
                    "type": "string",
                    "description": "Text to type into focused field after click",
                },
                "key": {
                    "type": "string",
                    "description": "Optional key after type (enter, tab)",
                },
                "goal": {"type": "string", "description": "Short task description"},
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
        "Open a URL in the in-app Browser rail (default). Only use system/external browser if the user asks.",
        computer_navigate,
        {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "target": {
                    "type": "string",
                    "description": (
                        "browser (default, in-app rail) | desktop/external only if "
                        "user asked for system browser"
                    ),
                },
                "hint": hint_prop,
            },
            "required": ["url"],
        },
    )
    reg.register_builtin_handler(
        "computer_windows",
        "List visible OS windows or focus by hwnd / title substring (desktop).",
        computer_windows,
        {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "list | focus",
                },
                "hwnd": {"type": "integer"},
                "title": {
                    "type": "string",
                    "description": "Window title substring for mode=focus",
                },
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
