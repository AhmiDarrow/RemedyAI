"""Dispatch computer actions to browser host bridge or Windows desktop."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from remedy.core.computer.audit import log_computer_action
from remedy.core.computer.host_bridge import get_host_bridge
from remedy.core.computer.router import (
    ComputerTarget,
    host_label,
    looks_like_url,
    normalize_url,
    resolve_target,
    wants_system_browser,
)
from remedy.core.computer.types import ComputerAction, public_result


class ComputerExecutor:
    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home_dir = home_dir
        self.bridge = get_host_bridge(home_dir)

    def _session_id(self, runtime: Any | None) -> str | None:
        if runtime is None:
            return None
        return str(getattr(runtime, "_session_id", None) or "") or None

    def _abort_check(self) -> bool:
        try:
            from remedy.core.turn_context import is_turn_aborted

            return bool(is_turn_aborted())
        except Exception:
            return False

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
        url = kwargs.get("url")
        hint = kwargs.get("hint") or kwargs.get("reason") or ""
        # Ref-based click is browser a11y — force browser unless user set desktop
        req_target = target
        # Navigate defaults to in-app rail unless user asked for system/external browser
        if act is ComputerAction.NAVIGATE:
            if wants_system_browser(str(hint), target):
                req_target = "desktop"
            else:
                req_target = "browser"
        if act is ComputerAction.CLICK and str(kwargs.get("ref") or "").strip():
            if (target or "auto").strip().lower() in ("", "auto"):
                req_target = "browser"
        # Snapshot auto: browser if host connected / web hint, else desktop windows
        if act is ComputerAction.SNAPSHOT and (target or "auto").strip().lower() in (
            "",
            "auto",
        ):
            hint_s = str(hint)
            if self.bridge.host_connected() or looks_like_url(hint_s) or (
                "browser" in hint_s.lower() or "web" in hint_s.lower() or "page" in hint_s.lower()
                or "wiki" in hint_s.lower()
            ):
                req_target = "browser"
            else:
                req_target = "desktop"
        tgt = resolve_target(
            req_target,
            url=url,
            hint=str(hint),
            action=act.value,
        )
        try:
            if self._abort_check():
                self.bridge.cancel_pending_and_running(reason="aborted")
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

            # If Stop fired mid-action, surface abort even if partial work finished
            if self._abort_check():
                self.bridge.cancel_pending_and_running(reason="aborted")
                result = public_result(
                    ok=False,
                    target=host_label(tgt),
                    action=act.value,
                    message="Aborted by user",
                    extra={"partial": result} if result else None,
                )

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
        if act is ComputerAction.CLICK:
            if self._abort_check():
                raise RuntimeError("Aborted by user")
            ref = str(kwargs.get("ref") or "").strip()
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
            x, y = int(kwargs.get("x", 0)), int(kwargs.get("y", 0))
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
            win.type_text(text, abort_check=self._abort_check)
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
            dy = int(kwargs.get("dy") if kwargs.get("dy") is not None else -3)
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
                if not hwnd:
                    return public_result(
                        ok=False,
                        target="desktop",
                        action="windows",
                        message="hwnd required for focus",
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
            # Desktop navigate: open URL in system default browser
            url = normalize_url(str(kwargs.get("url") or ""))
            if not url:
                return public_result(
                    ok=False,
                    target="desktop",
                    action="navigate",
                    message="url required",
                )
            info = win.open_url(url)
            return public_result(
                ok=True,
                target="desktop",
                action="navigate",
                message=f"Opened system browser: {url}",
                extra=info,
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
            payload["url"] = normalize_url(str(payload["url"]))

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
        ):
            payload.setdefault("ui", {})
            if isinstance(payload.get("ui"), dict):
                payload["ui"]["open_browser"] = True

        # Non-navigate without host: screenshots can still use OS capture
        if not self.bridge.host_connected() and act is not ComputerAction.NAVIGATE:
            if act is ComputerAction.SCREENSHOT:
                r = self._run_desktop(ComputerAction.SCREENSHOT)
                r["note"] = (
                    "desktop host offline — full desktop screenshot "
                    "(start Remedy Desktop for in-rail browser shots)"
                )
                r["target"] = "desktop"
                return r
            if act is ComputerAction.WINDOWS:
                return self._run_desktop(act, **kwargs)
            if act is ComputerAction.SNAPSHOT:
                # fall through to enqueue briefly, then desktop snapshot fallback
                pass
            elif act not in (ComputerAction.NAVIGATE, ComputerAction.SNAPSHOT):
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

        # Navigate / simple actions: fail fast if poller never claims (was 45s hangs)
        unclaimed = 2.5 if act is ComputerAction.NAVIGATE else 3.5
        total_wait = float(kwargs.get("timeout_s") or (12.0 if act is ComputerAction.NAVIGATE else 30.0))
        job = self.bridge.enqueue(act.value, payload)
        finished = self.bridge.wait(
            job.id,
            timeout_s=total_wait,
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
        # Navigate fallback if host timed out / never claimed
        if act is ComputerAction.NAVIGATE and payload.get("url"):
            fb = self._run_desktop(act, **kwargs)
            fb["note"] = (
                f"in-rail browser did not pick up the job ({err}); "
                f"opened the system default browser instead. "
                f"Ensure Remedy Desktop is the feature/computer-use build "
                f"and status shows PC host."
            )
            fb["ok"] = True
            fb["rail_failed"] = True
            return fb
        # Snapshot offline → desktop window snapshot so task can continue
        if act is ComputerAction.SNAPSHOT:
            fb = self._run_desktop(act, **kwargs)
            fb["note"] = f"browser host failed ({err}); returned desktop window snapshot"
            return fb
        return public_result(
            ok=False,
            target="browser",
            action=act.value,
            message=str(err),
            extra={"job_id": job.id},
        )


_executor: ComputerExecutor | None = None
_exec_lock = threading.Lock()


def get_computer_executor(home_dir: Path | str | None = None) -> ComputerExecutor:
    global _executor
    with _exec_lock:
        if _executor is None:
            _executor = ComputerExecutor(home_dir=home_dir)
        return _executor
