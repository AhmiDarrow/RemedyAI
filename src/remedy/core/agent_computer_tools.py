"""Register provider-agnostic computer-use tools on the runtime."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from remedy.core.computer.executor import get_computer_executor
from remedy.core.computer.types import ComputerAction


async def _run_computer(ex: Any, action: Any, **kwargs: Any) -> str:
    """Run the sync executor off the event loop (run() uses time.sleep).

    Stamp the turn session id on this task *before* to_thread so a worker
    without the ContextVar cannot inherit a sibling tab's runtime._session_id.
    """
    from remedy.core.turn_context import turn_session_id

    runtime = kwargs.get("runtime")
    sid = turn_session_id(runtime)
    return await asyncio.to_thread(ex.run, action, session_id=sid, **kwargs)


def _resolve_ref_label(ex: Any, ref: str) -> str:
    """Best-effort element label for a snapshot ref (for approval summaries).

    Returns "" when unknown — the gate then treats an unresolvable ref click
    on a mutation as needing approval in Ask mode anyway; the value here is
    making the payment classifier see 'Place order' when the model clicks by
    ref instead of text.
    """
    r = (ref or "").strip()
    if not r:
        return ""
    try:
        info = ex.bridge.last_elements_info()
        for el in info.get("elements") or []:
            if str(el.get("ref") or "") == r:
                return str(el.get("name") or el.get("text") or "")
    except Exception:
        pass
    return ""


def _page_context(ex: Any) -> str:
    """Best-effort text of the current rail surface (URL + last element labels).

    Used only to detect a checkout/payment surface for the owner checkpoint —
    never logged, never sent to a model. Prefers a URL the host actually
    observed (snapshot / page_text / navigate complete) over the last
    requested navigate, so SPA checkout hops still classify.
    """
    bits: list[str] = []
    with contextlib.suppress(Exception):
        live = ""
        if hasattr(ex.bridge, "last_observed_url"):
            live = str(ex.bridge.last_observed_url() or "")
        bits.append(live or str(ex.bridge.last_navigate_url() or ""))
    try:
        info = ex.bridge.last_elements_info() or {}
        bits.append(str(info.get("target") or ""))
        for el in (info.get("elements") or [])[:40]:
            bits.append(str(el.get("name") or el.get("text") or ""))
    except Exception:
        pass
    return " ".join(b for b in bits if b)[:2000]


def _page_origin(page_context: str) -> str:
    from remedy.core.approvals import _origin_host

    return _origin_host(page_context)


def _computer_approval_gate(
    runtime: Any,
    tool_name: str,
    summary: str,
    *,
    page_context: str = "",
    label_resolved: bool = True,
    key: str = "",
    typed_text: str = "",
) -> str | None:
    """Return APPROVAL_REQUIRED text when Ask mode blocks a mutation, else None."""
    from remedy.core.approvals import (
        APPROVALS,
        payment_surface_checkpoint,
        raw_secret_checkpoint,
    )
    from remedy.core.turn_context import turn_session_id

    ask_reason: str | None = None
    # The payment-surface + raw-card heads-ups are for the CAUTIOUS DEFAULT
    # only. When the owner has set auto/full, they have deliberately handed
    # Remedy the keys to the machine — she uses the PC as if it were hers,
    # effortlessly, with no per-action roadblocks (the standing grant is the
    # countersignature). These extra checks apply solely in ``ask`` mode so a
    # brand-new user still gets a money heads-up until they trust her fully.
    if APPROVALS.mode == "ask":
        ask_reason = payment_surface_checkpoint(
            tool_name,
            label_resolved=label_resolved,
            page_context=page_context,
            key=key,
        )
        if not ask_reason:
            ask_reason = raw_secret_checkpoint(tool_name, typed_text)
    if not ask_reason:
        ask_reason = APPROVALS.needs_ask(summary, tool_name=tool_name)
    if not ask_reason:
        return None
    sid = turn_session_id(runtime)
    from remedy.core.approvals import SENSITIVE_PREFIX

    origin = _page_origin(page_context)
    sensitive = ask_reason.startswith(SENSITIVE_PREFIX)
    if sensitive:
        # Sensitive actions never ride a persisted approval — only a one-shot
        # grant consumed here. is_approved (session/always) does not apply.
        if APPROVALS.take_one_shot(
            tool_name, summary, session_id=sid, origin=origin
        ):
            return None
    elif APPROVALS.is_approved(tool_name, summary, session_id=sid):
        return None
    item = APPROVALS.create(
        tool_name=tool_name,
        command=summary,
        reason=ask_reason,
        session_id=sid,
        origin=origin,
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
        return await _run_computer(ex,
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
        return await _run_computer(ex,
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
        return await _run_computer(ex,
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
        # Resolve ref → element label so the payment/owner checkpoint can see
        # snapshot clicks (the primary click path), not just text= clicks.
        label = text or _resolve_ref_label(ex, ref)
        summary = (
            f"click text={(label or text)!r} ref={ref!r} x={x} y={y} "
            f"button={button} clicks={clicks} target={target or 'auto'}"
        )
        _label_ok = bool((text or "").strip()) or bool(_resolve_ref_label(ex, ref))
        blocked = _computer_approval_gate(
            runtime, "computer_click", summary,
            page_context=_page_context(ex),
            label_resolved=_label_ok,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
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
        return await _run_computer(ex,
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
        return await _run_computer(ex,
            ComputerAction.APP,
            target="desktop",
            hint=hint,
            runtime=runtime,
            app=app,
        )

    async def computer_page_text(
        hint: str = "", target: str = "browser", hwnd: int = 0
    ) -> str:
        """Extract visible text — Browser rail page, or a NATIVE window's content.

        target=desktop reads the actual content of a native app via UI
        Automation: edit fields and documents contribute their live values,
        labels their names. hwnd= picks the window (default: foreground).
        This is how you READ what an app shows or what you just typed — no
        screenshot needed.
        """
        want_desktop = str(target or "").strip().lower() == "desktop" or bool(hwnd)
        return await _run_computer(ex,
            ComputerAction.PAGE_TEXT,
            target="desktop" if want_desktop else "browser",
            hint=hint,
            hwnd=hwnd,
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
        return await _run_computer(ex,
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
        target: str = "auto",
        hint: str = "",
        expect_url: str = "",
        expect_text: str = "",
    ) -> str:
        """Multi-step computer action in ONE call (fast path).

        Optional: navigate url → click label → type text → key.
        Prefer this for login/search flows instead of many tiny tool rounds.
        Example: url=https://mail.google.com click=\"Sign in\" type=\"user@gmail.com\" key=enter
        Default target=auto: URL → rail; after computer_app → desktop.

        Verification: after acting, the machine observes the page and reports
        ``observed`` url/title (+ ``page_changed``). Pass ``expect_url=`` and/or
        ``expect_text=`` (substring) to make the call FAIL when the outcome does
        not match — e.g. expect_text=\"added to cart\". Success without
        verification is reported as unverified; do not claim the user's goal is
        done from an unverified result.
        """
        # Do not put typed secrets in the approval banner; only lengths / labels.
        type_note = f"type_chars={len(type)}" if type else "type=-"
        try:
            from remedy.core.vault import token_handles

            handles = token_handles(type or "")
            if handles:
                type_note += f" vault={','.join(handles)}"
        except Exception:
            pass
        summary = (
            f"act url={url!r} click={click!r} {type_note} key={key!r} "
            f"goal={goal!r} target={target or 'auto'}"
        )
        blocked = _computer_approval_gate(
            runtime, "computer_act", summary,
            page_context=_page_context(ex),
            label_resolved=bool((click or "").strip()),
            key=key,
            typed_text=type,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.ACT,
            target=target or "auto",
            hint=hint or goal,
            runtime=runtime,
            url=url,
            click=click,
            type=type,
            type_text=type,
            key=key,
            goal=goal,
            text=click,
            expect_url=expect_url,
            expect_text=expect_text,
        )

    async def vault_list() -> str:
        """List the owner's stored secrets as handles + metadata (never values).

        Use the returned token (e.g. ``{{vault:card-visa}}``) in computer_type /
        computer_act type= to fill it machine-side. Secrets are added by the
        owner in Settings → Vault — never ask the user to paste secret values
        into chat.
        """
        import json as _json

        try:
            from remedy.core import vault

            home = None
            with __import__("contextlib").suppress(Exception):
                home = getattr(getattr(runtime, "config", None), "home_dir", None)
            items = vault.vault_list(home)
            if not items:
                return _json.dumps(
                    {
                        "ok": True,
                        "items": [],
                        "note": (
                            "Vault is empty. The owner can add payment info / "
                            "credentials in Settings → Vault; values never "
                            "appear in chat."
                        ),
                    }
                )
            return _json.dumps({"ok": True, "items": items})
        except Exception as exc:
            return _json.dumps({"ok": False, "error": str(exc)})

    async def computer_type(
        text: str = "",
        target: str = "auto",
        hint: str = "",
        ref: str = "",
    ) -> str:
        """Type text into the focused UI (browser or desktop).

        Stored secrets: pass a vault token like ``{{vault:card-visa}}`` — the
        machine substitutes the real value at the input path (you never see
        it), enforces the item's site binding, and asks the owner first.
        Never ask the user to paste card numbers or passwords into chat; use
        vault_list to find handles.
        """
        vault_note = ""
        try:
            from remedy.core.vault import token_handles

            handles = token_handles(text or "")
            if handles:
                vault_note = f" vault={','.join(handles)}"
        except Exception:
            pass
        summary = (
            f"type chars={len(text or '')} target={target or 'auto'}{vault_note}"
        )
        blocked = _computer_approval_gate(
            runtime, "computer_type", summary,
            typed_text=text,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.TYPE,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            text=text,
            ref=ref,
        )

    async def computer_key(
        key: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Press a key or combo (enter, tab, ctrl+s, alt+f4, …)."""
        summary = f"key={key!r} target={target or 'auto'}"
        blocked = _computer_approval_gate(
            runtime, "computer_key", summary,
            page_context=_page_context(ex),
            label_resolved=False,
            key=key,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
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
        return await _run_computer(ex,
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
        return await _run_computer(ex,
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
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        target: str = "desktop",
        hint: str = "",
    ) -> str:
        """List / focus / minimize / maximize / restore / close / move / resize windows."""
        return await _run_computer(ex,
            ComputerAction.WINDOWS,
            target=target or "desktop",
            hint=hint or title,
            runtime=runtime,
            mode=mode,
            hwnd=hwnd,
            title=title,
            limit=limit,
            x=x,
            y=y,
            width=width,
            height=height,
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
        blocked = _computer_approval_gate(
            runtime, "computer_drag", summary,
            page_context=_page_context(ex),
            label_resolved=False,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.DRAG,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            x2=x2,
            y2=y2,
        )

    async def computer_press_hold(
        text: str = "",
        ref: str = "",
        x: int = 0,
        y: int = 0,
        hold_ms: int = 2600,
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Press and HOLD a control (press-and-hold verification, a hold button).

        The owner's authorized hands for an accessibility gesture they may be
        unable to perform. Locate by text/ref or pass x/y (from a screenshot).
        """
        label = text or _resolve_ref_label(ex, ref)
        where = label or text or ref or f"({x},{y})"
        summary = f"press-and-hold {where} for {hold_ms}ms target={target or 'auto'}"
        blocked = _computer_approval_gate(
            runtime, "computer_press_hold", summary,
            page_context=_page_context(ex),
            label_resolved=bool((text or "").strip()) or bool(_resolve_ref_label(ex, ref)),
        )
        if blocked:
            return blocked
        # (0, 0) with no locator is the schema default (unset). A lone 0 on
        # one axis is a real edge coordinate — do not coerce it to None.
        point = (int(x or 0), int(y or 0)) != (0, 0)
        return await _run_computer(ex,
            ComputerAction.PRESS_HOLD,
            target=target or "auto",
            hint=hint,
            runtime=runtime,
            text=text or None,
            ref=ref or None,
            x=(int(x) if point or text or ref else None),
            y=(int(y) if point or text or ref else None),
            hold_ms=int(hold_ms) if hold_ms else 2600,
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
        "Screenshot browser or desktop. Built-in vision auto-decodes the PNG "
        "(OCR + click targets). Use for games / UIs with no accessibility tree.",
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
        "Launch a Windows app: notepad, calc, explorer, chrome, edge, or a project-relative .exe (game.exe, .\\hello.exe).",
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
        "Read visible text — Browser rail page, OR a native app's content (target=desktop reads edit fields/documents via UI Automation, no screenshot).",
        computer_page_text,
        {
            "type": "object",
            "properties": {
                "hint": hint_prop,
                "target": target_prop,
                "hwnd": {
                    "type": "integer",
                    "description": "Native window to read (default: foreground). Implies target=desktop.",
                },
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
                "expect_url": {
                    "type": "string",
                    "description": "Fail unless the observed URL contains this after acting",
                },
                "expect_text": {
                    "type": "string",
                    "description": "Fail unless observed page text/title contains this after acting",
                },
            },
        },
    )
    reg.register_builtin_handler(
        "computer_type",
        "Type text into the focused control (browser or desktop). Long text pastes atomically. ref= (desktop cN) sets that control's value directly via UIA — most reliable for fields. Stored secrets: pass {{vault:handle}} (see vault_list).",
        computer_type,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "ref": {
                    "type": "string",
                    "description": (
                        "Desktop control ref (cN from computer_snapshot): set its "
                        "value directly via UIA — atomic, verified, no focus races."
                    ),
                },
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["text"],
        },
    )
    reg.register_builtin_handler(
        "vault_list",
        "List the owner's stored secret handles (cards, passwords) — metadata only, never values. Fill with {{vault:handle}} in computer_type/computer_act.",
        vault_list,
        {"type": "object", "properties": {}},
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
        "List, focus, or MANAGE OS windows: minimize / maximize / restore / close / move / resize by hwnd or title substring (desktop).",
        computer_windows,
        {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": (
                        "list | focus | minimize | maximize | restore | close | "
                        "move | resize. close is polite (WM_CLOSE — the app may "
                        "show a save prompt; snapshot to drive it)."
                    ),
                },
                "hwnd": {"type": "integer"},
                "title": {
                    "type": "string",
                    "description": "Window title substring for focus/manage modes",
                },
                "x": {"type": "integer", "description": "move: new left"},
                "y": {"type": "integer", "description": "move: new top"},
                "width": {"type": "integer", "description": "resize: new width"},
                "height": {"type": "integer", "description": "resize: new height"},
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
    reg.register_builtin_handler(
        "computer_press_hold",
        "Press and HOLD a control (press-and-hold / 'activate and hold' "
        "verification, a hold-to-confirm button) — Remedy as the owner's "
        "authorized hands for an accessibility gesture. Locate by text= or "
        "ref= from a snapshot, or pass x=/y= from a screenshot. hold_ms "
        "defaults to 2600.",
        computer_press_hold,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Visible label to press-hold"},
                "ref": {"type": "string", "description": "Element ref (e3 / c5) from snapshot"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "hold_ms": {
                    "type": "integer",
                    "description": "Hold duration in ms (default 2600)",
                },
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
