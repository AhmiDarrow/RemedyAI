"""Repro: a phone POST with a JSON body through the Connect proxy must reach
the upstream route with its body intact (terminal input was 404-ing)."""

from __future__ import annotations

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


async def _run_upstream(seen: dict):
    from aiohttp import web

    async def handler(request: web.Request) -> web.Response:
        seen["method"] = request.method
        seen["path"] = request.path
        seen["ctype"] = request.headers.get("Content-Type", "")
        seen["clen"] = request.headers.get("Content-Length", "")
        body = await request.read()
        seen["body"] = body
        return web.json_response({"ok": True, "echo": body.decode("utf-8", "replace")})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "headers", "body"),
    [
        ("POST", "/api/terminal", "Content-Type: application/json", b'{"cols":100,"rows":28}'),
        ("POST", "/api/terminal/term-abc/input", "Content-Type: application/json", b'{"data":"echo hi\\n"}'),
        ("POST", "/api/terminal/term-abc/input", "", b'{"data":"echo hi\\n"}'),
    ],
)
async def test_proxy_forwards_json_body(home, method, path, headers, body):
    from remedy.connect.pipe import HttpRequest, iter_request_http

    seen: dict = {}
    runner, port = await _run_upstream(seen)
    try:
        hdrs = {}
        for line in headers.split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()
        req = HttpRequest(method=method, path=path, query="", headers=hdrs, body=body)
        chunks: list[bytes] = []
        async for piece in iter_request_http(
            req,
            device={"id": "d1", "name": "phone"},
            sidecar_port=port,
            api_key="k",
            config={"connect_panes": {"rails": True, "sessions": True, "chat": True}},
        ):
            chunks.append(piece)
        blob = b"".join(chunks)
        status_line = blob.split(b"\r\n", 1)[0].decode("latin-1")
        assert seen.get("method") == method, seen
        assert seen.get("body") == body, (seen.get("body"), body)
        assert b"200" in status_line.encode(), status_line
    finally:
        await runner.cleanup()
