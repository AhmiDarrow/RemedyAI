"""Owner-run Grove Connect relay.

TCP listen on a **chosen IPv4**. Clients send a 16-byte session id, then framed
blobs (``u32be length | rest``). Two peers that share a session id are spliced:
bytes are copied, never decrypted, never logged.

Wildcard binds (``0.0.0.0`` / ``*`` / ``::``) are refused.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable

from remedy.connect.bind import assert_chosen_bind as _assert_bind
from remedy.connect.bind import is_chosen_ipv4

log = logging.getLogger(__name__)

SESSION_ID_LEN = 16
MAX_PAYLOAD = 65536  # 64 KiB
MAX_FRAME = MAX_PAYLOAD + 4
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7402
# How long a lone peer waits for its counterpart. Tests keep this short;
# production host reconnects, so a few minutes covers a phone opening the app.
WAIT_PEER_S = 60.0


def assert_chosen_bind(host: str) -> str:
    """Refuse wildcard / non-IPv4 binds. Relays listen on a chosen IPv4 only."""
    text = _assert_bind(host)
    if not is_chosen_ipv4(text):
        raise ValueError(f"connect-relay needs a chosen IPv4, not {host!r}")
    return text


def _recvall(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return bytes(buf) if buf else None
        buf.extend(chunk)
    return bytes(buf)


class _Slot:
    __slots__ = ("lock", "peers")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.peers: list[socket.socket] = []


def _copy_frames(src: socket.socket, dst: socket.socket) -> None:
    """Copy framed blobs src → dst. Does not inspect or log ``rest``."""
    try:
        while True:
            header = _recvall(src, 4)
            if header is None or len(header) < 4:
                break
            length = struct.unpack("!I", header)[0]
            if length > MAX_PAYLOAD:
                break
            payload = b"" if length == 0 else _recvall(src, length)
            if payload is None or len(payload) != length:
                break
            try:
                dst.sendall(header + payload)
            except OSError:
                break
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)


def _handle_client(
    conn: socket.socket,
    sessions: dict[bytes, _Slot],
    sessions_lock: threading.Lock,
    wait_peer_s: float = 60.0,
) -> None:
    conn.settimeout(60.0)
    sid = _recvall(conn, SESSION_ID_LEN)
    if sid is None or len(sid) != SESSION_ID_LEN:
        conn.close()
        return
    with sessions_lock:
        slot = sessions.get(sid)
        if slot is None:
            slot = _Slot()
            sessions[sid] = slot
    peer: socket.socket | None = None
    with slot.lock:
        if len(slot.peers) >= 2:
            conn.close()
            return
        slot.peers.append(conn)
        if len(slot.peers) == 2:
            peer = slot.peers[0] if slot.peers[1] is conn else slot.peers[1]
    if peer is None:
        deadline = time.monotonic() + float(wait_peer_s)
        while time.monotonic() < deadline:
            with slot.lock:
                if len(slot.peers) == 2:
                    peer = slot.peers[0] if slot.peers[1] is conn else slot.peers[1]
                    break
            time.sleep(0.02)
        if peer is None:
            with slot.lock:
                if conn in slot.peers:
                    slot.peers.remove(conn)
            with sessions_lock:
                if sessions.get(sid) is slot and not slot.peers:
                    sessions.pop(sid, None)
            conn.close()
            return
    conn.settimeout(None)
    with contextlib.suppress(OSError):
        peer.settimeout(None)
    try:
        _copy_frames(conn, peer)
    finally:
        with contextlib.suppress(OSError):
            conn.close()
        with slot.lock:
            if conn in slot.peers:
                slot.peers.remove(conn)
        with sessions_lock:
            if sessions.get(sid) is slot and not slot.peers:
                sessions.pop(sid, None)


def _accept_loop(
    listen: socket.socket, stop: threading.Event, wait_peer_s: float
) -> None:
    sessions: dict[bytes, _Slot] = {}
    sessions_lock = threading.Lock()
    while not stop.is_set():
        try:
            conn, _addr = listen.accept()
        except TimeoutError:
            continue
        except OSError:
            if stop.is_set():
                break
            continue
        threading.Thread(
            target=_handle_client,
            args=(conn, sessions, sessions_lock, wait_peer_s),
            daemon=True,
            name="connect-relay-peer",
        ).start()


def start_relay(
    host: str, port: int = 0, *, wait_peer_s: float | None = None
) -> tuple[int, Callable[[], None]]:
    """Bind a chosen IPv4 and serve in a daemon thread. Returns ``(port, stop)``."""
    wait_s = float(WAIT_PEER_S if wait_peer_s is None else wait_peer_s)
    chosen = assert_chosen_bind(host)
    listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listen.bind((chosen, int(port)))
        listen.listen(32)
    except OSError:
        listen.close()
        raise
    listen.settimeout(0.2)
    bound = int(listen.getsockname()[1])
    stop = threading.Event()
    thread = threading.Thread(
        target=_accept_loop,
        args=(listen, stop, wait_s),
        daemon=True,
        name="connect-relay",
    )
    thread.start()
    log.info("connect-relay listening on %s:%s", chosen, bound)

    def _stop() -> None:
        stop.set()
        with contextlib.suppress(OSError):
            listen.close()
        thread.join(timeout=2.0)

    return bound, _stop


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Blocking listen until KeyboardInterrupt."""
    _bound, stop = start_relay(host, port)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop()


def main(
    argv: list[str] | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
) -> int:
    """CLI / ``python -m remedy.connect.relay`` entry. Returns a process exit code."""
    if host is None or port is None:
        parser = argparse.ArgumentParser(
            prog="remedy connect-relay",
            description=(
                "Owner-run Grove Connect relay. Forwards framed blobs between two "
                "peers that share a session id. Does not decrypt. Chosen IPv4 only."
            ),
        )
        parser.add_argument("--host", default=DEFAULT_HOST, help="Chosen IPv4 (not 0.0.0.0)")
        parser.add_argument("--port", type=int, default=DEFAULT_PORT)
        ns = parser.parse_args(argv)
        if host is None:
            host = ns.host
        if port is None:
            port = ns.port
    assert host is not None and port is not None
    try:
        bound, stop = start_relay(host, port, wait_peer_s=900.0)
    except ValueError as exc:
        print(f"connect-relay: {exc}", file=sys.stderr)
        return 2
    print(
        f"connect-relay listening on {host}:{bound} "
        "(forwards framed blobs; does not decrypt)",
        file=sys.stderr,
    )
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        stop()


if __name__ == "__main__":
    raise SystemExit(main())
