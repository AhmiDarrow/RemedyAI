"""Inner-frame polish: immediate SSE flush, 413/429 instead of silence, idle timeout."""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


class _ListWriter:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    async def drain(self) -> None:
        return None


def _inner_res(writes: list[bytes], msg_id: int) -> tuple[bytes, bool]:
    from remedy.connect.pipe import FLAG_FIN, TYPE_HTTP_RES, decode_inner

    parts: list[bytes] = []
    fin = False
    for blob in writes:
        try:
            typ, mid, flags, payload = decode_inner(blob)
        except ValueError:
            continue
        if typ != TYPE_HTTP_RES or mid != msg_id:
            continue
        parts.append(payload)
        if flags & FLAG_FIN:
            fin = True
    return b"".join(parts), fin


async def _wait_for(pred, timeout: float = 2.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


async def _session(monkeypatch, incoming: asyncio.Queue):
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
        session_loop(object(), writer, host_kp=object(), sidecar_port=9, api_key="t", config={})
    )
    return writer, task


async def _finish(incoming: asyncio.Queue, task: asyncio.Task) -> None:
    await incoming.put(None)
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except (asyncio.CancelledError, TimeoutError):
        task.cancel()


@pytest.mark.asyncio
async def test_sse_piece_reaches_phone_before_the_next_one(home, monkeypatch):
    """Each proxied piece is forwarded as it arrives; FIN is a separate empty frame."""
    from remedy.connect.pipe import TYPE_HTTP_REQ, encode_http_req, encode_inner

    hang = asyncio.Event()

    async def fake_proxy(method, path, query, headers, body, **_k):
        yield b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
        yield b"data: first\n\n"
        await hang.wait()
        yield b"data: second\n\n"

    monkeypatch.setattr("remedy.connect.pipe.iter_proxy_response", fake_proxy)
    incoming: asyncio.Queue = asyncio.Queue()
    writer, task = await _session(monkeypatch, incoming)
    await incoming.put(
        encode_inner(TYPE_HTTP_REQ, 7, encode_http_req("GET", "/api/events/x", "", b""))
    )
    # Old behaviour withheld "first" until "second" arrived.
    await _wait_for(lambda: b"data: first" in _inner_res(writer.writes, 7)[0])
    assert _inner_res(writer.writes, 7)[1] is False
    hang.set()
    await _wait_for(lambda: _inner_res(writer.writes, 7)[1])
    payload, fin = _inner_res(writer.writes, 7)
    assert fin and b"data: second" in payload
    await _finish(incoming, task)


@pytest.mark.asyncio
async def test_oversize_fragmented_request_gets_413(home, monkeypatch):
    from remedy.connect import pipe
    from remedy.connect.pipe import TYPE_HTTP_REQ, encode_inner

    monkeypatch.setattr(pipe, "MAX_FRAG_BYTES", 1000)
    incoming: asyncio.Queue = asyncio.Queue()
    writer, task = await _session(monkeypatch, incoming)
    await incoming.put(encode_inner(TYPE_HTTP_REQ, 3, b"x" * 600, fin=False))
    await incoming.put(encode_inner(TYPE_HTTP_REQ, 3, b"x" * 600, fin=False))
    await _wait_for(lambda: _inner_res(writer.writes, 3)[1])
    payload, _fin = _inner_res(writer.writes, 3)
    assert payload.startswith(b"HTTP/1.1 413 ")
    await _finish(incoming, task)


@pytest.mark.asyncio
async def test_too_many_partial_messages_gets_429_and_evicts_oldest(home, monkeypatch):
    from remedy.connect import pipe
    from remedy.connect.pipe import TYPE_HTTP_REQ, encode_inner

    monkeypatch.setattr(pipe, "MAX_FRAGMENTS", 2)
    incoming: asyncio.Queue = asyncio.Queue()
    writer, task = await _session(monkeypatch, incoming)
    await incoming.put(encode_inner(TYPE_HTTP_REQ, 1, b"a", fin=False))
    await incoming.put(encode_inner(TYPE_HTTP_REQ, 2, b"b", fin=False))
    await incoming.put(encode_inner(TYPE_HTTP_REQ, 3, b"c", fin=False))
    await _wait_for(lambda: _inner_res(writer.writes, 3)[1])
    payload, _fin = _inner_res(writer.writes, 3)
    assert payload.startswith(b"HTTP/1.1 429 ")
    await _finish(incoming, task)


@pytest.mark.asyncio
async def test_silent_session_times_out(home, monkeypatch):
    from remedy.connect import pipe

    monkeypatch.setattr(pipe, "IDLE_TIMEOUT_S", 0.05)
    incoming: asyncio.Queue = asyncio.Queue()
    _writer, task = await _session(monkeypatch, incoming)
    device = await asyncio.wait_for(task, timeout=2.0)
    assert device == {"id": "dev1", "name": "phone"}
