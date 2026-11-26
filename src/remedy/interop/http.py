"""Stdlib HTTP app for A2A + Streamable MCP. Used by tests and `remedy mcp serve --http`.

Production inbound on :7400 is served by the Go runtime. This listener
defaults to 127.0.0.1:7401 and must not bind :7400.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from remedy.interop.a2a import A2AHandler
from remedy.interop.card import WELL_KNOWN_LEGACY_PATH, WELL_KNOWN_PATH

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7401  # not 7400 — that belongs to remedy-runtime
RESERVED_PORT = 7400


class HTTPResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes, sse: list[dict[str, Any]] | None = None) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.sse = sse or []


def _json_response(status: int, payload: Any, extra_headers: dict[str, str] | None = None) -> HTTPResponse:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if extra_headers:
        headers.update(extra_headers)
    return HTTPResponse(status, headers, body)


def _error(status: int, message: str) -> HTTPResponse:
    return _json_response(status, {"error": {"message": message}})


def origin_allowed(origin: str | None, bind: str) -> bool:
    """Reject DNS-rebinding. Empty Origin is allowed for non-browser clients."""
    if not origin:
        return True
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    bind_l = (bind or "127.0.0.1").lower()
    loopback = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
    if bind_l in loopback or bind_l in {"loopback", "off"}:
        return host in loopback or host.endswith(".localhost")
    # lan / 0.0.0.0: still require an Origin that is not a random website;
    # allow same-host and loopback. Anything else is refused.
    return host in loopback or host.endswith(".localhost") or host == bind_l


def handle_request(
    runtime: Any,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
) -> HTTPResponse:
    """One request. Auth, bind, and inbound-enabled checks live on the runtime."""
    path_only = path.split("?", 1)[0]
    hdrs = {k.lower(): v for k, v in headers.items()}
    method = method.upper()

    allowed, reason, status = runtime.authorize_http(method, path_only, hdrs)
    if not allowed:
        return _error(status, reason)

    origin = hdrs.get("origin")
    if not origin_allowed(origin, runtime.bind_host()):
        return _error(403, "invalid Origin")

    if path_only in {WELL_KNOWN_PATH, WELL_KNOWN_LEGACY_PATH} and method == "GET":
        return _json_response(200, runtime.public_card())

    if path_only in {"/extendedAgentCard", "/a2a/extendedAgentCard"} and method == "GET":
        return _json_response(200, runtime.extended_card())

    if path_only in {"/a2a", "/a2a/v1", "/a2a/v1/jsonrpc"} and method == "POST":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_response(200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        if not isinstance(payload, dict):
            return _json_response(200, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
        handler = A2AHandler(runtime)
        want_stream = str(payload.get("method") or "") in {
            "SendStreamingMessage",
            "message/stream",
            "SubscribeToTask",
            "tasks/resubscribe",
        }
        result = handler.handle(payload, headers=hdrs)
        accept = hdrs.get("accept") or ""
        if want_stream and "text/event-stream" in accept and "error" not in result:
            events = [{"result": result}]
            # Close the stream after the initial result (task or message).
            return HTTPResponse(
                200,
                {"Content-Type": "text/event-stream", "Cache-Control": "no-store"},
                _encode_sse(events),
                sse=events,
            )
        return _json_response(200, result)

    if path_only in {"/mcp", "/mcp/"} and method == "POST":
        return _handle_mcp(runtime, hdrs, body)

    return _error(404, "not found")


def _handle_mcp(runtime: Any, hdrs: dict[str, str], body: bytes) -> HTTPResponse:
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_response(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    if not isinstance(payload, dict):
        return _json_response(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
    # Notifications (no id): 202 Accepted.
    if payload.get("id") is None and payload.get("method"):
        runtime.handle_mcp(payload)
        return HTTPResponse(202, {"Cache-Control": "no-store"}, b"")
    result = runtime.handle_mcp(payload)
    accept = hdrs.get("accept") or ""
    if "text/event-stream" in accept and "application/json" in accept:
        # Either JSON or SSE is allowed; prefer JSON for a completed request.
        return _json_response(200, result)
    if "text/event-stream" in accept:
        events = [{"result": result["result"]}] if isinstance(result, dict) and "result" in result else [result]
        return HTTPResponse(
            200,
            {"Content-Type": "text/event-stream", "Cache-Control": "no-store"},
            _encode_sse(events),
            sse=events,
        )
    return _json_response(200, result)


def _encode_sse(events: list[Any]) -> bytes:
    chunks = []
    for event in events:
        chunks.append("data: " + json.dumps(event, ensure_ascii=False) + "\n\n")
    return "".join(chunks).encode("utf-8")


def run_http_server(runtime: Any, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    if port == RESERVED_PORT:
        raise ValueError("port 7400 is reserved for remedy serve (Go runtime); use 7401 or another port")
    host = host or DEFAULT_HOST

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            # Keep secrets out of logs; do not print Authorization or bodies.
            sys_stderr = __import__("sys").stderr
            sys_stderr.write("interop-http %s\n" % (fmt % args))

        def _dispatch(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            headers = dict(self.headers.items())
            resp = handle_request(
                runtime,
                method=self.command,
                path=self.path,
                headers=headers,
                body=raw,
            )
            self.send_response(resp.status)
            for key, value in resp.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if resp.body and self.command != "HEAD":
                self.wfile.write(resp.body)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch()

    httpd = ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
