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

# Host JS strips these from query= (they are drive targets, not field labels).
# Python must agree so vault cannot treat query="browser" as a named field.
_TYPE_ROUTING_TOKENS = frozenset(
    {
        "browser",
        "desktop",
        "auto",
        "system",
        "grove",
        "alongside",
        "studio",
        "chrome",
        "rail",
        "web",
        "os",
    }
)


def field_locator(*candidates: Any) -> str:
    """Visible field label, or empty when the value is a routing token."""
    for raw in candidates:
        q = str(raw or "").strip()
        if q and q.lower() not in _TYPE_ROUTING_TOKENS:
            return q
    return ""

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


_EDIT_ITYPES = frozenset({"password", "text", "email", "search", "tel", "url", "number"})
_DENY_ITYPES = frozenset({"submit", "button", "checkbox", "hidden", "radio", "reset", "image"})
_EDIT_ROLES = frozenset(
    {
        "textbox",
        "textarea",
        "edit",
        "searchbox",
        "combobox",
        "spinbutton",
        "entry",
        "password text",
        "password",
        "editbar",
        "spin button",
        "editable text",
    }
)
_DENY_ROLES = frozenset(
    {
        "submit",
        "button",
        "push button",
        "checkbox",
        "check box",
        "hidden",
        "radio",
        "radio button",
        "toggle button",
        "link",
        "hyperlink",
    }
)


def _element_is_editable(el: dict[str, Any] | None) -> bool:
    """True only for real text/password fields — not OCR labels or submit."""
    if not el:
        return False
    role = str(el.get("role") or "").strip().lower()
    tag = str(el.get("tag") or el.get("control_type") or "").strip().lower()
    itype = str(el.get("type") or el.get("input_type") or "").strip().lower()
    if itype in _DENY_ITYPES or role in _DENY_ROLES or tag in _DENY_ROLES:
        return False
    if itype in _EDIT_ITYPES:
        return True
    if role in _EDIT_ROLES or tag in _EDIT_ROLES:
        return True
    # "editbar" / "editable-text" — never bare "text" or "input"
    return "edit" in role or "edit" in tag


def _desktop_vault_uia(el: dict[str, Any] | None) -> bool:
    """Windows Value pattern destination."""
    return bool(el is not None and el.get("hwnd") and el.get("uia"))


