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
        from remedy.connect.bind import assert_chosen_bind, is_chosen_ipv4, is_wildcard_bind

        if is_wildcard_bind(host):
            return False, host, port
        if not is_chosen_ipv4(host):
            return False, host, port
        assert_chosen_bind(host)
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

    async def _boot() -> None:
        await start_connect_server(
            host,
            port,
            sidecar_port=sidecar_port,
            api_key=api_key,
            config=config,
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
        loop.close()
        if _loop is loop:
            _loop = None


def stop_connect() -> None:
    """Stop the Connect listener and drop sessions."""
    global _thread, _loop
    with _lock:
        loop = _loop
        thread = _thread
        _thread = None
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
    _saved["app"] = app
    _saved["api_key"] = str(api_key or "")
    _saved["sidecar_port"] = int(sidecar_port or 7400)
    ok, host, port = _enabled_chosen(config)
    if not ok:
        return
    addr = listening_addr()
    if addr is not None and addr[0] == host and (port == 0 or addr[1] == port):
        return
    stop_connect()
    ready = threading.Event()
    err: list[BaseException] = []
    thread = threading.Thread(
        target=_thread_main,
        name="remedy-connect",
        args=(host, port, str(api_key or ""), int(sidecar_port or 7400), dict(config or {}), ready, err),
        daemon=True,
    )
    with _lock:
        global _thread
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
    maybe_start_connect(
        _saved.get("app"),
        cfg,
        api_key=str(_saved.get("api_key") or ""),
        sidecar_port=int(_saved.get("sidecar_port") or 7400),
    )
