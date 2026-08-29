"""Owner-run Grove Connect relay — splice frames, never decrypt, no wildcards."""

from __future__ import annotations

import contextlib
import importlib
import socket
import struct
import sys
from pathlib import Path

import pytest

from remedy.connect.relay import (
    assert_chosen_bind,
    start_relay,
)
from remedy.connect.relay import (
    main as relay_main,
)
from remedy.interfaces.cli.parser import build_parser


def _recvall(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf.extend(chunk)
    return bytes(buf)


def test_wildcard_bind_refused() -> None:
    for host in ("0.0.0.0", "*", "::", "[::]"):
        with pytest.raises(ValueError):
            start_relay(host, 0)
        with pytest.raises(ValueError):
            assert_chosen_bind(host)


def test_main_wildcard_exits_2() -> None:
    assert relay_main(host="0.0.0.0", port=7402) == 2
    assert relay_main(host="*", port=7402) == 2
    assert relay_main(host="::", port=7402) == 2


def test_relay_forwards_frames_equal_no_aead() -> None:
    """Two sockets: framed blobs arrive identical. No AEAD import required."""
    src = Path(__file__).resolve().parents[1] / "src" / "remedy" / "connect" / "relay.py"
    text = src.read_text(encoding="utf-8")
    assert "aead" not in text.lower()
    assert "from cryptography" not in text
    assert "nacl" not in text

    port, stop = start_relay("127.0.0.1", 0)
    a: socket.socket | None = None
    b: socket.socket | None = None
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=5)
        b = socket.create_connection(("127.0.0.1", port), timeout=5)
        a.settimeout(5)
        b.settimeout(5)
        sid = b"0123456789abcdef"
        assert len(sid) == 16
        a.sendall(sid)
        b.sendall(sid)

        payload = b"hello"
        frame = struct.pack("!I", len(payload)) + payload
        a.sendall(frame)
        assert _recvall(b, len(frame)) == frame

        blob = bytes(range(256)) + b"\x00\xff" * 64
        frame2 = struct.pack("!I", len(blob)) + blob
        b.sendall(frame2)
        assert _recvall(a, len(frame2)) == frame2
    finally:
        stop()
        for s in (a, b):
            if s is not None:
                with contextlib.suppress(OSError):
                    s.close()


def test_oversize_frame_is_dropped() -> None:
    port, stop = start_relay("127.0.0.1", 0)
    a: socket.socket | None = None
    b: socket.socket | None = None
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=5)
        b = socket.create_connection(("127.0.0.1", port), timeout=5)
        a.settimeout(2)
        b.settimeout(2)
        sid = b"abcdefghijklmnop"
        a.sendall(sid)
        b.sendall(sid)
        a.sendall(struct.pack("!I", 65537) + b"x")
        try:
            data = b.recv(16)
        except TimeoutError:
            data = b""
        assert data == b""
    finally:
        stop()
        for s in (a, b):
            if s is not None:
                with contextlib.suppress(OSError):
                    s.close()


def test_cli_parser_connect_relay() -> None:
    parser = build_parser()
    ns = parser.parse_args(["connect-relay", "--host", "127.0.0.1", "--port", "7402"])
    assert ns.command == "connect-relay"
    assert ns.host == "127.0.0.1"
    assert ns.port == 7402


def test_cli_dispatches_connect_relay(monkeypatch, tmp_path: Path) -> None:
    # The cli package re-exports `main`, which would shadow the submodule.
    M = importlib.import_module("remedy.interfaces.cli.main")

    called: dict[str, object] = {}

    def fake_main(*, host: str, port: int) -> int:
        called["host"] = host
        called["port"] = port
        return 0

    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")
    monkeypatch.setattr("remedy.connect.relay.main", fake_main)
    with pytest.raises(SystemExit) as ei:
        M.main(["connect-relay", "--host", "10.0.0.8", "--port", "7402"])
    assert ei.value.code == 0
    assert called == {"host": "10.0.0.8", "port": 7402}


def test_relay_module_does_not_import_aead() -> None:
    import remedy.connect.relay as relay

    for name in list(sys.modules):
        lower = name.lower()
        if "aead" in lower and "remedy.connect.relay" in lower:
            pytest.fail(f"relay pulled in AEAD module {name}")
    src = Path(relay.__file__).read_text(encoding="utf-8")
    assert "cryptography" not in src
    assert "nacl" not in src


def test_chosen_bind_allows_loopback() -> None:
    assert assert_chosen_bind("127.0.0.1") == "127.0.0.1"


def test_relay_stop_is_idempotent() -> None:
    _port, stop = start_relay("127.0.0.1", 0)
    stop()
    stop()