def _atspi_vault_fillable(el: dict[str, Any] | None) -> bool:
    """Linux AT-SPI entry/password with this control's center — not any x/y."""
    if not el or not _element_is_editable(el):
        return False
    if str(el.get("source") or "").strip().lower() != "atspi":
        return False
    return el.get("x") is not None and el.get("y") is not None


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
_OFF_RAIL_PIXEL_REFUSAL = (
    "Refused: a web page is open in the Browser rail. Desktop x/y, Ctrl+L, "
    "and Maximize are how a socials run walked off the page onto Grove chrome "
    "(including negative-Y clicks on the other monitor). Use computer_click / "
    "computer_type / computer_snapshot target=browser. If the rail is "
    "unreachable, computer_wait 2 and retry the rail — do not pixel-drive "
    "the desktop."
)
_ADDRESS_BAR_KEYS = frozenset(
    {
        "ctrl+l",
        "control+l",
        "ctrl+t",
        "control+t",
        "alt+d",
        "ctrl+lenter",
    }
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

                if act.value in ("click", "act", "drag", "press_hold", "hover"):
                    host = _skill_host(
                        self.bridge.last_navigate_url() or host_label(tgt)
                    )
                    # A tool-ok that landed on the wrong control, walked to a
                    # GIF picker, or never verified the post is a FAIL to learn
                    # from — otherwise X.com "learns" the broken GIF-click path.
                    skill_ok = bool(result.get("ok")) and not (
                        result.get("wrong_control")
                        or result.get("unverified")
                        or result.get("modal")
                        or result.get("refused")
                    )
                    record_action(
                        host,
                        act.value,
                        approach_of(act.value, kwargs),
                        skill_ok,
                        home=self.home_dir,
                    )
                    # Newly mastered a site → fold it into who she is (once).
                    if skill_ok:
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
        raw_origin = result.get("origin")
        origin: dict[str, Any] = raw_origin if isinstance(raw_origin, dict) else {}
        width = result.get("width")
        height = result.get("height")

        if act is ComputerAction.SCREENSHOT and path:
            surface = str(result.get("target") or "")
            ocr_block = self._attach_ocr(
                result,
                path=path,
                surface=surface,
                origin=origin,
            )
            decoded = observe_screenshot(
                path,
                runtime=runtime,
                origin=origin,
                width=int(width) if width else None,
                height=int(height) if height else None,
                hint=hint,
            )
            block = format_vision_block(
                decoded,
                origin=origin,
                path=path,
                surface=surface,
            )
            result["vision_ok"] = bool(decoded.get("ok"))
            result["message"] = (
                f"{result.get('message') or ''}\n\n{ocr_block}\n\n{block}"
            ).strip()
            if decoded.get("text"):
                result["vision"] = decoded["text"]
            with contextlib.suppress(Exception):
                self.bridge.set_last_shot(
                    origin=origin,
                    width=int(width) if width else None,
                    height=int(height) if height else None,
                    path=path,
                    scale=float(result.get("coord_scale") or result.get("scale") or 0) or None,
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
                from remedy.core.computer.desktop_os import native

                win = native()

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
                block = format_vision_block(
                    decoded,
                    origin=origin,
                    path=path,
                    surface=str(result.get("target") or last_t or ""),
                )
                result["fallback"] = result.get("fallback") or "vision"
                result["path"] = path
                result["width"] = info.get("width")
                result["height"] = info.get("height")
                result["origin"] = origin
                result["vision_ok"] = bool(decoded.get("ok"))
                if decoded.get("text"):
                    result["vision"] = decoded["text"]
                ocr_block = self._attach_ocr(
                    result,
                    path=path,
                    surface=str(result.get("target") or last_t or "desktop"),
                    origin=origin if isinstance(origin, dict) else {},
                )
                result["message"] = (
                    f"{result.get('message') or ''}\n"
                    "UIA/DOM had no clickable controls — captured the pixels, "
                    "ran OCR, then built-in vision.\n"
                    f"{ocr_block}\n"
                    f"{block}"
                ).strip()
            except Exception as e:
                result["message"] = (
                    f"{result.get('message') or ''}\n"
                    f"Vision fallback failed ({e}). "
                    "Retry computer_screenshot target=desktop."
                ).strip()
        # Empty rail snapshot (SPA not painted / no DOM): OCR the rail capture
        # so "What's happening?" / "Post" are still clickable.
        if (
            act is ComputerAction.SNAPSHOT
            and str(result.get("target") or "") == "browser"
            and not result.get("ocr_ok")
            and not any(
                str(e.get("ref") or "").lower().startswith("e")
                for e in (result.get("elements") or [])
            )
        ):
            with contextlib.suppress(Exception):
                bounds = self.bridge.get_browser_bounds()
                scale = float((bounds or {}).get("scale") or 1.0)
                if bounds and int(bounds.get("width") or 0) > 40:
                    from remedy.core.computer.desktop_os import native

                    info = native().screenshot_region_png(
                        int(bounds["x"]),
                        int(bounds["y"]),
                        int(bounds["width"]),
                        int(bounds["height"]),
                        scale=scale,
                    )
                    result["path"] = str(info.get("path") or "")
                    result["coord_scale"] = scale
                    ocr_block = self._attach_ocr(
                        result,
                        path=str(info.get("path") or ""),
                        surface="browser",
                        origin={},
                    )
                    if result.get("ocr_ok"):
                        result["message"] = (
                            f"{result.get('message') or '(no interactive elements)'}\n"
                            "DOM was empty — OCR of the rail:\n"
                            f"{ocr_block}"
                        ).strip()
        return result

    def _attach_ocr(
        self,
        result: dict[str, Any],
        *,
        path: str,
        surface: str,
        origin: dict[str, Any],
    ) -> str:
        """Read word boxes from a screenshot; merge as clickable oN refs.

        Better than a skipped SmolVLM decode for labels (Post, What's happening?,
        dialog titles) — the socials run went blind when RMB held VRAM.
        """
        from remedy.core.computer.elements import format_som_list
        from remedy.core.computer.ocr import (
            merge_ocr_elements,
            read_screenshot_ocr,
            words_to_elements,
        )

        ocr = read_screenshot_ocr(path)
        result["ocr_ok"] = bool(ocr.get("ok"))
        result["ocr_backend"] = str(ocr.get("backend") or "")
        if not ocr.get("ok") or not ocr.get("words"):
            err = str(ocr.get("error") or "no words")
            return (
                f"## OCR skipped\n({err})\n"
                "If this is a web page, retry computer_snapshot target=browser. "
                "Do not guess desktop x/y."
            )
        web = (surface or "").strip().lower() in ("browser", "web", "rail")
        scale = float(result.get("coord_scale") or result.get("scale") or 1.0)
        ox = float((origin or {}).get("x") or 0)
        oy = float((origin or {}).get("y") or 0)
        els = words_to_elements(
            list(ocr.get("words") or []),
            scale=scale if web else 1.0,
            origin_x=ox,
            origin_y=oy,
            space="page" if web else "screen",
        )
        result["ocr_elements"] = els
        result["ocr_text"] = str(ocr.get("text") or "")[:1500]
        info = {}
        with contextlib.suppress(Exception):
            info = self.bridge.last_elements_info() or {}
        merged = merge_ocr_elements(list(info.get("elements") or []), els)
        tgt = surface if surface in ("browser", "desktop") else str(info.get("target") or surface or "desktop")
        with contextlib.suppress(Exception):
            self.bridge.set_last_elements(merged, target=tgt or "desktop")
        som = format_som_list(els, limit=40)
        return (
            f"## OCR ({ocr.get('backend')}) — click with computer_click "
            f"ref=oN or text=\n"
            f"{som}\n"
            "These boxes are real word positions, not a VLM guess. "
            + (
                "computer_click target=browser ref=oN (page coordinates)."
                if web
                else "computer_click target=desktop ref=oN (screen coordinates)."
            )
        )

    def _ocr_click_scale(self, shot: dict[str, Any], *, web: bool) -> float:
        """HiDPI rail captures are physical pixels; clicks are CSS coords."""
        try:
            scale = float(shot.get("coord_scale") or shot.get("scale") or 0)
        except (TypeError, ValueError):
            scale = 0.0
        if web and scale <= 0:
            with contextlib.suppress(Exception):
                bounds = self.bridge.get_browser_bounds()
                scale = float((bounds or {}).get("scale") or 1.0)
        return scale if scale > 0 else 1.0

    def _ocr_locate_text(self, text_q: str, *, surface: str) -> dict[str, Any] | None:
        """Match a label against OCR word boxes. Returns the element or None."""
        from remedy.core.computer.elements import find_best_element
        from remedy.core.computer.ocr import (
            merge_ocr_elements,
            read_screenshot_ocr,
            words_to_elements,
        )

        shot: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            shot = self.bridge.last_shot() or {}
        web = surface == "browser"
        path = str(shot.get("path") or "")
        if (not path or not Path(path).is_file()) and web:
            with contextlib.suppress(Exception):
                bounds = self.bridge.get_browser_bounds()
                if bounds and int(bounds.get("width") or 0) > 40:
                    from remedy.core.computer.desktop_os import native

                    sc = float(bounds.get("scale") or 1.0)
                    info = native().screenshot_region_png(
                        int(bounds["x"]),
                        int(bounds["y"]),
                        int(bounds["width"]),
                        int(bounds["height"]),
                        scale=sc,
                    )
                    path = str(info.get("path") or "")
                    shot = {
                        **shot,
                        "path": path,
                        "coord_scale": sc,
                        "scale": sc,
                        "origin": {},
                    }
                    self.bridge.set_last_shot(
                        origin={},
                        width=int(info.get("width") or 0) or None,
                        height=int(info.get("height") or 0) or None,
                        path=path,
                        scale=sc,
                    )
        if not path or not Path(path).is_file():
            return None
        ocr = read_screenshot_ocr(path)
        if not ocr.get("ok") or not ocr.get("words"):
            return None
        scale = self._ocr_click_scale(shot, web=web)
        ox = float((shot.get("origin") or {}).get("x") or 0)
        oy = float((shot.get("origin") or {}).get("y") or 0)
        info = {}
        with contextlib.suppress(Exception):
            info = self.bridge.last_elements_info() or {}
        els = words_to_elements(
            list(ocr.get("words") or []),
            scale=scale,
            origin_x=ox,
            origin_y=oy,
            space="page" if web else "screen",
        )
        merged = merge_ocr_elements(list(info.get("elements") or []), els)
        with contextlib.suppress(Exception):
            self.bridge.set_last_elements(merged, target=surface)
        # Floor 40 matches Rust host click_text — weak 1-word overlaps stay misses.
        el = find_best_element(els, text_q, min_score=40.0)
        if el is None:
            return None
        return el

    def _ocr_click_text(self, text_q: str, *, surface: str) -> dict[str, Any] | None:
        """Match a label against OCR word boxes and click that coordinate."""
        el = self._ocr_locate_text(text_q, surface=surface)
        if el is None:
            return None
        x, y = int(el.get("x") or 0), int(el.get("y") or 0)
        web = surface == "browser"
        if web:
            job = self._enqueue(
                "click",
                {"ui": {"open_browser": True}, "x": x, "y": y, "action": "click"},
            )
            fin = self.bridge.wait(
                job.id,
                timeout_s=4.0,
                poll_s=0.05,
                abort_check=self._abort_check,
                unclaimed_timeout_s=2.0,
                grace_s=0.15,
            )
            if fin.status != "done" or not fin.result:
                return None
            out = dict(fin.result)
            out.setdefault("ok", True)
            out["text"] = text_q
            out["ref"] = el.get("ref")
            out["source"] = "ocr"
            out["message"] = (
                f"Clicked text={text_q!r} via OCR {el.get('ref')} "
                f"({str(el.get('name') or '')[:40]}) at ({x},{y})"
            )
            return out
        try:
            from remedy.core.computer.desktop_os import native

            native().click(x, y)
        except Exception:
            return None
        return public_result(
            ok=True,
            target="desktop",
            action="click",
            message=(
                f"Clicked text={text_q!r} via OCR {el.get('ref')} "
                f"({str(el.get('name') or '')[:40]}) at ({x},{y})"
            ),
            extra={"ref": el.get("ref"), "x": x, "y": y, "source": "ocr", "text": text_q},
        )



    def _resolve_label_point(
        self,
        win: Any,
        *,
        text: str = "",
        ref: str = "",
        x: int = 0,
        y: int = 0,
        surface: str = "desktop",
    ) -> tuple[int, int, dict[str, Any]] | None:
        """Resolve text=/ref=/x,y into a screen (or page) point.

        Explicit non-zero coords win. Otherwise last snapshot / UIA tree,
        then OCR word boxes — same family as click and press_hold.
        Returns (x, y, meta) or None when the locator misses.
        """
        text_q = str(text or "").strip()
        ref_q = str(ref or "").strip()
        px, py = int(x or 0), int(y or 0)
        if (px or py) and not text_q and not ref_q:
            return px, py, {"source": "coords", "x": px, "y": py}
        if ref_q and not text_q:
            el = self.bridge.get_element_by_ref(ref_q)
            if el is None and surface == "desktop" and win is not None:
                with contextlib.suppress(Exception):
                    elements = win.desktop_snapshot(limit=60, mode="auto")
                    self.bridge.set_last_elements(elements, target="desktop")
                    el = self.bridge.get_element_by_ref(ref_q)
            if el is not None:
                if surface == "desktop" and el.get("offscreen") and el.get("hwnd"):
                    with contextlib.suppress(Exception):
                        from remedy.core.computer.desktop_uia import element_action
                        from remedy.core.computer.elements import find_best_element

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
                            upd = find_best_element(fresh, str(el.get("name") or ""))
                            if upd is not None and not upd.get("offscreen"):
                                el = upd
                hx, hy = int(el.get("x") or 0), int(el.get("y") or 0)
                return hx, hy, {
                    "source": "ref",
                    "ref": ref_q,
                    "x": hx,
                    "y": hy,
                    "name": str(el.get("name") or "")[:40],
                }
            return None
        if text_q:
            info = self.bridge.last_elements_info()
            want = "desktop" if surface == "desktop" else str(info.get("target") or "")
            elements = (
                list(info.get("elements") or [])
                if (surface == "desktop" and want == "desktop")
                or (surface == "browser" and want == "browser")
                else []
            )
            if not elements and surface == "desktop" and win is not None:
                elements = win.desktop_snapshot(limit=60, mode="auto")
                self.bridge.set_last_elements(elements, target="desktop")
            from remedy.core.computer.elements import find_best_element

            el = find_best_element(elements, text_q) if elements else None
            if el is None:
                ocr_el = self._ocr_locate_text(text_q, surface=surface)
                if ocr_el is None:
                    return None
                hx, hy = int(ocr_el.get("x") or 0), int(ocr_el.get("y") or 0)
                return hx, hy, {
                    "source": "ocr",
                    "ref": ocr_el.get("ref"),
                    "text": text_q,
                    "x": hx,
                    "y": hy,
                    "name": str(ocr_el.get("name") or "")[:40],
                }
            if surface == "desktop" and el.get("offscreen") and el.get("hwnd") and win is not None:
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
                        upd = find_best_element(fresh, text_q)
                        if upd is not None and not upd.get("offscreen"):
                            el = upd
            hx, hy = int(el.get("x") or 0), int(el.get("y") or 0)
            return hx, hy, {
                "source": "text",
                "ref": el.get("ref"),
                "text": text_q,
                "x": hx,
                "y": hy,
                "name": str(el.get("name") or "")[:40],
                "match_score": el.get("match_score"),
            }
        if px or py:
            return px, py, {"source": "coords", "x": px, "y": py}
        return None

    def _ocr_press_hold_text(
        self, text_q: str, *, hold_ms: int
    ) -> dict[str, Any] | None:
        """Match a label against OCR word boxes and press-hold that coordinate.

        Same locate as click-by-text OCR so a native hold button that UIA
        cannot name (a game, a canvas, a custom control) still works.
        """
        el = self._ocr_locate_text(text_q, surface="desktop")
        if el is None:
            return None
        x, y = int(el.get("x") or 0), int(el.get("y") or 0)
        try:
            from remedy.core.computer.desktop_os import native

            info = native().press_hold(
                x, y, hold_ms=hold_ms, abort_check=self._abort_check
            )
        except Exception:
            return None
        return public_result(
            ok=True,
            target="desktop",
            action="press_hold",
            message=(
                f"Held text={text_q!r} via OCR {el.get('ref')} "
                f"({str(el.get('name') or '')[:40]}) at ({x},{y}) for "
                f"{int(info.get('held_ms') or hold_ms)}ms"
            ),
            extra={
                "ref": el.get("ref"),
                "x": x,
                "y": y,
                "source": "ocr",
                "text": text_q,
                **info,
                **self._desktop_evidence(),
            },
        )

    @staticmethod
    def _launch_result(
        win: Any,
        info: dict[str, Any],
        *,
        action: str,
        name: str,
        started: str,
    ) -> dict[str, Any]:
        """Launch-attempt plus a cheap observe — never invent a window."""
        launched = bool((info or {}).get("ok", True))
        title = ""
        with contextlib.suppress(Exception):
            fg = win.foreground_window_info() or {}
            title = str(fg.get("title") or "").strip()
        observed = bool(title)
        if not launched:
            msg = str((info or {}).get("message") or f"Could not start {name}")
        elif observed:
            msg = f"{started} {name} — I see: {title[:80]}"
        else:
            msg = f"{started} {name}; I don't see a window yet."
        extra = dict(info or {})
        extra["observed"] = observed
        extra["observed_title"] = title[:120]
        return public_result(
            ok=launched,
            target="desktop",
            action=action,
            message=msg,
            extra=extra,
        )

    def _web_task_in_flight(self) -> bool:
        """A rail URL is open and we have not switched to a native app."""
        last = (self.bridge.last_drive_target() or "").strip().lower()
        if last == "desktop":
            return False
        url = (self.bridge.last_navigate_url() or "").strip().lower()
        return url.startswith("http://") or url.startswith("https://")

    def _refuse_off_rail_desktop(self, action: str, **extra: Any) -> dict[str, Any]:
        return public_result(
            ok=False,
            target="desktop",
            action=action,
            message=_OFF_RAIL_PIXEL_REFUSAL,
            extra={"refused": "off_rail", **extra},
        )

    def _remembered_query_for_ref(self, ref: str) -> tuple[str, dict[str, Any] | None]:
        remembered = self.bridge.get_element_by_ref(ref) if ref else None
        name = str((remembered or {}).get("name") or "").strip()
        ctx = str((remembered or {}).get("context") or "")
        ctx_bits = " ".join(w for w in re.findall(r"[A-Za-z0-9]{3,}", ctx)[:4])
        query = (name + " " + ctx_bits).strip()
        return query, remembered if isinstance(remembered, dict) else None

    def _relocate_browser_ref(self, ref: str) -> dict[str, Any] | None:
        """Fresh snapshot + label match for a dead eN ref."""
        query, _ = self._remembered_query_for_ref(ref)
        if not query:
            return None
        snap = self._browser_snapshot_now({})
        if not (snap.get("ok") and snap.get("elements")):
            return None
        from remedy.core.computer.elements import find_best_element

        el = find_best_element(list(snap.get("elements") or []), query)
        if el and el.get("ref"):
            return el
        return None

    def _browser_vault_target_editable(
        self, *, ref: str = "", query: str = ""
    ) -> bool | None:
        """True/False from last snapshot; None if the destination is unknown."""
        if ref:
            bel = self.bridge.get_element_by_ref(ref)
            if bel is not None:
                return _element_is_editable(bel)
        if query:
            with contextlib.suppress(Exception):
                from remedy.core.computer.elements import find_best_element

                els = list((self.bridge.last_elements_info() or {}).get("elements") or [])
                el = find_best_element(els, query) if els else None
                if el is not None:
                    return _element_is_editable(el)
        return None

    def _click_matches_query(self, text_q: str, out: dict[str, Any]) -> bool:
        from remedy.core.computer.elements import (
            label_matches_query,
            parse_click_landed,
        )

        landed = parse_click_landed(
            str(out.get("message") or ""),
            str(out.get("detail") or ""),
        )
        name = str(landed.get("name") or "").strip()
        if not name:
            return True
        if label_matches_query(name, text_q):
            return True
        out["wrong_control"] = True
        out["landed"] = landed
        out["ok"] = False
        out["message"] = (
            f"click for {text_q!r} landed on {name!r} "
            f"({landed.get('tag') or '?'}) — that is not the control. "
            "Snapshot and click the field or the Post/Submit *button* by ref=."
        )
        return False

    @staticmethod
    def _desktop_evidence() -> dict[str, Any]:
        """Post-action proof for desktop acts: foreground window + focused element.

        The desktop analogue of the rail's page probe — without this, native
        clicks/types report success with zero observation.
        """
        ev: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            from remedy.core.computer.desktop_os import native

            win = native()

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
        hwnd = el.get("hwnd")
        if hwnd and clicks == 1 and str(button or "left").lower() == "left":
            with contextlib.suppress(Exception):
                from remedy.core.computer.desktop_uia import (
                    element_action,
                    preferred_click_action,
                )

                action = preferred_click_action(str(el.get("role") or el.get("tag") or ""))
                res = element_action(
                    int(hwnd),
                    str(el.get("name") or ""),
                    role=str(el.get("role") or ""),
                    action=action,
                )
                if res.get("ok"):
                    out = dict(el)
                    out["_method"] = f"uia_{action}"
                    return out
        win.click_element(el, button=button, clicks=clicks)
        out = dict(el)
        out["_method"] = "click_center"
        return out

    def _run_desktop(self, act: ComputerAction, **kwargs: Any) -> dict[str, Any]:
        from remedy.core.computer.desktop_os import native

        win = native()

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
            from remedy.core.computer.desktop_uia import structured_observe_hint

            hint_obs = structured_observe_hint(n_windows=n_w, n_controls=n_c)
            return public_result(
                ok=True,
                target="desktop",
                action="snapshot",
                message=(
                    f"{len(elements)} elements (windows={n_w}, controls={n_c}). "
                    f"{hint_obs}"
                ),
                extra={
                    "elements": elements,
                    "mode": mode,
                    "observe": hint_obs,
                    "structured": n_c > 0,
                },
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
                return self._launch_result(
                    win,
                    info,
                    action="app",
                    name=str(info.get("target") or folder),
                    started="Opened folder",
                )
            search_dirs = list(kwargs.get("search_dirs") or [])
            info = win.open_app(app, search_dirs=search_dirs or None)
            # New window — drop stale UIA refs from the previous app
            with contextlib.suppress(Exception):
                self.bridge.set_last_elements([], target="desktop")
            time.sleep(0.4)
            return self._launch_result(
                win, info, action="app", name=app, started="Started"
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
                    ocr_hit = self._ocr_click_text(text_q, surface="desktop")
                    if ocr_hit is not None:
                        return ocr_hit
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
                method = str(el.get("_method") or "click_center")
                return public_result(
                    ok=True,
                    target="desktop",
                    action="click",
                    message=(
                        f"{'Invoked' if method.startswith('uia_') else 'Clicked'} "
                        f"text={text_q!r} → {el.get('ref')} "
                        f"({str(el.get('name') or '')[:40]})"
                    ),
                    extra={
                        "ref": el.get("ref"),
                        "text": text_q,
                        "x": el.get("x"),
                        "y": el.get("y"),
                        "match_score": el.get("match_score"),
                        "method": method,
                        **self._desktop_evidence(),
                    },
                )
            if ref.lower().startswith("o"):
                el = self.bridge.get_element_by_ref(ref)
                if el is None:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="click",
                        message=(
                            f"Unknown OCR ref {ref} — run computer_screenshot "
                            "first and click ref=oN from that list"
                        ),
                    )
                x, y = int(el.get("x") or 0), int(el.get("y") or 0)
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
                    message=f"Clicked OCR {ref} ({str(el.get('name') or '')[:40]}) at ({x},{y})",
                    extra={"ref": ref, "x": x, "y": y, "source": "ocr", **self._desktop_evidence()},
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
                method = str(el.get("_method") or "click_center")
                return public_result(
                    ok=True,
                    target="desktop",
                    action="click",
                    message=(
                        f"{'Invoked' if method.startswith('uia_') else 'Clicked'} "
                        f"ref={ref} ({el.get('name', '')[:40]})"
                    ),
                    extra={
                        "ref": ref,
                        "x": el.get("x"),
                        "y": el.get("y"),
                        "hwnd": el.get("hwnd"),
                        "method": method,
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
            if self._web_task_in_flight():
                return self._refuse_off_rail_desktop(
                    "click", x=x, y=y
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
        if act is ComputerAction.HOVER:
            if self._abort_check():
                raise RuntimeError("Aborted by user")
            text_q = str(kwargs.get("text") or kwargs.get("query") or "").strip()
            ref_q = str(kwargs.get("ref") or "").strip()
            x, y = int(kwargs.get("x") or 0), int(kwargs.get("y") or 0)
            got = self._resolve_label_point(
                win,
                text=text_q,
                ref=ref_q,
                x=x,
                y=y,
                surface="desktop",
            )
            if got is None:
                label = text_q or ref_q or f"({x},{y})"
                return public_result(
                    ok=False,
                    target="desktop",
                    action="hover",
                    message=(
                        f"No desktop control matching hover target {label!r} — "
                        "try computer_snapshot or pass x/y"
                    ),
                )
            hx, hy, meta = got
            win.move_mouse(hx, hy)
            bits = [f"Hover ({hx},{hy})"]
            if text_q:
                bits.append(f"text={text_q!r}")
            return public_result(
                ok=True,
                target="desktop",
                action="hover",
                message=" ".join(bits),
                extra={"x": hx, "y": hy, "located": meta, **self._desktop_evidence()},
            )
        if act is ComputerAction.DRAG:
            if self._abort_check():
                raise RuntimeError("Aborted by user")
            # Guidance advertises drag addressed by label (text/ref) like click /
            # press_hold. Schema still accepts bare x/y/x2/y2; from_text/to_text
            # (and text=/ref= aliases for the start) locate endpoints.
            from_text = str(
                kwargs.get("from_text") or kwargs.get("text") or ""
            ).strip()
            to_text = str(kwargs.get("to_text") or "").strip()
            from_ref = str(
                kwargs.get("from_ref") or kwargs.get("ref") or ""
            ).strip()
            to_ref = str(kwargs.get("to_ref") or "").strip()
            x1, y1 = int(kwargs.get("x") or 0), int(kwargs.get("y") or 0)
            x2, y2 = int(kwargs.get("x2") or 0), int(kwargs.get("y2") or 0)
            start_meta: dict[str, Any] = {}
            end_meta: dict[str, Any] = {}
            start_unset = (x1, y1) == (0, 0)
            if from_text or from_ref or start_unset:
                got = self._resolve_label_point(
                    win,
                    text=from_text,
                    ref=from_ref,
                    x=x1,
                    y=y1,
                    surface="desktop",
                )
                if got is None:
                    label = from_text or from_ref or f"({x1},{y1})"
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="drag",
                        message=(
                            f"No desktop control matching drag start "
                            f"{label!r} — try computer_snapshot or pass x/y"
                        ),
                    )
                x1, y1, start_meta = got
            else:
                start_meta = {"source": "coords", "x": x1, "y": y1}
            end_unset = (x2, y2) == (0, 0)
            if to_text or to_ref or end_unset:
                got = self._resolve_label_point(
                    win,
                    text=to_text,
                    ref=to_ref,
                    x=x2,
                    y=y2,
                    surface="desktop",
                )
                if got is None:
                    label = to_text or to_ref or f"({x2},{y2})"
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="drag",
                        message=(
                            f"No desktop control matching drag end "
                            f"{label!r} — try computer_snapshot or pass x2/y2"
                        ),
                    )
                x2, y2, end_meta = got
            else:
                end_meta = {"source": "coords", "x": x2, "y": y2}
            if not ((x1 or y1) or (x2 or y2)):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="drag",
                    message=(
                        "drag needs from_text=/to_text= (or text=/ref=), "
                        "from_ref=/to_ref=, or explicit x/y/x2/y2"
                    ),
                )
            win.drag(x1, y1, x2, y2)
            bits = [f"Drag ({x1},{y1})→({x2},{y2})"]
            if from_text:
                bits.append(f"from_text={from_text!r}")
            if to_text:
                bits.append(f"to_text={to_text!r}")
            return public_result(
                ok=True,
                target="desktop",
                action="drag",
                message=" ".join(bits),
                extra={
                    "x": x1,
                    "y": y1,
                    "x2": x2,
                    "y2": y2,
                    "from": start_meta,
                    "to": end_meta,
                    **self._desktop_evidence(),
                },
            )
        if act is ComputerAction.TYPE:
            text = str(kwargs.get("text") or "")
            # Pre-expansion length so a vault secret's true length never leaks.
            reported_len = len(text)
            had_vault = "{{" in text
            set_ref = str(kwargs.get("ref") or "").strip()
            set_query = field_locator(kwargs.get("query"), kwargs.get("label"))
            locate_meta: dict[str, Any] = {}
            if set_query:
                got = self._resolve_label_point(
                    win,
                    text=set_query,
                    ref=set_ref,
                    surface="desktop",
                )
                if got is None:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=(
                            f"No desktop field matching {set_query!r} — "
                            "try computer_snapshot or pass ref="
                        ),
                    )
                _, _, locate_meta = got
                hit_ref = str(locate_meta.get("ref") or "").strip()
                if hit_ref:
                    set_ref = hit_ref
            if had_vault and not set_ref and not set_query:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="type",
                    message=(
                        "Vault secrets only type into a named field. Pass ref= "
                        "from computer_snapshot or query= the visible label so "
                        "the value lands in that control, not whatever currently "
                        "has focus."
                    ),
                    extra={"length": None, "needs": "ref"},
                )
            if self._web_task_in_flight() and not (
                set_ref.lower().startswith("c") or set_ref.lower().startswith("w")
            ):
                return self._refuse_off_rail_desktop("type")
            focused = False
            el = None
            if set_ref:
                el = self.bridge.get_element_by_ref(set_ref)
            if had_vault:
                if el is None or not _element_is_editable(el):
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=(
                            "Vault secrets only type into an editable field. "
                            "Snapshot again and pass ref= for the input, not a "
                            "button or unlabeled click target."
                        ),
                        extra={"length": None, "needs": "ref"},
                    )
                if not _desktop_vault_uia(el) and not _atspi_vault_fillable(el):
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=(
                            "Vault secrets only type into an editable field "
                            "(UIA Value or AT-SPI entry). Will not click a "
                            "generic x/y target."
                        ),
                        extra={"length": None, "needs": "ref"},
                    )
            text, vault_err = self._expand_vault_text(
                text, destination_url="", action="type", target="desktop"
            )
            if vault_err is not None:
                return vault_err
            if _desktop_vault_uia(el):
                from remedy.core.computer.desktop_uia import element_action

                res = element_action(
                    int(el["hwnd"]),
                    str(el.get("name") or ""),
                    role=str(el.get("role") or ""),
                    action="set_value",
                    text=text,
                )
                if res.get("ok"):
                    extra = {
                        "ref": set_ref,
                        "length": None if had_vault else reported_len,
                        "method": "uia_set_value",
                        "verified": bool(res.get("verified")),
                        **self._desktop_evidence(),
                    }
                    if set_query:
                        extra["query"] = set_query
                    if locate_meta:
                        extra["located"] = locate_meta
                    return public_result(
                        ok=True,
                        target="desktop",
                        action="type",
                        message=str(res.get("message") or "Set value"),
                        extra=extra,
                    )
                if had_vault:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=(
                            "Vault secrets only type into an editable field "
                            "(UIA Value). This control is not settable — "
                            "will not click and keystroke the secret."
                        ),
                        extra={"length": None, "needs": "ref"},
                    )
                with contextlib.suppress(Exception):
                    win.click_element(el)
                    time.sleep(0.1)
                focused = True
            elif had_vault and _atspi_vault_fillable(el):
                with contextlib.suppress(Exception):
                    win.click_element(el)
                    time.sleep(0.1)
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
                        return public_result(
                            ok=False,
                            target="desktop",
                            action="type",
                            message="Aborted by user during type",
                            extra={"length": None, "typed": None, "aborted": True},
                        )
                    raise
                extra = {
                    "ref": set_ref,
                    "length": None,
                    "method": "atspi_type",
                    **self._desktop_evidence(),
                }
                if set_query:
                    extra["query"] = set_query
                return public_result(
                    ok=True,
                    target="desktop",
                    action="type",
                    message="Typed a stored secret",
                    extra=extra,
                )
            elif el is not None:
                if had_vault:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=(
                            "Vault secrets only type into an editable field. "
                            "Will not click and keystroke the secret."
                        ),
                        extra={"length": None, "needs": "ref"},
                    )
                with contextlib.suppress(Exception):
                    win.click_element(el)
                    time.sleep(0.1)
                focused = True
            if locate_meta and not focused:
                if had_vault:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=(
                            "Vault secrets only type into an editable field. "
                            "Will not click and keystroke the secret."
                        ),
                        extra={"length": None, "needs": "ref"},
                    )
                with contextlib.suppress(Exception):
                    win.click(
                        int(locate_meta.get("x") or 0),
                        int(locate_meta.get("y") or 0),
                    )
                    time.sleep(0.1)
            if had_vault:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="type",
                    message=(
                        "Vault secrets only type into an editable field. "
                        "Will not click and keystroke the secret."
                    ),
                    extra={"length": None, "needs": "ref"},
                )
            typed_key_box: list[int] = [0]
            type_method = "keystrokes"
            try:
                tf = win.type_text_fast(
                    text,
                    abort_check=self._abort_check,
                    chars_typed=typed_key_box,
                )
                type_method = str(tf.get("method") or "keystrokes")
            except RuntimeError as e:
                if "abort" in str(e).lower():
                    self._cancel_open_jobs(reason="aborted")
                    n = int(typed_key_box[0] if typed_key_box else 0)
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="type",
                        message=(
                            "Aborted by user during type"
                            if had_vault
                            else f"Aborted by user during type after {n} chars"
                        ),
                        extra={
                            "length": None if had_vault else reported_len,
                            "typed": None if had_vault else n,
                            "aborted": True,
                        },
                    )
                raise
            if self._abort_check():
                self._cancel_open_jobs(reason="aborted")
                n = int(typed_key_box[0] if typed_key_box else 0)
                return public_result(
                    ok=False,
                    target="desktop",
                    action="type",
                    message=(
                        "Aborted by user during type"
                        if had_vault
                        else f"Aborted by user during type after {n} chars"
                    ),
                    extra={
                        "length": None if had_vault else reported_len,
                        "typed": None if had_vault else n,
                        "aborted": True,
                    },
                )
            length = "a stored secret" if had_vault else f"{reported_len} chars"
            verb = "Pasted" if type_method == "paste" else "Typed"
            into = f" into {set_query!r}" if set_query else ""
            extra = {
                "length": reported_len if not had_vault else None,
                "method": type_method,
                **self._desktop_evidence(),
            }
            if set_query:
                extra["query"] = set_query
            if set_ref:
                extra["ref"] = set_ref
            if locate_meta:
                extra["located"] = locate_meta
            return public_result(
                ok=True,
                target="desktop",
                action="type",
                message=f"{verb} {length}{into}",
                extra=extra,
            )
        if act is ComputerAction.KEY:
            key = str(kwargs.get("key") or "")
            key_n = key.strip().lower().replace(" ", "")
            if self._web_task_in_flight() and (
                key_n in _ADDRESS_BAR_KEYS or key_n.startswith("ctrl+l")
            ):
                return self._refuse_off_rail_desktop("key", key=key)
            win.press_key(key)
            return public_result(
                ok=True,
                target="desktop",
                action="key",
                message=f"Pressed {key}",
                extra={"key": key, **self._desktop_evidence()},
            )
        if act is ComputerAction.SCROLL:
            # Guidance addresses scroll by label (text/ref) like click /
            # press_hold / drag. Schema still accepts bare x/y; text=/ref=
            # locate the pane then wheel at that point.
            text_q = str(kwargs.get("text") or "").strip()
            ref_q = str(kwargs.get("ref") or "").strip()
            x, y = int(kwargs.get("x") or 0), int(kwargs.get("y") or 0)
            point_usable = (x, y) != (0, 0)
            scroll_meta: dict[str, Any] = {}
            if (text_q or ref_q) and not point_usable:
                got = self._resolve_label_point(
                    win,
                    text=text_q,
                    ref=ref_q,
                    x=x,
                    y=y,
                    surface="desktop",
                )
                if got is None:
                    label = text_q or ref_q or f"({x},{y})"
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="scroll",
                        message=(
                            f"No desktop control matching scroll target "
                            f"{label!r} — try computer_snapshot or pass x/y"
                        ),
                    )
                x, y, scroll_meta = got
            elif not point_usable:
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
                scroll_meta = {"source": "foreground_center", "x": x, "y": y}
            else:
                scroll_meta = {"source": "coords", "x": x, "y": y}
            raw_dy = kwargs.get("dy")
            dy = int(raw_dy) if raw_dy is not None else -3
            win.scroll(x, y, dy=dy)
            bits = [f"Scrolled at ({x},{y}) dy={dy}"]
            if text_q:
                bits.append(f"text={text_q!r}")
            if ref_q and not text_q:
                bits.append(f"ref={ref_q!r}")
            return public_result(
                ok=True,
                target="desktop",
                action="scroll",
                message=" ".join(bits),
                extra={"x": x, "y": y, "dy": dy, **scroll_meta},
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
                    brought = bool(win.focus_window(int(match["hwnd"])))
                    seen = ""
                    with contextlib.suppress(Exception):
                        seen = str(
                            (win.foreground_window_info() or {}).get("title") or ""
                        )
                    if not brought:
                        return public_result(
                            ok=False,
                            target="desktop",
                            action="windows",
                            message=(
                                f"Could not bring {real[:80]!r} forward — "
                                f"I see: {seen[:80] or 'no window'}"
                            ),
                            extra={"hwnd": int(match["hwnd"]), "title": real, "seen": seen},
                        )
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
                brought = bool(win.focus_window(hwnd))
                seen = ""
                with contextlib.suppress(Exception):
                    seen = str((win.foreground_window_info() or {}).get("title") or "")
                if not brought:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="windows",
                        message=(
                            f"Could not bring hwnd={hwnd} forward — "
                            f"I see: {seen[:80] or 'no window'}"
                        ),
                        extra={"hwnd": hwnd, "seen": seen},
                    )
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
            ref = str(kwargs.get("ref") or "").strip()
            text_q = str(kwargs.get("text") or "").strip()
            hold_ms = int(kwargs.get("hold_ms") or 2600)
            x, y = int(kwargs.get("x") or 0), int(kwargs.get("y") or 0)

            def _hold_at(
                hx: int,
                hy: int,
                *,
                message: str,
                extra: dict[str, Any],
            ) -> dict[str, Any]:
                info = win.press_hold(
                    hx, hy, hold_ms=hold_ms, abort_check=self._abort_check
                )
                return public_result(
                    ok=True,
                    target="desktop",
                    action="press_hold",
                    message=message,
                    extra={**info, **extra, **self._desktop_evidence()},
                )

            def _bring_on_screen(el: dict[str, Any]) -> dict[str, Any]:
                if not (el.get("offscreen") and el.get("hwnd")):
                    return el
                with contextlib.suppress(Exception):
                    from remedy.core.computer.desktop_uia import element_action
                    from remedy.core.computer.elements import find_best_element

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
                        upd = find_best_element(fresh, str(el.get("name") or ""))
                        if upd is not None and not upd.get("offscreen"):
                            return upd
                return el

            # text= is the advertised locator (same as computer_click). The
            # tool handler still forwards schema-default (0, 0) alongside a
            # label — resolve the control, do not demand x/y from the model.
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
                    ocr_hit = self._ocr_press_hold_text(text_q, hold_ms=hold_ms)
                    if ocr_hit is not None:
                        return ocr_hit
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="press_hold",
                        message=(
                            f"No desktop control matching text={text_q!r} — "
                            "try computer_snapshot"
                        ),
                    )
                el = _bring_on_screen(el)
                hx, hy = int(el.get("x") or 0), int(el.get("y") or 0)
                return _hold_at(
                    hx,
                    hy,
                    message=(
                        f"Held text={text_q!r} → {el.get('ref')} "
                        f"({str(el.get('name') or '')[:40]}) for "
                        f"{hold_ms}ms"
                    ),
                    extra={
                        "ref": el.get("ref"),
                        "text": text_q,
                        "x": hx,
                        "y": hy,
                        "match_score": el.get("match_score"),
                    },
                )
            if ref.lower().startswith("o") and not (x or y):
                el = self.bridge.get_element_by_ref(ref)
                if el is None:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="press_hold",
                        message=(
                            f"Unknown OCR ref {ref} — run computer_screenshot "
                            "first and press-hold ref=oN from that list"
                        ),
                    )
                hx, hy = int(el.get("x") or 0), int(el.get("y") or 0)
                return _hold_at(
                    hx,
                    hy,
                    message=(
                        f"Held OCR {ref} ({str(el.get('name') or '')[:40]}) "
                        f"at ({hx},{hy}) for {hold_ms}ms"
                    ),
                    extra={
                        "ref": ref,
                        "x": hx,
                        "y": hy,
                        "source": "ocr",
                    },
                )
            if ref and not (x or y):
                el = self.bridge.get_element_by_ref(ref)
                if el is not None:
                    el = _bring_on_screen(el)
                    x, y = int(el.get("x") or 0), int(el.get("y") or 0)
            if not (x or y):
                return public_result(
                    ok=False,
                    target="desktop",
                    action="press_hold",
                    message=(
                        "press_hold needs text=, x/y, or a ref from "
                        "computer_snapshot"
                    ),
                )
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
        # Drag-by-label: guidance + approach_of treat drag like click. The rail
        # host only accepts x/y/x2/y2 today — resolve labels to coords here so
        # from_text/to_text work without a desktop rebuild.
        if act is ComputerAction.DRAG and (
            payload.get("from_text")
            or payload.get("to_text")
            or payload.get("text")
            or payload.get("from_ref")
            or payload.get("to_ref")
            or payload.get("ref")
        ):
            from_text = str(
                payload.get("from_text") or payload.get("text") or ""
            ).strip()
            to_text = str(payload.get("to_text") or "").strip()
            from_ref = str(
                payload.get("from_ref") or payload.get("ref") or ""
            ).strip()
            to_ref = str(payload.get("to_ref") or "").strip()
            sx = int(payload.get("x") or 0)
            sy = int(payload.get("y") or 0)
            ex_ = int(payload.get("x2") or 0)
            ey = int(payload.get("y2") or 0)
            start = self._resolve_label_point(
                None,
                text=from_text,
                ref=from_ref,
                x=sx,
                y=sy,
                surface="browser",
            )
            if start is None and (from_text or from_ref or not (sx or sy)):
                label = from_text or from_ref or f"({sx},{sy})"
                return public_result(
                    ok=False,
                    target="browser",
                    action="drag",
                    message=(
                        f"No browser control matching drag start {label!r} — "
                        "try computer_snapshot or pass x/y"
                    ),
                )
            if start is not None:
                sx, sy, _ = start
            end = self._resolve_label_point(
                None,
                text=to_text,
                ref=to_ref,
                x=ex_,
                y=ey,
                surface="browser",
            )
            if end is None and (to_text or to_ref or not (ex_ or ey)):
                label = to_text or to_ref or f"({ex_},{ey})"
                return public_result(
                    ok=False,
                    target="browser",
                    action="drag",
                    message=(
                        f"No browser control matching drag end {label!r} — "
                        "try computer_snapshot or pass x2/y2"
                    ),
                )
            if end is not None:
                ex_, ey, _ = end
            payload["x"], payload["y"] = sx, sy
            payload["x2"], payload["y2"] = ex_, ey
            for drop in ("from_text", "to_text", "from_ref", "to_ref", "text", "ref"):
                payload.pop(drop, None)
        # Scroll-by-label: guidance + approach_of treat scroll like click. The
        # rail host only accepts x/y/dy today — resolve text=/ref= to coords
        # here so named panes scroll without a desktop rebuild.
        if act is ComputerAction.SCROLL and (
            payload.get("text") or payload.get("ref")
        ):
            text_q = str(payload.get("text") or "").strip()
            ref_q = str(payload.get("ref") or "").strip()
            sx = int(payload.get("x") or 0)
            sy = int(payload.get("y") or 0)
            if (text_q or ref_q) and (sx, sy) == (0, 0):
                hit = self._resolve_label_point(
                    None,
                    text=text_q,
                    ref=ref_q,
                    x=sx,
                    y=sy,
                    surface="browser",
                )
                if hit is None:
                    label = text_q or ref_q or f"({sx},{sy})"
                    return public_result(
                        ok=False,
                        target="browser",
                        action="scroll",
                        message=(
                            f"No browser control matching scroll target "
                            f"{label!r} — try computer_snapshot or pass x/y"
                        ),
                    )
                sx, sy, _ = hit
                payload["x"], payload["y"] = sx, sy
            for drop in ("text", "ref"):
                payload.pop(drop, None)
        # Vault tokens in typed text expand machine-side, bound to the rail's
        # current site — the model and job log only ever saw the handle.
        if act is ComputerAction.TYPE and payload.get("text"):
            text = str(payload.get("text") or "")
            had_vault = "{{" in text
            set_ref = str(payload.get("ref") or kwargs.get("ref") or "").strip()
            set_query = field_locator(
                payload.get("query"),
                kwargs.get("query"),
                kwargs.get("label"),
            )
            if set_query:
                payload["query"] = set_query
            if had_vault and not set_ref and not set_query:
                return public_result(
                    ok=False,
                    target="browser",
                    action="type",
                    message=(
                        "Vault secrets only type into a named field. Pass ref= "
                        "from computer_snapshot or query= the visible label so "
                        "the value lands in that control, not whatever currently "
                        "has focus."
                    ),
                    extra={"needs": "ref"},
                )
            if had_vault:
                editable = self._browser_vault_target_editable(
                    ref=set_ref, query=set_query
                )
                if editable is not True:
                    return public_result(
                        ok=False,
                        target="browser",
                        action="type",
                        message=(
                            "Vault secrets only type into an editable field. "
                            "That control is not an input."
                        ),
                        extra={"needs": "ref"},
                    )
            # Binding domain is the LIVE page (probed inside), not last navigate.
            expanded, vault_err = self._expand_vault_text(
                text,
                action="type",
                target="browser",
            )
            if vault_err is not None:
                return vault_err
            payload["text"] = expanded
            if had_vault:
                payload["_has_secret"] = True
            if set_ref:
                payload["ref"] = set_ref
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
            ComputerAction.HOVER,
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
            ComputerAction.HOVER,
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
                ComputerAction.HOVER,
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
                    from remedy.core.computer.desktop_os import native

                    win = native()

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
                from remedy.core.computer.desktop_os import native

                win = native()

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
            return public_result(
                ok=False,
                target="browser",
                action="screenshot",
                message=(
                    "Browser rail bounds missing — open the Browser rail and "
                    "wait for bounds. Not capturing the full desktop as a rail shot."
                ),
            )

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

        if act is ComputerAction.HOVER:
            text_q = str(kwargs.get("text") or kwargs.get("query") or "").strip()
            ref = str(kwargs.get("ref") or "").strip()
            payload["action"] = "hover"
            if text_q:
                payload["text"] = text_q
            if ref:
                payload["ref"] = ref
            if kwargs.get("x") is not None:
                payload["x"] = kwargs.get("x")
            if kwargs.get("y") is not None:
                payload["y"] = kwargs.get("y")

        # Atomic click-by-text in the rail (one JS pass — no vision)
        if act is ComputerAction.CLICK:
            text_q = str(kwargs.get("text") or "").strip()
            ref = str(kwargs.get("ref") or "").strip()
            if text_q and not ref:
                return self._browser_click_text(text_q, kwargs)
            if ref.lower().startswith("o"):
                # OCR word box — no data-remedy-ref in the DOM. Click the
                # page coordinates we stored from the screenshot.
                el = self.bridge.get_element_by_ref(ref)
                if el is None:
                    return public_result(
                        ok=False,
                        target="browser",
                        action="click",
                        message=(
                            f"Unknown OCR ref {ref} — run computer_screenshot "
                            "on the rail first and click ref=oN from that list"
                        ),
                    )
                payload.pop("ref", None)
                payload["x"] = int(el.get("x") or 0)
                payload["y"] = int(el.get("y") or 0)
                payload["action"] = "click"
            elif ref:
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
                """Host offline / rail miss → window+UIA tree (never hang the agent).

                ``ok=False``: this is not a successful *page* observe. Returning
                ok with a desktop tree made the model click Maximize / type a
                URL into chrome with negative Y coords and still think it was
                on X.com.
                """
                desk = self._run_desktop(
                    ComputerAction.SNAPSHOT,
                    limit=kwargs.get("limit") or 40,
                    mode=kwargs.get("mode") or "auto",
                    hwnd=kwargs.get("hwnd"),
                )
                warn = (
                    f"BROWSER RAIL UNREACHABLE ({reason}) — the elements below "
                    "are DESKTOP WINDOWS, NOT the web page. Do NOT click them "
                    "as the site. Do NOT drive the owner's own browser "
                    "(Firefox/Chrome/Edge, Ctrl+T, typing URLs) — web tasks "
                    "live in the in-app rail only. computer_wait 2 then retry "
                    "computer_snapshot target=browser; if it stays unreachable, "
                    "tell the owner instead of improvising on the desktop."
                )
                desk["note"] = warn
                desk["message"] = warn
                desk["fallback"] = "desktop"
                desk["ok"] = False
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
                    from remedy.core.computer.elements import detect_modal_obstacle

                    modal = detect_modal_obstacle(
                        elements=elements,
                        url=str(out.get("url") or ""),
                        title=str(out.get("title") or ""),
                        text=str(out.get("text") or ""),
                    )
                    if modal:
                        out["modal"] = modal
                        out["message"] = (
                            f"MODAL/POPUP in front ({modal.get('kind')}: "
                            f"{modal.get('detail')}). {modal.get('hint')} "
                            "Do not type into the page underneath.\n"
                        ) + str(out.get("message") or "")
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
            query, remembered = self._remembered_query_for_ref(ref)
            name = str((remembered or {}).get("name") or "").strip()
            if query:
                recovered = self._browser_click_text(query, kwargs)
                if recovered.get("ok"):
                    recovered["note"] = (
                        f"ref {ref} was stale (page changed) — re-located by "
                        f"label {name or query!r} and clicked"
                    )
                    recovered["recovered_from"] = "stale_ref"
                    return recovered
        if act is ComputerAction.TYPE and "missing-ref" in fail_msg:
            ref = str(kwargs.get("ref") or payload.get("ref") or "").strip()
            text = str(payload.get("text") or kwargs.get("text") or "")
            el = self._relocate_browser_ref(ref)
            if el and el.get("ref") and text:
                job2 = self._enqueue(
                    "type",
                    {
                        "ui": {"open_browser": True},
                        "text": text,
                        "ref": str(el.get("ref")),
                        "query": str(el.get("name") or "") or None,
                    },
                )
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
                    if out.get("ok", True) is not False:
                        out.setdefault("ok", True)
                        out.setdefault("target", "browser")
                        out.setdefault("action", "type")
                        out["note"] = (
                            f"ref {ref} was stale — re-located to "
                            f"{el.get('ref')} ({str(el.get('name') or '')[:40]}) "
                            "and typed"
                        )
                        out["recovered_from"] = "stale_ref"
                        out["ref"] = el.get("ref")
                        return out
            query, remembered = self._remembered_query_for_ref(ref)
            name = str((remembered or {}).get("name") or "").strip()
            if query and text:
                focused = self._browser_click_text(query, kwargs)
                if focused.get("ok"):
                    job3 = self._enqueue(
                        "type",
                        {
                            "ui": {"open_browser": True},
                            "text": text,
                            "query": query,
                        },
                    )
                    fin3 = self.bridge.wait(
                        job3.id,
                        timeout_s=4.0,
                        poll_s=0.05,
                        abort_check=self._abort_check,
                        unclaimed_timeout_s=2.0,
                        grace_s=0.15,
                    )
                    if fin3.status == "done" and (fin3.result or {}).get("ok", True) is not False:
                        return public_result(
                            ok=True,
                            target="browser",
                            action="type",
                            message=(
                                f"ref {ref} was stale — focused {name or query!r} "
                                "by label and typed"
                            ),
                            extra={"recovered_from": "stale_ref", "ref": None},
                        )

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
                    if self._click_matches_query(text_q, out):
                        return out
                    last_err = str(out.get("message") or "wrong-control")
                    continue
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
        # DOM miss → OCR the rail capture (custom paint, late SPA, vision idle).
        ocr_hit = self._ocr_click_text(text_q, surface="browser")
        if ocr_hit is not None:
            return ocr_hit
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
                if label and not ref and "{{" not in value:
                    # Vault values must not click-then-type; TYPE checks editable.
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
                    query=label or None,
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
            ok=False,
            target="browser",
            action="fill",
            message=(
                f"UNVERIFIED: filled {len(results)} field(s) but did not read "
                "them back — computer_page_text or snapshot before Submit."
            ),
            extra={"fields": results, "verified": False, "unverified": True},
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
            # After a compound navigate, capture the page we landed on so a
            # later field-prompt click that walks off to GIF-search / a
            # download interstitial fails closed.
            if mutating and (click or type_text) and pre_state is None:
                pre_state = self._page_probe(max_wait_s=1.5)

        from remedy.core.computer.elements import looks_like_publish_verb

        # computer_act(click="Post", type="hello") used to click Post first
        # (often a nav link) then type into whatever opened. Type the body,
        # then press the submit button.
        type_before_click = bool(
            type_text and click and looks_like_publish_verb(click)
        )

        def _act_click() -> dict[str, Any] | None:
            ck = self._browser_click_text(click, payload)
            log.append(f"click:{ck.get('ok')} {click!r} → {ck.get('message', '')[:60]}")
            if not ck.get("ok"):
                return public_result(
                    ok=False,
                    target="browser",
                    action="act",
                    message=f"act: click failed — {ck.get('message')}",
                    extra={"steps": log, "detail": ck, "wrong_control": ck.get("wrong_control")},
                )
            from remedy.core.computer.elements import (
                label_matches_query,
                parse_click_landed,
            )

            landed = parse_click_landed(
                str(ck.get("message") or ""),
                str(ck.get("detail") or ""),
            )
            landed_name = str(landed.get("name") or "").strip()
            if landed_name and not label_matches_query(landed_name, click):
                return public_result(
                    ok=False,
                    target="browser",
                    action="act",
                    message=(
                        f"act: click for {click!r} landed on {landed_name!r} "
                        f"({landed.get('tag') or '?'}) — that is not the control. "
                        "computer_snapshot, then click by ref= of the field or "
                        "the Post/Submit *button* (not a nav link). Nothing was typed."
                    ),
                    extra={
                        "steps": log,
                        "detail": ck,
                        "landed": landed,
                        "wrong_control": True,
                    },
                )
            time.sleep(0.25)
            return None

        def _act_type() -> dict[str, Any] | None:
            nonlocal type_text
            had_vault = "{{" in type_text
            typed_reported = "a stored secret" if had_vault else f"{len(type_text)} chars"
            type_query = field_locator(click)
            if had_vault and not type_query:
                return public_result(
                    ok=False,
                    target="browser",
                    action="act",
                    message=(
                        "Vault secrets only type into a named field. Pass click= "
                        "the visible label (or computer_type with ref=/query=) so "
                        "the value lands in that control, not whatever currently "
                        "has focus."
                    ),
                    extra={"steps": log, "needs": "ref"},
                )
            if had_vault:
                editable = self._browser_vault_target_editable(query=type_query)
                if editable is not True:
                    return public_result(
                        ok=False,
                        target="browser",
                        action="act",
                        message=(
                            "Vault secrets only type into an editable field. "
                            "That control is not an input — will not type the secret."
                        ),
                        extra={"steps": log, "needs": "ref"},
                    )
            type_text, vault_err = self._expand_vault_text(
                type_text,
                action="act",
                target="browser",
            )
            if vault_err is not None:
                vault_err["steps"] = log
                return vault_err
            if not click and any(
                w in (goal + " " + type_text).lower()
                for w in ("email", "user", "login", "@")
            ):
                for label in ("email", "email or phone", "username", "phone or email"):
                    pre = self._browser_click_text(label, payload)
                    if pre.get("ok"):
                        log.append(f"focus:{label}")
                        type_query = field_locator(label) or type_query
                        break
            type_payload: dict[str, Any] = {
                "ui": {"open_browser": True},
                "text": type_text,
            }
            if type_query:
                type_payload["query"] = type_query
            job = self._enqueue(
                "type",
                type_payload,
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
            return None

        if type_before_click:
            log.append("order:type-then-Post")
            fail = _act_type()
            if fail is not None:
                return fail
            fail = _act_click()
            if fail is not None:
                return fail
        else:
            if click:
                fail = _act_click()
                if fail is not None:
                    return fail
            if type_text:
                fail = _act_type()
                if fail is not None:
                    return fail

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
            pre=pre_state,
            expect=expect,
            acted=mutating,
            click=click,
            typed=bool(type_text),
            typed_text=type_text,
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
        click: str = "",
        typed: bool = False,
        typed_text: str = "",
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
        # Field-prompt clicks ("What's happening?", Title, Body) must stay on
        # the same path. Navigating to GIF search / app-store after "click the
        # composer then type" is a miss, even when the caller passed no expect=.
        if typed and click:
            from remedy.core.computer.elements import (
                looks_like_field_prompt,
                urls_path_diverged,
            )

            if looks_like_field_prompt(click) and pre and pre.get("ok"):
                if urls_path_diverged(str(pre.get("url") or ""), str(post.get("url") or "")):
                    extra["verified"] = False
                    return extra, (
                        f"act ran but left the form: clicked {click!r} then typed, "
                        f"and the URL moved from {pre.get('url')!r} to "
                        f"{observed.get('url')!r} ({observed.get('title')!r}). "
                        "That click did not focus the field. Snapshot, click the "
                        "textarea/input by ref=, then type."
                    )
        from remedy.core.computer.elements import (
            detect_modal_obstacle,
            draft_still_on_page,
            is_compose_url,
            looks_like_publish_verb,
        )

        modal = detect_modal_obstacle(
            url=str(post.get("url") or ""),
            title=str(post.get("title") or ""),
            text=str(post.get("text_head") or ""),
        )
        if modal:
            extra["modal"] = modal
            extra["verified"] = False
            return extra, (
                f"act ran but a dialog/popup is in front ({modal.get('kind')}: "
                f"{modal.get('detail')}). {modal.get('hint')} "
                "Do not claim the post/submit succeeded."
            )
        if looks_like_publish_verb(click):
            hay = str(post.get("text_head") or "")
            if typed_text and draft_still_on_page(typed_text, hay):
                extra["verified"] = False
                extra["unverified"] = True
                return extra, (
                    "Post/Submit was clicked but the draft is still on the form — "
                    "it did not go out. Snapshot for a blocking modal or the real "
                    "Post button (not a nav link)."
                )
            title_l = str(post.get("title") or "").lower()
            if is_compose_url(str(post.get("url") or "")) or "compose" in title_l:
                extra["verified"] = False
                extra["unverified"] = True
                return extra, (
                    "Post/Submit was clicked but you are still on the compose form "
                    f"({observed.get('url')}). That is not proof the post is live. "
                    "computer_page_text / snapshot — if the timeline post is "
                    "visible, continue; if Post is still there, the click missed."
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
        # Dedicated action so the desktop host can claim page_text.
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
        """Open URL in Browser rail without waiting the full host timeout.

        Strategy:
        1. Enqueue + ui_command (Desktop poller drives WebView).
        2. Wait up to ~0.9s for host complete (normal path is 50–300ms).
        3. If host is alive and still pending → return immediately with
           observed=false / pending_load (host will still open the page).
           Never burn 8–14s on open-url; never claim SUCCESS unseen.
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
            tres = dict(twin.result)
            # Unobserved optimistic opens are not a loaded page.
            if not tres.get("pending_load") and tres.get("observed") is not False:
                tres["reconciled"] = True
                tres["job_id"] = job.id
                return _nav_ok(tres, optimistic=False)

        # Desktop is alive → return immediately so open-url stays instant.
        # Do not say SUCCESS / observed: the page has not been seen yet.
        # Type/click still wait for settle. Never complete after Stop.
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
                    f"Opening {url} in the rail — I have not seen the page yet. "
                    "Do NOT say the rail failed. Do NOT open system browser. "
                    "Do NOT web_fetch this page. "
                    "Before typing or clicking, run computer_snapshot or "
                    "computer_page_text to confirm the form is visible."
                ),
                "url": url,
                "via": "optimistic",
                "observed": False,
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
