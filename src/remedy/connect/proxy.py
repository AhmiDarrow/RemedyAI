"""Loopback HTTP/SSE proxy used by the Connect pipe.

The phone never sees ``local_api_token``. Bearer is injected only on the
127.0.0.1 hop to the sidecar.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

logger = logging.getLogger(__name__)

# Management routes refuse this so a phone cannot mint QR / retarget bind.
CONNECT_HOP_HEADER = "X-Remedy-Connect-Hop"

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "authorization",
        "cookie",
        "host",
        "x-remedy-token",
        "x-api-key",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "forwarded",
        CONNECT_HOP_HEADER.lower(),
    }
)

_STRIP_RESPONSE = frozenset(
    {
        "authorization",
        "www-authenticate",
        "proxy-authenticate",
        "x-remedy-token",
        "set-cookie",
        CONNECT_HOP_HEADER.lower(),
    }
)

_SSE_HINTS = ("text/event-stream",)


def is_sse_content_type(content_type: str) -> bool:
    low = (content_type or "").split(";", 1)[0].strip().lower()
    return any(h in low for h in _SSE_HINTS)


def _filter_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lk = str(key).strip().lower()
        if lk in _HOP_BY_HOP or lk.startswith("proxy-"):
            continue
        out[str(key)] = str(value)
    return out


def _filter_response_headers(headers: Mapping[str, str], *, sse: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lk = str(key).strip().lower()
        if lk in {"transfer-encoding", "connection", "keep-alive"}:
            continue
        if lk in _STRIP_RESPONSE:
            continue
        if sse and lk == "content-length":
            continue
        out[str(key)] = str(value)
    return out


def encode_http_head(
    status: int,
    reason: str,
    headers: Mapping[str, str],
) -> bytes:
    lines = [f"HTTP/1.1 {int(status)} {reason or 'OK'}"]
    for key, value in headers.items():
        lines.append(f"{key}: {value}")
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("iso-8859-1")


def encode_http_error(status: int, reason: str, body: bytes) -> bytes:
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
        "Connection": "keep-alive",
    }
    return encode_http_head(status, reason, headers) + body


async def iter_proxy_response(
    method: str,
    path: str,
    query: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    sidecar_port: int,
    api_key: str,
    session: Any | None = None,
) -> AsyncIterator[bytes]:
    """Yield HTTP response bytes as they arrive. SSE is not fully buffered."""
    import aiohttp

    from remedy.connect.deny import sanitize_origin_path

    port = int(sidecar_port or 7400)
    if port <= 0 or port > 65535:
        raise ValueError("sidecar port out of range")
    safe = sanitize_origin_path(path)
    if safe is None:
        body = b'{"error":"path"}'
        yield encode_http_error(400, "Bad Request", body)
        return
    q = (query or "").lstrip("?")
    url = f"http://127.0.0.1:{port}{safe}"
    if q:
        url = f"{url}?{q}"
    req_headers = _filter_request_headers(headers)
    req_headers["Host"] = f"127.0.0.1:{port}"
    req_headers[CONNECT_HOP_HEADER] = "1"
    token = str(api_key or "")
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
    owns_session = session is None
    client = session
    if owns_session:
        client = aiohttp.ClientSession(timeout=timeout)
    assert client is not None
    try:
        cm = client.request(
            (method or "GET").upper(),
            url,
            headers=req_headers,
            data=body if body else None,
            allow_redirects=False,
        )
        async with cm as resp:
            ctype = ""
            try:
                ctype = str(resp.headers.get("Content-Type") or "")
            except Exception:
                ctype = ""
            sse = is_sse_content_type(ctype)
            out_headers = _filter_response_headers(resp.headers, sse=sse)
            reason = getattr(resp, "reason", None) or "OK"
            status = int(getattr(resp, "status", 200) or 200)
            if sse:
                out_headers["Cache-Control"] = "no-cache"
                out_headers.setdefault("Content-Type", "text/event-stream")
                te_headers = dict(out_headers)
                te_headers["Transfer-Encoding"] = "chunked"
                te_headers.pop("Content-Length", None)
                te_headers.pop("content-length", None)
                yield encode_http_head(status, str(reason), te_headers)
                content = getattr(resp, "content", None)
                if content is not None and hasattr(content, "iter_chunked"):
                    async for chunk in content.iter_chunked(16384):
                        if not chunk:
                            continue
                        yield _chunked_block(chunk)
                elif content is not None and hasattr(content, "iter_any"):
                    async for chunk in content.iter_any():
                        if not chunk:
                            continue
                        yield _chunked_block(chunk)
                yield b"0\r\n\r\n"
                return
            # Non-SSE: stream body chunks; if Content-Length is known keep it.
            has_cl = any(k.lower() == "content-length" for k in out_headers)
            if not has_cl:
                out_headers["Transfer-Encoding"] = "chunked"
                yield encode_http_head(status, str(reason), out_headers)
                content = getattr(resp, "content", None)
                if content is not None and hasattr(content, "iter_chunked"):
                    async for chunk in content.iter_chunked(16384):
                        if chunk:
                            yield _chunked_block(chunk)
                yield b"0\r\n\r\n"
                return
            yield encode_http_head(status, str(reason), out_headers)
            content = getattr(resp, "content", None)
            if content is not None and hasattr(content, "iter_chunked"):
                async for chunk in content.iter_chunked(16384):
                    if chunk:
                        yield bytes(chunk)
            elif content is not None and hasattr(content, "read"):
                # Last resort: still read in slices, not one giant buffer.
                while True:
                    piece = await content.read(16384)
                    if not piece:
                        break
                    yield bytes(piece)
    finally:
        if owns_session:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


def _chunked_block(chunk: bytes) -> bytes:
    return f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n"
