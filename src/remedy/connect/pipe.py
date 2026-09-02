"""Framed Noise TCP session: handshake payload, deny, loopback proxy."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from remedy.connect.audit import append_event
from remedy.connect.deny import connect_forbidden
from remedy.connect.pair import complete_pair, parse_handshake_payload
from remedy.connect.panes import panes_from_config
from remedy.connect.proxy import encode_http_error, iter_proxy_response
from remedy.connect.store import find_device_by_public, get_device, is_paused

logger = logging.getLogger(__name__)

REKEY_INTERVAL_S = 15 * 60
REKEY_RECORDS = 65536

INNER_VERSION = 1
TYPE_HTTP_REQ = 0x01
TYPE_HTTP_RES = 0x02
TYPE_PING = 0x10
TYPE_PONG = 0x11
TYPE_REKEY = 0x20
FLAG_FIN = 0x01
INNER_HDR = 11
MAX_BODY = 1_048_576
MAX_FRAGMENTS = 16
MAX_FRAG_BYTES = 256 * 1024
MAX_INNER_TASKS = 8
HANDSHAKE_TIMEOUT_S = 20.0
# The phone polls /connect/me every 2.5s while attached, so a socket that is
# silent this long belongs to a phone that vanished (mobile data drop, app
# killed) and would otherwise stay registered until TCP keepalive gives up.
IDLE_TIMEOUT_S = 180.0
# Relay / rendezvous only: undecryptable records tolerated before the
# session is dropped (a public-broker subscriber can publish junk on any
# topic it enumerates; the phone applies the same bound).
MAX_BAD_RECORDS = 32
_BLOCKED_METHODS = frozenset({"CONNECT", "TRACE", "TRACK"})


def _max_record() -> int:
    from remedy.connect.record import MAX_RECORD

    return int(MAX_RECORD) or 65536


def _max_plain() -> int:
    from remedy.connect.record import MAX_PLAINTEXT

    return int(MAX_PLAINTEXT) or 63 * 1024


async def _read_len_prefixed(reader: Any) -> bytes:
    """u32be length + body (handshake messages)."""
    header = await reader.readexactly(4)
    (length,) = struct.unpack("!I", header)
    if length < 1 or length > _max_record():
        raise ValueError("invalid handshake length")
    return await reader.readexactly(length)


def _write_len_prefixed(writer: Any, body: bytes) -> None:
    writer.write(struct.pack("!I", len(body)) + body)


async def read_transport(reader: Any) -> bytes:
    """Read one transport record and decrypt-prep as a packed blob."""
    from remedy.connect.record import pack_record, read_record

    nonce12, ciphertext = await read_record(reader)
    return pack_record(nonce12, ciphertext)


def _encrypt(cs: Any, plaintext: bytes) -> bytes:
    """Return a packed transport record ready to write on the socket."""
    from remedy.connect.record import encrypt_record

    return bytes(encrypt_record(cs, plaintext))


def _decrypt(cs: Any, blob: bytes) -> bytes:
    from remedy.connect.record import decrypt_record

    return bytes(decrypt_record(cs, blob))


def _as_pub_bytes(rs: object) -> bytes | None:
    if rs is None:
        return None
    if isinstance(rs, (bytes, bytearray)) and len(rs) == 32:
        return bytes(rs)
    for attr in ("public", "public_bytes", "pk", "public_key"):
        value = getattr(rs, attr, None)
        if value is None:
            continue
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        if isinstance(value, (bytes, bytearray)) and len(value) == 32:
            return bytes(value)
        encode = getattr(value, "encode", None)
        if callable(encode):
            try:
                encoded = encode()
            except TypeError:
                encoded = None
            if isinstance(encoded, (bytes, bytearray)) and len(encoded) == 32:
                return bytes(encoded)
    return None


def _rekey_cs(cs: Any) -> None:
    fn = getattr(cs, "rekey", None)
    if callable(fn):
        fn()


def encode_inner(typ: int, msg_id: int, payload: bytes, *, fin: bool = True) -> bytes:
    flags = FLAG_FIN if fin else 0
    return struct.pack("!BBIBI", INNER_VERSION, typ, msg_id, flags, len(payload)) + payload


def decode_inner(raw: bytes) -> tuple[int, int, int, bytes]:
    if len(raw) < INNER_HDR:
        raise ValueError("short inner frame")
    ver, typ, msg_id, flags, ln = struct.unpack("!BBIBI", raw[:INNER_HDR])
    if ver != INNER_VERSION:
        raise ValueError("inner version")
    if ln < 0 or INNER_HDR + ln > len(raw):
        raise ValueError("short inner payload")
    return int(typ), int(msg_id), int(flags), raw[INNER_HDR : INNER_HDR + ln]


def encode_http_req(method: str, target: str, headers: str, body: bytes) -> bytes:
    m = method.encode("ascii")
    t = target.encode("utf-8")
    h = headers.encode("utf-8")
    if len(m) > 255:
        raise ValueError("method too long")
    return (
        bytes([len(m)])
        + m
        + struct.pack("!H", len(t))
        + t
        + struct.pack("!H", len(h))
        + h
        + struct.pack("!I", len(body))
        + body
    )


def decode_http_req(payload: bytes) -> HttpRequest:
    if len(payload) < 1:
        raise ValueError("short http req")
    mlen = payload[0]
    pos = 1
    if pos + mlen + 2 > len(payload):
        raise ValueError("short http req")
    method = payload[pos : pos + mlen].decode("ascii", errors="replace").strip().upper()
    pos += mlen
    tlen = struct.unpack("!H", payload[pos : pos + 2])[0]
    pos += 2
    if pos + tlen + 2 > len(payload):
        raise ValueError("short http req")
    target = payload[pos : pos + tlen].decode("utf-8", errors="replace") or "/"
    pos += tlen
    hlen = struct.unpack("!H", payload[pos : pos + 2])[0]
    pos += 2
    if pos + hlen + 4 > len(payload):
        raise ValueError("short http req")
    headers_raw = payload[pos : pos + hlen].decode("utf-8", errors="replace")
    pos += hlen
    blen = struct.unpack("!I", payload[pos : pos + 4])[0]
    pos += 4
    if blen < 0 or pos + blen > len(payload):
        raise ValueError("short http body")
    body = payload[pos : pos + blen]
    if "://" in target or target.startswith("//"):
        raise ValueError("absolute URI refused")
    path, _, query = target.partition("?")
    headers: dict[str, str] = {}
    for line in headers_raw.replace("\n", "\r\n").split("\r\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return HttpRequest(method=method, path=path or "/", query=query, headers=headers, body=body)


def encode_http_res(status: int, headers: str, body: bytes) -> bytes:
    hb = headers.encode("utf-8")
    return struct.pack("!HH", int(status) & 0xFFFF, len(hb)) + hb + struct.pack("!I", len(body)) + body


def decode_http_res(payload: bytes) -> tuple[int, str, bytes]:
    if len(payload) < 4:
        raise ValueError("short http res")
    status, hlen = struct.unpack("!HH", payload[:4])
    pos = 4
    if pos + hlen + 4 > len(payload):
        raise ValueError("short http res")
    headers = payload[pos : pos + hlen].decode("utf-8", errors="replace")
    pos += hlen
    blen = struct.unpack("!I", payload[pos : pos + 4])[0]
    pos += 4
    if blen < 0 or pos + blen > len(payload):
        raise ValueError("short http body")
    return int(status), headers, payload[pos : pos + blen]


def fragment_inner(
    typ: int, msg_id: int, payload: bytes, max_plain: int, *, fin: bool = True
) -> list[bytes]:
    chunk = max(1, int(max_plain) - INNER_HDR)
    if len(payload) <= chunk:
        return [encode_inner(typ, msg_id, payload, fin=fin)]
    out: list[bytes] = []
    offset = 0
    while offset < len(payload):
        end = min(len(payload), offset + chunk)
        last = end == len(payload)
        out.append(encode_inner(typ, msg_id, payload[offset:end], fin=fin and last))
        offset = end
    return out


def _decode_chunked(data: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(data):
        nl = data.find(b"\r\n", pos)
        if nl < 0:
            break
        size_s = data[pos:nl].split(b";", 1)[0].strip()
        try:
            n = int(size_s, 16)
        except ValueError:
            break
        pos = nl + 2
        if n == 0:
            break
        out.extend(data[pos : pos + n])
        pos += n
        if data[pos : pos + 2] == b"\r\n":
            pos += 2
    return bytes(out)


def split_http_response(blob: bytes) -> tuple[int, str, bytes]:
    """Turn concatenated HTTP/1.1 bytes (Content-Length or chunked) into a triple."""
    sep = blob.find(b"\r\n\r\n")
    if sep < 0:
        return 502, "Content-Type: text/plain\r\n", b"bad upstream"
    head = blob[:sep].decode("iso-8859-1", errors="replace")
    rest = blob[sep + 4 :]
    lines = head.split("\r\n")
    status = 502
    if lines:
        parts = lines[0].split(" ")
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])
    headers_list = [h for h in lines[1:] if h]
    chunked = any(
        h.lower().startswith("transfer-encoding:") and "chunked" in h.lower() for h in headers_list
    )
    keep = [
        h
        for h in headers_list
        if not h.lower().startswith("transfer-encoding:")
        and not h.lower().startswith("content-length:")
    ]
    body = _decode_chunked(rest) if chunked else rest
    hdr = "\r\n".join(keep)
    if hdr and not hdr.endswith("\r\n"):
        hdr += "\r\n"
    return status, hdr, body


@dataclass
class HttpRequest:
    method: str
    path: str
    query: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def target(self) -> str:
        if self.query:
            return f"{self.path}?{self.query}"
        return self.path


def try_parse_http(buf: bytes) -> tuple[HttpRequest | None, bytes]:
    """Parse one HTTP/1.1 request from *buf*. Incomplete → ``(None, buf)``."""
    sep = buf.find(b"\r\n\r\n")
    if sep < 0:
        if len(buf) > 1024 * 1024:
            raise ValueError("headers too large")
        return None, buf
    try:
        head = buf[:sep].decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise ValueError("bad headers") from exc
    rest = buf[sep + 4 :]
    lines = head.split("\r\n")
    if not lines or not lines[0]:
        raise ValueError("empty request line")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    method = parts[0].strip().upper()
    if method in _BLOCKED_METHODS:
        raise ValueError("method not allowed")
    target = parts[1].strip() or "/"
    if "://" in target or target.startswith("//"):
        raise ValueError("absolute URI refused")
    path, _, query = target.partition("?")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    try:
        cl = int(headers.get("content-length", "0") or 0)
    except ValueError as exc:
        raise ValueError("bad content-length") from exc
    if cl < 0 or cl > MAX_BODY:
        raise ValueError("bad content-length")
    if len(rest) < cl:
        return None, buf
    body = rest[:cl]
    leftover = rest[cl:]
    return HttpRequest(method=method, path=path, query=query, headers=headers, body=body), leftover


class SessionCrypto:
    def __init__(self, send: Any, recv: Any) -> None:
        self.send = send
        self.recv = recv
        self.send_n = 0
        self.send_t0 = time.monotonic()
        self.inner = False
        self.write_lock = asyncio.Lock()

    def note_send(self) -> bool:
        """True when the send cipher should rekey after this record is written."""
        self.send_n += 1
        if self.send_n >= REKEY_RECORDS or (time.monotonic() - self.send_t0) >= REKEY_INTERVAL_S:
            self.send_n = 0
            self.send_t0 = time.monotonic()
            return True
        return False


async def handshake_responder(
    reader: Any,
    writer: Any,
    host_kp: Any,
) -> tuple[SessionCrypto, bytes, bytes | None]:
    """Noise IK responder. Returns (crypto, first_payload, device_pub)."""
    from remedy.connect.noise import HandshakeState

    hs = HandshakeState(initiator=False, s=host_kp, rs=None)
    raw = await _read_len_prefixed(reader)
    payload = bytes(hs.read_message(raw))
    out = hs.write_message(b"")
    _write_len_prefixed(writer, bytes(out))
    await writer.drain()
    send, recv = hs.split()
    # Prefer public ``rs`` if the core exposes it; otherwise the private slot.
    rs = _as_pub_bytes(getattr(hs, "rs", None) or getattr(hs, "_rs", None))
    return SessionCrypto(send, recv), payload, rs


async def _send_plain_unlocked(
    writer: Any,
    crypto: SessionCrypto,
    plaintext: bytes,
    *,
    inner_rekey: bool = False,
) -> None:
    max_n = _max_plain()
    view = memoryview(plaintext)
    offset = 0
    while offset < len(view):
        chunk = bytes(view[offset : offset + max_n])
        offset += len(chunk)
        writer.write(_encrypt(crypto.send, chunk))
        if crypto.note_send() and inner_rekey:
            writer.write(_encrypt(crypto.send, encode_inner(TYPE_REKEY, 0, b"")))
            _rekey_cs(crypto.send)
    await writer.drain()


async def _send_plain(
    writer: Any,
    crypto: SessionCrypto,
    plaintext: bytes,
    *,
    inner_rekey: bool = False,
) -> None:
    lock = getattr(crypto, "write_lock", None)
    if lock is None:
        await _send_plain_unlocked(writer, crypto, plaintext, inner_rekey=inner_rekey)
        return
    async with lock:
        await _send_plain_unlocked(writer, crypto, plaintext, inner_rekey=inner_rekey)


def _json_error(status: int, reason: str, code: str) -> bytes:
    body = json.dumps({"error": code, "reason": reason}).encode("utf-8")
    phrase = _PHRASES.get(status, "Error" if status >= 500 else "OK")
    return encode_http_error(status, phrase, body)


_PHRASES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    413: "Payload Too Large",
    429: "Too Many Requests",
    502: "Bad Gateway",
}


def _json_ok(obj: dict[str, Any]) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    return encode_http_error(200, "OK", body)


def _me_body(device: dict[str, Any], panes: dict[str, bool]) -> dict[str, Any]:
    paused = is_paused()
    did = str(device.get("id") or "")
    session_id: str | None = None
    with contextlib.suppress(Exception):
        from remedy.core.stream_lock import active_session_ids

        sids = [s for s in active_session_ids() if s]
        session_id = sids[0] if sids else None
    return {
        "panes": panes,
        "paused": paused,
        "reachable": "paused" if paused else "lan",
        "device_id": did,
        "session_id": session_id,
        "device": {
            "id": did,
            "name": device.get("name"),
        },
    }


async def iter_request_http(
    req: HttpRequest,
    *,
    device: dict[str, Any],
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any] | None,
) -> AsyncIterator[bytes]:
    panes = panes_from_config(config)
    path = req.path or "/"
    from remedy.connect.deny import (
        sanitize_origin_path,
        settings_body_safe_provider,
        settings_write_locked,
    )

    safe = sanitize_origin_path(path)
    if safe is None:
        yield _json_error(400, "path", "path")
        return
    path = safe
    req = HttpRequest(
        method=req.method,
        path=path,
        query=req.query,
        headers=req.headers,
        body=req.body,
    )
    if req.method.upper() in ("PUT", "PATCH") and path.startswith("/api/settings"):
        locked = settings_write_locked(req.body)
        if locked:
            yield _json_error(403, locked, "forbidden")
            return
        # Provider/model switch is safe for the phone: no secrets, no connect
        # keys (the body lock above already rejected those). Without this the
        # settings_write pane (off by default) blocks even a plain switch.
        if settings_body_safe_provider(req.body):
            panes = dict(panes)
            panes["settings_write"] = True
    if path in ("/connect/me", "/api/connect/me"):
        yield _json_ok(_me_body(device, panes))
        return
    if path in ("/connect/preview", "/api/connect/preview"):
        if not panes.get("computer_preview"):
            yield _json_error(403, "pane:computer_preview", "forbidden")
            return
        req = HttpRequest(
            method="POST",
            path="/api/computer/capture",
            query=req.query,
            headers=req.headers,
            body=req.body,
        )
        path = req.path

    # A phone may revoke ITSELF (never another device): removes the record,
    # drops its sockets, and the next reconnect is refused by the allowlist.
    if (
        req.method.upper() == "POST"
        and path.startswith("/api/connect/devices/")
        and path.endswith("/revoke")
    ):
        me_id = str(device.get("id") or "").strip().lower()
        revoke_id = path.split("/")[4].strip().lower()
        if not me_id or revoke_id != me_id:
            yield _json_error(403, "connect:mgmt", "forbidden")
            return
        try:
            from remedy.connect.lifecycle import drop_sessions_for_device
            from remedy.connect.store import revoke_device

            revoke_device(revoke_id)
            drop_sessions_for_device(revoke_id)
        except Exception:
            yield _json_error(500, "revoke", "revoke failed")
            return
        yield _json_ok({"ok": True, "id": revoke_id, "revoked": True})
        return

    reason = connect_forbidden(req.method, path, req.query, panes)
    if reason:
        yield _json_error(403, reason, "forbidden")
        return

    if (
        req.method.upper() == "POST"
        and path.startswith("/api/approvals/")
        and path.endswith("/resolve")
    ):
        with contextlib.suppress(Exception):
            append_event(
                "approve-from-phone",
                device_id=str(device.get("id") or ""),
            )

    async for piece in iter_proxy_response(
        req.method,
        path,
        req.query,
        req.headers,
        req.body,
        sidecar_port=sidecar_port,
        api_key=api_key,
    ):
        yield piece


async def handle_http_request(
    req: HttpRequest,
    *,
    crypto: SessionCrypto,
    writer: Any,
    device: dict[str, Any],
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any] | None,
) -> None:
    async for piece in iter_request_http(
        req,
        device=device,
        sidecar_port=sidecar_port,
        api_key=api_key,
        config=config,
    ):
        await _send_plain(writer, crypto, piece, inner_rekey=crypto.inner)


async def _send_inner_res(
    writer: Any,
    crypto: SessionCrypto,
    msg_id: int,
    status: int,
    headers: str,
    body: bytes,
) -> None:
    payload = encode_http_res(status, headers, body)
    for frame in fragment_inner(TYPE_HTTP_RES, msg_id, payload, _max_plain()):
        await _send_plain(writer, crypto, frame, inner_rekey=True)


async def _send_inner_raw(
    writer: Any,
    crypto: SessionCrypto,
    msg_id: int,
    payload: bytes,
    *,
    fin: bool,
) -> None:
    for frame in fragment_inner(TYPE_HTTP_RES, msg_id, payload, _max_plain(), fin=fin):
        await _send_plain(writer, crypto, frame, inner_rekey=True)


async def _stream_inner_http(
    req: HttpRequest,
    *,
    msg_id: int,
    crypto: SessionCrypto,
    writer: Any,
    device: dict[str, Any],
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any] | None,
) -> None:
    """Yield TYPE_HTTP_RES as raw HTTP chunks, each forwarded as it arrives.

    Every piece goes out immediately with ``fin=False`` and an empty
    ``fin=True`` frame closes the message, so an SSE event (or a terminal
    line) reaches the phone now rather than when the *next* one arrives.
    """
    sent_any = False
    try:
        async for piece in iter_request_http(
            req,
            device=device,
            sidecar_port=sidecar_port,
            api_key=api_key,
            config=config,
        ):
            if not piece:
                continue
            await _send_inner_raw(writer, crypto, msg_id, piece, fin=False)
            sent_any = True
        if not sent_any:
            await _send_inner_raw(
                writer, crypto, msg_id, _json_error(502, "empty", "empty"), fin=True
            )
            return
        await _send_inner_raw(writer, crypto, msg_id, b"", fin=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("connect inner http failed", exc_info=True)
        with contextlib.suppress(Exception):
            await _send_inner_raw(
                writer, crypto, msg_id, _json_error(502, "pipe", "pipe"), fin=True
            )


async def handle_inner_frame(
    plaintext: bytes,
    *,
    crypto: SessionCrypto,
    writer: Any,
    device: dict[str, Any],
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any] | None,
    fragments: dict[int, bytearray],
    inflight: set[asyncio.Task[Any]] | None = None,
) -> None:
    typ, msg_id, flags, payload = decode_inner(plaintext)
    if typ == TYPE_REKEY:
        _rekey_cs(crypto.recv)
        return
    if typ == TYPE_PING:
        await _send_plain(
            writer, crypto, encode_inner(TYPE_PONG, msg_id, b""), inner_rekey=True
        )
        return
    if typ == TYPE_PONG:
        return
    if typ != TYPE_HTTP_REQ:
        return
    buf = fragments.get(msg_id)
    if buf is None:
        if len(fragments) >= MAX_FRAGMENTS:
            # Too many half-received messages: evict the oldest partial so a
            # client that lost a FIN cannot wedge itself, and tell this one.
            oldest = next(iter(fragments))
            fragments.pop(oldest, None)
            await _send_inner_raw(
                writer, crypto, msg_id, _json_error(429, "fragments", "busy"), fin=True
            )
            return
        buf = bytearray()
        fragments[msg_id] = buf
    if len(buf) + len(payload) > MAX_FRAG_BYTES:
        # Answer instead of dropping silently; the phone would otherwise
        # wait on its own timeout with no idea why.
        fragments.pop(msg_id, None)
        await _send_inner_raw(
            writer, crypto, msg_id, _json_error(413, "too large", "too_large"), fin=True
        )
        return
    buf.extend(payload)
    if not (flags & FLAG_FIN):
        return
    joined = bytes(buf)
    fragments.pop(msg_id, None)
    try:
        req = decode_http_req(joined)
    except ValueError:
        await _send_inner_raw(
            writer, crypto, msg_id, _json_error(400, "http", "http"), fin=True
        )
        return
    tasks = inflight if inflight is not None else set()
    live = {t for t in tasks if not t.done()}
    if len(live) >= MAX_INNER_TASKS:
        await _send_inner_raw(
            writer, crypto, msg_id, _json_error(429, "busy", "busy"), fin=True
        )
        return
    task = asyncio.create_task(
        _stream_inner_http(
            req,
            msg_id=msg_id,
            crypto=crypto,
            writer=writer,
            device=device,
            sidecar_port=sidecar_port,
            api_key=api_key,
            config=config,
        ),
        name="connect-inner-http",
    )
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    if inflight is None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def authenticate_payload(
    payload: bytes,
    device_pub: bytes | None,
) -> dict[str, Any]:
    kind, fields = parse_handshake_payload(payload)
    if kind == "pair":
        try:
            if device_pub is None or len(device_pub) != 32:
                raise ValueError("missing initiator static")
            device_id = complete_pair(
                fields["secret"], device_pub, str(fields.get("name") or "phone")
            )
            rec = get_device(device_id)
            if rec is None:
                raise ValueError("pair failed")
            return rec
        except ValueError:
            # Noise already proved possession of the device static. An allowlisted
            # phone may reconnect after the one-use QR secret is consumed.
            if device_pub is not None and len(device_pub) == 32:
                rec = find_device_by_public(device_pub.hex())
                if rec is not None and not rec.get("revoked"):
                    return rec
            raise
    device_id = str(fields.get("device_id") or "").strip()
    rec = get_device(device_id)
    if rec is None or rec.get("revoked"):
        raise ValueError("not allowlisted")
    # Fail closed: a record with no stored public key must not accept any
    # static, and a hello must always be bound to the Noise-proven key.
    want = str(rec.get("public_hex") or "").strip().lower()
    if not want or device_pub is None or device_pub.hex() != want:
        raise ValueError("static mismatch")
    return rec


async def session_loop(
    reader: Any,
    writer: Any,
    *,
    host_kp: Any,
    sidecar_port: int,
    api_key: str,
    config: dict[str, Any] | None,
    should_stop: Callable[[], bool] | None = None,
    on_device: Callable[[dict[str, Any]], None] | None = None,
    lenient_decrypt: bool = False,
) -> dict[str, Any] | None:
    """Handshake + request loop. Returns the device record if authenticated.

    ``lenient_decrypt`` is for relay / public-rendezvous transports, where any
    ``remedy/#`` subscriber can publish junk on the session topic. A record
    that fails the nonce/tag check leaves the cipher untouched, so skipping
    it is safe; on the LAN it stays fatal (fail closed).
    """
    inflight: set[asyncio.Task[Any]] = set()
    bad_records = 0
    if is_paused():
        return None
    try:
        crypto, payload, rs = await asyncio.wait_for(
            handshake_responder(reader, writer, host_kp),
            timeout=HANDSHAKE_TIMEOUT_S,
        )
    except TimeoutError:
        return None
    if is_paused():
        return None
    device = await authenticate_payload(payload, rs)
    if on_device is not None:
        with contextlib.suppress(Exception):
            on_device(device)
    buf = b""
    fragments: dict[int, bytearray] = {}
    try:
        while True:
            if should_stop is not None and should_stop():
                return device
            if is_paused():
                return device
            try:
                rec = await asyncio.wait_for(read_transport(reader), timeout=IDLE_TIMEOUT_S)
            except TimeoutError:
                # Silent for too long: the phone is gone. Drop the socket so
                # the device row goes offline and the slot is freed.
                return device
            try:
                plain = _decrypt(crypto.recv, rec)
            except Exception:
                # Replay / auth failure fail closed on the LAN; on a shared
                # broker tolerate a bounded amount of third-party noise.
                bad_records += 1
                if not lenient_decrypt or bad_records > MAX_BAD_RECORDS:
                    return device
                continue
            if plain[:1] == b"\x01":
                crypto.inner = True
                await handle_inner_frame(
                    plain,
                    crypto=crypto,
                    writer=writer,
                    device=device,
                    sidecar_port=sidecar_port,
                    api_key=api_key,
                    config=config,
                    fragments=fragments,
                    inflight=inflight,
                )
                continue
            buf += plain
            while True:
                req, buf = try_parse_http(buf)
                if req is None:
                    break
                await handle_http_request(
                    req,
                    crypto=crypto,
                    writer=writer,
                    device=device,
                    sidecar_port=sidecar_port,
                    api_key=api_key,
                    config=config,
                )
    finally:
        for task in list(inflight):
            task.cancel()
        if inflight:
            with contextlib.suppress(Exception):
                await asyncio.gather(*inflight, return_exceptions=True)
    return device
