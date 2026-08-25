"""Dispatch computer actions to browser host bridge or Windows desktop."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
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

# Owner's own web browsers. Remedy has the in-app Browser rail for every web
# task — she must NEVER launch or focus one of these. Driving the owner's
# browser gives her no page eyes (the rail's snapshot/page_text/click do not
# reach it), hijacks the owner's logged-in session, and is exactly what she
# fell back to when the rail poller was starved. This is a hard executor-level
# refusal, not a prompt suggestion (guidance.py states the same rule for the
# model; this enforces it even if the model ignores it).
# Exact executable/app basenames of the owner's own web browsers.
_HOST_BROWSER_EXES = frozenset(
    {
        "firefox", "chrome", "msedge", "edge", "opera", "opera_gx",
        "brave", "vivaldi", "iexplore", "chromium", "waterfox", "librewolf",
        "google chrome", "microsoft edge", "mozilla firefox", "opera gx",
        "brave browser",
    }
)
# Browser window titles read "<page> — Google Chrome" / "<page> - Mozilla Firefox".
_HOST_BROWSER_TITLE_RE = re.compile(
    r"(?i)[-–—|]\s*(mozilla firefox|google chrome|microsoft edge|opera gx|"
    r"opera|brave|vivaldi|internet explorer|chromium|waterfox|librewolf)\s*$"
)

# Grove / Alongside / Studio live inside Remedy's WebView — capturing
# "monitor 0" on a multi-display desk often photographs wallpaper instead.
_SELF_UI_HINT_RE = re.compile(
    r"(?i)\b(grove|alongside|storyline|studio|remedy desktop|"
    r"own (ui|interface|chrome|window)|in-app (ui|chrome))\b"
)


def wants_self_ui_capture(hint: str) -> bool:
    """True when the shot is of Remedy's own Grove/Studio, not the OS desk."""
    return bool(_SELF_UI_HINT_RE.search(hint or ""))


def _expect_url_matches(want: str, hay: str) -> bool:
    """Host-aware expect_url match (rejects github.com ⊆ github.com.evil.com)."""
    w = (want or "").strip().lower()
    h = (hay or "").strip().lower()
    if not w or not h:
        return False
    from urllib.parse import urlsplit

    def _split(raw: str) -> tuple[str, str, str]:
        blob = raw if "://" in raw else f"https://{raw.lstrip('/')}"
        p = urlsplit(blob)
        host = (p.hostname or "").lower().removeprefix("www.")
        path = (p.path or "/").rstrip("/") or "/"
        return host, path, (p.query or "")

    try:
        wh, wp, wq = _split(w)
        hh, hp, hq = _split(h)
    except Exception:
        return w in h
    if not wh:
        return w in h
    if wh != hh:
        return False
    if wp != "/" and wp not in hp and hp not in wp:
        return False
    return not (wq and wq not in hq)


def _is_host_browser(name: str) -> bool:
    """True when *name* names one of the owner's own web browsers.

    Precise on purpose: the browser token must be the whole exe/app basename
    (``computer_app``) or the trailing "— <Browser>" of a window title
    (``windows focus``). A substring match wrongly refused legitimate apps and
    games whose name merely contains 'edge'/'brave'/'opera' (Edge of Tomorrow,
    a game called Brave, an Opera music player) — those must still launch.
    """
    s = (name or "").strip().strip("\"'")
    if not s:
        return False
    # Basename stem (strip path + .exe) — the computer_app case.
    stem = re.split(r"[\\/]", s)[-1].strip().strip("\"'")
    if stem.lower().endswith(".exe"):
        stem = stem[:-4]
    if stem.strip().lower() in _HOST_BROWSER_EXES:
        return True
    # Window-title case: "<page> — <Browser>".
    return bool(_HOST_BROWSER_TITLE_RE.search(s))


_HOST_BROWSER_REFUSAL = (
    "Refused: web tasks live in the in-app Browser rail, not the owner's own "
    "browser. Use computer_navigate / computer_act (target=browser) — you have "
    "no page eyes in a Firefox/Chrome/Edge window and it hijacks the owner's "
    "session. If the rail is unreachable, computer_wait 2 and retry the rail, "
    "then tell the owner — do not open a desktop browser."
)


def _skill_host(url_or_label: str) -> str:
    """Host origin only for skill memory — never the path/query (no secrets)."""
    s = (url_or_label or "").strip()
    if not s:
        return ""
    with contextlib.suppress(Exception):
        if "://" in s:
            from urllib.parse import urlparse

            return (urlparse(s).hostname or "").lower()
    # Already a bare host label / desktop tag.
    return s.split("/", 1)[0].lower()


