"""Portable ComfyUI discovery (no machine-specific hardcoding required)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from remedy.core.errors import SecurityError
from remedy.tools import comfyui as comfy


def test_resolve_base_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:9191")
    assert comfy.resolve_base_url() == "http://127.0.0.1:9191"
    assert comfy.resolve_base_url("http://localhost:7777") == "http://localhost:7777"


def test_resolve_base_url_rejects_ssrf_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env / override pointing off-loopback must not become the base URL."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "remedy-home"))
    monkeypatch.delenv("COMFYUI_PORT", raising=False)
    monkeypatch.setenv("COMFYUI_URL", "http://169.254.169.254/latest/meta-data")
    monkeypatch.delenv("REMEDY_COMFYUI_URL", raising=False)
    # Falls back to default loopback (poisoned env ignored)
    assert comfy.resolve_base_url().startswith("http://127.0.0.1")
    # Tool override to public IP also ignored
    assert comfy.resolve_base_url("http://8.8.8.8:8188").startswith("http://127.0.0.1")
    # file: scheme ignored
    assert comfy.resolve_base_url("file:///C:/Windows/win.ini").startswith(
        "http://127.0.0.1"
    )


def test_request_blocks_non_loopback_base() -> None:
    with pytest.raises(SecurityError, match="loopback"):
        comfy._request("GET", "/system_stats", base="http://10.0.0.5:8188")  # noqa: SLF001
    with pytest.raises(SecurityError, match="loopback"):
        comfy._request(
            "GET",
            "/system_stats",
            base="http://8.8.8.8:8188",
        )  # noqa: SLF001
    with pytest.raises(SecurityError, match="loopback"):
        comfy._request(
            "GET",
            "/system_stats",
            base="http://169.254.169.254/",
        )  # noqa: SLF001


def test_request_blocks_path_scheme_injection() -> None:
    with pytest.raises(RuntimeError, match="Invalid ComfyUI path"):
        comfy._request(
            "GET",
            "//evil.example/steal",
            base="http://127.0.0.1:8188",
        )  # noqa: SLF001
    with pytest.raises(RuntimeError, match="Invalid ComfyUI path"):
        comfy._request(
            "GET",
            "http://evil.example/",
            base="http://127.0.0.1:8188",
        )  # noqa: SLF001
    with pytest.raises(RuntimeError, match="Invalid ComfyUI path"):
        comfy._request(
            "GET",
            "/../secret",
            base="http://127.0.0.1:8188",
        )  # noqa: SLF001
    with pytest.raises(RuntimeError, match="Invalid ComfyUI path"):
        comfy._request(
            "GET",
            "/api/../admin",
            base="http://127.0.0.1:8188",
        )  # noqa: SLF001


def test_request_does_not_follow_off_loopback_redirect() -> None:
    """ComfyUI client must not chase Location to metadata/LAN."""
    import http.server
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        hits_meta = 0

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/system_stats":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data")
                self.end_headers()
            else:
                type(self).hits_meta += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"stolen")

        def log_message(self, *_args: object) -> None:
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    thr = threading.Thread(target=httpd.serve_forever, daemon=True)
    thr.start()
    try:
        with pytest.raises(RuntimeError, match="HTTP 302"):
            comfy._request(
                "GET",
                "/system_stats",
                base=f"http://127.0.0.1:{port}",
                timeout=2.0,
            )  # noqa: SLF001
        # Never reached a second request path (redirect body / follow)
        assert _H.hits_meta == 0
    finally:
        httpd.shutdown()


def test_discover_api_skips_non_loopback_resolved_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if resolve returned something odd, probe list stays loopback-only."""
    monkeypatch.setattr(comfy, "resolve_base_url", lambda: "http://192.168.1.50:8188")
    with patch.object(comfy, "_probe_api", return_value={"base_url": "x"}):
        # Non-loopback from resolve skipped; only real loopback hosts may hit probe
        found = comfy.discover_api_endpoints()
    for ep in found:
        assert "192.168" not in str(ep.get("base_url", ""))


def test_resolve_base_url_port_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate from the developer's real ~/.remedy/comfyui.json (side_url).
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "remedy-home"))
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    monkeypatch.delenv("REMEDY_COMFYUI_URL", raising=False)
    monkeypatch.setenv("COMFYUI_PORT", "8189")
    assert comfy.resolve_base_url().endswith(":8189")


def test_discover_installs_from_fake_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any layout with main.py under COMFYUI_HOME is found."""
    root = tmp_path / "my-ai" / "ComfyUI"
    root.mkdir(parents=True)
    (root / "main.py").write_text("# fake comfy\n", encoding="utf-8")
    monkeypatch.setenv("COMFYUI_HOME", str(root))
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "remedy-home"))
    found = comfy.discover_installs()
    paths = {f["path"] for f in found}
    assert str(root.resolve()) in paths
    assert all("start_hint" in f for f in found)


def test_side_json_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".remedy"
    home.mkdir()
    (home / "comfyui.json").write_text(
        json.dumps({"url": "http://127.0.0.1:5555", "home": str(tmp_path / "x")}),
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    monkeypatch.delenv("REMEDY_COMFYUI_URL", raising=False)
    assert comfy.resolve_base_url() == "http://127.0.0.1:5555"


def test_locate_shape() -> None:
    loc = comfy.locate()
    assert "live_endpoints" in loc
    assert "installs" in loc
    assert "config_keys" in loc
    assert "COMFYUI_HOME" in loc["config_keys"]["env"]
