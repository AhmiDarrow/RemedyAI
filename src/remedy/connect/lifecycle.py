"""Serve lifecycle for the Grove Connect gateway. Default OFF."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from remedy.connect.server import (
    drop_all_sessions as _drop,
)
from remedy.connect.server import (
    drop_sessions_for_device as _drop_one,
)
from remedy.connect.server import (
    listening_addr,
    start_connect_server,
    stop_connect_server,
)

logger = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_saved: dict[str, Any] = {
    "app": None,
    "api_key": "",
    "sidecar_port": 7400,
}

# The one config dict the running gateway reads per request. Mutated in place
# (never rebound) so the listener thread's reference stays current after a
# Settings change that does not require a re-bind (panes, relay, rdv).
_live_cfg: dict[str, Any] = {}


_RESTART_KEYS = ("connect_relay_url", "connect_rdv_enabled", "connect_allow_ipv6")


def _publish_config(config: dict[str, Any] | None) -> None:
    cfg = config if isinstance(config, dict) else {}
    _live_cfg.clear()
    _live_cfg.update(cfg)


def current_config() -> dict[str, Any]:
    """Live Connect config as the gateway sees it (read-only for callers)."""
    return _live_cfg


def connect_listening_addr() -> tuple[str, int] | None:
    return listening_addr()


def drop_all_sessions() -> None:
    """Pause: drop live phone sockets. :7400 is untouched."""
    _drop()


def drop_sessions_for_device(device_id: str) -> None:
    """Revoke: drop sockets for one paired device. :7400 is untouched."""
    _drop_one(device_id)


def _enabled_chosen(config: dict[str, Any] | None) -> tuple[bool, str, int]:
    if not isinstance(config, dict):
        return False, "", 7401
    if not bool(config.get("connect_enabled")):
        return False, "", 7401
    host = str(config.get("connect_bind_host") or "").strip()
    try:
        raw_port = config.get("connect_bind_port")
        port = 7401 if raw_port is None or raw_port == "" else int(raw_port)
    except (TypeError, ValueError):
        port = 7401
    if port < 0 or port > 65535:
        port = 7401
    if not host:
        return False, "", port
    try:
        from remedy.connect.bind import (
            assert_chosen_bind,
            is_chosen_ipv4,
            is_wildcard_bind,
            reachable_lan_host,
        )

        if is_wildcard_bind(host):
            return False, host, port
        if not is_chosen_ipv4(host):
            return False, host, port
        assert_chosen_bind(host)
        # Heal a stale/loopback/virtual-NAT bind (WSL/Docker/Hyper-V) to the
        # address a phone on the LAN can reach, so the listener is reachable.
        healed = reachable_lan_host(host)
        if healed and healed != host and is_chosen_ipv4(healed):
            host = healed
    except ImportError:
        # Sibling bind.py not imported yet — refuse rather than bind wildcard.
        if host in ("0.0.0.0", "::", "[::]", "*"):
            return False, host, port
        # Tests bind 127.0.0.1; treat a concrete IPv4 as chosen when bind is absent.
        parts = host.split(".")
        if len(parts) != 4 or host.startswith("0."):
            return False, host, port
    except Exception:
        logger.warning("connect bind refused")
        return False, host, port
    return True, host, port


def _thread_main(
    host: str,
    port: int,
    api_key: str,
    sidecar_port: int,
    config: dict[str, Any],
    ready: threading.Event,
    err: list[BaseException],
) -> None:
    global _loop
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)

    # A stray task exception on the gateway loop must never surface as an
    # unraisable during a phone disconnect — log it and keep serving.
    def _on_loop_error(_loop: object, context: dict[str, object]) -> None:
        exc = context.get("exception")
        msg = context.get("message") or "connect loop error"
        logger.warning("connect loop handler: %s (%s)", msg, exc)

    loop.set_exception_handler(_on_loop_error)

    ts_host = ""
    try:
        from remedy.connect.bind import tailscale_ipv4

        ts_host = tailscale_ipv4()
    except Exception:
        ts_host = ""

    async def _boot() -> None:
        await start_connect_server(
            host,
            port,
            sidecar_port=sidecar_port,
            api_key=api_key,
            config=config,
            tailscale_host=ts_host,
        )

    try:
        loop.run_until_complete(_boot())
        ready.set()
        loop.run_forever()
    except BaseException as exc:
        err.append(exc)
        ready.set()
    finally:
        with contextlib.suppress(Exception):
            loop.run_until_complete(stop_connect_server())
        # Defensive drain: the gateway loop owns no unrelated application
        # tasks, so nothing may survive into loop.close().  This catches a
        # transport/helper task created immediately before shutdown.
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            with contextlib.suppress(Exception):
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        if _loop is loop:
            _loop = None


def stop_connect() -> None:
    """Stop the Connect listener and drop sessions."""
    global _thread, _loop
    with _lock:
        loop = _loop
        thread = _thread
    if loop is not None:
        try:

            async def _stop() -> None:
                await stop_connect_server()
                loop.stop()

            fut = asyncio.run_coroutine_threadsafe(_stop(), loop)
            fut.result(timeout=5)
        except Exception:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(loop.stop)
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=5)
    with _lock:
        # Do not lose ownership of a thread that did not finish within the
        # bounded join.  A later stop can still finish it instead of starting
        # a second gateway beside it.
        if _thread is thread and (thread is None or not thread.is_alive()):
            _thread = None
    drop_all_sessions()


def maybe_start_connect(
    app: Any,
    config: dict[str, Any] | None,
    *,
    api_key: str,
    sidecar_port: int,
) -> None:
    """Start the Connect listener only when enabled with a chosen IPv4.

    Missing / default-off config is a no-op. Never changes the :7400 bind.
    """
    global _thread
    _saved["app"] = app
    _saved["api_key"] = str(api_key or "")
    _saved["sidecar_port"] = int(sidecar_port or 7400)
    _publish_config(config)
    ok, host, port = _enabled_chosen(config)
    if not ok:
        return
    addr = listening_addr()
    if addr is not None and addr[0] == host and (port == 0 or addr[1] == port):
        # Same bind: the gateway keeps running and reads panes/relay from the
        # live config published above, so a Settings change applies now.
        return
    stop_connect()
    with _lock:
        existing = _thread
    if existing is not None and existing.is_alive():
        logger.warning("connect gateway restart deferred until prior listener stops")
        return
    ready = threading.Event()
    err: list[BaseException] = []
    thread = threading.Thread(
        target=_thread_main,
        name="remedy-connect",
        args=(host, port, str(api_key or ""), int(sidecar_port or 7400), _live_cfg, ready, err),
        daemon=True,
    )
    with _lock:
        _thread = thread
    thread.start()
    if not ready.wait(timeout=5):
        logger.warning("connect gateway start timed out")
        return
    if err:
        logger.warning("connect gateway failed to start: %s", err[0])
        return
    if app is not None:
        with contextlib.suppress(Exception):
            app.state.connect_bind = listening_addr()


def on_connect_settings_changed(config: dict[str, Any] | None) -> None:
    """Hot-apply pause / enable from settings. Drops sockets on pause."""
    from remedy.connect.store import set_paused

    cfg = config if isinstance(config, dict) else {}
    paused = bool(cfg.get("connect_paused", False))
    set_paused(paused)
    if paused:
        drop_all_sessions()
    if not bool(cfg.get("connect_enabled")):
        stop_connect()
        return
    # Listener-shaped keys (relay dial, rendezvous, IPv6 second socket) are
    # wired at start; panes apply live, these need a re-bind.
    for key in _RESTART_KEYS:
        if listening_addr() is not None and _live_cfg.get(key) != cfg.get(key):
            stop_connect()
            break
    maybe_start_connect(
        _saved.get("app"),
        cfg,
        api_key=str(_saved.get("api_key") or ""),
        sidecar_port=int(_saved.get("sidecar_port") or 7400),
    )