class ComputerExecutor:
    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home_dir = home_dir
        self.bridge = get_host_bridge(home_dir)
        # Per-thread sid for the current run() — never a process-wide overwrite.
        self._run_tls = threading.local()

    def _session_id(self, runtime: Any | None) -> str | None:
        tls = self._run_session_id()
        if tls:
            return tls
        try:
            from remedy.core.turn_context import turn_session_id

            # turn_session_id already falls back to runtime when *not* in a turn.
            # Never add a second process-wide read — that stamps Tab B onto Tab A.
            return turn_session_id(runtime)
        except Exception:
            return None

    def _run_session_id(self) -> str | None:
        return getattr(self._run_tls, "session_id", None)

    def _abort_check(self) -> bool:
        try:
            from remedy.core.turn_context import is_turn_aborted

            return bool(is_turn_aborted())
        except Exception:
            return False

    def _sleep_abortable(self, sec: float) -> bool:
        """Sleep in short slices. True if the turn was aborted."""
        deadline = time.time() + max(0.0, float(sec))
        while time.time() < deadline:
            if self._abort_check():
                return True
            time.sleep(min(0.1, max(0.02, deadline - time.time())))
        return False

    def _cancel_open_jobs(self, reason: str = "aborted") -> int:
        """Cancel open host jobs for this turn's session only (multi-tab safe)."""
        return self.bridge.cancel_pending_and_running(
            reason=reason,
            session_id=self._run_session_id(),
        )

    def _enqueue(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> Any:
        """Enqueue a host job stamped with the active session id."""
        pl = dict(payload or {})
        sid = session_id if session_id is not None else self._run_session_id()
        if sid:
            pl.setdefault("session_id", sid)
        return self.bridge.enqueue(action, pl, session_id=sid)

    def run(
        self,
        action: ComputerAction | str,
        *,
        target: str = "auto",
        runtime: Any | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        act = (
            action
            if isinstance(action, ComputerAction)
            else ComputerAction(str(action).lower())
        )
        sid = str(session_id or "").strip() or self._session_id(runtime)
        session_id = sid
        self._run_tls.session_id = session_id
        try:
            return self._run_body(
                act,
                runtime=runtime,
                session_id=session_id,
                target=target,
                **kwargs,
            )
        finally:
            self._run_tls.session_id = None

    def _run_body(
        self,
        act: ComputerAction,
        *,
        runtime: Any | None,
        session_id: str | None,
        target: str = "auto",
        **kwargs: Any,
    ) -> str:
        _ = session_id
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
            # Explicit desktop → UIA text read of a native window; default rail.
            req_target = "desktop" if str(target or "").strip().lower() == "desktop" else "browser"
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
                    ComputerAction.SELECT,
                    ComputerAction.FILL,
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
                with contextlib.suppress(Exception):
                    self.bridge.set_last_drive_target(host_label(tgt))
            log_computer_action(
                action=act.value,
                target=host_label(tgt),
                ok=bool(result.get("ok")),
                detail={k: result.get(k) for k in ("path", "url", "x", "y", "message") if k in result},
                session_id=self._session_id(runtime),
                home_dir=self.home_dir,
            )
            # Skill memory: learn which approach works per site (click-family).
            with contextlib.suppress(Exception):
                from remedy.core.computer.computer_skill import (
                    approach_of,
                    record_action,
                )

                if act.value in ("click", "act", "drag", "press_hold"):
                    host = _skill_host(
                        self.bridge.last_navigate_url() or host_label(tgt)
                    )
                    record_action(
                        host,
                        act.value,
                        approach_of(act.value, kwargs),
                        bool(result.get("ok")),
                        home=self.home_dir,
                    )
                    # Newly mastered a site → fold it into who she is (once).
                    if bool(result.get("ok")):
                        from remedy.core.computer.computer_skill import (
                            maybe_site_lesson,
                        )

                        lesson = maybe_site_lesson(host, home=self.home_dir)
                        if lesson:
                            from remedy.memory.soul.update import (
                                record_self_inject_lesson,
                            )

                            record_self_inject_lesson(
                                outcome=lesson["outcome"],
                                tree=lesson["tree"],
                                summary=lesson["summary"],
                                gate_detail=lesson["gate_detail"],
                                home=self.home_dir,
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
            with contextlib.suppress(Exception):
                self.bridge.set_last_shot(
                    origin=origin,
                    width=int(width) if width else None,
                    height=int(height) if height else None,
                    path=path,
                )
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
                info = win.print_window_png(int(hwnd)) if hwnd else win.screenshot_png()
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

    @staticmethod
    def _desktop_evidence() -> dict[str, Any]:
        """Post-action proof for desktop acts: foreground window + focused element.

        The desktop analogue of the rail's page probe — without this, native
        clicks/types report success with zero observation.
        """
        ev: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            from remedy.core.computer import desktop_win as win

            fg = win.foreground_window_info()
            if fg.get("title"):
                ev["foreground"] = str(fg["title"])[:100]
                ev["foreground_hwnd"] = int(fg.get("hwnd") or 0)
        with contextlib.suppress(Exception):
            from remedy.core.computer.desktop_uia import focused_element_info

            fi = focused_element_info()
            if fi:
                ev["focused"] = {
                    "name": fi.get("name", ""),
                    "role": fi.get("role", ""),
                    "value": str(fi.get("value") or "")[:200],
                }
        return ev

    def _desktop_click_element(
        self, win: Any, el: dict[str, Any], *, button: str, clicks: int
    ) -> dict[str, Any]:
        """Click a desktop element the reliable way.

        Offscreen elements (kept + flagged by the UIA snapshot) are scrolled
        into view first, then re-located so the click lands on real pixels.
        Returns the possibly-updated element dict.
        """
        if el.get("offscreen") and el.get("hwnd"):
            with contextlib.suppress(Exception):
                from remedy.core.computer.desktop_uia import element_action

                res = element_action(
                    int(el["hwnd"]),
                    str(el.get("name") or ""),
                    role=str(el.get("role") or ""),
                    action="scroll_into_view",
                )
                if res.get("ok"):
                    time.sleep(0.15)
                    fresh = win.desktop_snapshot(
                        limit=80, mode="controls", hwnd=int(el["hwnd"])
                    )
                    from remedy.core.computer.elements import find_best_element

                    upd = find_best_element(fresh, str(el.get("name") or ""))
                    if upd is not None and not upd.get("offscreen"):
                        el = upd
        win.click_element(el, button=button, clicks=clicks)
        return el

    def _run_desktop(self, act: ComputerAction, **kwargs: Any) -> dict[str, Any]:
        from remedy.core.computer import desktop_win as win

        # UAC / secure-desktop guard: SendInput cannot touch the secure desktop,
        # so a click/type/key there would silently no-op and we'd report a false
        # success. Tell the owner honestly instead.
        if act in (
            ComputerAction.CLICK,
            ComputerAction.TYPE,
            ComputerAction.KEY,
            ComputerAction.PRESS_HOLD,
            ComputerAction.DRAG,
        ):
            with contextlib.suppress(Exception):
                sysp = win.detect_system_prompt()
                if sysp.get("blocked"):
                    return public_result(
                        ok=False,
                        target="desktop",
                        action=act.value,
                        message=sysp["message"],
                        extra={"blocked": sysp.get("kind") or "system_prompt"},
                    )

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
            hint_s = str(kwargs.get("hint") or "")
            if wants_self_ui_capture(hint_s):
                hwnd = None
                with contextlib.suppress(Exception):
                    hwnd = win.find_remedy_desktop_hwnd()
                if hwnd:
                    info = win.print_window_png(int(hwnd))
                    return public_result(
                        ok=True,
                        target="desktop",
                        action="screenshot",
                        message=(
                            f"Remedy Desktop window capture "
                            f"({info['width']}x{info['height']}) — her own "
                            "Grove/Studio/Alongside, not a random monitor."
                        ),
                        extra=info,
                    )
            mon = kwargs.get("monitor")
            # Set-of-Mark: overlay numbered boxes at the last desktop snapshot's
            # elements so a vision model can "click mark N" instead of guessing.
            mark_flag = bool(kwargs.get("mark"))
            marks: list[dict[str, Any]] = []
            legend: list[dict[str, Any]] = []
            marks_from_pixels = False
            if mark_flag:
                info0 = self.bridge.last_elements_info()
                els = (
                    list(info0.get("elements") or [])
                    if str(info0.get("target") or "") == "desktop"
                    else []
                )
                if not els:
                    els = win.desktop_snapshot(limit=40, mode="auto")
                    self.bridge.set_last_elements(els, target="desktop")
                if els:
                    for i, el in enumerate(els[:30], start=1):
                        marks.append(
                            {"n": i, "x": int(el.get("x") or 0), "y": int(el.get("y") or 0)}
                        )
                        legend.append(
                            {
                                "mark": i,
                                "ref": el.get("ref"),
                                "name": str(el.get("name") or "")[:50],
                            }
                        )
                else:
                    # No accessibility tree (game / canvas / custom paint):
                    # detect candidate targets from pixels and mark those.
                    marks_from_pixels = True
                    raw, strd, cw, ch, ox, oy = win._capture_virtual_screen()
                    cands = win.detect_ui_candidates(raw, strd, cw, ch, max_marks=20)
                    for i, c in enumerate(cands, start=1):
                        marks.append(
                            {"n": i, "x": int(c["x"]) + ox, "y": int(c["y"]) + oy}
                        )
                        legend.append(
                            {
                                "mark": i,
                                "x": int(c["x"]) + ox,
                                "y": int(c["y"]) + oy,
                                "w": c["w"],
                                "h": c["h"],
                                "source": "pixels",
                            }
                        )
            if mon is not None and str(mon).strip() != "" and str(mon).lower() != "all":
                info = win.screenshot_monitor_png(int(mon))
                msg = f"Monitor {mon} screenshot ({info['width']}x{info['height']})"
            else:
                info = win.screenshot_png(marks=marks or None)
                msg = f"Screenshot saved ({info['width']}x{info['height']})"
                if legend:
                    info["marks"] = legend
                    src = "pixel-detected" if marks_from_pixels else "element"
                    msg += f" with {len(legend)} {src} marks (click by mark's x/y or ref)"
            return public_result(
                ok=True,
                target="desktop",
                action="screenshot",
                message=msg,
                extra=info,
            )
        if act is ComputerAction.MONITORS:
            mons = win.list_monitors()
            home = next((m["index"] for m in mons if m.get("remedy")), None)
            msg = f"{len(mons)} monitor(s)"
            if home is not None:
                msg += (
                    f"; Remedy Desktop is on monitor {home} "
                    "(use computer_screenshot hint='grove' to capture her window)"
                )
            return public_result(
                ok=True,
                target="desktop",
                action="monitors",
                message=msg,
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
            if self._sleep_abortable(sec):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="wait",
                    message="Aborted by user",
                    extra={"seconds": sec, "aborted": True},
                )
            return public_result(
                ok=True,
                target="desktop",
                action="wait",
                message=f"Waited {sec:.2f}s",
                extra={"seconds": sec},
            )
        if act is ComputerAction.APP:
            app = str(kwargs.get("app") or kwargs.get("name") or "")
            folder = str(kwargs.get("path") or kwargs.get("folder") or "").strip()
            if _is_host_browser(app):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="app",
                    message=_HOST_BROWSER_REFUSAL,
                    extra={"refused": "host_browser", "app": app},
                )
            if folder:
                from remedy.core.open_folder import open_folder_os

                info = open_folder_os(folder)
                with contextlib.suppress(Exception):
                    self.bridge.set_last_elements([], target="desktop")
                time.sleep(0.4)
                return public_result(
                    ok=True,
                    target="desktop",
                    action="app",
                    message=f"Opened folder: {info.get('target') or folder}",
                    extra=info,
                )
            search_dirs = list(kwargs.get("search_dirs") or [])
            info = win.open_app(app, search_dirs=search_dirs or None)
            # New window — drop stale UIA refs from the previous app
            with contextlib.suppress(Exception):
                self.bridge.set_last_elements([], target="desktop")
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
            _find_hwnd_raw = kwargs.get("hwnd")
            elements = win.desktop_snapshot(
                limit=int(kwargs.get("limit") or 60),
                mode=str(kwargs.get("mode") or "auto"),
                hwnd=int(_find_hwnd_raw) if _find_hwnd_raw not in (None, "", 0, "0") else None,
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
                el = self._desktop_click_element(
                    win,
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
                        **self._desktop_evidence(),
                    },
                )
            if ref:
                if self.bridge.snapshot_is_stale() or self.bridge.get_element_by_ref(ref) is None:
                    # Re-observe — never click coordinates from a stale snapshot.
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
                el = self._desktop_click_element(
                    win,
                    el,
                    button=str(kwargs.get("button") or "left"),
                    clicks=int(kwargs.get("clicks") or 1),
                )
                return public_result(
                    ok=True,
                    target="desktop",
                    action="click",
                    message=f"Clicked ref={ref} ({el.get('name', '')[:40]})",
                    extra={
                        "ref": ref,
                        "x": el.get("x"),
                        "y": el.get("y"),
                        "hwnd": el.get("hwnd"),
                        **self._desktop_evidence(),
                    },
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
            # Report the pre-expansion length so a vault secret's true length
            # never leaks to the model via char counts.
            reported_len = len(text)
            had_vault = "{{" in text
            # Desktop destination is unverifiable → domain-bound vault items
            # refuse here by design (owner can store an unbound item if wanted).
            text, vault_err = self._expand_vault_text(
                text, destination_url="", action="type", target="desktop"
            )
            if vault_err is not None:
                return vault_err
            # ref= → UIA ValuePattern.SetValue: sets the WHOLE value atomically
            # into that specific control — no focus races, verified read-back.
            set_ref = str(kwargs.get("ref") or "").strip()
            if had_vault and not set_ref:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="type",
                    message=(
                        "Vault secrets only type into a named field. Pass ref= "
                        "from computer_snapshot so the value lands in that control, "
                        "not whatever currently has focus."
                    ),
                    extra={"length": reported_len, "needs": "ref"},
                )
            if set_ref:
                el = self.bridge.get_element_by_ref(set_ref)
                if el is not None and el.get("hwnd") and el.get("uia"):
                    from remedy.core.computer.desktop_uia import element_action

                    res = element_action(
                        int(el["hwnd"]),
                        str(el.get("name") or ""),
                        role=str(el.get("role") or ""),
                        action="set_value",
                        text=text,
                    )
                    if res.get("ok"):
                        return public_result(
                            ok=True,
                            target="desktop",
                            action="type",
                            message=str(res.get("message") or "Set value"),
                            extra={
                                "ref": set_ref,
                                "length": reported_len,
                                "method": "uia_set_value",
                                "verified": bool(res.get("verified")),
                                **self._desktop_evidence(),
                            },
                        )
                    # Not settable → click it to focus, then fall through to keys.
                    with contextlib.suppress(Exception):
                        win.click_element(el)
                        time.sleep(0.1)
            typed_box: list[int] = [0]
            type_method = "keystrokes"
            try:
                # Secrets always go per-char (never through the clipboard).
                if had_vault:
                    win.type_text(
                        text,
                        abort_check=self._abort_check,
                        chars_typed=typed_box,
                    )
                else:
                    tf = win.type_text_fast(
                        text,
                        abort_check=self._abort_check,
                        chars_typed=typed_box,
                    )
                    type_method = str(tf.get("method") or "keystrokes")
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
            length = "a stored secret" if had_vault else f"{reported_len} chars"
            verb = "Pasted" if type_method == "paste" else "Typed"
            return public_result(
                ok=True,
                target="desktop",
                action="type",
                message=f"{verb} {length}",
                extra={
                    "length": reported_len if not had_vault else None,
                    "method": type_method,
                    **self._desktop_evidence(),
                },
            )
        if act is ComputerAction.KEY:
            key = str(kwargs.get("key") or "")
            win.press_key(key)
            return public_result(
                ok=True,
                target="desktop",
                action="key",
                message=f"Pressed {key}",
                extra={"key": key, **self._desktop_evidence()},
            )
        if act is ComputerAction.SCROLL:
            x, y = int(kwargs.get("x", 0)), int(kwargs.get("y", 0))
            if x == 0 and y == 0:
                # No point given → scroll the FOREGROUND window's center, not the
                # top-left corner of the screen (which scrolled nothing useful).
                with contextlib.suppress(Exception):
                    fg = win.foreground_window_info()
                    for w in win.list_windows(limit=40):
                        if int(w.get("hwnd") or 0) == int(fg.get("hwnd") or 0):
                            b = w.get("bounds") or {}
                            x = (int(b.get("left") or 0) + int(b.get("right") or 0)) // 2
                            y = (int(b.get("top") or 0) + int(b.get("bottom") or 0)) // 2
                            break
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
            if mode in ("minimize", "maximize", "restore", "close", "move", "resize"):
                hwnd = int(kwargs.get("hwnd") or 0)
                title = str(kwargs.get("title") or kwargs.get("hint") or "").strip()
                if not hwnd and title:
                    needle = title.lower()
                    with contextlib.suppress(Exception):
                        for w in win.list_windows(limit=80):
                            if needle in str(w.get("title") or "").lower():
                                hwnd = int(w.get("hwnd") or 0)
                                title = str(w.get("title") or "")
                                break
                if not hwnd:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="windows",
                        message=f"hwnd or matching title required for {mode}",
                    )
                real = title
                with contextlib.suppress(Exception):
                    for w in win.list_windows(limit=80):
                        if int(w.get("hwnd") or 0) == hwnd:
                            real = str(w.get("title") or "")
                            break
                if real and _is_host_browser(real):
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="windows",
                        message=_HOST_BROWSER_REFUSAL,
                        extra={"refused": "host_browser", "title": real},
                    )
                res = win.manage_window(
                    hwnd,
                    mode,
                    x=kwargs.get("x"),
                    y=kwargs.get("y"),
                    width=kwargs.get("width"),
                    height=kwargs.get("height"),
                )
                return public_result(
                    ok=bool(res.get("ok")),
                    target="desktop",
                    action="windows",
                    message=str(res.get("message") or mode),
                    extra={"hwnd": hwnd, "mode": mode, "title": real[:80]},
                )
            if mode in ("focus", "activate"):
                hwnd = int(kwargs.get("hwnd") or 0)
                title = str(kwargs.get("title") or kwargs.get("hint") or "").strip()
                if title and _is_host_browser(title):
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="windows",
                        message=_HOST_BROWSER_REFUSAL,
                        extra={"refused": "host_browser", "title": title},
                    )
                if not hwnd and title:
                    needle = title.lower()
                    match: dict[str, Any] | None = None
                    with contextlib.suppress(Exception):
                        for w in win.list_windows(limit=80):
                            real = str(w.get("title") or "")
                            if needle in real.lower():
                                match = w
                                break
                    if not match:
                        return public_result(
                            ok=False,
                            target="desktop",
                            action="windows",
                            message=f"No window matching title={title!r}",
                        )
                    real = str(match.get("title") or "")
                    if _is_host_browser(real):
                        return public_result(
                            ok=False,
                            target="desktop",
                            action="windows",
                            message=_HOST_BROWSER_REFUSAL,
                            extra={"refused": "host_browser", "title": real},
                        )
                    win.focus_window(int(match["hwnd"]))
                    return public_result(
                        ok=True,
                        target="desktop",
                        action="windows",
                        message=f"Focused window: {real[:80]}",
                        extra={"hwnd": int(match["hwnd"]), "title": real},
                    )
                if not hwnd:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="windows",
                        message="hwnd or title required for focus",
                    )
                # hwnd path: refuse if the resolved window is a host browser.
                with contextlib.suppress(Exception):
                    for w in win.list_windows(limit=80):
                        if int(w.get("hwnd") or 0) == hwnd:
                            real = str(w.get("title") or "")
                            if _is_host_browser(real):
                                return public_result(
                                    ok=False,
                                    target="desktop",
                                    action="windows",
                                    message=_HOST_BROWSER_REFUSAL,
                                    extra={"refused": "host_browser", "title": real},
                                )
                            break
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
        if act is ComputerAction.PAGE_TEXT:
            # Native page_text: read the actual CONTENT of a window via UIA
            # (edit/document values + labels) — Remedy can read what she typed.
            from remedy.core.computer.desktop_uia import read_window_text

            hwnd = int(kwargs.get("hwnd") or 0)
            if not hwnd:
                fg = win.foreground_window_info()
                hwnd = int(fg.get("hwnd") or 0)
            if not hwnd:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="page_text",
                    message="No window to read — pass hwnd= or focus one first",
                )
            # Its own name: ``read_window_text`` may return None, while the
            # ``info`` this function reuses elsewhere never does. Sharing the
            # name was the one thing standing between this package and a clean
            # mypy run, and a clean run is what makes the next error visible.
            window_text = read_window_text(hwnd)
            if not window_text:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="page_text",
                    message=(
                        f"UIA could not read hwnd={hwnd} — "
                        "computer_screenshot for a pixel view instead"
                    ),
                )
            n_fields = len(window_text.get("fields") or [])
            return public_result(
                ok=True,
                target="desktop",
                action="page_text",
                message=(
                    f"Read {window_text.get('title') or 'window'!r}: "
                    f"{len(window_text.get('text') or '')} chars, {n_fields} field(s)"
                ),
                extra=window_text,
            )
        if act is ComputerAction.PRESS_HOLD:
            if self._abort_check():
                raise RuntimeError("Aborted by user")
            x, y = int(kwargs.get("x") or 0), int(kwargs.get("y") or 0)
            ref = str(kwargs.get("ref") or "").strip()
            if ref and not (x or y):
                el = self.bridge.get_element_by_ref(ref)
                if el is not None:
                    x, y = int(el.get("x") or 0), int(el.get("y") or 0)
            if not (x or y):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="press_hold",
                    message="press_hold needs x/y or a ref from computer_snapshot",
                )
            hold_ms = int(kwargs.get("hold_ms") or 2600)
            info = win.press_hold(
                x, y, hold_ms=hold_ms, abort_check=self._abort_check
            )
            return public_result(
                ok=True,
                target="desktop",
                action="press_hold",
                message=f"Held ({x},{y}) for {info['held_ms']}ms",
                extra={**info, **self._desktop_evidence()},
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
        # Vault tokens in typed text expand machine-side, bound to the rail's
        # current site — the model and job log only ever saw the handle.
        if act is ComputerAction.TYPE and payload.get("text"):
            # Binding domain is the LIVE page (probed inside), not last navigate.
            expanded, vault_err = self._expand_vault_text(
                str(payload.get("text") or ""),
                action="type",
                target="browser",
            )
            if vault_err is not None:
                return vault_err
            payload["text"] = expanded
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
            ComputerAction.PRESS_HOLD,
            ComputerAction.DRAG,
            ComputerAction.SELECT,
            ComputerAction.FILL,
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
            ComputerAction.SELECT,
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
                ComputerAction.DRAG,
                ComputerAction.PRESS_HOLD,
                ComputerAction.ACT,
                ComputerAction.SELECT,
                ComputerAction.FILL,
            ):
                # Optimistic enqueue — host poller may still be alive on disk.
                # PRESS_HOLD/DRAG belong here too: they enqueue exactly like
                # CLICK, so a stale host_connected flag must not hard-fail the
                # owner's accessibility hold when a click would have gone through.
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

        # Screenshot for coordinate-guided action. Prefer the KNOWN rail bounds
        # crop (always the rail, and it carries devicePixelRatio) over
        # PrintWindow (which may capture the wrong Remedy webview and carries no
        # scale). The coordinate contract must be DPI-honest: the capture is in
        # PHYSICAL pixels but computer_click / computer_press_hold x,y are CSS
        # viewport pixels, so on a scaled display (dpr!=1) the model must divide
        # what it reads by the scale. Saying "pixels ARE click coords" is only
        # true at dpr=1 — anything else was off by the DPR factor.
        if act is ComputerAction.SCREENSHOT:
            def _coord_msg(width: int, height: int, scale: float) -> str:
                base = f"Browser rail capture ({width}x{height}). "
                if abs(scale - 1.0) < 0.01:
                    return base + (
                        "Pixel coordinates in this image ARE the page's click "
                        "coordinates — for anything you can see but that has no "
                        "snapshot element (a challenge iframe, a canvas, a custom "
                        "control), use computer_click x=… y=… or "
                        "computer_press_hold x=… y=… with the x,y you read here."
                    )
                return base + (
                    f"This image is {scale:g}x the page (HiDPI). For anything "
                    "you can see but that has no snapshot element, DIVIDE the "
                    f"x,y you read here by {scale:g}, then use computer_click "
                    "x=… y=… or computer_press_hold x=… y=… — those take page "
                    "(CSS) coordinates, not image pixels."
                )

            bounds = self.bridge.get_browser_bounds()
            scale = float((bounds or {}).get("scale") or 1.0)
            if bounds and bounds.get("width", 0) > 40 and bounds.get("height", 0) > 40:
                try:
                    from remedy.core.computer import desktop_win as win

                    info = win.screenshot_region_png(
                        int(bounds["x"]),
                        int(bounds["y"]),
                        int(bounds["width"]),
                        int(bounds["height"]),
                        scale=scale,
                    )
                    return public_result(
                        ok=True,
                        target="browser",
                        action="screenshot",
                        message=_coord_msg(info["width"], info["height"], scale),
                        extra={
                            **info,
                            "bounds": bounds,
                            "method": "region_crop",
                            "coord_scale": scale,
                            "coord_space": "css_over_scale" if scale != 1.0 else "page_viewport",
                        },
                    )
                except Exception:
                    pass  # fall through to PrintWindow / host job
            # Fallback: PrintWindow the rail webview. No reliable scale here, so
            # tell the model the coords may need the page's devicePixelRatio.
            try:
                from remedy.core.computer import desktop_win as win

                wv = win.find_webview_host_hwnd()
                if wv:
                    info = win.print_window_png(wv)
                    return public_result(
                        ok=True,
                        target="browser",
                        action="screenshot",
                        message=_coord_msg(info["width"], info["height"], scale),
                        extra={
                            **info,
                            "method": "PrintWindow",
                            "coord_scale": scale,
                            "coord_space": "css_over_scale" if scale != 1.0 else "page_viewport",
                        },
                    )
            except Exception:
                pass

        if act is ComputerAction.FILL:
            return self._computer_fill(kwargs)

        if act is ComputerAction.SELECT:
            choice = str(
                kwargs.get("value")
                or kwargs.get("option")
                or kwargs.get("text")
                or ""
            ).strip()
            payload["action"] = "select"
            payload["value"] = choice
            payload["text"] = choice
            if kwargs.get("ref"):
                payload["ref"] = str(kwargs.get("ref") or "")
            hint = str(kwargs.get("hint") or "").strip()
            if hint:
                payload["hint"] = hint

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
                if self.bridge.snapshot_is_stale():
                    snap = self._browser_snapshot_now(kwargs)
                    if not snap.get("ok"):
                        return public_result(
                            ok=False,
                            target="browser",
                            action="click",
                            message=(
                                "snapshot stale — take a fresh computer_snapshot "
                                "before clicking this ref"
                            ),
                        )
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
            if self._sleep_abortable(sec):
                return public_result(
                    ok=False,
                    target="browser",
                    action="wait",
                    message="Aborted by user",
                    extra={"seconds": sec, "aborted": True},
                )
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
                    f"BROWSER RAIL UNREACHABLE ({reason}) — the elements below "
                    "are DESKTOP WINDOWS, NOT the web page. Do NOT drive the "
                    "owner's own browser (Firefox/Chrome/Edge windows, Ctrl+T, "
                    "typing URLs) — web tasks live in the in-app rail only. "
                    "computer_wait 2 then retry computer_snapshot "
                    "target=browser; if it stays unreachable, tell the owner "
                    "instead of improvising on the desktop."
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
            # Host runs up to 2×9s rail evals + ready poll — heavy retail
            # pages (Walmart) stall evals past the old 5s and Remedy lost
            # her eyes mid-shop. Cover the host's worst case.
            total_wait = float(
                kwargs.get("timeout_s")
                or (22.0 if host_looks_live else 5.0)
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

        # An outstanding ui_command means "the host is about to run this" only
        # if a host exists. With Desktop closed nobody ever takes the command,
        # so wait()'s unclaimed fail-fast reads the stale file as progress and
        # burns the whole timeout. host_connected() is the honest signal.
        host_looks_live = self.bridge.host_connected(max_age_s=12.0)
        unclaimed = 3.0 if host_looks_live else 1.5
        # Covers 2×9s rail evals on slow retail pages; no host, no such wait.
        total_wait = float(
            kwargs.get("timeout_s") or (20.0 if host_looks_live else 4.0)
        )
        job = self._enqueue(act.value, payload)
        finished = self.bridge.wait(
            job.id,
            timeout_s=total_wait,
            poll_s=0.08,
            abort_check=self._abort_check,
            unclaimed_timeout_s=unclaimed,
        )
        done_out: dict[str, Any] | None = None
        if finished.status == "done" and finished.result:
            done_out = dict(finished.result)
            done_out.setdefault("ok", True)
            done_out.setdefault("target", "browser")
            done_out.setdefault("action", act.value)
            if act is ComputerAction.SNAPSHOT and done_out.get("elements"):
                self.bridge.set_last_elements(
                    list(done_out.get("elements") or []),
                    target="browser",
                )
            if done_out.get("ok"):
                return done_out

        # Stale-ref auto-recovery: a ref is scoped to the snapshot that made
        # it; if the page changed the DOM element is gone and the host returns
        # "missing-ref" (as a failed result OR a job error). Rather than fail,
        # re-locate the SAME control by its remembered label + card context
        # (which re-scans the live DOM) — the adapt-and-overcome move, for her.
        fail_msg = str(
            (done_out or {}).get("message")
            or (done_out or {}).get("detail")
            or finished.error
            or ""
        )
        if act is ComputerAction.CLICK and "missing-ref" in fail_msg:
            ref = str(kwargs.get("ref") or payload.get("ref") or "").strip()
            remembered = self.bridge.get_element_by_ref(ref) if ref else None
            name = str((remembered or {}).get("name") or "").strip()
            if name:
                ctx = str((remembered or {}).get("context") or "")
                ctx_bits = " ".join(w for w in re.findall(r"[A-Za-z0-9]{3,}", ctx)[:4])
                query = (name + " " + ctx_bits).strip()
                recovered = self._browser_click_text(query, kwargs)
                if recovered.get("ok"):
                    recovered["note"] = (
                        f"ref {ref} was stale (page changed) — re-located by "
                        f"label {name!r} and clicked"
                    )
                    recovered["recovered_from"] = "stale_ref"
                    return recovered

        if done_out is not None:
            return done_out
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
                timeout_s=18.0,
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
            elif finished.status == "cancelled" or self._abort_check():
                return public_result(
                    ok=False,
                    target="browser",
                    action="click",
                    message="Aborted by user",
                    extra={"aborted": True, "text": text_q},
                )
            else:
                last_err = finished.error or finished.status or "timeout"
            # Retry: scroll down then try again (wait/cancel scroll; abortable)
            if attempt == 0:
                if self._abort_check():
                    return public_result(
                        ok=False,
                        target="browser",
                        action="click",
                        message="Aborted by user",
                        extra={"aborted": True, "text": text_q},
                    )
                scroll_job = self._enqueue(
                    "scroll",
                    {"ui": {"open_browser": True}, "x": 200, "y": 300, "dy": -4},
                )
                self.bridge.wait(
                    scroll_job.id,
                    timeout_s=0.8,
                    poll_s=0.05,
                    abort_check=self._abort_check,
                    unclaimed_timeout_s=0.4,
                    grace_s=0.1,
                )
                if self._abort_check() or self._sleep_abortable(0.25):
                    return public_result(
                        ok=False,
                        target="browser",
                        action="click",
                        message="Aborted by user",
                        extra={"aborted": True, "text": text_q},
                    )
        # Stop before snapshot/ref/desktop fallbacks after user abort
        if self._abort_check():
            return public_result(
                ok=False,
                target="browser",
                action="click",
                message="Aborted by user",
                extra={"aborted": True, "text": text_q},
            )
        # Fallback: snapshot + match + click ref
        snap = self._browser_snapshot_now(kwargs)
        if self._abort_check():
            return public_result(
                ok=False,
                target="browser",
                action="click",
                message="Aborted by user",
                extra={"aborted": True, "text": text_q},
            )
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
                if fin2.status == "cancelled" or self._abort_check():
                    return public_result(
                        ok=False,
                        target="browser",
                        action="click",
                        message="Aborted by user",
                        extra={"aborted": True, "text": text_q},
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
        if self._abort_check():
            return public_result(
                ok=False,
                target="browser",
                action="click",
                message="Aborted by user",
                extra={"aborted": True, "text": text_q},
            )
        # A missed rail click must NOT fall back to driving the desktop — web
        # work lives in the rail (the old desktop-click fallback is how Remedy
        # walked off the rail onto a look-alike control).
        #
        # A press-and-hold / "I'm not a robot" wall is NOT a dead end: Remedy is
        # the owner's authorized hands (many owners are disabled and cannot do
        # the gesture themselves), so she performs the mechanical action rather
        # than punting it. Point her at the hold capability, still on the rail.
        hold_wall = any(
            w in text_q.lower()
            for w in ("press & hold", "press and hold", "hold to confirm", "hold to verify",
                      "activate and hold", "press hold", "i'm not a robot", "captcha")
        )
        if hold_wall:
            return public_result(
                ok=False,
                target="browser",
                action="click",
                message=(
                    "That is a human-check wall (press-and-hold / CAPTCHA). Pause "
                    "and hand it to the owner: describe what you see and wait. "
                    "Do not complete the challenge yourself."
                ),
                extra={"text": text_q, "needs": "owner_handoff"},
            )
        return public_result(
            ok=False,
            target="browser",
            action="click",
            message=(
                f"Could not click text={text_q!r} in the Browser rail ({last_err}). "
                "Re-read the rail with computer_snapshot / computer_page_text and try "
                "again by ref, or add the item's card text to disambiguate "
                "(e.g. text='Add to cart <product name>'). Stay on the rail — do "
                "NOT switch to the desktop for a web page."
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
            typed_reported = "a stored secret" if "{{" in type_text else f"{len(type_text)} chars"
            # _run_desktop TYPE expands vault tokens itself (unbound items only)
            ty = self._run_desktop(ComputerAction.TYPE, text=type_text, hint=hint)
            log.append(f"type:{ty.get('ok')} {typed_reported}")
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

    def _computer_fill(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Fill several form fields in one call (label or ref + value/select)."""
        raw = kwargs.get("fields")
        if raw is None:
            raw = kwargs.get("text") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return public_result(
                    ok=False,
                    target="browser",
                    action="fill",
                    message='fields= must be a JSON list of {ref or text, value or select}',
                )
        if not isinstance(raw, list) or not raw:
            return public_result(
                ok=False,
                target="browser",
                action="fill",
                message="computer_fill needs fields=[{ref or text, value or select}, …]",
            )
        results: list[dict[str, Any]] = []
        for i, row in enumerate(raw):
            if not isinstance(row, dict):
                return public_result(
                    ok=False,
                    target="browser",
                    action="fill",
                    message=f"field {i} is not an object",
                    extra={"filled": results},
                )
            ref = str(row.get("ref") or "").strip()
            label = str(row.get("text") or row.get("label") or "").strip()
            option = str(row.get("select") or row.get("option") or "").strip()
            value = str(row.get("value") or row.get("type") or "").strip()
            if option:
                r = self._run_browser(
                    ComputerAction.SELECT,
                    ref=ref or None,
                    value=option,
                    text=option,
                    hint=label or None,
                )
            elif value:
                if label and not ref:
                    # A missed label must not fall through to typing into
                    # whatever happens to have focus.
                    clicked = self._run_browser(ComputerAction.CLICK, text=label)
                    if not clicked.get("ok"):
                        fail = dict(clicked)
                        fail["message"] = (
                            f"field {i}: could not find a field labelled "
                            f"{label!r} ({clicked.get('message') or 'no match'})"
                        )
                        fail["filled"] = results
                        return fail
                r = self._run_browser(
                    ComputerAction.TYPE,
                    text=value,
                    ref=ref or None,
                )
            else:
                return public_result(
                    ok=False,
                    target="browser",
                    action="fill",
                    message=f"field {i} needs value= or select=",
                    extra={"filled": results},
                )
            results.append(
                {
                    "i": i,
                    "ok": bool(r.get("ok")),
                    "message": r.get("message"),
                    "ref": ref or None,
                }
            )
            if not r.get("ok"):
                fail = dict(r)
                fail["filled"] = results
                return fail
        return public_result(
            ok=True,
            target="browser",
            action="fill",
            message=(
                f"Filled {len(results)} field(s). Values were not read back — "
                "computer_page_text or snapshot before Submit."
            ),
            extra={"fields": results, "verified": False},
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
        expect = self._parse_expect(payload)
        mutating = bool(click or type_text or key)
        # Pre-state only when acting on the *current* page (page_changed signal).
        pre_state: dict[str, Any] | None = None
        if mutating and not url and (click or key):
            pre_state = self._page_probe(max_wait_s=1.5)

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
            typed_reported = "a stored secret" if "{{" in type_text else f"{len(type_text)} chars"
            # Bind to the live page (post-click/redirect), probed inside — a
            # click earlier in this same act may have changed the origin.
            type_text, vault_err = self._expand_vault_text(
                type_text,
                action="act",
                target="browser",
            )
            if vault_err is not None:
                vault_err["steps"] = log
                return vault_err
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
            ok_t = fin.status == "done" and (fin.result or {}).get("ok", True) is not False
            log.append(f"type:{ok_t} {typed_reported}")
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
            key_ok = fin.status == "done" and (fin.result or {}).get("ok", True) is not False
            log.append(f"key:{key_ok} {key}")
            if not key_ok:
                return public_result(
                    ok=False,
                    target="browser",
                    action="act",
                    message=f"act: key {key!r} failed — {fin.error or fin.status}",
                    extra={"steps": log},
                )

        # Observe the outcome — success is observed, not asserted
        # (docs/LIFE_TASK_PARTNER.md §2.3/§2.5).
        verify_extra, verify_fail = self._verify_act_outcome(
            pre=pre_state, expect=expect, acted=mutating
        )
        if verify_fail:
            return public_result(
                ok=False,
                target="browser",
                action="act",
                message=verify_fail,
                extra={
                    "steps": log,
                    "url": url or None,
                    "click": click or None,
                    **verify_extra,
                },
            )
        observed = verify_extra.get("observed")
        unverified = bool(verify_extra.get("unverified"))
        prefix = "UNVERIFIED: " if unverified else "SUCCESS: "
        msg = prefix + " | ".join(log)
        if isinstance(observed, dict) and (observed.get("url") or observed.get("title")):
            msg += (
                f" | observed: {str(observed.get('title') or '')[:60]!r}"
                f" @ {str(observed.get('url') or '')[:100]}"
            )
        if unverified:
            msg += (
                " | note: outcome not verified (page probe unavailable) — "
                "do not claim the goal is complete. Run computer_page_text or "
                "computer_snapshot first."
            )
        return public_result(
            ok=not unverified,
            target="browser",
            action="act",
            message=msg,
            extra={
                "steps": log,
                "url": url or None,
                "click": click or None,
                **verify_extra,
            },
        )

    def _expand_vault_text(
        self,
        text: str,
        *,
        destination_url: str = "",
        action: str = "type",
        target: str = "browser",
    ) -> tuple[str, dict[str, Any] | None]:
        """Machine-side ``{{vault:handle}}`` → secret substitution.

        The plaintext exists only on the wire from vault to input path — never
        in model context or tool results. Domain-bound items refuse to decrypt
        for the wrong destination (docs/LIFE_TASK_PARTNER.md §2.3: secrets only
        type into verified fields). Returns ``(expanded, error_result|None)``;
        error results are plain-language and secret-free.

        Security: for the browser rail the binding domain is the **live** page
        URL (fresh ``_page_probe``), never the last *explicit navigate* — a
        click/redirect/SSO hop that changed the page must not let a bound
        secret type into an unexpected origin. Fail closed: if the probe
        cannot confirm the current URL, refuse.
        """
        if not text or "{{" not in text:
            return text, None
        try:
            from remedy.core import vault

            if not vault.contains_vault_token(text):
                return text, None

            bind_url = destination_url
            if target == "browser":
                probe = self._page_probe(max_wait_s=2.0)
                if probe.get("ok") and probe.get("url"):
                    bind_url = str(probe.get("url"))
                else:
                    # Only enforce fail-closed when a domain-bound item is at
                    # stake; unbound items are fine to fill anywhere.
                    handles = vault.token_handles(text)
                    items = {i["handle"]: i for i in vault.vault_list(self.home_dir)}
                    if any(items.get(h, {}).get("domains") for h in handles):
                        return text, public_result(
                            ok=False,
                            target=target,
                            action=action,
                            message=(
                                "Vault refused: I couldn't confirm which page "
                                "is open, and this secret is locked to specific "
                                "sites. Open the site in the rail and retry. "
                                "Nothing was typed."
                            ),
                            extra={"vault_refused": True},
                        )
            expanded, _handles = vault.expand_text(
                text, destination_url=bind_url, home=self.home_dir
            )
            return expanded, None
        except Exception as exc:
            # VaultError/VaultDomainError messages are safe (no secret material).
            return text, public_result(
                ok=False,
                target=target,
                action=action,
                message=(
                    f"Vault refused to fill this secret: {exc} "
                    "Nothing was typed."
                ),
                extra={"vault_refused": True},
            )

    def _page_probe(self, *, max_wait_s: float = 2.5) -> dict[str, Any]:
        """Light best-effort page observation for post-action verification.

        Returns ``{"ok", "url", "title", "text_hash", "text_head"}``. Never
        raises; ``ok=False`` when the host is offline/slow — verification is
        then reported as *unverified*, not as failure (life-task doctrine:
        observe outcomes when we can, never fabricate them).
        """
        out = {"ok": False, "url": "", "title": "", "text_hash": "", "text_head": ""}
        try:
            job = self._enqueue(
                "page_text",
                {"ui": {"open_browser": True}, "browser_action": "page_text"},
            )
            fin = self.bridge.wait(
                job.id,
                timeout_s=max(0.5, float(max_wait_s)),
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=min(1.2, float(max_wait_s)),
                grace_s=0.1,
            )
            if fin.status == "done" and fin.result:
                res = dict(fin.result)
                text = str(res.get("text") or "")
                out["ok"] = True
                out["url"] = str(res.get("url") or "")
                out["title"] = str(res.get("title") or "")
                out["text_head"] = text[:280]
                if text:
                    out["text_hash"] = hashlib.sha256(
                        text.encode("utf-8", "replace")
                    ).hexdigest()[:16]
        except Exception:
            pass
        return out

    @staticmethod
    def _parse_expect(payload: dict[str, Any]) -> dict[str, str]:
        """Normalize expect_url= / expect_text= / expect={...} into one dict."""
        expect: dict[str, str] = {}
        raw = payload.get("expect")
        if isinstance(raw, dict):
            for src, dst in (
                ("url_contains", "url"),
                ("text_contains", "text"),
                ("url", "url"),
                ("text", "text"),
            ):
                v = str(raw.get(src) or "").strip()
                if v and dst not in expect:
                    expect[dst] = v
        for key, dst in (("expect_url", "url"), ("expect_text", "text")):
            v = str(payload.get(key) or "").strip()
            if v and dst not in expect:
                expect[dst] = v
        return expect

    def _verify_act_outcome(
        self,
        *,
        pre: dict[str, Any] | None,
        expect: dict[str, str],
        acted: bool,
    ) -> tuple[dict[str, Any], str | None]:
        """Post-action observation → (extra fields, failure message | None)."""
        extra: dict[str, Any] = {}
        if not acted:
            return extra, None
        post = self._page_probe()
        if not post.get("ok"):
            extra["verified"] = False
            extra["unverified"] = True
            return extra, None
        observed = {"url": post.get("url") or "", "title": post.get("title") or ""}
        extra["observed"] = observed
        if pre and pre.get("ok"):
            extra["page_changed"] = bool(
                (pre.get("url") or "") != (post.get("url") or "")
                or (pre.get("text_hash") or "") != (post.get("text_hash") or "")
            )
        if expect:
            hay_url = str(post.get("url") or "").lower()
            hay_text = (
                str(post.get("title") or "") + "\n" + str(post.get("text_head") or "")
            ).lower()
            want_url = str(expect.get("url") or "").lower()
            want_text = str(expect.get("text") or "").lower()
            if want_url and not _expect_url_matches(want_url, hay_url):
                extra["verified"] = False
                return extra, (
                    f"act ran but verification failed: expected URL containing "
                    f"{expect.get('url')!r}, observed {observed.get('url')!r} "
                    f"({observed.get('title')!r}). Re-observe with "
                    "computer_snapshot before retrying."
                )
            if want_text and want_text not in hay_text:
                extra["verified"] = False
                return extra, (
                    f"act ran but verification failed: expected page text containing "
                    f"{expect.get('text')!r}; observed title {observed.get('title')!r}. "
                    "Re-observe with computer_snapshot or computer_page_text "
                    "before retrying."
                )
            extra["verified"] = True
        return extra, None

    def _browser_page_text(self) -> dict[str, Any]:
        self.bridge.settle_after_navigate(min_s=0.6, max_s=1.5)
        # Prefer dedicated action so Rust host can claim (only=…page_text).
        # Legacy SPA path still handles payload.browser_action on click jobs.
        last_err = "page_text failed — open a page in the rail first"
        last_job_id = ""
        # Both attempts still run with Desktop closed; they just do not each
        # sit out a 14s rail-eval budget no host is spending. See _run_browser.
        host_looks_live = self.bridge.host_connected(max_age_s=12.0)
        for attempt, action in enumerate(("page_text", "page_text")):
            job = self._enqueue(
                action,
                {"ui": {"open_browser": True}, "browser_action": "page_text"},
            )
            last_job_id = job.id
            finished = self.bridge.wait(
                job.id,
                timeout_s=14.0 if host_looks_live else 3.5,
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=5.0 if host_looks_live else 1.5,
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
                capped = min(max(settle, 0.0), 1.5)
                if self._sleep_abortable(capped):
                    out["ok"] = False
                    out["aborted"] = True
                    out["message"] = "Aborted by user"
                    out["settled_s"] = 0.0
                    return out
                out["settled_s"] = capped
            if optimistic:
                out.setdefault("ready_for_input", False)
                out.setdefault("pending_load", True)
            else:
                out.setdefault("ready_for_input", True)
                self.bridge.clear_navigate_optimistic()
            return out

        if finished.status == "cancelled" or self._abort_check():
            return public_result(
                ok=False,
                target="browser",
                action="navigate",
                message="Aborted by user",
                extra={"aborted": True, "job_id": job.id},
            )
        if finished.status == "done" and finished.result:
            return _nav_ok(dict(finished.result), optimistic=False)

        # Brief re-read (host may complete mid-return) — abortable
        for _ in range(8):
            if self._abort_check():
                return public_result(
                    ok=False,
                    target="browser",
                    action="navigate",
                    message="Aborted by user",
                    extra={"aborted": True, "job_id": job.id},
                )
            again = self.bridge._read(job.id)
            if again and again.status == "done" and again.result:
                return _nav_ok(dict(again.result), optimistic=False)
            if self._sleep_abortable(0.025):
                return public_result(
                    ok=False,
                    target="browser",
                    action="navigate",
                    message="Aborted by user",
                    extra={"aborted": True, "job_id": job.id},
                )

        if self._abort_check():
            return public_result(
                ok=False,
                target="browser",
                action="navigate",
                message="Aborted by user",
                extra={"aborted": True, "job_id": job.id},
            )

        twin = self.bridge.find_recent_success(
            action="navigate",
            url=url,
            max_age_s=15.0,
            session_id=str(job.session_id or ""),
            job_id=job.id,
        )
        if twin and twin.result:
            out = dict(twin.result)
            out["reconciled"] = True
            out["job_id"] = job.id
            return _nav_ok(out, optimistic=False)

        # Desktop is alive → fire-and-forget SUCCESS so the model never claims
        # the rail failed while the page is opening (open-url must be instant).
        # Mark optimistic so type/click wait for settle before acting.
        # Never optimistic-complete after Stop — would still open the URL.
        if self._abort_check():
            return public_result(
                ok=False,
                target="browser",
                action="navigate",
                message="Aborted by user",
                extra={"aborted": True, "job_id": job.id},
            )
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
