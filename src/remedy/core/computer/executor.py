"""Dispatch computer actions to browser host bridge or Windows desktop."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from remedy.core.computer.audit import log_computer_action
from remedy.core.computer.host_bridge import get_host_bridge
from remedy.core.computer.router import (
    ComputerTarget,
    host_label,
    infer_sticky_target,
    is_valid_navigate_url,
    normalize_url,
    resolve_target,
    wants_system_browser,
)
from remedy.core.computer.types import ComputerAction, public_result


class ComputerExecutor:
    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home_dir = home_dir
        self.bridge = get_host_bridge(home_dir)
        # Bound for the current run() so browser enqueue/cancel are session-scoped.
        self._active_session_id: str | None = None

    def _session_id(self, runtime: Any | None) -> str | None:
        if runtime is not None:
            raw = str(getattr(runtime, "_session_id", None) or "").strip()
            if raw:
                return raw
        try:
            from remedy.core.turn_context import current_session_id

            return current_session_id()
        except Exception:
            return None

    def _abort_check(self) -> bool:
        try:
            from remedy.core.turn_context import is_turn_aborted

            return bool(is_turn_aborted())
        except Exception:
            return False

    def _cancel_open_jobs(self, reason: str = "aborted") -> int:
        """Cancel open host jobs for this turn's session only (multi-tab safe)."""
        return self.bridge.cancel_pending_and_running(
            reason=reason,
            session_id=self._active_session_id,
        )

    def _enqueue(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        """Enqueue a host job stamped with the active session id."""
        pl = dict(payload or {})
        sid = self._active_session_id
        if sid:
            pl.setdefault("session_id", sid)
        return self.bridge.enqueue(action, pl, session_id=sid)

    def run(
        self,
        action: ComputerAction | str,
        *,
        target: str = "auto",
        runtime: Any | None = None,
        **kwargs: Any,
    ) -> str:
        act = (
            action
            if isinstance(action, ComputerAction)
            else ComputerAction(str(action).lower())
        )
        self._active_session_id = self._session_id(runtime)
        url = kwargs.get("url")
        hint = kwargs.get("hint") or kwargs.get("reason") or ""
        # computer_app: resolve game.exe / hello.exe against the project folder
        if act is ComputerAction.APP and runtime is not None:
            dirs: list[Any] = []
            try:
                p = runtime.effective_project_path()
                if p is not None:
                    dirs.append(p)
            except Exception:
                pass
            if dirs:
                kwargs = {**kwargs, "search_dirs": dirs}
        goal = kwargs.get("goal") or ""
        ref = str(kwargs.get("ref") or "").strip()
        hint_blob = f"{hint} {goal}".strip()
        # Navigate defaults to in-app rail unless user asked for system/external browser
        if act is ComputerAction.NAVIGATE:
            req_target = "desktop" if wants_system_browser(str(hint), target) else "browser"
        elif act is ComputerAction.PAGE_TEXT:
            req_target = "browser"
        elif act is ComputerAction.WAIT:
            req_target = "desktop"  # pure sleep; target irrelevant
        elif act is ComputerAction.APP:
            req_target = "desktop"
        else:
            # Sticky last target + ref prefix (eN rail, wN/cN desktop).
            # Do NOT force browser on text=/ref= clicks — that sent game/UI
            # clicks into the Browser rail after computer_app.
            last_t = ""
            last_el_tgt = ""
            try:
                last_t = self.bridge.last_drive_target() or ""
                last_el_tgt = str(
                    (self.bridge.last_elements_info() or {}).get("target") or ""
                )
            except Exception:
                last_t = ""
                last_el_tgt = ""
            req_target = infer_sticky_target(
                target,
                action=act.value,
                ref=ref,
                hint=hint_blob,
                url=str(url or ""),
                last_target=last_t,
                last_elements_target=last_el_tgt,
            )
            # Prefer the rail only after a successful navigate (not merely
            # because the Desktop poller is alive — that stole first desktop
            # clicks onto WebView).
            if (
                req_target == "auto"
                and act
                in (
                    ComputerAction.CLICK,
                    ComputerAction.FIND,
                    ComputerAction.ACT,
                    ComputerAction.SNAPSHOT,
                    ComputerAction.TYPE,
                    ComputerAction.KEY,
                    ComputerAction.SCROLL,
                )
                and last_t == "browser"
            ):
                req_target = "browser"
        tgt = resolve_target(
            req_target,
            url=url,
            hint=str(hint),
            action=act.value,
        )
        try:
            if self._abort_check():
                self._cancel_open_jobs(reason="aborted")
                return json.dumps(
                    public_result(
                        ok=False,
                        target=host_label(tgt),
                        action=act.value,
                        message="Aborted by user",
                    )
                )

            if tgt is ComputerTarget.BROWSER:
                result = self._run_browser(act, **kwargs)
            else:
                result = self._run_desktop(act, **kwargs)
            if result.get("ok"):
                result = self._see_if_needed(act, result, runtime=runtime, **kwargs)

            # If Stop fired mid-action, surface abort even if partial work finished
            if self._abort_check():
                self._cancel_open_jobs(reason="aborted")
                result = public_result(
                    ok=False,
                    target=host_label(tgt),
                    action=act.value,
                    message="Aborted by user",
                    extra={"partial": result} if result else None,
                )

            if result.get("ok") and act not in (
                ComputerAction.WAIT,
                ComputerAction.PAGE_TEXT,
                ComputerAction.SNAPSHOT,
                ComputerAction.FIND,
                ComputerAction.MONITORS,
                ComputerAction.SCREENSHOT,
            ):
                try:
                    self.bridge.set_last_drive_target(host_label(tgt))
                except Exception:
                    pass
            log_computer_action(
                action=act.value,
                target=host_label(tgt),
                ok=bool(result.get("ok")),
                detail={k: result.get(k) for k in ("path", "url", "x", "y", "message") if k in result},
                session_id=self._session_id(runtime),
                home_dir=self.home_dir,
            )
            return json.dumps(result, default=str)
        except Exception as e:
            err = public_result(
                ok=False,
                target=host_label(tgt),
                action=act.value,
                message=str(e),
            )
            log_computer_action(
                action=act.value,
                target=host_label(tgt),
                ok=False,
                detail={"error": str(e)},
                session_id=self._session_id(runtime),
                home_dir=self.home_dir,
            )
            return json.dumps(err, default=str)

    def _see_if_needed(
        self,
        act: ComputerAction,
        result: dict[str, Any],
        *,
        runtime: Any | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run built-in vision on screenshots and empty UIA snapshots."""
        from remedy.core.computer.vision_observe import (
            format_vision_block,
            observe_screenshot,
            snapshot_needs_vision,
        )

        hint = str(kwargs.get("hint") or kwargs.get("goal") or "")
        path = str(result.get("path") or "")
        origin = result.get("origin") if isinstance(result.get("origin"), dict) else {}
        width = result.get("width")
        height = result.get("height")

        if act is ComputerAction.SCREENSHOT and path:
            decoded = observe_screenshot(
                path,
                runtime=runtime,
                origin=origin,
                width=int(width) if width else None,
                height=int(height) if height else None,
                hint=hint,
            )
            block = format_vision_block(decoded, origin=origin, path=path)
            result["vision_ok"] = bool(decoded.get("ok"))
            result["message"] = f"{result.get('message') or ''}\n\n{block}".strip()
            if decoded.get("text"):
                result["vision"] = decoded["text"]
            try:
                self.bridge.set_last_shot(
                    origin=origin,
                    width=int(width) if width else None,
                    height=int(height) if height else None,
                    path=path,
                )
            except Exception:
                pass
            return result

        last_t = ""
        try:
            last_t = self.bridge.last_drive_target()
        except Exception:
            last_t = ""
        if act is ComputerAction.SNAPSHOT and snapshot_needs_vision(
            list(result.get("elements") or []),
            hint=hint,
            last_target=last_t,
            already_fallback=bool(result.get("fallback")),
        ):
            try:
                from remedy.core.computer import desktop_win as win

                hwnd = kwargs.get("hwnd")
                if hwnd:
                    info = win.print_window_png(int(hwnd))
                else:
                    info = win.screenshot_png()
                path = str(info.get("path") or "")
                origin = info.get("origin") if isinstance(info.get("origin"), dict) else {}
                decoded = observe_screenshot(
                    path,
                    runtime=runtime,
                    origin=origin,
                    width=info.get("width"),
                    height=info.get("height"),
                    hint=hint,
                )
                block = format_vision_block(decoded, origin=origin, path=path)
                result["fallback"] = result.get("fallback") or "vision"
                result["path"] = path
                result["width"] = info.get("width")
                result["height"] = info.get("height")
                result["origin"] = origin
                result["vision_ok"] = bool(decoded.get("ok"))
                if decoded.get("text"):
                    result["vision"] = decoded["text"]
                result["message"] = (
                    f"{result.get('message') or ''}\n"
                    "UIA/DOM had no clickable controls — captured the pixels "
                    "and ran built-in vision.\n"
                    f"{block}"
                ).strip()
            except Exception as e:
                result["message"] = (
                    f"{result.get('message') or ''}\n"
                    f"Vision fallback failed ({e}). "
                    "Retry computer_screenshot target=desktop."
                ).strip()
        return result

    def _run_desktop(self, act: ComputerAction, **kwargs: Any) -> dict[str, Any]:
        from remedy.core.computer import desktop_win as win

        if act is ComputerAction.SCREENSHOT:
            if kwargs.get("hwnd"):
                info = win.print_window_png(int(kwargs["hwnd"]))
                return public_result(
                    ok=True,
                    target="desktop",
                    action="screenshot",
                    message=f"Window PrintWindow capture ({info['width']}x{info['height']})",
                    extra=info,
                )
            mon = kwargs.get("monitor")
            if mon is not None and str(mon).strip() != "" and str(mon).lower() != "all":
                info = win.screenshot_monitor_png(int(mon))
                msg = f"Monitor {mon} screenshot ({info['width']}x{info['height']})"
            else:
                info = win.screenshot_png()
                msg = f"Screenshot saved ({info['width']}x{info['height']})"
            return public_result(
                ok=True,
                target="desktop",
                action="screenshot",
                message=msg,
                extra=info,
            )
        if act is ComputerAction.MONITORS:
            mons = win.list_monitors()
            return public_result(
                ok=True,
                target="desktop",
                action="monitors",
                message=f"{len(mons)} monitor(s)",
                extra={"monitors": mons},
            )
        if act is ComputerAction.SNAPSHOT:
            mode = str(kwargs.get("mode") or "auto")
            hwnd_raw = kwargs.get("hwnd")
            hwnd = int(hwnd_raw) if hwnd_raw not in (None, "", 0, "0") else None
            elements = win.desktop_snapshot(
                limit=int(kwargs.get("limit") or 40),
                mode=mode,
                hwnd=hwnd,
            )
            self.bridge.set_last_elements(elements, target="desktop")
            n_w = sum(1 for e in elements if str(e.get("ref", "")).startswith("w"))
            n_c = sum(1 for e in elements if str(e.get("ref", "")).startswith("c"))
            return public_result(
                ok=True,
                target="desktop",
                action="snapshot",
                message=f"{len(elements)} elements (windows={n_w}, controls={n_c})",
                extra={"elements": elements, "mode": mode},
            )
        if act is ComputerAction.WAIT:
            raw_sec = kwargs.get("seconds")
            sec = float(raw_sec) if raw_sec is not None else 0.5
            sec = max(0.05, min(sec, 30.0))
            time.sleep(sec)
            return public_result(
                ok=True,
                target="desktop",
                action="wait",
                message=f"Waited {sec:.2f}s",
                extra={"seconds": sec},
            )
        if act is ComputerAction.APP:
            app = str(kwargs.get("app") or kwargs.get("name") or "")
            search_dirs = list(kwargs.get("search_dirs") or [])
            info = win.open_app(app, search_dirs=search_dirs or None)
            # New window — drop stale UIA refs from the previous app
            try:
                self.bridge.set_last_elements([], target="desktop")
            except Exception:
                pass
            time.sleep(0.4)
            return public_result(
                ok=True,
                target="desktop",
                action="app",
                message=f"Launched app: {app}",
                extra=info,
            )
        if act is ComputerAction.FIND:
            query = str(kwargs.get("text") or kwargs.get("query") or kwargs.get("hint") or "")
            elements = win.desktop_snapshot(
                limit=int(kwargs.get("limit") or 60),
                mode=str(kwargs.get("mode") or "auto"),
            )
            self.bridge.set_last_elements(elements, target="desktop")
            from remedy.core.computer.elements import find_best_elements

            hits = find_best_elements(elements, query, top_k=int(kwargs.get("limit") or 8))
            return public_result(
                ok=True,
                target="desktop",
                action="find",
                message=f"{len(hits)} match(es) for {query!r}",
                extra={"query": query, "matches": hits, "elements": hits},
            )
        if act is ComputerAction.CLICK:
            if self._abort_check():
                raise RuntimeError("Aborted by user")
            ref = str(kwargs.get("ref") or "").strip()
            text_q = str(kwargs.get("text") or "").strip()
            if text_q and not ref:
                info = self.bridge.last_elements_info()
                elements = (
                    list(info.get("elements") or [])
                    if str(info.get("target") or "") == "desktop"
                    else []
                )
                if not elements:
                    elements = win.desktop_snapshot(limit=60, mode="auto")
                    self.bridge.set_last_elements(elements, target="desktop")
                from remedy.core.computer.elements import find_best_element

                el = find_best_element(elements, text_q)
                if el is None:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="click",
                        message=f"No desktop control matching text={text_q!r} — try computer_snapshot",
                    )
                win.click_element(
                    el,
                    button=str(kwargs.get("button") or "left"),
                    clicks=int(kwargs.get("clicks") or 1),
                )
                return public_result(
                    ok=True,
                    target="desktop",
                    action="click",
                    message=f"Clicked text={text_q!r} → {el.get('ref')} ({str(el.get('name') or '')[:40]})",
                    extra={
                        "ref": el.get("ref"),
                        "text": text_q,
                        "x": el.get("x"),
                        "y": el.get("y"),
                        "match_score": el.get("match_score"),
                    },
                )
            if ref:
                el = self.bridge.get_element_by_ref(ref)
                if el is None:
                    # Refresh window snapshot once
                    elements = win.desktop_snapshot()
                    self.bridge.set_last_elements(elements, target="desktop")
                    el = self.bridge.get_element_by_ref(ref)
                if el is None:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="click",
                        message=f"Unknown ref {ref} — run computer_snapshot first",
                    )
                win.click_element(
                    el,
                    button=str(kwargs.get("button") or "left"),
                    clicks=int(kwargs.get("clicks") or 1),
                )
                return public_result(
                    ok=True,
                    target="desktop",
                    action="click",
                    message=f"Clicked ref={ref} ({el.get('name', '')[:40]})",
                    extra={"ref": ref, "x": el.get("x"), "y": el.get("y"), "hwnd": el.get("hwnd")},
                )
            x, y = int(kwargs.get("x") or 0), int(kwargs.get("y") or 0)
            if x == 0 and y == 0:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="click",
                    message="Provide ref=, text=, or explicit x/y (refusing bare click at 0,0)",
                )
            try:
                shot = self.bridge.last_shot()
                ox = int((shot.get("origin") or {}).get("x") or 0)
                oy = int((shot.get("origin") or {}).get("y") or 0)
                sw = int(shot.get("width") or 0)
                sh = int(shot.get("height") or 0)
                if sw > 8 and sh > 8 and 0 <= x < sw and 0 <= y < sh:
                    x, y = x + ox, y + oy
            except Exception:
                pass
            win.click(
                x,
                y,
                button=str(kwargs.get("button") or "left"),
                clicks=int(kwargs.get("clicks") or 1),
            )
            return public_result(
                ok=True,
                target="desktop",
                action="click",
                message=f"Clicked ({x},{y})",
                extra={"x": x, "y": y},
            )
        if act is ComputerAction.DRAG:
            if self._abort_check():
                raise RuntimeError("Aborted by user")
            x1, y1 = int(kwargs.get("x", 0)), int(kwargs.get("y", 0))
            x2 = int(kwargs.get("x2", x1))
            y2 = int(kwargs.get("y2", y1))
            win.drag(x1, y1, x2, y2)
            return public_result(
                ok=True,
                target="desktop",
                action="drag",
                message=f"Drag ({x1},{y1})→({x2},{y2})",
                extra={"x": x1, "y": y1, "x2": x2, "y2": y2},
            )
        if act is ComputerAction.TYPE:
            text = str(kwargs.get("text") or "")
            typed_box: list[int] = [0]
            try:
                win.type_text(
                    text,
                    abort_check=self._abort_check,
                    chars_typed=typed_box,
                )
            except RuntimeError as e:
                if "abort" in str(e).lower():
                    self._cancel_open_jobs(reason="aborted")
                    n = int(typed_box[0] if typed_box else 0)
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=f"Aborted by user during type after {n} chars",
                        extra={
                            "length": len(text),
                            "typed": n,
                            "aborted": True,
                        },
                    )
                raise
            if self._abort_check():
                self._cancel_open_jobs(reason="aborted")
                n = int(typed_box[0] if typed_box else 0)
                return public_result(
                    ok=False,
                    target="desktop",
                    action="type",
                    message=f"Aborted by user during type after {n} chars",
                    extra={
                        "length": len(text),
                        "typed": n,
                        "aborted": True,
                    },
                )
            return public_result(
                ok=True,
                target="desktop",
                action="type",
                message=f"Typed {len(text)} chars",
                extra={"length": len(text)},
            )
        if act is ComputerAction.KEY:
            key = str(kwargs.get("key") or "")
            win.press_key(key)
            return public_result(
                ok=True,
                target="desktop",
                action="key",
                message=f"Pressed {key}",
                extra={"key": key},
            )
        if act is ComputerAction.SCROLL:
            x, y = int(kwargs.get("x", 0)), int(kwargs.get("y", 0))
            raw_dy = kwargs.get("dy")
            dy = int(raw_dy) if raw_dy is not None else -3
            win.scroll(x, y, dy=dy)
            return public_result(
                ok=True,
                target="desktop",
                action="scroll",
                message=f"Scrolled at ({x},{y}) dy={dy}",
                extra={"x": x, "y": y, "dy": dy},
            )
        if act is ComputerAction.WINDOWS:
            mode = str(kwargs.get("mode") or "list").lower()
            if mode in ("focus", "activate"):
                hwnd = int(kwargs.get("hwnd") or 0)
                title = str(kwargs.get("title") or kwargs.get("hint") or "").strip()
                if not hwnd and title:
                    found = win.focus_window_by_title(title)
                    if not found:
                        return public_result(
                            ok=False,
                            target="desktop",
                            action="windows",
                            message=f"No window matching title={title!r}",
                        )
                    return public_result(
                        ok=True,
                        target="desktop",
                        action="windows",
                        message=f"Focused window: {found.get('title', '')[:80]}",
                        extra=found,
                    )
                if not hwnd:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="windows",
                        message="hwnd or title required for focus",
                    )
                win.focus_window(hwnd)
                return public_result(
                    ok=True,
                    target="desktop",
                    action="windows",
                    message=f"Focused hwnd={hwnd}",
                    extra={"hwnd": hwnd},
                )
            wins = win.list_windows(limit=int(kwargs.get("limit") or 40))
            return public_result(
                ok=True,
                target="desktop",
                action="windows",
                message=f"{len(wins)} visible windows",
                extra={"windows": wins},
            )
        if act is ComputerAction.NAVIGATE:
            # Desktop navigate = OS default browser — only when explicitly requested.
            raw_u = str(kwargs.get("url") or "")
            url = normalize_url(raw_u)
            if not url or not is_valid_navigate_url(url):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="navigate",
                    message=(
                        "Invalid navigate URL (refusing task-text leak). "
                        "Pass a real https URL e.g. https://mail.google.com — "
                        f"not prose like {raw_u[:80]!r}."
                    ),
                )
            hint_s = str(kwargs.get("hint") or kwargs.get("reason") or "")
            tgt_s = str(kwargs.get("target") or "desktop")
            if not wants_system_browser(hint_s, tgt_s):
                # Mis-routed: prefer rail / fail instead of surprising OS browser.
                if self.bridge.host_connected(max_age_s=20.0):
                    return self._navigate_rail_fast(
                        {"url": url},
                        hint=hint_s,
                        req_target="browser",
                    )
                return public_result(
                    ok=False,
                    target="browser",
                    action="navigate",
                    message=(
                        f"Refusing system browser for {url}. "
                        "Use the in-app Browser rail (start Remedy Desktop) "
                        "or ask explicitly for the system/external browser."
                    ),
                    extra={"url": url, "rail_failed": True, "system_browser_blocked": True},
                )
            info = win.open_url(url)
            return public_result(
                ok=True,
                target="desktop",
                action="navigate",
                message=f"Opened system browser (explicitly requested): {url}",
                extra=info,
            )
        if act is ComputerAction.ACT:
            url = str(kwargs.get("url") or "").strip()
            # URL → rail compound. No URL → drive the focused desktop app
            # (games, notepad, compiled programs). Never bounce to the rail
            # just because the Desktop host happens to be polling.
            if url and self.bridge.host_connected():
                return self._computer_act(
                    dict(kwargs), hint=str(kwargs.get("hint") or ""), req_target="browser"
                )
            if url:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="act",
                    message="computer_act url= needs Browser rail host — open Remedy Desktop",
                )
            return self._computer_act_desktop(
                dict(kwargs), hint=str(kwargs.get("hint") or "")
            )
        return public_result(
            ok=False,
            target="desktop",
            action=act.value,
            message=f"Unsupported desktop action: {act.value}",
        )

    def _run_browser(self, act: ComputerAction, **kwargs: Any) -> dict[str, Any]:
        """Drive the in-app browser rail (default for web).

        Navigate always tries the **rail first**. System browser only if the user
        asked for it, or the Desktop host fails to claim the job quickly.
        """
        payload = {k: v for k, v in kwargs.items() if v is not None}
        if act is ComputerAction.NAVIGATE and payload.get("url"):
            raw_u = str(payload.get("url") or "")
            cleaned = normalize_url(raw_u)
            if not cleaned or not is_valid_navigate_url(cleaned):
                return public_result(
                    ok=False,
                    target="browser",
                    action="navigate",
                    message=(
                        "Invalid navigate URL (blocked task-text leak into address bar). "
                        "Use a real URL like https://mail.google.com, not the full user "
                        f"instruction. Got: {raw_u[:100]!r}."
                    ),
                    extra={"rail_failed": True, "rejected_url": raw_u[:200]},
                )
            payload["url"] = cleaned

        hint = str(kwargs.get("hint") or "")
        req_target = str(kwargs.get("target") or "auto")
        # Explicit system/external browser request — skip rail
        if act is ComputerAction.NAVIGATE and wants_system_browser(
            hint, req_target
        ):
            r = self._run_desktop(act, **kwargs)
            r["note"] = "Opened system browser (user/model requested external browser)"
            r["target"] = "desktop"
            return r

        # Prefer showing the Browser rail when driving the embed
        if act in (
            ComputerAction.NAVIGATE,
            ComputerAction.CLICK,
            ComputerAction.TYPE,
            ComputerAction.SCREENSHOT,
            ComputerAction.SNAPSHOT,
            ComputerAction.PAGE_TEXT,
            ComputerAction.FIND,
        ):
            payload.setdefault("ui", {})
            if isinstance(payload.get("ui"), dict):
                payload["ui"]["open_browser"] = True

        # After optimistic navigate, wait for paint before type/click/page text.
        if act in (
            ComputerAction.CLICK,
            ComputerAction.TYPE,
            ComputerAction.PAGE_TEXT,
            ComputerAction.SNAPSHOT,
            ComputerAction.FIND,
        ) and self.bridge.navigate_needs_settle():
            slept = self.bridge.settle_after_navigate(min_s=0.6, max_s=1.2)
            # Best-effort ready probe via host job (ignore failures).
            try:
                ready_job = self._enqueue("ready", {"action": "ready"})
                ready_fin = self.bridge.wait(
                    ready_job.id,
                    timeout_s=0.45,
                    poll_s=0.03,
                    abort_check=self._abort_check,
                    unclaimed_timeout_s=None,
                    grace_s=0.05,
                )
                if ready_fin and ready_fin.status == "done":
                    self.bridge.clear_navigate_optimistic()
                elif slept >= 0.55:
                    # Timed settle without ready confirm — allow action but keep flag briefly.
                    pass
            except Exception:
                pass

        # Non-navigate without host: screenshots can still use OS capture.
        # DOM jobs (snapshot/page_text/ready/click/type) still enqueue — the
        # Desktop Rust poller claims from disk even when this process's
        # in-memory host_connected flag is stale (CLI / after mark_host_dead).
        if not self.bridge.host_connected() and act is not ComputerAction.NAVIGATE:
            if act is ComputerAction.SCREENSHOT:
                # Fall through — PrintWindow / rail crop work without poller hello.
                pass
            elif act is ComputerAction.WINDOWS:
                return self._run_desktop(act, **kwargs)
            elif act in (
                ComputerAction.SNAPSHOT,
                ComputerAction.PAGE_TEXT,
                ComputerAction.FIND,
                ComputerAction.CLICK,
                ComputerAction.TYPE,
                ComputerAction.KEY,
                ComputerAction.SCROLL,
                ComputerAction.ACT,
            ):
                # Optimistic enqueue — host poller may still be alive on disk.
                pass
            else:
                return public_result(
                    ok=False,
                    target="browser",
                    action=act.value,
                    message=(
                        "Desktop host not connected. Open Remedy Desktop so the "
                        "in-rail browser can be driven."
                    ),
                )

        # Navigate: always queue for rail first (even if host_connected was false —
        # poller may wake; fail fast if unclaimed).

        # Screenshot: prefer PrintWindow on WebView host, else crop rail bounds
        if act is ComputerAction.SCREENSHOT:
            try:
                from remedy.core.computer import desktop_win as win

                wv = win.find_webview_host_hwnd()
                if wv:
                    info = win.print_window_png(wv)
                    return public_result(
                        ok=True,
                        target="browser",
                        action="screenshot",
                        message=(
                            f"WebView PrintWindow capture "
                            f"({info['width']}x{info['height']})"
                        ),
                        extra={**info, "method": "PrintWindow"},
                    )
            except Exception:
                pass
            bounds = self.bridge.get_browser_bounds()
            if bounds and bounds.get("width", 0) > 40 and bounds.get("height", 0) > 40:
                try:
                    from remedy.core.computer import desktop_win as win

                    info = win.screenshot_region_png(
                        int(bounds["x"]),
                        int(bounds["y"]),
                        int(bounds["width"]),
                        int(bounds["height"]),
                        scale=float(bounds.get("scale") or 1.0),
                    )
                    return public_result(
                        ok=True,
                        target="browser",
                        action="screenshot",
                        message=(
                            f"Browser rail region capture "
                            f"({info['width']}x{info['height']})"
                        ),
                        extra={**info, "bounds": bounds, "method": "region_crop"},
                    )
                except Exception:
                    pass  # fall through to host job / full desktop

        # Compound act: navigate → wait → click/type chain (research: fewer observe steps)
        if act is ComputerAction.ACT:
            return self._computer_act(payload, hint=hint, req_target=req_target)

        # Navigate must be lightning-fast for the agent (<1s tool return).
        # Host poller opens the WebView; we do not block on page load.
        if act is ComputerAction.NAVIGATE and payload.get("url"):
            return self._navigate_rail_fast(payload, hint=hint, req_target=req_target)

        # Atomic click-by-text in the rail (one JS pass — no vision)
        if act is ComputerAction.CLICK:
            text_q = str(kwargs.get("text") or "").strip()
            ref = str(kwargs.get("ref") or "").strip()
            if text_q and not ref:
                return self._browser_click_text(text_q, kwargs)
            if ref:
                # Prefer host job click_ref; also try last elements coords
                payload["ref"] = ref
                payload["action"] = "click"
            elif not (kwargs.get("x") or kwargs.get("y")):
                return public_result(
                    ok=False,
                    target="browser",
                    action="click",
                    message="Provide ref=, text=, or x/y for computer_click",
                )

        if act is ComputerAction.FIND:
            query = str(
                kwargs.get("text") or kwargs.get("query") or kwargs.get("hint") or ""
            )
            snap = self._browser_snapshot_now(kwargs)
            if not snap.get("ok"):
                return snap
            elements = list(snap.get("elements") or [])
            from remedy.core.computer.elements import find_best_elements

            hits = find_best_elements(
                elements, query, top_k=int(kwargs.get("limit") or 8)
            )
            return public_result(
                ok=True,
                target="browser",
                action="find",
                message=f"{len(hits)} match(es) for {query!r}",
                extra={"query": query, "matches": hits, "elements": hits},
            )

        if act is ComputerAction.PAGE_TEXT:
            return self._browser_page_text()

        if act is ComputerAction.WAIT:
            raw_sec = kwargs.get("seconds")
            sec = float(raw_sec) if raw_sec is not None else 0.5
            sec = max(0.05, min(sec, 30.0))
            time.sleep(sec)
            return public_result(
                ok=True,
                target="browser",
                action="wait",
                message=f"Waited {sec:.2f}s",
                extra={"seconds": sec},
            )

        # Snapshot: settle after navigate, then eval-callback (host retries mid-load).
        if act is ComputerAction.SNAPSHOT:
            query = str(kwargs.get("hint") or kwargs.get("query") or "").strip()

            def _desktop_snapshot_fallback(reason: str) -> dict[str, Any]:
                """Host offline / rail miss → window+UIA tree (never hang the agent)."""
                desk = self._run_desktop(
                    ComputerAction.SNAPSHOT,
                    limit=kwargs.get("limit") or 40,
                    mode=kwargs.get("mode") or "auto",
                    hwnd=kwargs.get("hwnd"),
                )
                desk["note"] = (
                    f"Browser rail unavailable ({reason}); "
                    "desktop window/control snapshot instead"
                )
                desk["fallback"] = "desktop"
                desk.setdefault("target", "desktop")
                return desk

            # In-process host_connected can be false (CLI / separate process) even when
            # Desktop is polling jobs from disk. Always try the rail job first; fall
            # back to desktop only if unclaimed / failed.
            host_looks_live = self.bridge.host_connected(max_age_s=12.0)
            slept = self.bridge.settle_after_navigate(min_s=0.7, max_s=1.8)
            # Shorter unclaimed wait when we already believe host is offline.
            unclaimed = 6.0 if host_looks_live else 2.5
            # Host may wait for page ready + up to 2×5s eval retries.
            total_wait = float(
                kwargs.get("timeout_s")
                or (14.0 if host_looks_live else 5.0)
            )
            last_err = ""
            last_job_id = ""
            for attempt in range(2):
                job = self._enqueue(act.value, payload)
                last_job_id = job.id
                finished = self.bridge.wait(
                    job.id,
                    timeout_s=total_wait,
                    poll_s=0.05,
                    abort_check=self._abort_check,
                    unclaimed_timeout_s=unclaimed,
                    grace_s=0.5,
                )
                if finished.status == "done" and finished.result:
                    out = dict(finished.result)
                    # Host may complete with ok:false + error string
                    if out.get("ok") is False and not out.get("elements"):
                        last_err = str(
                            out.get("message") or finished.error or "snapshot failed"
                        )
                        time.sleep(0.45)
                        continue
                    out.setdefault("ok", True)
                    out.setdefault("target", "browser")
                    out.setdefault("action", "snapshot")
                    elements = list(out.get("elements") or [])
                    if elements:
                        self.bridge.set_last_elements(elements, target="browser")
                    from remedy.core.computer.elements import (
                        find_best_elements,
                        format_som_list,
                    )

                    # Set-of-Mark list for the model (OSWorld / SoM practice)
                    out["som"] = format_som_list(
                        elements, limit=int(kwargs.get("limit") or 40), query=query
                    )
                    if query and elements:
                        matches = find_best_elements(elements, query, top_k=6)
                        out["matches"] = matches
                        out["query"] = query
                    # Prefer SoM text as the primary message the model reads
                    out["message"] = out.get("som") or out.get("message") or (
                        f"{len(elements)} interactive elements"
                    )
                    if slept:
                        out["settled_s"] = round(slept, 2)
                    if attempt:
                        out["attempt"] = attempt + 1
                    return out
                last_err = str(finished.error or finished.status or "snapshot failed")
                # Retry once on webview eval timeout / mid-load races
                if attempt == 0 and (
                    "timed out" in last_err.lower()
                    or "timeout" in last_err.lower()
                    or "not open" in last_err.lower()
                ):
                    time.sleep(0.55)
                    continue
                break
            # Rail miss after retries → desktop tree (soak: offline/host-dead path)
            low = last_err.lower()
            if any(
                k in low
                for k in (
                    "timeout",
                    "timed out",
                    "not claim",
                    "offline",
                    "not connected",
                    "not open",
                )
            ):
                return _desktop_snapshot_fallback(last_err)
            return public_result(
                ok=False,
                target="browser",
                action="snapshot",
                message=(
                    f"Browser rail snapshot failed ({last_err}). "
                    "Open/navigate the page in the Browser rail first, then retry "
                    "computer_snapshot. Prefer snapshot+click ref/text — do not use "
                    "screenshot+vision as the primary path (slow and often wrong)."
                ),
                extra={"job_id": last_job_id},
            )

        unclaimed = 3.0
        total_wait = float(kwargs.get("timeout_s") or 12.0)
        job = self._enqueue(act.value, payload)
        finished = self.bridge.wait(
            job.id,
            timeout_s=total_wait,
            poll_s=0.08,
            abort_check=self._abort_check,
            unclaimed_timeout_s=unclaimed,
        )
        if finished.status == "done" and finished.result:
            out = dict(finished.result)
            out.setdefault("ok", True)
            out.setdefault("target", "browser")
            out.setdefault("action", act.value)
            if act is ComputerAction.SNAPSHOT and out.get("elements"):
                self.bridge.set_last_elements(
                    list(out.get("elements") or []),
                    target="browser",
                )
            return out
        err = finished.error or finished.status
        return public_result(
            ok=False,
            target="browser",
            action=act.value,
            message=str(err),
            extra={"job_id": job.id},
        )

    def _browser_snapshot_now(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.bridge.settle_after_navigate(min_s=0.6, max_s=1.5)
        payload: dict[str, Any] = {"ui": {"open_browser": True}}
        last_err = "snapshot failed"
        last_job_id = ""
        for attempt in range(2):
            job = self._enqueue("snapshot", payload)
            last_job_id = job.id
            finished = self.bridge.wait(
                job.id,
                timeout_s=14.0,
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=5.0,
                grace_s=0.8,
            )
            if finished.status == "done" and finished.result:
                out = dict(finished.result)
                if out.get("ok") is False and not out.get("elements"):
                    last_err = str(out.get("message") or finished.error or last_err)
                    time.sleep(0.4)
                    continue
                out.setdefault("ok", True)
                if out.get("elements"):
                    self.bridge.set_last_elements(
                        list(out.get("elements") or []), target="browser"
                    )
                return out
            last_err = finished.error or finished.status or last_err
            if attempt == 0 and "timeout" in str(last_err).lower():
                time.sleep(0.5)
                continue
            break
        return public_result(
            ok=False,
            target="browser",
            action="snapshot",
            message=last_err,
            extra={"job_id": last_job_id},
        )

    def _browser_click_text(self, text_q: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """One-shot: find control by visible text and click it in the rail.

        Retries once after scroll (OSWorld: re-observe after failed action).
        """
        self.bridge.settle_after_navigate(min_s=0.35, max_s=0.9)
        last_err = ""
        for attempt in range(2):
            payload = {
                "ui": {"open_browser": True},
                "text": text_q,
                "click_text": True,
            }
            job = self._enqueue("click", payload)
            finished = self.bridge.wait(
                job.id,
                timeout_s=5.0,
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=2.5,
                grace_s=0.2,
            )
            if finished.status == "done" and finished.result:
                out = dict(finished.result)
                ok = out.get("ok", True)
                msg = str(out.get("message") or out.get("detail") or "")
                if ok and "no-match" not in msg and "failed" not in msg.lower():
                    out.setdefault("ok", True)
                    out.setdefault("target", "browser")
                    out.setdefault("action", "click")
                    out.setdefault("text", text_q)
                    out["attempt"] = attempt + 1
                    return out
                last_err = msg
            else:
                last_err = finished.error or finished.status or "timeout"
            # Retry: scroll down then try again
            if attempt == 0:
                self._enqueue(
                    "scroll",
                    {"ui": {"open_browser": True}, "x": 200, "y": 300, "dy": -4},
                )
                time.sleep(0.25)
        # Fallback: snapshot + match + click ref
        snap = self._browser_snapshot_now(kwargs)
        if snap.get("ok") and snap.get("elements"):
            from remedy.core.computer.elements import find_best_element

            el = find_best_element(list(snap.get("elements") or []), text_q)
            if el and el.get("ref"):
                payload2 = {
                    "ui": {"open_browser": True},
                    "ref": el["ref"],
                    "x": el.get("x"),
                    "y": el.get("y"),
                }
                job2 = self._enqueue("click", payload2)
                fin2 = self.bridge.wait(
                    job2.id,
                    timeout_s=4.0,
                    poll_s=0.05,
                    abort_check=self._abort_check,
                    unclaimed_timeout_s=2.0,
                    grace_s=0.15,
                )
                if fin2.status == "done" and fin2.result:
                    out = dict(fin2.result)
                    out.setdefault("ok", True)
                    out["text"] = text_q
                    out["ref"] = el.get("ref")
                    out["match_score"] = el.get("match_score")
                    out["message"] = (
                        f"Clicked text={text_q!r} → {el.get('ref')} "
                        f"({str(el.get('name') or '')[:40]})"
                    )
                    return out
        # Rail miss → desktop UIA (game / native window after computer_app)
        try:
            desk = self._run_desktop(ComputerAction.CLICK, text=text_q, **{
                k: v for k, v in kwargs.items() if k not in ("text", "ref", "target")
            })
            if desk.get("ok"):
                desk["note"] = (
                    f"Browser rail click missed ({last_err}); "
                    "clicked matching desktop control instead"
                )
                desk["fallback"] = "desktop"
                return desk
        except Exception:
            pass
        return public_result(
            ok=False,
            target="browser",
            action="click",
            message=(
                f"Could not click text={text_q!r} in Browser rail ({last_err}). "
                "If this is a desktop app/game, retry computer_click "
                "target=desktop (or computer_snapshot target=desktop first)."
            ),
            extra={"text": text_q},
        )

    def _computer_act_desktop(
        self,
        payload: dict[str, Any],
        *,
        hint: str,
    ) -> dict[str, Any]:
        """Compound click/type/key on the focused OS window (games, native apps)."""
        log: list[str] = []
        click = str(payload.get("click") or payload.get("text") or "").strip()
        type_text = str(payload.get("type") or payload.get("type_text") or "").strip()
        key = str(payload.get("key") or "").strip()
        if not (click or type_text or key):
            return public_result(
                ok=False,
                target="desktop",
                action="act",
                message=(
                    "computer_act on desktop needs click=, type=, or key=. "
                    "For a URL, pass url= to use the Browser rail."
                ),
                extra={"hint": hint},
            )
        if click:
            ck = self._run_desktop(ComputerAction.CLICK, text=click, hint=hint)
            log.append(f"click:{ck.get('ok')} {click!r} → {str(ck.get('message') or '')[:60]}")
            if not ck.get("ok"):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="act",
                    message=f"act: desktop click failed — {ck.get('message')}",
                    extra={"steps": log, "detail": ck},
                )
            time.sleep(0.2)
        if type_text:
            ty = self._run_desktop(ComputerAction.TYPE, text=type_text, hint=hint)
            log.append(f"type:{ty.get('ok')} chars={len(type_text)}")
            if not ty.get("ok"):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="act",
                    message=f"act: desktop type failed — {ty.get('message')}",
                    extra={"steps": log, "detail": ty},
                )
        if key:
            ky = self._run_desktop(ComputerAction.KEY, key=key, hint=hint)
            log.append(f"key:{ky.get('ok')} {key}")
            if not ky.get("ok"):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="act",
                    message=f"act: desktop key failed — {ky.get('message')}",
                    extra={"steps": log, "detail": ky},
                )
        return public_result(
            ok=True,
            target="desktop",
            action="act",
            message="SUCCESS: " + " | ".join(log),
            extra={"steps": log, "click": click or None},
        )

    def _computer_act(
        self,
        payload: dict[str, Any],
        *,
        hint: str,
        req_target: str,
    ) -> dict[str, Any]:
        """High-level multi-step action (fast path for agents).

        Research (OSWorld, CUA): fewer LLM rounds + structured observe-act beats
        screenshot thrash. Supports optional navigate, click text, type, key.
        """
        log: list[str] = []
        url = str(payload.get("url") or "").strip()
        click = str(payload.get("click") or payload.get("text") or "").strip()
        type_text = str(payload.get("type") or payload.get("type_text") or "").strip()
        key = str(payload.get("key") or "").strip()
        goal = str(payload.get("goal") or hint or "")

        if url:
            nav = self._navigate_rail_fast(
                {"url": normalize_url(url), "ui": {"open_browser": True}, "settle_s": 0.45},
                hint=hint,
                req_target="browser",
            )
            log.append(f"navigate:{nav.get('ok')} {url}")
            if not nav.get("ok"):
                return public_result(
                    ok=False,
                    target="browser",
                    action="act",
                    message=f"act: navigate failed — {nav.get('message')}",
                    extra={"steps": log, "detail": nav},
                )

        if click:
            ck = self._browser_click_text(click, payload)
            log.append(f"click:{ck.get('ok')} {click!r} → {ck.get('message', '')[:60]}")
            if not ck.get("ok"):
                return public_result(
                    ok=False,
                    target="browser",
                    action="act",
                    message=f"act: click failed — {ck.get('message')}",
                    extra={"steps": log, "detail": ck},
                )
            time.sleep(0.25)

        if type_text:
            # Prefer focusing email/username field if goal implies login
            if not click and any(
                w in (goal + " " + type_text).lower()
                for w in ("email", "user", "login", "@")
            ):
                for label in ("email", "email or phone", "username", "phone or email"):
                    pre = self._browser_click_text(label, payload)
                    if pre.get("ok"):
                        log.append(f"focus:{label}")
                        break
            job = self._enqueue(
                "type",
                {"ui": {"open_browser": True}, "text": type_text},
            )
            fin = self.bridge.wait(
                job.id,
                timeout_s=4.0,
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=2.0,
                grace_s=0.15,
            )
            ok_t = fin.status == "done"
            log.append(f"type:{ok_t} chars={len(type_text)}")
            if not ok_t:
                return public_result(
                    ok=False,
                    target="browser",
                    action="act",
                    message=f"act: type failed — {fin.error}",
                    extra={"steps": log},
                )

        if key:
            job = self._enqueue(
                "key",
                {"ui": {"open_browser": True}, "key": key},
            )
            fin = self.bridge.wait(
                job.id,
                timeout_s=3.0,
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=2.0,
                grace_s=0.1,
            )
            log.append(f"key:{fin.status == 'done'} {key}")

        return public_result(
            ok=True,
            target="browser",
            action="act",
            message="SUCCESS: " + " | ".join(log),
            extra={"steps": log, "url": url or None, "click": click or None},
        )

    def _browser_page_text(self) -> dict[str, Any]:
        self.bridge.settle_after_navigate(min_s=0.6, max_s=1.5)
        # Prefer dedicated action so Rust host can claim (only=…page_text).
        # Legacy SPA path still handles payload.browser_action on click jobs.
        last_err = "page_text failed — open a page in the rail first"
        last_job_id = ""
        for attempt, action in enumerate(("page_text", "page_text")):
            job = self._enqueue(
                action,
                {"ui": {"open_browser": True}, "browser_action": "page_text"},
            )
            last_job_id = job.id
            finished = self.bridge.wait(
                job.id,
                timeout_s=14.0,
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=5.0,
                grace_s=0.8,
            )
            if finished.status == "done" and finished.result:
                out = dict(finished.result)
                if out.get("ok") is False and not (
                    out.get("text") or out.get("title")
                ):
                    last_err = str(out.get("message") or finished.error or last_err)
                    time.sleep(0.4)
                    continue
                out.setdefault("ok", True)
                out.setdefault("action", "page_text")
                out.setdefault("target", "browser")
                return out
            last_err = finished.error or finished.status or last_err
            if attempt == 0:
                time.sleep(0.45)
                continue
            break
        return public_result(
            ok=False,
            target="browser",
            action="page_text",
            message=last_err,
            extra={"job_id": last_job_id},
        )

    def _navigate_rail_fast(
        self,
        payload: dict[str, Any],
        *,
        hint: str,
        req_target: str,
    ) -> dict[str, Any]:
        """Open URL in Browser rail with sub-second agent-visible SUCCESS.

        Strategy:
        1. Enqueue + ui_command (Desktop poller drives WebView).
        2. Wait up to ~0.9s for host complete (normal path is 50–300ms).
        3. If host is alive and still pending → complete SUCCESS optimistically
           (host will still open the page). Never burn 8–14s on open-url.
        """
        url = str(payload.get("url") or "")
        if not url or not is_valid_navigate_url(url):
            return public_result(
                ok=False,
                target="browser",
                action="navigate",
                message=(
                    "Invalid navigate URL (blocked). "
                    f"Got: {url[:100]!r}. Use https://mail.google.com style URLs only."
                ),
                extra={"rail_failed": True},
            )
        if wants_system_browser(hint, req_target):
            r = self._run_desktop(ComputerAction.NAVIGATE, url=url, hint=hint)
            r["note"] = "Opened system browser (user/model requested external browser)"
            r["target"] = "desktop"
            return r

        job = self._enqueue("navigate", payload)
        # Host usually completes in <200ms once poller is hot
        finished = self.bridge.wait(
            job.id,
            timeout_s=0.4,
            poll_s=0.02,
            abort_check=self._abort_check,
            unclaimed_timeout_s=None,
            grace_s=0.08,
        )
        def _nav_ok(out: dict[str, Any], *, optimistic: bool = False) -> dict[str, Any]:
            self.bridge.mark_navigated(url, optimistic=optimistic)
            out.setdefault("ok", True)
            out.setdefault("target", "browser")
            out.setdefault("action", "navigate")
            # Brief settle so follow-up snapshot/click sees painted DOM
            default_settle = 0.55 if optimistic else 0.35
            raw_settle = payload.get("settle_s")
            settle = float(raw_settle) if raw_settle is not None else default_settle
            if settle > 0:
                time.sleep(min(max(settle, 0.0), 1.5))
                out["settled_s"] = min(max(settle, 0.0), 1.5)
            if optimistic:
                out.setdefault("ready_for_input", False)
                out.setdefault("pending_load", True)
            else:
                out.setdefault("ready_for_input", True)
                self.bridge.clear_navigate_optimistic()
            return out

        if finished.status == "done" and finished.result:
            return _nav_ok(dict(finished.result), optimistic=False)

        # Brief re-read (host may complete mid-return)
        for _ in range(8):
            again = self.bridge._read(job.id)
            if again and again.status == "done" and again.result:
                return _nav_ok(dict(again.result), optimistic=False)
            time.sleep(0.025)

        twin = self.bridge.find_recent_success(
            action="navigate", url=url, max_age_s=15.0
        )
        if twin and twin.result:
            out = dict(twin.result)
            out["reconciled"] = True
            out["job_id"] = job.id
            return _nav_ok(out, optimistic=False)

        # Desktop is alive → fire-and-forget SUCCESS so the model never claims
        # the rail failed while the page is opening (open-url must be instant).
        # Mark optimistic so type/click wait for settle before acting.
        if self.bridge.host_connected(max_age_s=20.0):
            result = {
                "ok": True,
                "target": "browser",
                "action": "navigate",
                "message": (
                    f"SUCCESS: Browser rail is opening {url}. "
                    "The user will see the page as it loads. "
                    "Do NOT say the rail failed. Do NOT open system browser. "
                    "Do NOT web_fetch this page. "
                    "Before typing passwords or clicking login controls, run "
                    "computer_snapshot or computer_page_text to confirm the form "
                    "is visible (avoid typing into the previous page)."
                ),
                "url": url,
                "via": "optimistic",
                "user_visible": True,
                "ready_for_input": False,
                "pending_load": True,
                "job_id": job.id,
            }
            # Re-publish ui_command *before* complete so poller still has work
            self.bridge.set_ui_command(
                {
                    "action": "open_browser",
                    "url": url,
                    "job_id": job.id,
                    "job_action": "navigate",
                    "optimistic": True,
                }
            )
            self.bridge.complete(job.id, ok=True, result=result)
            return _nav_ok(result, optimistic=True)

        if wants_system_browser(hint, req_target):
            fb = self._run_desktop(ComputerAction.NAVIGATE, url=url, hint=hint)
            fb["note"] = "rail host offline; opened system browser as requested"
            return fb
        return public_result(
            ok=False,
            target="browser",
            action="navigate",
            message=(
                f"Desktop host not connected — cannot open Browser rail. URL: {url}. "
                "Start Remedy Desktop, then retry computer_navigate. "
                "Do NOT open the system browser unless the user asks."
            ),
            extra={"url": url, "job_id": job.id, "rail_failed": True},
        )


_executor: ComputerExecutor | None = None
_exec_lock = threading.Lock()


def get_computer_executor(home_dir: Path | str | None = None) -> ComputerExecutor:
    global _executor
    with _exec_lock:
        if _executor is None:
            _executor = ComputerExecutor(home_dir=home_dir)
        return _executor
