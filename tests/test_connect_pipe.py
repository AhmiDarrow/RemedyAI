"""Connect pipe: pause/revoke drop sockets; SSE is not fully buffered."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct

import pytest

from remedy.connect.lifecycle import (
    connect_listening_addr,
    drop_all_sessions,
    maybe_start_connect,
    stop_connect,
)
from remedy.connect.proxy import iter_proxy_response
from remedy.connect.store import set_paused


@pytest.fixture(autouse=True)
def _stop_gateway():
    yield
    stop_connect()
    set_paused(False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _wait_addr(timeout: float = 3.0) -> tuple[str, int]:
    deadline = asyncio.get_event_loop().time() + timeout if False else None
    _ = deadline
    import time as _t

    end = _t.monotonic() + timeout
    while _t.monotonic() < end:
        addr = connect_listening_addr()
        if addr is not None:
            return addr
        _t.sleep(0.02)
    raise AssertionError("connect gateway did not bind")


def test_pause_drops_live_socket_without_touching_7400_concept(home):
    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app

    port = _free_port()
    app = create_app(api_key="")
    maybe_start_connect(
        app,
        {
            "connect_enabled": True,
            "connect_bind_host": "127.0.0.1",
            "connect_bind_port": port,
        },
        api_key="tok-connect-test-not-a-secret",
        sidecar_port=7400,
    )
    host, bound = _wait_addr()
    sock = socket.create_connection((host, bound), timeout=2)
    sock.settimeout(2)
    try:
        set_paused(True)
        drop_all_sessions()
        try:
            data = sock.recv(1)
        except OSError:
            data = b""
        assert data == b""
    finally:
        sock.close()
    # :7400 management API is a different listener; pause does not take it down.
    client = TestClient(app)
    r = client.get("/api/status")
    assert r.status_code == 200


def test_revoke_mid_session_drops_socket(home):
    port = _free_port()
    maybe_start_connect(
        None,
        {
            "connect_enabled": True,
            "connect_bind_host": "127.0.0.1",
            "connect_bind_port": port,
        },
        api_key="tok-connect-test-not-a-secret",
        sidecar_port=7400,
    )
    host, bound = _wait_addr()
    sock = socket.create_connection((host, bound), timeout=2)
    sock.settimeout(2)
    try:
        from remedy.connect.pair import complete_pair, start_pair
        from remedy.connect.store import revoke_device

        qr = start_pair(loopback=True, bind_host="127.0.0.1", bind_port=bound)
        from remedy.connect.pair import parse_pair_secret

        device_id = complete_pair(parse_pair_secret(qr), b"\x66" * 32, "phone")
        revoke_device(device_id)
        drop_all_sessions()
        try:
            data = sock.recv(1)
        except OSError:
            data = b""
        assert data == b""
    finally:
        sock.close()


@pytest.mark.asyncio
async def test_sse_proxy_not_fully_buffered(monkeypatch):
    chunks_seen: list[int] = []

    class FakeContent:
        async def iter_chunked(self, _n: int):
            chunks_seen.append(1)
            yield b"data: 1\n\n"
            chunks_seen.append(2)
            yield b"data: 2\n\n"
            chunks_seen.append(3)
            yield b"data: 3\n\n"

    class FakeResp:
        status = 200
        reason = "OK"
        headers = {"Content-Type": "text/event-stream"}
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

    class FakeSession:
        def request(self, *_a, **_k):
            return FakeResp()

        async def close(self):
            return None

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: FakeSession())

    body_parts = 0
    async for part in iter_proxy_response(
        "GET",
        "/api/events/sessions",
        "",
        {},
        b"",
        sidecar_port=9,
        api_key="t",
    ):
        if b"data: 1" in part:
            # Upstream chunk 2/3 must not have been consumed yet (not fully buffered).
            assert chunks_seen == [1]
            body_parts += 1
        elif b"data: 2" in part:
            assert 3 not in chunks_seen
            body_parts += 1
    assert body_parts >= 1
    assert chunks_seen == [1, 2, 3]


def _inner_writes(writes: list[bytes]) -> list[tuple[int, int, int, bytes]]:
    from remedy.connect.pipe import decode_inner

    out: list[tuple[int, int, int, bytes]] = []
    for blob in writes:
        try:
            out.append(decode_inner(blob))
        except ValueError:
            continue
    return out


def _inner_res_payload(writes: list[bytes], msg_id: int) -> tuple[bytes, bool]:
    from remedy.connect.pipe import FLAG_FIN, TYPE_HTTP_RES

    parts: list[bytes] = []
    saw_fin = False
    for typ, mid, flags, payload in _inner_writes(writes):
        if typ != TYPE_HTTP_RES or mid != msg_id:
            continue
        parts.append(payload)
        if flags & FLAG_FIN:
            saw_fin = True
    return b"".join(parts), saw_fin


def _decode_inner_http(payload: bytes):
    from remedy.connect.pipe import decode_http_res, split_http_response

    if payload.startswith(b"HTTP/"):
        return split_http_response(payload)
    return decode_http_res(payload)


async def _wait_inner_res(writes: list[bytes], msg_id: int, *, need_fin: bool, timeout: float = 2.0):
    import time as _t

    end = _t.monotonic() + timeout
    while _t.monotonic() < end:
        payload, saw_fin = _inner_res_payload(writes, msg_id)
        if payload and (saw_fin or not need_fin):
            if need_fin:
                return _decode_inner_http(payload)
            return payload, saw_fin
        await asyncio.sleep(0.01)
    raise AssertionError(f"inner TYPE_HTTP_RES id={msg_id} did not arrive")


async def _run_inner_session(monkeypatch, incoming: asyncio.Queue):
    from remedy.connect.pipe import SessionCrypto, session_loop

    writer = _ListWriter()

    async def fake_hs(_reader, _writer, _host_kp):
        return SessionCrypto(object(), object()), b"payload", b"\x11" * 32

    async def fake_auth(_payload, _rs):
        return {"id": "dev1", "name": "phone"}

    async def fake_read(_reader):
        rec = await incoming.get()
        if rec is None:
            raise asyncio.CancelledError
        return rec

    monkeypatch.setattr("remedy.connect.pipe.handshake_responder", fake_hs)
    monkeypatch.setattr("remedy.connect.pipe.authenticate_payload", fake_auth)
    monkeypatch.setattr("remedy.connect.pipe.read_transport", fake_read)
    monkeypatch.setattr("remedy.connect.pipe._decrypt", lambda _cs, blob: blob)
    monkeypatch.setattr("remedy.connect.pipe._encrypt", lambda _cs, plaintext: plaintext)
    monkeypatch.setattr("remedy.connect.store.is_paused", lambda _home=None: False)
    monkeypatch.setattr("remedy.connect.pipe.is_paused", lambda: False)

    task = asyncio.create_task(
        session_loop(
            object(),
            writer,
            host_kp=object(),
            sidecar_port=9,
            api_key="t",
            config={},
        )
    )
    return writer, task


class _ListWriter:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    async def drain(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hang_target",
    [
        "/api/events/sessions",
        "/api/events/sessions/",
        "/api/events/sessions?after=0",
        "/api/events/other",
        "/api/status",
    ],
)
async def test_inner_hanging_stream_family_does_not_block_second_inner_request(
    home, monkeypatch, hang_target
):
    """Long-lived inner GET (SSE / hung chunked) must not stall a later inner request."""
    from remedy.connect.pipe import TYPE_HTTP_REQ, encode_http_req, encode_inner

    hang = asyncio.Event()
    chunks_seen: list[int] = []
    sse = "/events/" in hang_target or hang_target.startswith("/api/events")

    async def fake_proxy(method, path, query, headers, body, **_k):
        _ = (method, query, headers, body)
        if "events" in (path or ""):
            chunks_seen.append(1)
            yield (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            yield b"data: 1\n\n"
            await hang.wait()
            chunks_seen.append(2)
            yield b"data: 2\n\n"
            return
        chunks_seen.append(1)
        yield (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        yield b"2\r\n{}\r\n"
        await hang.wait()
        chunks_seen.append(2)
        yield b"0\r\n\r\n"

    monkeypatch.setattr("remedy.connect.pipe.iter_proxy_response", fake_proxy)
    incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
    writer, loop_task = await _run_inner_session(monkeypatch, incoming)
    try:
        await incoming.put(
            encode_inner(TYPE_HTTP_REQ, 1, encode_http_req("GET", hang_target, "", b""))
        )
        if sse:
            first = await _wait_inner_res(writer.writes, 1, need_fin=False)
            assert first[0]
            # Upstream event 2 must not have been consumed (not fully buffered).
            assert 2 not in chunks_seen
        await incoming.put(
            encode_inner(TYPE_HTTP_REQ, 2, encode_http_req("GET", "/connect/me", "", b""))
        )
        status, _headers, body = await _wait_inner_res(writer.writes, 2, need_fin=True)
        assert status == 200
        assert b"panes" in body
        assert 2 not in chunks_seen
        hang_payload, hang_fin = _inner_res_payload(writer.writes, 1)
        if sse:
            assert hang_payload
            assert hang_fin is False
        else:
            # Hung non-SSE still must not have finished (would require joining the stream).
            assert hang_fin is False
    finally:
        hang.set()
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await loop_task


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["/connect/me", "/api/connect/me"])
async def test_inner_finite_response_family_is_http(home, monkeypatch, target):
    """Short inner replies FIN with a parseable JSON body (raw HTTP/1.1)."""
    from remedy.connect.pipe import TYPE_HTTP_REQ, encode_http_req, encode_inner

    incoming: asyncio.Queue[bytes | None] = asyncio.Queue()
    writer, loop_task = await _run_inner_session(monkeypatch, incoming)
    try:
        await incoming.put(encode_inner(TYPE_HTTP_REQ, 9, encode_http_req("GET", target, "", b"")))
        status, _headers, body = await _wait_inner_res(writer.writes, 9, need_fin=True)
        assert status == 200
        assert b"panes" in body
        payload, saw_fin = _inner_res_payload(writer.writes, 9)
        assert saw_fin
        assert payload.startswith(b"HTTP/1.1")
        again_status, _, again_body = _decode_inner_http(payload)
        assert again_status == 200
        assert b"panes" in again_body
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await loop_task


@pytest.mark.asyncio
async def test_proxy_injects_bearer_only_on_loopback(monkeypatch):
    seen: dict[str, object] = {}

    class FakeContent:
        async def iter_chunked(self, _n: int):
            yield b"{}"

    class FakeResp:
        status = 200
        reason = "OK"
        headers = {"Content-Type": "application/json", "Content-Length": "2"}
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

    class FakeSession:
        def request(self, method, url, headers=None, data=None, allow_redirects=False):
            seen["url"] = url
            seen["headers"] = dict(headers or {})
            seen["method"] = method
            return FakeResp()

        async def close(self):
            return None

    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: FakeSession())

    chunks = []
    async for part in iter_proxy_response(
        "GET",
        "/api/status",
        "",
        {"Authorization": "Bearer from-phone", "Cookie": "x=1"},
        b"",
        sidecar_port=7400,
        api_key="tok-connect-test-not-a-secret",
    ):
        chunks.append(part)
    assert seen["url"] == "http://127.0.0.1:7400/api/status"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers.get("Authorization") == "Bearer tok-connect-test-not-a-secret"
    assert headers.get("X-Remedy-Connect-Hop") == "1"
    assert "from-phone" not in str(headers)
    assert not any(str(k).lower() == "cookie" for k in headers)
    assert chunks


@pytest.mark.asyncio
async def test_noise_handshake_and_connect_me(home):
    """Round-trip IK + /connect/me when the sibling Noise core is present."""
    from remedy.connect.keys import load_or_create_host_keypair
    from remedy.connect.noise import HandshakeState, KeyPair
    from remedy.connect.pair import parse_pair_secret, start_pair
    from remedy.connect.record import decrypt_record, encrypt_record, pack_record, read_record

    port = _free_port()
    maybe_start_connect(
        None,
        {
            "connect_enabled": True,
            "connect_bind_host": "127.0.0.1",
            "connect_bind_port": port,
        },
        api_key="tok-connect-test-not-a-secret",
        sidecar_port=7400,
    )
    host, bound = _wait_addr()
    host_kp = load_or_create_host_keypair()
    qr = start_pair(loopback=True, bind_host=host, bind_port=bound)
    secret = parse_pair_secret(qr)
    device_kp = KeyPair.generate()
    reader, writer = await asyncio.open_connection(host, bound)
    try:
        hs = HandshakeState(initiator=True, s=device_kp, rs=host_kp.public)
        from remedy.connect.pair import pair_payload

        msg1 = hs.write_message(pair_payload(secret, "phone"))
        writer.write(struct.pack("!I", len(msg1)) + msg1)
        await writer.drain()
        header = await reader.readexactly(4)
        (n,) = struct.unpack("!I", header)
        msg2 = await reader.readexactly(n)
        hs.read_message(msg2)
        send, recv = hs.split()
        req = b"GET /connect/me HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
        writer.write(encrypt_record(send, req))
        await writer.drain()
        nonce12, ct = await asyncio.wait_for(read_record(reader), timeout=3)
        plain = decrypt_record(recv, pack_record(nonce12, ct))
        assert b"200" in plain
        assert b"panes" in plain
        assert b'"paused"' in plain
        assert b"reachable" in plain
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_android_raw_secret_and_inner_connect_me(home):
    """Phone wire: 32-byte pair payload + multiplexed inner HTTP frame."""
    from remedy.connect.keys import load_or_create_host_keypair
    from remedy.connect.noise import HandshakeState, KeyPair
    from remedy.connect.pair import parse_pair_secret, start_pair
    from remedy.connect.pipe import (
        TYPE_HTTP_REQ,
        decode_http_res,
        decode_inner,
        encode_http_req,
        encode_inner,
    )
    from remedy.connect.record import decrypt_record, encrypt_record, pack_record, read_record

    port = _free_port()
    maybe_start_connect(
        None,
        {
            "connect_enabled": True,
            "connect_bind_host": "127.0.0.1",
            "connect_bind_port": port,
        },
        api_key="tok-connect-test-not-a-secret",
        sidecar_port=7400,
    )
    host, bound = _wait_addr()
    host_kp = load_or_create_host_keypair()
    qr = start_pair(loopback=True, bind_host=host, bind_port=bound)
    secret = parse_pair_secret(qr)
    device_kp = KeyPair.generate()
    reader, writer = await asyncio.open_connection(host, bound)
    try:
        hs = HandshakeState(initiator=True, s=device_kp, rs=host_kp.public)
        msg1 = hs.write_message(secret)
        writer.write(struct.pack("!I", len(msg1)) + msg1)
        await writer.drain()
        header = await reader.readexactly(4)
        (n,) = struct.unpack("!I", header)
        msg2 = await reader.readexactly(n)
        hs.read_message(msg2)
        send, recv = hs.split()
        inner = encode_inner(TYPE_HTTP_REQ, 7, encode_http_req("GET", "/connect/me", "", b""))
        writer.write(encrypt_record(send, inner))
        await writer.drain()
        nonce12, ct = await asyncio.wait_for(read_record(reader), timeout=3)
        plain = decrypt_record(recv, pack_record(nonce12, ct))
        typ, msg_id, flags, payload = decode_inner(plain)
        assert typ == 0x02
        assert msg_id == 7
        if payload.startswith(b"HTTP/"):
            from remedy.connect.pipe import split_http_response

            status, _headers, body = split_http_response(payload)
        else:
            status, _headers, body = decode_http_res(payload)
        assert status == 200
        assert b"panes" in body
        assert b"reachable" in body
        assert b"session_id" in body
        assert b"local_api_token" not in body
        assert b"Bearer" not in body
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_allowlisted_device_reconnects_after_secret_consumed(home):
    from remedy.connect.keys import load_or_create_host_keypair
    from remedy.connect.noise import HandshakeState, KeyPair
    from remedy.connect.pair import complete_pair, parse_pair_secret, start_pair
    from remedy.connect.record import decrypt_record, encrypt_record, pack_record, read_record

    port = _free_port()
    maybe_start_connect(
        None,
        {
            "connect_enabled": True,
            "connect_bind_host": "127.0.0.1",
            "connect_bind_port": port,
        },
        api_key="tok-connect-test-not-a-secret",
        sidecar_port=7400,
    )
    host, bound = _wait_addr()
    host_kp = load_or_create_host_keypair()
    qr = start_pair(loopback=True, bind_host=host, bind_port=bound)
    secret = parse_pair_secret(qr)
    device_kp = KeyPair.generate()
    complete_pair(secret, device_kp.public, "phone")

    reader, writer = await asyncio.open_connection(host, bound)
    try:
        hs = HandshakeState(initiator=True, s=device_kp, rs=host_kp.public)
        msg1 = hs.write_message(secret)
        writer.write(struct.pack("!I", len(msg1)) + msg1)
        await writer.drain()
        header = await reader.readexactly(4)
        (n,) = struct.unpack("!I", header)
        msg2 = await reader.readexactly(n)
        hs.read_message(msg2)
        send, recv = hs.split()
        req = b"GET /connect/me HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
        writer.write(encrypt_record(send, req))
        await writer.drain()
        nonce12, ct = await asyncio.wait_for(read_record(reader), timeout=3)
        plain = decrypt_record(recv, pack_record(nonce12, ct))
        assert b"200" in plain
        assert b"panes" in plain
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


class _SinkWriter:
    def __init__(self) -> None:
        self.closed = False

    def write(self, _data: bytes) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def drain(self) -> None:
        return None


def test_revoke_one_device_does_not_drop_other(home):
    from remedy.connect.server import (
        bind_writer_device,
        drop_all_sessions,
        drop_sessions_for_device,
        register_writer,
        unregister_writer,
    )

    a, b = _SinkWriter(), _SinkWriter()
    register_writer(a)
    bind_writer_device(a, "aaaaaaaaaaaaaaaa")
    register_writer(b)
    bind_writer_device(b, "bbbbbbbbbbbbbbbb")
    try:
        drop_sessions_for_device("aaaaaaaaaaaaaaaa")
        assert a.closed is True
        assert b.closed is False
    finally:
        unregister_writer(a)
        unregister_writer(b)
        drop_all_sessions()


@pytest.mark.asyncio
async def test_iter_request_http_denies_pair_start_and_apikey(home):
    from remedy.connect.pipe import HttpRequest, iter_request_http

    device = {"id": "dev1", "name": "phone"}
    req = HttpRequest(
        method="POST",
        path="/api/connect/pair/start",
        query="",
        headers={},
        body=b"",
    )
    blob = b"".join(
        [
            p
            async for p in iter_request_http(
                req, device=device, sidecar_port=9, api_key="t", config=None
            )
        ]
    )
    assert b"403" in blob
    assert b"ps=" not in blob

    key_req = HttpRequest(
        method="POST",
        path="/api/auth/xai/apikey",
        query="",
        headers={"content-type": "application/json"},
        body=b'{"api_key":"not-a-real-key"}',
    )
    key_blob = b"".join(
        [
            p
            async for p in iter_request_http(
                key_req, device=device, sidecar_port=9, api_key="t", config=None
            )
        ]
    )
    assert b"403" in key_blob
    assert b"not-a-real-key" not in key_blob


@pytest.mark.asyncio
async def test_inner_http_does_not_block_second_request(monkeypatch):
    """SSE inner request must not stall the only session reader."""
    hang = asyncio.Event()
    saw_second = asyncio.Event()

    async def fake_iter(req, **_kwargs):
        path = req.path
        if path.startswith("/api/events"):
            yield b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
            yield b"data: 1\n\n"
            await hang.wait()
            yield b"data: 2\n\n"
            return
        saw_second.set()
        yield (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b'Content-Length: 11\r\n\r\n{"ok":true}'
        )

    sent: list[bytes] = []

    async def fake_send(_writer, _crypto, plaintext, *, inner_rekey=False):
        _ = inner_rekey
        sent.append(plaintext)

    monkeypatch.setattr("remedy.connect.pipe.iter_request_http", fake_iter)
    monkeypatch.setattr("remedy.connect.pipe._send_plain", fake_send)

    from remedy.connect.pipe import (
        TYPE_HTTP_REQ,
        SessionCrypto,
        encode_http_req,
        encode_inner,
        handle_inner_frame,
    )

    crypto = SessionCrypto(send=object(), recv=object())
    inflight: set[asyncio.Task] = set()
    device = {"id": "dev1"}
    sse = encode_inner(
        TYPE_HTTP_REQ, 1, encode_http_req("GET", "/api/events/sessions", "", b"")
    )
    other = encode_inner(
        TYPE_HTTP_REQ, 2, encode_http_req("GET", "/connect/me", "", b"")
    )
    await handle_inner_frame(
        sse,
        crypto=crypto,
        writer=object(),
        device=device,
        sidecar_port=9,
        api_key="t",
        config=None,
        fragments={},
        inflight=inflight,
    )
    await handle_inner_frame(
        other,
        crypto=crypto,
        writer=object(),
        device=device,
        sidecar_port=9,
        api_key="t",
        config=None,
        fragments={},
        inflight=inflight,
    )
    await asyncio.wait_for(saw_second.wait(), timeout=2.0)
    hang.set()
    if inflight:
        await asyncio.gather(*inflight, return_exceptions=True)
    assert sent
