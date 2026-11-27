"""One-time WebUI links: `remedy serve` prints one, `remedy open` makes one."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from remedy.interfaces import bootstrap_link as bl

TOKEN = "link-test-token-not-a-secret"


class _Runtime(BaseHTTPRequestHandler):
    minted: list[str] = []

    def log_message(self, *args: object) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802
        if self.path != bl.LINK_PATH:
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        host = self.headers.get("Host")
        url = f"http://{host}/?bootstrap=nonce{len(self.minted)}"
        self.minted.append(url)
        body = json.dumps({"url": url, "expires_in": 600, "single_use": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def runtime() -> str:
    _Runtime.minted = []
    srv = HTTPServer(("127.0.0.1", 0), _Runtime)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_mint_needs_the_token(runtime: str) -> None:
    assert bl.mint_bootstrap_link(runtime, "") == ""
    assert bl.mint_bootstrap_link(runtime, "wrong-token") == ""
    link = bl.mint_bootstrap_link(runtime, TOKEN)
    assert link.startswith(runtime + "/?bootstrap=")


def test_wait_for_link_reads_the_token_file(
    runtime: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REMEDY_API_KEY", raising=False)
    monkeypatch.setenv("REMEDY_API_AUTH", "1")
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "local_api_token").write_text(TOKEN + "\n", encoding="utf-8")
    link = bl.wait_for_link(runtime, tmp_path, deadline_s=5.0)
    assert link.startswith(runtime + "/?bootstrap=")


def test_wait_for_link_gives_up_without_a_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_API_KEY", TOKEN)
    ticks = iter(range(100))
    link = bl.wait_for_link(
        "http://127.0.0.1:9",
        tmp_path,
        deadline_s=3.0,
        sleep=lambda _s: None,
        clock=lambda: float(next(ticks)),
    )
    assert link == ""


