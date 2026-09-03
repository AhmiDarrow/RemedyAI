"""Serve lifecycle for the Grove Connect gateway. Default OFF."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
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
_restart_waiter: threading.Thread | None = None
_lock = threading.Lock()
# Set by stop_connect() so a gateway loop that ends on purpose is not
# mistaken for a crash by the self-heal path below.
_stop_requested = threading.Event()
# Crash bookkeeping for the desktop server panel / GET /api/connect.
_health: dict[str, Any] = {
    "crashes": 0,
    "last_crash": "",
    "last_crash_ts": 0.0,
    "healing": False,
    # True while a gateway loop is past boot and serving phones.
    "serving": False,
}
_HEAL_DELAYS_S = (1.0, 3.0, 8.0, 15.0)
_MAX_HEALS = 8
_saved: dict[str, Any] = {
    "app": None,
    "api_key": "",
    "sidecar_port": 7400,
    "config": {},
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


def gateway_health() -> dict[str, Any]:
    """Crash / self-heal state of the Connect gateway thread (read-only)."""
    with _lock:
        thread = _thread
        snap = dict(_health)
    snap["thread_alive"] = bool(thread is not None and thread.is_alive())
    snap["listening"] = listening_addr()
    return snap


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

    crashed: BaseException | None = None
    booted = False
    try:
        loop.run_until_complete(_boot())
        booted = True
        with _lock:
            _health["serving"] = True
        ready.set()
        loop.run_forever()
    except BaseException as exc:
        err.append(exc)
        # A bind failure is reported to the caller through ``err``; only a
        # listener that was already serving phones is self-healed.
        crashed = exc if booted else None
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
        with _lock:
            _health["serving"] = False
        if _loop is loop:
            _loop = None
        if crashed is not None and not _stop_requested.is_set():
            # The listener died on its own (a phone disconnect must never do
            # this, but if it does the owner must not lose Connect until the
            # next Settings change).  The API on :7400 is untouched either
            # way; only the phone gateway is brought back.
            _note_crash(crashed)
            if int(_health.get("crashes") or 0) <= _MAX_HEALS:
                _schedule_self_heal(threading.current_thread())
            else:
                logger.critical(
                    "connect gateway crashed %s times; not restarting until Settings change",
                    _health.get("crashes"),
                )


def _note_crash(exc: BaseException) -> None:
    logger.critical("connect gateway thread crashed: %r — self-healing", exc, exc_info=exc)
    with _lock:
        _health["crashes"] = int(_health.get("crashes") or 0) + 1
        _health["last_crash"] = f"{type(exc).__name__}: {exc}"[:300]
        _health["last_crash_ts"] = time.time()


def _schedule_self_heal(dead: threading.Thread) -> None:
    """Restart the gateway after a crash, with a short backoff per crash."""
    global _restart_waiter

    def _heal() -> None:
        global _restart_waiter, _thread
        with _lock:
            n = max(1, int(_health.get("crashes") or 1))
            _health["healing"] = True
        delay = _HEAL_DELAYS_S[min(n, len(_HEAL_DELAYS_S)) - 1]
        # Wait out the delay in small steps so a stop_connect() during the
        # backoff (Settings → disable) cancels the heal instead of racing it.
        end = time.monotonic() + delay
        while time.monotonic() < end:
            if _stop_requested.is_set():
                break
            time.sleep(0.1)
        with _lock:
            if _thread is dead:
                _thread = None
            if _restart_waiter is threading.current_thread():
                _restart_waiter = None
            app = _saved.get("app")
            api_key = str(_saved.get("api_key") or "")
            sidecar_port = int(_saved.get("sidecar_port") or 7400)
            config = dict(_saved.get("config") or {})
        try:
            if _stop_requested.is_set():
                return
            ok, _host, _port = _enabled_chosen(config)
            if not ok:
                return
            try:
                maybe_start_connect(app, config, api_key=api_key, sidecar_port=sidecar_port)
            except Exception:
                logger.warning("connect gateway self-heal failed", exc_info=True)
        finally:
            with _lock:
                _health["healing"] = False

    with _lock:
        if _restart_waiter is not None and _restart_waiter.is_alive():
            return
        waiter = threading.Thread(target=_heal, name="remedy-connect-heal", daemon=True)
        _restart_waiter = waiter
        waiter.start()


def stop_connect() -> None:
    """Stop the Connect listener and drop sessions."""
    global _thread, _loop
    _stop_requested.set()
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


def _defer_restart_until_stopped(prior: threading.Thread) -> None:
    """Start the latest requested configuration once *prior* actually exits.

    A bounded join keeps Settings responsive, but losing the restart request
    after that timeout leaves Connect offline. One shared waiter follows the
    retiring owner and re-reads the latest saved config, so repeated changes
    coalesce without ever starting two listeners.
    """
    global _restart_waiter, _thread

    def _wait() -> None:
        global _restart_waiter, _thread
        prior.join()
        with _lock:
            if _thread is prior:
                _thread = None
            if _restart_waiter is threading.current_thread():
                _restart_waiter = None
            app = _saved.get("app")
            api_key = str(_saved.get("api_key") or "")
            sidecar_port = int(_saved.get("sidecar_port") or 7400)
            config = dict(_saved.get("config") or {})
        ok, _host, _port = _enabled_chosen(config)
        if not ok:
            return
        maybe_start_connect(
            app,
            config,
            api_key=api_key,
            sidecar_port=sidecar_port,
        )

    with _lock:
        if _restart_waiter is not None and _restart_waiter.is_alive():
            return
        waiter = threading.Thread(
            target=_wait,
            name="remedy-connect-restart",
            daemon=True,
        )
        _restart_waiter = waiter
        waiter.start()


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
    _saved["config"] = dict(config or {})
    _publish_config(config)
    ok, host, port = _enabled_chosen(config)
    if not ok:
        return
    with _lock:
        # An owner-driven start is a fresh slate for the crash budget, but a
        # restart that is *part of* a self-heal (directly, or through the
        # deferred-restart waiter) must not erase the crash it is recovering.
        healing = bool(_health.get("healing")) or (
            _restart_waiter is not None and _restart_waiter.is_alive()
        )
        if not healing:
            _health["crashes"] = 0
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
        _defer_restart_until_stopped(existing)
        return
    ready = threading.Event()
    err: list[BaseException] = []
    _stop_requested.clear()
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
    previous = dict(_live_cfg)
    _saved["config"] = dict(cfg)
    _publish_config(cfg)
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
        if listening_addr() is not None and previous.get(key) != cfg.get(key):
            stop_connect()
            break
    maybe_start_connect(
        _saved.get("app"),
        cfg,
        api_key=str(_saved.get("api_key") or ""),
        sidecar_port=int(_saved.get("sidecar_port") or 7400),
    )
