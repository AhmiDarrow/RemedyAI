"""In-process computer host for CLI chat/serve when Desktop is not running.

Desktop Tauri owns the real Browser rail. Without it, computer tools still need
a live poller so ``host_connected`` stays true and navigate/open jobs complete.

This host:
  * marks the local :class:`ComputerHostBridge` as poller-alive
  * claims pending jobs and completes them with best-effort OS actions
  * opens navigates in the system browser (CLI substitute for the rail)
  * falls back to desktop UIA/Win32 for snapshot/screenshot/click where possible
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from remedy.home import default_home

logger = logging.getLogger(__name__)


class LocalComputerHost:
    """Daemon thread that drives the filesystem job queue for CLI computer-use."""

    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home_dir = home_dir
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mode = "cli"
        self.jobs_completed = 0
        self.last_action: str = ""
        self.last_error: str = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="remedy-cli-computer-host",
            daemon=True,
        )
        self._thread.start()
        # Brief wait so first mark_host_alive lands before tools run
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                from remedy.core.computer.host_bridge import get_host_bridge

                if get_host_bridge(self.home_dir).host_connected():
                    return True
            except Exception:
                pass
            time.sleep(0.05)
        return self.running

    def stop(self, *, timeout: float = 2.0, force: bool = False) -> bool:
        """Stop the worker. Returns True once it has actually gone.

        A worker that outlives its join is still holding the filesystem job
        queue. Dropping the handle then would make ``running`` lie and let the
        next ``start()`` clear the stop flag and put a *second* worker on that
        same queue, so by default the handle is kept and this returns False.

        ``force=True`` abandons it anyway — the original behaviour, kept as the
        escape hatch for a wedged worker that will never return.
        """
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
            if t.is_alive() and not force:
                return False
        self._thread = None
        return True

    def status(self) -> dict[str, Any]:
        from remedy.core.computer.host_bridge import get_host_bridge

        bridge = get_host_bridge(self.home_dir)
        return {
            "mode": self._mode,
            "running": self.running,
            "host_connected": bridge.host_connected(),
            "pending_jobs": bridge.pending_count(),
            "jobs_completed": self.jobs_completed,
            "last_action": self.last_action,
            "last_error": self.last_error,
            "home": str(
                Path(self.home_dir).expanduser()
                if self.home_dir
                else default_home()
            ),
        }

    def _loop(self) -> None:
        from remedy.core.computer.host_bridge import get_host_bridge

        bridge = get_host_bridge(self.home_dir)
        logger.info("CLI computer host started (home=%s)", bridge.root)
        while not self._stop.is_set():
            try:
                if bridge.host_driver() == "rust":
                    logger.info("CLI computer host yielding to Desktop")
                    break
                bridge.mark_host_alive(poller=True, driver="cli")
                # Drain UI commands (Desktop opens rail; CLI just clears + may open URL)
                cmd = bridge.take_ui_command()
                if cmd and isinstance(cmd, dict):
                    self._handle_ui(bridge, cmd)
                job = bridge.claim_next()
                if job is not None:
                    self._handle_job(bridge, job)
                else:
                    self._stop.wait(0.08)
            except Exception as exc:
                self.last_error = str(exc)
                logger.debug("CLI computer host tick failed: %s", exc, exc_info=True)
                self._stop.wait(0.25)
        logger.info("CLI computer host stopped")

    def _handle_ui(self, bridge: Any, cmd: dict[str, Any]) -> None:
        action = str(cmd.get("action") or cmd.get("job_action") or "").lower()
        url = str(cmd.get("url") or "").strip()
        job_id = cmd.get("job_id")
        if action in ("open_browser", "navigate") and url:
            try:
                from remedy.core.computer.desktop_os import native

                win = native()
                from remedy.core.computer.router import is_valid_navigate_url, normalize_url

                cleaned = normalize_url(url)
                if not cleaned or not is_valid_navigate_url(cleaned):
                    self.last_error = f"ui open refused invalid URL: {url[:80]!r}"
                    return
                win.open_url(cleaned)
                self.last_action = f"ui_open:{cleaned[:80]}"
            except Exception as exc:
                self.last_error = f"ui open failed: {exc}"
        # If ui_command carried a job_id and job is still pending, leave it for claim_next
        # (navigate jobs are also enqueued separately).
        _ = job_id

    def _handle_job(self, bridge: Any, job: Any) -> None:
        act = str(job.action or "").lower()
        payload = dict(job.payload or {})
        self.last_action = act
        try:
            result = self._run_action(act, payload, bridge)
            ok = bool(result.get("ok", True))
            bridge.complete(
                job.id,
                ok=ok,
                result=result,
                error=None if ok else str(result.get("message") or "failed"),
            )
            if ok:
                self.jobs_completed += 1
                self.last_error = ""
            else:
                self.last_error = str(result.get("message") or "failed")
        except Exception as exc:
            self.last_error = str(exc)
            bridge.complete(
                job.id,
                ok=False,
                result={
                    "ok": False,
                    "target": "cli",
                    "action": act,
                    "message": f"CLI host error: {exc}",
                },
                error=str(exc),
            )

    def _run_action(
        self, act: str, payload: dict[str, Any], bridge: Any
    ) -> dict[str, Any]:
        from remedy.core.computer.desktop_os import native

        win = native()
        from remedy.core.computer.router import is_valid_navigate_url, normalize_url
        from remedy.core.computer.types import public_result

        if act in ("navigate", "open"):
            raw = str(payload.get("url") or "")
            url = normalize_url(raw)
            if not url or not is_valid_navigate_url(url):
                return public_result(
                    ok=False,
                    target="cli",
                    action="navigate",
                    message=f"Invalid URL for CLI host: {raw[:100]!r}",
                )
            info = win.open_url(url)
            bridge.mark_navigated(url, optimistic=False)
            return public_result(
                ok=True,
                target="cli",
                action="navigate",
                message=(
                    f"CLI host opened system browser: {url}. "
                    "(In-app Browser rail requires Remedy Desktop.)"
                ),
                extra={**info, "via": "cli_host", "url": url, "ready_for_input": True},
            )

        if act == "ready":
            return public_result(
                ok=True,
                target="cli",
                action="ready",
                message="CLI host ready",
                extra={"via": "cli_host"},
            )

        if act == "screenshot":
            info = win.screenshot_png()
            return public_result(
                ok=True,
                target="desktop",
                action="screenshot",
                message=f"CLI host desktop screenshot ({info.get('width')}x{info.get('height')})",
                extra={**info, "via": "cli_host"},
            )

        if act in ("snapshot", "a11y"):
            elements = win.desktop_snapshot(
                limit=int(payload.get("limit") or 40),
                mode=str(payload.get("mode") or "auto"),
            )
            bridge.set_last_elements(elements, target="desktop")
            return public_result(
                ok=True,
                target="desktop",
                action="snapshot",
                message=(
                    f"CLI host desktop snapshot: {len(elements)} elements "
                    "(no Browser rail DOM without Desktop)"
                ),
                extra={"elements": elements, "via": "cli_host"},
            )

        if act == "page_text":
            return public_result(
                ok=False,
                target="cli",
                action="page_text",
                message=(
                    "page_text needs Browser rail DOM — start Remedy Desktop, "
                    "or use computer_snapshot / desktop tools from CLI."
                ),
                extra={"via": "cli_host"},
            )

        if act in ("click", "type", "key", "scroll", "drag"):
            # Best-effort: prefer desktop coords/refs if present
            if act == "click":
                ref = str(payload.get("ref") or "").strip()
                text_q = str(payload.get("text") or "").strip()
                x, y = payload.get("x"), payload.get("y")
                if ref:
                    el = bridge.get_element_by_ref(ref)
                    if el is None:
                        elements = win.desktop_snapshot(limit=60, mode="auto")
                        bridge.set_last_elements(elements, target="desktop")
                        el = bridge.get_element_by_ref(ref)
                    if el is not None:
                        win.click_element(
                            el,
                            button=str(payload.get("button") or "left"),
                            clicks=int(payload.get("clicks") or 1),
                        )
                        return public_result(
                            ok=True,
                            target="desktop",
                            action="click",
                            message=f"CLI host clicked ref={ref}",
                            extra={"ref": ref, "via": "cli_host"},
                        )
                if text_q:
                    elements = win.desktop_snapshot(limit=60, mode="auto")
                    bridge.set_last_elements(elements, target="desktop")
                    from remedy.core.computer.elements import find_best_element

                    el = find_best_element(elements, text_q)
                    if el is not None:
                        win.click_element(el)
                        return public_result(
                            ok=True,
                            target="desktop",
                            action="click",
                            message=f"CLI host clicked text={text_q!r}",
                            extra={"text": text_q, "ref": el.get("ref"), "via": "cli_host"},
                        )
                if x is not None and y is not None:
                    win.click(
                        int(float(x)),
                        int(float(y)),
                        button=str(payload.get("button") or "left"),
                        clicks=int(payload.get("clicks") or 1),
                    )
                    return public_result(
                        ok=True,
                        target="desktop",
                        action="click",
                        message=f"CLI host clicked ({x},{y})",
                        extra={"x": x, "y": y, "via": "cli_host"},
                    )
            if act == "type":
                text = str(payload.get("text") or payload.get("content") or "")
                if text:
                    win.type_text(text)
                    return public_result(
                        ok=True,
                        target="desktop",
                        action="type",
                        message=f"CLI host typed {len(text)} chars",
                        extra={"via": "cli_host"},
                    )
            if act == "key":
                key = str(payload.get("key") or payload.get("keys") or "")
                if key:
                    win.press_key(key)
                    return public_result(
                        ok=True,
                        target="desktop",
                        action="key",
                        message=f"CLI host pressed {key}",
                        extra={"via": "cli_host"},
                    )
            return public_result(
                ok=False,
                target="cli",
                action=act,
                message=(
                    f"CLI host could not complete {act} — need coords/ref/text "
                    "or start Remedy Desktop for Browser rail controls."
                ),
                extra={"via": "cli_host"},
            )

        # Unknown / compound rail actions need Desktop — do not claim success.
        return public_result(
            ok=False,
            target="cli",
            action=act,
            message=(
                f"CLI host cannot complete {act}. Start Remedy Desktop for "
                "Browser rail controls (fill/select/press_hold/act/hover)."
            ),
            extra={"via": "cli_host", "payload_keys": list(payload.keys())[:12]},
        )


_local_host: LocalComputerHost | None = None
_local_lock = threading.Lock()


def get_local_computer_host(
    home_dir: Path | str | None = None,
) -> LocalComputerHost:
    global _local_host
    with _local_lock:
        if _local_host is None:
            _local_host = LocalComputerHost(home_dir=home_dir)
        elif home_dir is not None and _local_host.home_dir is None:
            _local_host.home_dir = home_dir
        return _local_host


def start_cli_computer_host(home_dir: Path | str | None = None) -> LocalComputerHost:
    from remedy.core.computer.host_bridge import get_host_bridge

    host = get_local_computer_host(home_dir)
    if get_host_bridge(home_dir).host_driver() == "rust":
        logger.info("CLI computer host not started — Desktop owns jobs/next")
        host.last_error = "desktop_owns_host"
        return host
    host.last_error = ""
    host.start()
    return host


def stop_cli_computer_host(*, timeout: float = 2.0, force: bool = False) -> bool:
    """Stop the shared host. Returns True once the worker has actually gone.

    The singleton itself is deliberately kept: ``LocalComputerHost`` restarts
    cleanly (``start`` clears the stop flag and makes a fresh thread), and
    holding it keeps ``status()`` — jobs completed, last action, last error —
    answerable after shutdown. The ``global`` that used to sit here assigned
    nothing.
    """
    with _local_lock:
        if _local_host is None:
            return True
        return _local_host.stop(timeout=timeout, force=force)
