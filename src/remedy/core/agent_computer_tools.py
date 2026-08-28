"""Register provider-agnostic computer-use tools on the runtime."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from remedy.core.build_oracle import coerce_text_arg
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
    r = coerce_text_arg(ref)
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


_KEY_ALIASES = {
    "enter": "Enter",
    "return": "Enter",
    "\n": "Enter",
    "tab": "Tab",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "space": " ",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
}


def _canonical_key(key: str) -> str:
    raw = coerce_text_arg(key)
    if not raw:
        return raw
    # Combos (ctrl+s) keep the last token canonical so Enter aliases work.
    if "+" in raw:
        parts = raw.split("+")
        parts[-1] = _KEY_ALIASES.get(parts[-1].lower(), parts[-1])
        return "+".join(parts)
    return _KEY_ALIASES.get(raw.lower(), raw)


def _fill_typed_text(fields: list | str) -> str:
    """Concatenate the values a computer_fill call will type (for checkpoints)."""
    rows: Any = fields
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except (json.JSONDecodeError, TypeError):
            return rows
    if not isinstance(rows, list):
        return ""
    parts: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            for key in ("value", "type", "select", "option"):
                v = row.get(key)
                if v:
                    parts.append(str(v))
    return "\n".join(parts)


def _computer_approval_gate(
    runtime: Any,
    tool_name: str,
    summary: str,
    *,
    page_context: str = "",
    label_resolved: bool = True,
    key: str = "",
    typed_text: str = "",
    label: str = "",
) -> str | None:
    """Return APPROVAL_REQUIRED text when Ask mode blocks a mutation, else None."""
    from remedy.core.approvals import (
        APPROVALS,
        challenge_wall_checkpoint,
        payment_surface_checkpoint,
        raw_secret_checkpoint,
    )
    from remedy.core.turn_context import turn_session_id

    ask_reason: str | None = None
    # Money / credentials / irreversible-send: no approval mode may waive
    # these (AGENTS.md Q3, LIFE_TASK §2.2). Run them BEFORE needs_ask so
    # auto/full cannot skip a checkout pixel-click or a raw PAN.
    ask_reason = payment_surface_checkpoint(
        tool_name,
        label_resolved=label_resolved,
        page_context=page_context,
        key=key,
        label=label,
    )
    if not ask_reason:
        ask_reason = raw_secret_checkpoint(tool_name, typed_text)
    if not ask_reason:
        ask_reason = challenge_wall_checkpoint(tool_name, page_context, label)
    # Vault / click-copy checkpoints live in needs_ask(summary). PolicyEngine
    # may have allowed on an empty command; still stop owner-checkpoint text.
    if not ask_reason:
        from remedy.core.approvals import sensitive_computer_checkpoint
        from remedy.core.turn_pipeline import gate_already_passed, gate_command

        ask_reason = sensitive_computer_checkpoint(tool_name, summary)
        # authorize_tool already consumed the owner yes for this same
        # payment/vault family. Fingerprints differ (typed args vs a
        # sanitized handler summary) — do not ask twice. payment_surface /
        # raw PAN / challenge-wall still run above; PolicyEngine cannot
        # see the live page.
        if (
            ask_reason
            and gate_already_passed(tool_name)
            and sensitive_computer_checkpoint(tool_name, gate_command())
        ):
            ask_reason = None
    if not ask_reason:
        from remedy.core.turn_pipeline import gate_already_passed

        if not gate_already_passed(tool_name):
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
    with contextlib.suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    ex = get_computer_executor(home)

    async def computer_screenshot(
        target: str = "auto",
        hint: str = "",
        monitor: str = "",
        mark: bool = False,
    ) -> str:
        """Capture the browser rail or full desktop. Prefer before click/type.

        monitor: empty = full virtual screen / rail; integer index for one display.
        mark=true (desktop): overlay numbered boxes on the last snapshot's
        elements and return a mark→ref legend — for pixel-only apps, reference a
        numbered mark instead of estimating x/y.
        """
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        monitor = coerce_text_arg(monitor)
        return await _run_computer(ex,
            ComputerAction.SCREENSHOT,
            target=target,
            hint=hint,
            runtime=runtime,
            monitor=monitor or None,
            mark=mark,
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
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        mode = coerce_text_arg(mode)
        return await _run_computer(ex,
            ComputerAction.SNAPSHOT,
            target=target,
            hint=hint,
            runtime=runtime,
            limit=limit,
            mode=mode,
            hwnd=hwnd or None,
        )

    async def computer_monitors(hint: str = "") -> str:
        """List displays (index, size, primary) for multi-monitor screenshots."""
        hint = coerce_text_arg(hint)
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
        # Models send JSON arrays for str fields; registry coerce is skipped
        # on direct handler calls, so (text or "").strip() used to crash.
        text = coerce_text_arg(text)
        ref = coerce_text_arg(ref)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        button = coerce_text_arg(button)
        # Resolve ref → element label so the payment/owner checkpoint can see
        # snapshot clicks (the primary click path), not just text= clicks.
        label = text or _resolve_ref_label(ex, ref)
        summary = (
            f"click text={(label or text)!r} ref={ref!r} x={x} y={y} "
            f"button={button} clicks={clicks} target={target or 'auto'}"
        )
        _label_ok = bool(text) or bool(_resolve_ref_label(ex, ref))
        blocked = _computer_approval_gate(
            runtime, "computer_click", summary,
            page_context=_page_context(ex),
            label_resolved=_label_ok,
            label=label,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.CLICK,
            target=target,
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            button=button,
            clicks=clicks,
            ref=ref,
            text=text,
        )

    async def computer_hover(
        x: int = 0,
        y: int = 0,
        ref: str = "",
        text: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Move the pointer onto a control without clicking.

        Opens menus and CSS :hover flyouts the way a hand does. Prefer
        text=\"File\" or ref= from snapshot; x/y last.
        """
        text = coerce_text_arg(text)
        ref = coerce_text_arg(ref)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        summary = (
            f"hover text={text!r} ref={ref!r} x={x} y={y} target={target or 'auto'}"
        )
        blocked = _computer_approval_gate(
            runtime, "computer_hover", summary,
            page_context=_page_context(ex),
            label_resolved=bool(text or ref),
            label=text or _resolve_ref_label(ex, ref),
        )
        if blocked:
            return blocked
        return await _run_computer(
            ex,
            ComputerAction.HOVER,
            target=target,
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            ref=ref,
            text=text,
        )

    async def computer_wait(seconds: float = 0.5, hint: str = "") -> str:
        """Pause briefly (page paint, app launch). Prefer 0.3–1.5s, max 30s."""
        hint = coerce_text_arg(hint)
        return await _run_computer(ex,
            ComputerAction.WAIT,
            target="desktop",
            hint=hint,
            runtime=runtime,
            seconds=seconds,
        )

    async def computer_app(app: str = "", hint: str = "", path: str = "") -> str:
        """Launch a desktop app (notepad, calc, explorer, chrome, path to .exe).

        path= opens that directory in the OS file manager (Explorer). Prefer
        app_control open_panel files path= to show it in Studio's Files rail.
        """
        app = coerce_text_arg(app)
        hint = coerce_text_arg(hint)
        path = coerce_text_arg(path)
        summary = f"app launch app={app!r}" + (f" path={path!r}" if path else "")
        blocked = _computer_approval_gate(runtime, "computer_app", summary)
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.APP,
            target="desktop",
            hint=hint,
            runtime=runtime,
            app=app,
            path=path,
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
        hint = coerce_text_arg(hint)
        target = coerce_text_arg(target)
        want_desktop = target.lower() == "desktop" or bool(hwnd)
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
        text = coerce_text_arg(text)
        query = coerce_text_arg(query)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        return await _run_computer(ex,
            ComputerAction.FIND,
            target=target,
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
        url = coerce_text_arg(url)
        click = coerce_text_arg(click)
        type = coerce_text_arg(type)
        goal = coerce_text_arg(goal)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        expect_url = coerce_text_arg(expect_url)
        expect_text = coerce_text_arg(expect_text)
        # Do not put typed secrets in the approval banner; only lengths / labels.
        key = _canonical_key(key)
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
            label_resolved=bool(click),
            key=key,
            typed_text=type,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.ACT,
            target=target,
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
            with contextlib.suppress(Exception):
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
        query: str = "",
        label: str = "",
    ) -> str:
        """Type text into a field (browser or desktop).

        query=/label= is the visible field label, placeholder, or hint —
        same family as computer_click text=. ref= still wins; a stale ref
        relocates via query in the host.

        Stored secrets: pass a vault token like ``{{vault:card-visa}}`` — the
        machine substitutes the real value at the input path (you never see
        it), enforces the item's site binding, and asks the owner first.
        Never ask the user to paste card numbers or passwords into chat; use
        vault_list to find handles.
        """
        text = coerce_text_arg(text)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        ref = coerce_text_arg(ref)
        query = coerce_text_arg(query) or coerce_text_arg(label)
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
            page_context=_page_context(ex),
            typed_text=text,
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.TYPE,
            target=target,
            hint=hint,
            runtime=runtime,
            text=text,
            ref=ref,
            query=query or None,
        )

    async def computer_select(
        value: str = "",
        option: str = "",
        text: str = "",
        ref: str = "",
        target: str = "browser",
        hint: str = "",
    ) -> str:
        """Choose an option in a <select> (dropdown) by visible text or value."""
        value = coerce_text_arg(value)
        option = coerce_text_arg(option)
        text = coerce_text_arg(text)
        ref = coerce_text_arg(ref)
        target = coerce_text_arg(target) or "browser"
        hint = coerce_text_arg(hint)
        choice = value or option or text
        summary = f"select {choice!r} ref={ref or '-'}"
        blocked = _computer_approval_gate(
            runtime, "computer_select", summary,
            page_context=_page_context(ex),
            label_resolved=bool(ref or choice),
        )
        if blocked:
            return blocked
        return await _run_computer(
            ex,
            ComputerAction.SELECT,
            target=target,
            hint=hint,
            runtime=runtime,
            value=choice,
            text=choice,
            ref=ref,
        )

    async def computer_fill(
        fields: list | str = "",
        target: str = "browser",
        hint: str = "",
    ) -> str:
        """Fill several form fields in one call.

        fields is a list of objects: {ref or text (label), value and/or select}.
        Example: [{\"text\":\"First name\",\"value\":\"Ada\"},
        {\"text\":\"State\",\"select\":\"California\"}]
        """
        target = coerce_text_arg(target) or "browser"
        hint = coerce_text_arg(hint)
        typed = _fill_typed_text(fields)
        vault_note = ""
        try:
            from remedy.core.vault import token_handles

            handles = token_handles(typed)
            if handles:
                vault_note = f" vault={','.join(handles)}"
        except Exception:
            pass
        # Unique fingerprint per origin+handles so one generic fill cannot
        # authorize a later card fill (sensitive_computer_checkpoint sees vault=).
        summary = f"fill form fields{vault_note}"
        blocked = _computer_approval_gate(
            runtime, "computer_fill", summary,
            page_context=_page_context(ex),
            typed_text=typed,
        )
        if blocked:
            return blocked
        return await _run_computer(
            ex,
            ComputerAction.FILL,
            target=target,
            hint=hint,
            runtime=runtime,
            fields=fields,
        )

    async def computer_key(
        key: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Press a key or combo (enter, tab, ctrl+s, alt+f4, …)."""
        key = _canonical_key(key)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
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
            target=target,
            hint=hint,
            runtime=runtime,
            key=key,
        )

    async def computer_scroll(
        x: int = 0,
        y: int = 0,
        dy: int = -3,
        text: str = "",
        ref: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Scroll at a point, or locate the pane/list by visible label.

        Guidance addresses scroll by label like click / press_hold / drag.
        Prefer text= or ref= when the DOM / UIA exposes a name; bare x/y still
        work for canvas / pixel targets. dy>0 scrolls up, dy<0 scrolls down.
        """
        text = coerce_text_arg(text)
        ref = coerce_text_arg(ref)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        return await _run_computer(ex,
            ComputerAction.SCROLL,
            target=target,
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            dy=dy,
            text=text or None,
            ref=ref or None,
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
        url = coerce_text_arg(url)
        target = coerce_text_arg(target) or "browser"
        hint = coerce_text_arg(hint)
        return await _run_computer(ex,
            ComputerAction.NAVIGATE,
            target=target,
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
        mode = coerce_text_arg(mode)
        title = coerce_text_arg(title)
        target = coerce_text_arg(target) or "desktop"
        hint = coerce_text_arg(hint)
        mode_l = (mode or "list").strip().lower()
        if mode_l == "close":
            summary = f"close window title={title!r} hwnd={hwnd}"
            blocked = _computer_approval_gate(runtime, "computer_windows", summary)
            if blocked:
                return blocked
        return await _run_computer(ex,
            ComputerAction.WINDOWS,
            target=target,
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
        from_text: str = "",
        to_text: str = "",
        from_ref: str = "",
        to_ref: str = "",
        text: str = "",
        ref: str = "",
        target: str = "auto",
        hint: str = "",
    ) -> str:
        """Drag from (x,y) to (x2,y2), or locate endpoints by visible label.

        Guidance already addresses drag by label like click / press_hold.
        Prefer from_text=/to_text= (or text= for the start) when the DOM / UIA
        exposes names; bare coords still work for canvas / pixel targets.
        """
        from_text = coerce_text_arg(from_text) or coerce_text_arg(text)
        to_text = coerce_text_arg(to_text)
        from_ref = coerce_text_arg(from_ref) or coerce_text_arg(ref)
        to_ref = coerce_text_arg(to_ref)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        start = from_text or from_ref or f"({x},{y})"
        end = to_text or to_ref or f"({x2},{y2})"
        summary = f"drag {start}->{end} target={target or 'auto'}"
        blocked = _computer_approval_gate(
            runtime, "computer_drag", summary,
            page_context=_page_context(ex),
            label_resolved=bool(from_text or to_text or from_ref or to_ref),
        )
        if blocked:
            return blocked
        return await _run_computer(ex,
            ComputerAction.DRAG,
            target=target,
            hint=hint,
            runtime=runtime,
            x=x,
            y=y,
            x2=x2,
            y2=y2,
            from_text=from_text or None,
            to_text=to_text or None,
            from_ref=from_ref or None,
            to_ref=to_ref or None,
            text=from_text or None,
            ref=from_ref or None,
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
        """Press and HOLD a control (a hold button). Human-check walls
        (CAPTCHA / I'm not a robot) are owner handoffs — do not complete them.
        """
        text = coerce_text_arg(text)
        ref = coerce_text_arg(ref)
        target = coerce_text_arg(target) or "auto"
        hint = coerce_text_arg(hint)
        label = text or _resolve_ref_label(ex, ref)
        where = label or text or ref or f"({x},{y})"
        summary = f"press-and-hold {where} for {hold_ms}ms target={target or 'auto'}"
        blocked = _computer_approval_gate(
            runtime, "computer_press_hold", summary,
            page_context=_page_context(ex),
            label_resolved=bool(text) or bool(_resolve_ref_label(ex, ref)),
        )
        if blocked:
            return blocked
        # (0, 0) with no locator is the schema default (unset). A lone 0 on
        # one axis is a real edge coordinate — do not coerce it to None.
        point = (int(x or 0), int(y or 0)) != (0, 0)
        return await _run_computer(ex,
            ComputerAction.PRESS_HOLD,
            target=target,
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
        "(OCR + click targets). For Remedy's own Grove / Alongside / Studio, "
        "set hint='grove' (or alongside/studio) so we capture HER window — "
        "do not pass monitor=0 on a multi-display desk (that is often wallpaper). "
        "Use computer_monitors to see which display holds Remedy Desktop.",
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
                "mark": {
                    "type": "boolean",
                    "description": (
                        "Desktop: overlay numbered marks on snapshot elements + "
                        "return a mark→ref legend (Set-of-Mark for pixel targeting)."
                    ),
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
        "Launch an app from this house (Start Menu / XDG inventory — "
        "computer_apps lists them). Natural names: Spotify, Notepad, VLC, "
        "or a project-relative .exe (game.exe, .\\hello.exe). path= opens a "
        "folder in the OS file manager. For the in-app Files rail use "
        "app_control panel=files path=.",
        computer_app,
        {
            "type": "object",
            "properties": {
                "app": {"type": "string", "description": "App name or path"},
                "path": {
                    "type": "string",
                    "description": "Directory to open in the OS file manager (with app=explorer or alone)",
                },
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
        "Type text into a field (browser or desktop). Long text pastes atomically. "
        "query=/label= is the visible field label or placeholder (like click text=). "
        "ref= from computer_snapshot still wins; a stale ref relocates via query. "
        "desktop cN sets the value via UIA. Stored secrets: pass {{vault:handle}} "
        "(see vault_list).",
        computer_type,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "ref": {
                    "type": "string",
                    "description": (
                        "Snapshot ref: browser eN focuses/writes that field; "
                        "desktop cN sets the value via UIA (atomic, verified)."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Visible field label, placeholder, or hint. Host "
                        "relocates like click_text when ref is missing or stale."
                    ),
                },
                "label": {
                    "type": "string",
                    "description": "Alias for query= (associated <label> text).",
                },
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["text"],
        },
    )
    reg.register_builtin_handler(
        "computer_select",
        "Choose an option in a dropdown (<select>) by visible text or value. "
        "Pass ref= from computer_snapshot when you have it, plus value= the option.",
        computer_select,
        {
            "type": "object",
            "properties": {
                "value": {"type": "string", "description": "Option value or visible text"},
                "option": {"type": "string"},
                "text": {"type": "string", "description": "Option label if value omitted"},
                "ref": {"type": "string", "description": "eN from computer_snapshot"},
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_fill",
        "Fill several form fields in ONE call. fields=[{ref or text, value or select}, …]. "
        "Prefer this over many computer_type rounds on a form.",
        computer_fill,
        {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "description": "List of {ref|text, value|select}",
                },
                "target": target_prop,
                "hint": hint_prop,
            },
            "required": ["fields"],
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
        "computer_hover",
        "Move the pointer onto a control without clicking (menus, tooltips, "
        "CSS :hover). Locate by text= or ref= like computer_click.",
        computer_hover,
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Visible label to hover (File, Account, …)",
                },
                "ref": {
                    "type": "string",
                    "description": "Snapshot ref (e3 / c5)",
                },
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "target": target_prop,
                "hint": hint_prop,
            },
        },
    )
    reg.register_builtin_handler(
        "computer_scroll",
        "Scroll the wheel at a pane/list. Locate by text= or ref= from a "
        "snapshot (same family as computer_click / press_hold / drag), or "
        "pass x/y from a screenshot. dy notches (negative = down).",
        computer_scroll,
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Visible label of the pane/list/control to scroll",
                },
                "ref": {
                    "type": "string",
                    "description": "Snapshot ref of the pane/list (e3 / c5)",
                },
                "x": {"type": "integer", "description": "Scroll x (screenshot coords)"},
                "y": {"type": "integer", "description": "Scroll y"},
                "dy": {"type": "integer", "description": "Wheel notches (negative = down)"},
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
        "Drag from one control to another. Locate endpoints by "
        "from_text=/to_text= (visible labels) or from_ref=/to_ref= from a "
        "snapshot — same family as computer_click / computer_press_hold — or "
        "pass x/y/x2/y2 from a screenshot for canvas / pixel targets.",
        computer_drag,
        {
            "type": "object",
            "properties": {
                "from_text": {
                    "type": "string",
                    "description": "Visible label of the drag start control",
                },
                "to_text": {
                    "type": "string",
                    "description": "Visible label of the drag end control",
                },
                "from_ref": {
                    "type": "string",
                    "description": "Snapshot ref for the start (e3 / c5)",
                },
                "to_ref": {
                    "type": "string",
                    "description": "Snapshot ref for the end (e3 / c5)",
                },
                "text": {
                    "type": "string",
                    "description": "Alias for from_text (start label)",
                },
                "ref": {
                    "type": "string",
                    "description": "Alias for from_ref (start ref)",
                },
                "x": {"type": "integer", "description": "Start x (screenshot coords)"},
                "y": {"type": "integer", "description": "Start y"},
                "x2": {"type": "integer", "description": "End x"},
                "y2": {"type": "integer", "description": "End y"},
                "target": target_prop,
                "hint": hint_prop,
            },
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

    async def computer_apps(query: str = "", limit: int = 30) -> str:
        """List launchable apps in this house (Start Menu / .desktop inventory)."""
        from remedy.core.computer.appliances import appliance_overview

        home_dir = None
        with contextlib.suppress(Exception):
            home_dir = getattr(getattr(runtime, "config", None), "home_dir", None)
        return json.dumps(
            appliance_overview(query, home_dir, limit=max(1, min(80, int(limit or 30)))),
            indent=2,
            default=str,
        )

    async def house_walkthrough_tool() -> str:
        from remedy.core.computer.household import house_walkthrough as _walk

        home_dir = None
        with contextlib.suppress(Exception):
            home_dir = getattr(getattr(runtime, "config", None), "home_dir", None)
        return json.dumps(_walk(home_dir), indent=2, default=str)

    async def house_addition(package: str = "", manager: str = "") -> str:
        from remedy.core.computer.household import plan_addition

        plan = plan_addition(package, manager=manager)
        if plan.get("ok"):
            plan["next"] = (
                "This is a PLAN. After the owner approves, run it with "
                "host_run argv= that list. Then local_discover action=stretch."
            )
        return json.dumps(plan, indent=2, default=str)

    async def house_status() -> str:
        """Combined self + machine map: census, organs, vault, apps, how to drive each."""
        home_dir = None
        with contextlib.suppress(Exception):
            home_dir = getattr(getattr(runtime, "config", None), "home_dir", None)
        out: dict[str, Any] = {"ok": True}
        with contextlib.suppress(Exception):
            from remedy.core.metabolism.machine_map import get_machine_map
            from remedy.core.turn_context import turn_session_id

            mm = get_machine_map(turn_session_id(runtime))
            mm.refresh_house_organs(home_dir)
            out["house_line"] = mm.organ_hint()
        with contextlib.suppress(Exception):
            from remedy.execution.host.stretch import format_home_whoami

            out["census"] = format_home_whoami(home=home_dir)
        with contextlib.suppress(Exception):
            from remedy.core.computer.appliances import appliance_overview

            apps = appliance_overview("", home_dir, limit=12)
            out["appliances_known"] = apps.get("total_known")
            out["appliances_sample"] = apps.get("appliances")
        with contextlib.suppress(Exception):
            from remedy.core import vault

            items = vault.vault_list(home_dir) or []
            out["vault_handles"] = len(items)
        out["drive"] = {
            "rmb": "rmb action=status|start|stop|use (local llama.cpp muscle)",
            "vision": "vision_decode action=status|install|decode",
            "voice": "voice_identity / voice_adjust",
            "vault": "vault_list (handles only — owner adds values in Settings)",
            "apps": "computer_apps then computer_app app=<name>",
            "walkthrough": "house_walkthrough (live doors, read-only)",
            "add_tool": "house_addition package=ffmpeg → host_run the argv after approval",
            "census": "local_discover action=stretch (PATH / GPU / ports)",
            "settings": "get_settings / update_settings",
            "self_ui": "app_control (Grove / Settings / rails)",
        }
        out["note"] = (
            "This PC is her house. Use the drive table — do not list_dir C:\\ "
            "or only point the owner at Settings."
        )
        return json.dumps(out, indent=2, default=str)

    reg.register_builtin_handler(
        "computer_apps",
        "List apps installed in this house (Start Menu / XDG). "
        "query= filters by name. Launch with computer_app app=<name>. "
        "Do not list_dir Program Files to find apps.",
        computer_apps,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional name filter"},
                "limit": {"type": "integer"},
            },
        },
    )
    reg.register_builtin_handler(
        "house_walkthrough",
        "Read-only security round of this PC: known doors (Remedy, RMB, "
        "vision, Ollama, ComfyUI), unexpected listeners, vault presence, "
        "census freshness. Observes; never changes.",
        house_walkthrough_tool,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "house_addition",
        "Plan adding a PATH tool via this house's package manager "
        "(winget/choco/scoop or brew/apt/dnf/pacman). Never installs itself — "
        "returns argv for host_run after the owner countersigns.",
        house_addition,
        {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": "Package id (no URLs/paths)",
                },
                "manager": {
                    "type": "string",
                    "description": "Optional manager override",
                },
            },
            "required": ["package"],
        },
    )
    reg.register_builtin_handler(
        "house_status",
        "What she is and what this PC is: census, RMB/vision/vault/apps, "
        "and which tool drives each organ. Call this instead of guessing "
        "or sending the owner to Settings.",
        house_status,
        {"type": "object", "properties": {}},
    )
