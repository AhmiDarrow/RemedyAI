"""TCP listener for Grove Connect on a chosen IPv4 (never 0.0.0.0)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any

from remedy.connect.store import is_paused

logger = logging.getLogger(__name__)

HANDSHAKE_RATE = 10
HANDSHAKE_WINDOW_S = 60.0

_writers: dict[Any, str] = {}
# Loop each writer's transport belongs to; closes from another thread must
# hop onto it (asyncio transports are not thread-safe).
_writer_loops: dict[Any, asyncio.AbstractEventLoop] = {}
_writers_lock = threading.Lock()
_rate: dict[str, deque[float]] = defaultdict(deque)
_server: asyncio.AbstractServer | None = None
_extra_servers: list[asyncio.AbstractServer] = []
_bind: tuple[str, int] | None = None
_supervisor_task: asyncio.Task[Any] | None = None
_rdv_task: asyncio.Task[Any] | None = None
_mdns_stop: Any = None


def live_writers() -> set[Any]:
    with _writers_lock:
        return set(_writers)


def listening_addr() -> tuple[str, int] | None:
    return _bind


def register_writer(writer: Any, device_id: str = "") -> None:
    loop: asyncio.AbstractEventLoop | None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    with _writers_lock:
        _writers[writer] = str(device_id or "")
        if loop is not None:
            _writer_loops[writer] = loop


def bind_writer_device(writer: Any, device_id: str) -> None:
    with _writers_lock:
        if writer in _writers:
            _writers[writer] = str(device_id or "").strip()


def writer_device(writer: Any) -> str:
    with _writers_lock:
        return str(_writers.get(writer) or "")


def unregister_writer(writer: Any) -> None:
    with _writers_lock:
        _writers.pop(writer, None)
        _writer_loops.pop(writer, None)


def _close_writer(writer: Any) -> None:
    """Close on the owning loop; direct when already there (or loop unknown)."""
    with _writers_lock:
        loop = _writer_loops.get(writer)
    on_loop = False
    if loop is not None:
        try:
            on_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_loop = False
    if loop is not None and not on_loop and not loop.is_closed():
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(lambda: _safe_close(writer))
        return
    _safe_close(writer)


def _safe_close(writer: Any) -> None:
    with contextlib.suppress(Exception):
        writer.close()


def drop_sessions_for_device(device_id: str) -> None:
    """Close sockets bound to *device_id*. Unmapped sockets fail closed."""
    want = str(device_id or "").strip()
    if not want:
        drop_all_sessions()
        return
    with _writers_lock:
        items = list(_writers.items())
    mapped = any(str(did or "").strip() for _w, did in items)
    if not mapped:
        drop_all_sessions()
        return
    for writer, did in items:
        did_s = str(did or "").strip()
        if did_s and did_s != want:
            continue
        _close_writer(writer)
        with _writers_lock:
            _writers.pop(writer, None)
            _writer_loops.pop(writer, None)


def drop_all_sessions() -> None:
    """Close every live Connect socket (pause)."""
    with _writers_lock:
        items = list(_writers)
    for writer in items:
        _close_writer(writer)
    with _writers_lock:
        _writers.clear()
        _writer_loops.clear()


def _peer_ip(writer: Any) -> str:
    try:
        peer = writer.get_extra_info("peername")
    except Exception:
        peer = None
    if isinstance(peer, tuple) and peer:
        return str(peer[0])
    return "?"


def _rate_allow(ip: str) -> bool:
    now = time.monotonic()
    bucket = _rate[ip]
    while bucket and (now - bucket[0]) > HANDSHAKE_WINDOW_S:
        bucket.popleft()
    if len(bucket) >= HANDSHAKE_RATE:
        return False
    bucket.append(now)
    return True


def _assert_bind(host: str) -> None:
    from remedy.connect.bind import assert_chosen_bind, is_chosen_ipv4, is_wildcard_bind

    h = str(host or "").strip()
    if not h or is_wildcard_bind(h) or not is_chosen_ipv4(h):
        raise ValueError("connect bind must be a chosen IPv4, not wildcard")
    assert_chosen_bind(h)


def _load_host_kp() -> Any:
    from remedy.connect.keys import load_or_create_host_keypair

    return load_or_create_host_keypair()


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any],
    skip_rate: bool = False,
) -> None:
    register_writer(writer)
    try:
        if is_paused():
            return
        ip = _peer_ip(writer)
        # Only skip rate limits on *outbound* relay dials (same relay IP for every
        # device). LAN still rate-limits even when a relay URL is configured.
        if not skip_rate and not _rate_allow(ip):
            logger.warning("connect handshake rate-limited")
            return
        from remedy.connect.pipe import session_loop
        from remedy.connect.store import is_revoked_live

        def _should_stop() -> bool:
            # Checked before every record: pause, or a revoke that landed while
            # this socket was mid-session (the close itself is dispatched onto
            # this loop; this is the belt to that suspender).
            if is_paused():
                return True
            did = writer_device(writer)
            return bool(did) and is_revoked_live(did)

        await session_loop(
            reader,
            writer,
            host_kp=_load_host_kp(),
            sidecar_port=sidecar_port,
            api_key=api_key,
            config=config,
            should_stop=_should_stop,
            on_device=lambda rec: bind_writer_device(writer, str((rec or {}).get("id") or "")),
            # skip_rate is only set for outbound relay / rendezvous dials: the
            # same sessions a third party can inject junk records into.
            lenient_decrypt=skip_rate,
        )
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError, OSError):
        return
    except Exception:
        logger.warning("connect session ended", exc_info=True)
    finally:
        unregister_writer(writer)
        try:
            writer.close()
            wait = getattr(writer, "wait_closed", None)
            if callable(wait):
                await wait()
        except Exception:
            pass


async def start_connect_server(
    host: str,
    port: int,
    *,
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any] | None = None,
    tailscale_host: str = "",
) -> asyncio.AbstractServer:
    """Bind chosen IPv4:port. ``port=0`` picks an ephemeral port (tests)."""
    global _server, _bind, _supervisor_task, _rdv_task
    _assert_bind(host)
    # A restart must never overwrite the only references to the old listener
    # or its background dialers.  Normal lifecycle callers stop first, but
    # this guard also makes direct/API-driven restarts safe and idempotent.
    if _server is not None or _supervisor_task is not None or _rdv_task is not None:
        await stop_connect_server()
    # Keep the caller's dict (lifecycle mutates it in place on Settings
    # changes) so pane flags read per request are the live ones.
    cfg = config if isinstance(config, dict) else {}
    side = int(sidecar_port or 7400)
    key = str(api_key or "")

    async def _cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle(
            reader,
            writer,
            sidecar_port=side,
            api_key=key,
            config=cfg,
        )

    server = await asyncio.start_server(_cb, host=host, port=int(port), reuse_address=True)
    socks: list[Any] = list(server.sockets or [])
    if not socks:
        server.close()
        raise RuntimeError("connect server has no sockets")
    addr = socks[0].getsockname()
    _server = server
    bound_port = int(addr[1])
    bound_host = str(addr[0])
    _bind = (bound_host, bound_port)
    logger.info("connect gateway listening")
    _start_mdns(bound_host, bound_port)
    if bool(cfg.get("connect_allow_ipv6")):
        await _maybe_listen_v6(_cb, bound_port)
    ts_host = str(tailscale_host or "").strip()
    if ts_host and ts_host != bound_host:
        await _maybe_listen_tailscale(_cb, bound_port, ts_host)
    raw_relay = str(cfg.get("connect_relay_url") or "").strip()
    if raw_relay:
        try:
            from remedy.connect.relay_client import relay_configured

            relay_configured(cfg)
        except ValueError:
            logger.warning("connect relay URL refused")
        else:
            _supervisor_task = asyncio.create_task(
                _relay_supervisor(cfg, sidecar_port=side, api_key=key),
                name="connect-relay-supervisor",
            )
    if bool(cfg.get("connect_rdv_enabled", True)):
        _rdv_task = asyncio.create_task(
            _rdv_supervisor(sidecar_port=side, api_key=key, config=cfg),
            name="connect-rdv-supervisor",
        )
    return server


def _start_mdns(bound_host: str, bound_port: int) -> None:
    """Advertise ``_remedy-connect._udp`` (host-pub hash only)."""
    global _mdns_stop
    _stop_mdns()
    try:
        from remedy.connect.mdns import start_advertiser

        host_pub = bytes(_load_host_kp().public)
        _mdns_stop = start_advertiser(bound_host, bound_port, host_pub)
    except Exception:
        logger.debug("connect mDNS skipped", exc_info=True)
        _mdns_stop = None


def _stop_mdns() -> None:
    global _mdns_stop
    stop = _mdns_stop
    _mdns_stop = None
    if callable(stop):
        with contextlib.suppress(Exception):
            stop()


async def stop_connect_server() -> None:
    global _server, _bind, _supervisor_task, _rdv_task
    _stop_mdns()
    drop_all_sessions()
    task = _supervisor_task
    _supervisor_task = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    rdv = _rdv_task
    _rdv_task = None
    if rdv is not None:
        rdv.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await rdv
    for extra in list(_extra_servers):
        extra.close()
        with contextlib.suppress(Exception):
            await extra.wait_closed()
    _extra_servers.clear()
    server = _server
    _server = None
    _bind = None
    if server is None:
        return
    server.close()
    with contextlib.suppress(Exception):
        await server.wait_closed()


async def _maybe_listen_v6(cb: Any, port: int) -> None:
    """Optional second listener on a global IPv6, same port. Never ``::``."""
    try:
        from remedy.connect.bind import list_candidate_ipv6

        addrs = list_candidate_ipv6()
    except Exception:
        return
    if not addrs:
        return
    host = addrs[0]
    try:
        extra = await asyncio.start_server(cb, host=host, port=int(port), reuse_address=True)
    except OSError:
        logger.info("connect IPv6 listen skipped")
        return
    _extra_servers.append(extra)
    logger.info("connect gateway also on IPv6")


async def _maybe_listen_tailscale(cb: Any, port: int, tailscale_host: str) -> None:
    """Optional second listener on the Tailscale tailnet IPv4, same port."""
    host = str(tailscale_host or "").strip()
    if not host:
        return
    try:
        from remedy.connect.bind import is_chosen_ipv4

        if not is_chosen_ipv4(host):
            return
    except Exception:
        return
    try:
        extra = await asyncio.start_server(cb, host=host, port=int(port), reuse_address=True)
    except OSError:
        logger.info("connect Tailscale listen skipped")
        return
    _extra_servers.append(extra)
    logger.info("connect gateway also on Tailscale %s", host)


def _rendezvous_sids() -> list[bytes]:
    from remedy.connect.keys import load_or_create_host_keypair
    from remedy.connect.pair import pending_pair_rendezvous
    from remedy.connect.rendezvous import session_id_device
    from remedy.connect.store import list_devices

    out: list[bytes] = []
    pair_sid = pending_pair_rendezvous()
    if pair_sid:
        out.append(pair_sid)
    try:
        host_pub = bytes(load_or_create_host_keypair().public)
    except Exception:
        return out
    for rec in list_devices(include_revoked=False):
        hx = str(rec.get("public_hex") or "")
        try:
            pub = bytes.fromhex(hx)
        except ValueError:
            continue
        if len(pub) != 32:
            continue
        try:
            out.append(session_id_device(host_pub, pub))
        except ValueError:
            continue
    return out


async def _relay_supervisor(
    config: dict[str, Any],
    *,
    sidecar_port: int,
    api_key: str,
) -> None:
    """Keep one outbound relay waiter per pair-window and per paired device."""
    from remedy.connect.relay_client import dial_relay, relay_configured
    from remedy.connect.store import is_paused

    url = relay_configured(config)
    if not url:
        return
    live: dict[bytes, asyncio.Task[Any]] = {}

    async def _one(sid: bytes) -> None:
        backoff = 1.0
        while True:
            if is_paused():
                await asyncio.sleep(0.5)
                continue
            try:
                reader, writer = await dial_relay(url, sid, timeout=20.0)
                backoff = 1.0
                await _handle(
                    reader,
                    writer,
                    sidecar_port=sidecar_port,
                    api_key=api_key,
                    config=config,
                    skip_rate=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(15.0, backoff * 1.5)

    try:
        while True:
            wanted = set(_rendezvous_sids())
            retired: list[asyncio.Task[Any]] = []
            for sid in list(live):
                task = live[sid]
                if sid not in wanted or task.done():
                    if not task.done():
                        task.cancel()
                    retired.append(live.pop(sid))
            if retired:
                await asyncio.gather(*retired, return_exceptions=True)
            for sid in wanted:
                if sid not in live:
                    live[sid] = asyncio.create_task(_one(sid), name="connect-relay-dial")
            await asyncio.sleep(1.0)
    finally:
        # Teardown can race a closing loop (test teardown / Ctrl-C): cancel
        # is a call_soon, which raises "Event loop is closed" once the loop
        # is gone. Guard each cancel so a shutdown never logs an unraisable.
        for task in live.values():
            with contextlib.suppress(Exception):
                task.cancel()
        if live:
            with contextlib.suppress(Exception):
                await asyncio.gather(*live.values(), return_exceptions=True)


async def _rdv_supervisor(
    *,
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any],
) -> None:
    """Zero-setup rendezvous: hold a public-broker session per active sid.

    Both the PC and the phone dial *out* to a public MQTT broker (no account,
    no binary, no VPS), so a phone on mobile data can meet a NATed PC. The
    broker only ever sees the random session id and Noise ciphertext — the
    same trust model as an owner relay.
    """
    from remedy.connect.rdv import PUBLIC_RDV_ENDPOINTS, MqttSession, RendezvousSession
    from remedy.connect.store import is_paused

    live: dict[bytes, asyncio.Task[Any]] = {}

    async def _one(sid: bytes) -> None:
        backoff = 1.0
        while True:
            if is_paused():
                await asyncio.sleep(0.5)
                continue
            connected = False
            for host, port in PUBLIC_RDV_ENDPOINTS:
                mqtt: MqttSession | None = None
                session: RendezvousSession | None = None
                try:
                    mqtt = MqttSession(host, port)
                    await mqtt.connect()
                    session = RendezvousSession(mqtt, sid, role="pc")
                    reader, writer = await session.open()
                    connected = True
                    backoff = 1.0
                    await _handle(
                        reader,
                        writer,
                        sidecar_port=sidecar_port,
                        api_key=api_key,
                        config=config,
                        skip_rate=True,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
                finally:
                    # RendezvousSession owns three child tasks plus a socket
                    # pair.  Always await its teardown; merely cancelling the
                    # supervisor otherwise leaves those tasks on a closing
                    # loop and can retain the previous owner home.
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        if session is not None:
                            await session.aclose()
                        elif mqtt is not None:
                            await mqtt.aclose()
            if not connected:
                await asyncio.sleep(backoff)
                backoff = min(15.0, backoff * 1.5)

    try:
        while True:
            wanted = set(_rendezvous_sids())
            retired: list[asyncio.Task[Any]] = []
            for sid in list(live):
                task = live[sid]
                if sid not in wanted or task.done():
                    if not task.done():
                        task.cancel()
                    retired.append(live.pop(sid))
            if retired:
                await asyncio.gather(*retired, return_exceptions=True)
            for sid in wanted:
                if sid not in live:
                    live[sid] = asyncio.create_task(_one(sid), name="connect-rdv-dial")
            await asyncio.sleep(1.0)
    finally:
        # Teardown can race a closing loop (test teardown / Ctrl-C): cancel
        # is a call_soon, which raises "Event loop is closed" once the loop
        # is gone. Guard each cancel so a shutdown never logs an unraisable.
        for task in live.values():
            with contextlib.suppress(Exception):
                task.cancel()
        if live:
            with contextlib.suppress(Exception):
                await asyncio.gather(*live.values(), return_exceptions=True)
