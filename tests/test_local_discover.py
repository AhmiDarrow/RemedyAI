"""Portable local discovery framework (skills + built-ins)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remedy.core.local_discover import (
    BinarySpec,
    HttpServiceSpec,
    _loopback_hosts_only,
    discover_all,
    discover_binaries,
    discover_install_dirs,
    http_get_json,
    parse_skill_local_spec,
    probe_http_service,
)
from remedy.core.security import is_loopback_service_url


def test_parse_skill_local_frontmatter() -> None:
    fm = {
        "name": "comfyui",
        "local": {
            "services": [
                {
                    "id": "comfyui",
                    "ports": [8188, 8189],
                    "path": "/system_stats",
                    "env_url": ["COMFYUI_URL"],
                    "dir_names": ["ComfyUI"],
                    "entry": ["main.py"],
                }
            ]
        },
    }
    spec = parse_skill_local_spec("comfyui", fm)
    assert spec is not None
    assert len(spec.services) == 1
    assert spec.services[0].ports == [8188, 8189]


def test_discover_install_from_env_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "AnywhereAI" / "ComfyUI"
    root.mkdir(parents=True)
    (root / "main.py").write_text("# x\n", encoding="utf-8")
    monkeypatch.setenv("COMFYUI_HOME", str(root))
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "rh"))
    svc = HttpServiceSpec(
        id="comfyui",
        ports=[8188],
        path="/system_stats",
        env_home=["COMFYUI_HOME"],
        dir_names=["ComfyUI"],
        entry_files=["main.py"],
    )
    found = discover_install_dirs(svc)
    assert any(Path(f["path"]) == root.resolve() for f in found)


def test_discover_binaries_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # python is almost always on PATH in our test env
    result = discover_binaries(BinarySpec(id="python", names=["python", "python3"]))
    # soft assert — ok if missing in weird envs
    assert "ok" in result
    assert result["id"] == "python"


def test_discover_all_shape() -> None:
    out = discover_all(include_builtins=True)
    assert "services" in out
    assert "binaries" in out
    assert "note" in out
    ids = {s["id"] for s in out["services"]}
    assert "comfyui" in ids or "ollama" in ids


def test_probe_closed_port_returns_empty() -> None:
    svc = HttpServiceSpec(id="nothing", ports=[1], path="/")
    assert probe_http_service(svc) == []


def test_loopback_service_url_helper() -> None:
    assert is_loopback_service_url("http://127.0.0.1:8188")
    assert is_loopback_service_url("http://localhost:11434")
    assert is_loopback_service_url("http://[::1]:8080")
    assert is_loopback_service_url("https://127.0.0.1/")
    assert is_loopback_service_url("http://[0:0:0:0:0:0:0:1]/")
    assert not is_loopback_service_url("http://169.254.169.254/")
    assert not is_loopback_service_url("http://8.8.8.8/")
    assert not is_loopback_service_url("http://10.0.0.1:8188")
    assert not is_loopback_service_url("file:///etc/passwd")
    assert not is_loopback_service_url("http://user:secret@127.0.0.1/")
    assert not is_loopback_service_url("http://user@127.0.0.1/")
    assert not is_loopback_service_url("gopher://127.0.0.1/")
    # Encoded / alternate-form loopback bypass attempts
    assert not is_loopback_service_url("http://0.0.0.0:8188")
    assert not is_loopback_service_url("http://2130706433/")  # decimal 127.0.0.1
    assert not is_loopback_service_url("http://0x7f000001/")
    assert not is_loopback_service_url("http://[fc00::1]/")  # ULA
    assert not is_loopback_service_url("http://[fe80::1]/")  # link-local
    assert not is_loopback_service_url("http://[::ffff:169.254.169.254]/")
    assert not is_loopback_service_url("ftp://127.0.0.1/")
    assert not is_loopback_service_url("")


def test_loopback_service_url_mixed_dns_fail_closed() -> None:
    """If DNS returns any non-loopback A/AAAA, reject (anti-rebinding)."""
    fake = [
        (0, 0, 0, "", ("127.0.0.1", 0)),
        (0, 0, 0, "", ("8.8.8.8", 0)),
    ]
    with patch("socket.getaddrinfo", return_value=fake):
        assert not is_loopback_service_url("http://evil.example/")
    with patch("socket.getaddrinfo", side_effect=OSError("nxdomain")):
        assert not is_loopback_service_url("http://no-such.invalid/")


def test_http_get_json_refuses_ssrf_targets() -> None:
    """Must not open non-loopback URLs (no network call)."""
    with patch("remedy.core.security.urlopen_no_redirect") as mock_open:
        assert http_get_json("http://169.254.169.254/latest/meta-data") is None
        assert http_get_json("http://8.8.8.8/") is None
        assert http_get_json("file:///C:/Windows/win.ini") is None
        mock_open.assert_not_called()


def test_http_get_json_does_not_follow_off_loopback_redirect() -> None:
    """Loopback 302 → metadata/LAN must not become outbound SSRF."""
    import http.server
    import threading

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/redir-meta":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data")
                self.end_headers()
            elif self.path == "/redir-lan":
                self.send_response(302)
                self.send_header("Location", "http://10.0.0.5/secret")
                self.end_headers()
            elif self.path == "/ok":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    thr = threading.Thread(target=httpd.serve_forever, daemon=True)
    thr.start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert http_get_json(f"{base}/ok") == {"ok": True}
        # Redirect responses must not follow — returns None (HTTPError 302)
        assert http_get_json(f"{base}/redir-meta") is None
        assert http_get_json(f"{base}/redir-lan") is None
    finally:
        httpd.shutdown()


def test_loopback_hosts_only_clamps_skill_hosts() -> None:
    """Skill frontmatter hosts= must not enable LAN/metadata probes."""
    hosts = _loopback_hosts_only(
        ["127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.1", "localhost"]
    )
    assert "127.0.0.1" in hosts
    assert "localhost" in hosts
    assert "169.254.169.254" not in hosts
    assert "10.0.0.5" not in hosts
    assert "192.168.1.1" not in hosts


def test_probe_ignores_poisoned_env_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVIL_URL", "http://169.254.169.254/")
    svc = HttpServiceSpec(
        id="evil",
        ports=[1],
        path="/",
        env_url=["EVIL_URL"],
        hosts=["169.254.169.254"],
    )
    with patch("remedy.core.local_discover.http_get_json") as mock_get:
        mock_get.return_value = {"ok": True}
        hits = probe_http_service(svc)
    assert hits == []
    # If anything was probed it must still be loopback-only
    for call in mock_get.call_args_list:
        url = call.args[0] if call.args else ""
        assert is_loopback_service_url(url)


def test_probe_health_path_injection_clamped() -> None:
    """Skill health path with scheme / .. must not open arbitrary URLs."""
    svc = HttpServiceSpec(
        id="inject",
        ports=[1],
        path="http://evil.example/steal",
        hosts=["127.0.0.1"],
    )
    with patch("remedy.core.local_discover.http_get_json") as mock_get:
        mock_get.return_value = None
        with patch("remedy.core.local_discover.port_open", return_value=True):
            probe_http_service(svc)
    assert mock_get.call_args_list, "expected at least one loopback probe"
    for call in mock_get.call_args_list:
        url = str(call.args[0] if call.args else "")
        assert "evil.example" not in url
        # Health path collapsed to "/" so URL is loopback base + /
        assert url.endswith("/")
        assert is_loopback_service_url(url.rstrip("/") or url)


def test_urlopen_no_redirect_rejects_3xx_family() -> None:
    """301/302/303/307/308 must not be followed (absolute or relative Location)."""
    import http.server
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request

    from remedy.core.security import urlopen_no_redirect

    class _H(http.server.BaseHTTPRequestHandler):
        follow_hits = 0

        def do_GET(self) -> None:  # noqa: N802
            codes = {
                "/r301": 301,
                "/r302": 302,
                "/r303": 303,
                "/r307": 307,
                "/r308": 308,
            }
            if self.path in codes:
                self.send_response(codes[self.path])
                # Mix absolute metadata + relative paths (urllib would resolve)
                if self.path == "/r301":
                    self.send_header("Location", "http://169.254.169.254/latest/meta-data")
                elif self.path == "/r308":
                    self.send_header("Location", "http://10.0.0.9/lan")
                else:
                    self.send_header("Location", "/followed")
                self.end_headers()
                return
            if self.path == "/followed":
                type(self).follow_hits += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"stolen":true}')
                return
            if self.path == "/ok":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    thr = threading.Thread(target=httpd.serve_forever, daemon=True)
    thr.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # Happy path still works
        with urlopen_no_redirect(Request(f"{base}/ok"), timeout=2.0) as resp:
            body = resp.read()  # type: ignore[union-attr]
        assert b'"ok"' in body
        for path, code in (
            ("/r301", 301),
            ("/r302", 302),
            ("/r303", 303),
            ("/r307", 307),
            ("/r308", 308),
        ):
            with pytest.raises(HTTPError) as ei:
                urlopen_no_redirect(Request(f"{base}{path}"), timeout=2.0)
            assert ei.value.code == code
        assert _H.follow_hits == 0
    finally:
        httpd.shutdown()


def test_discover_binaries_glob_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from remedy.core import local_discover

    monkeypatch.setattr(local_discover.shutil, "which", lambda _n: None)
    exe = tmp_path / "Programs" / "Godot" / "Godot_v4.3_console.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    spec = BinarySpec(id="godot", names=["godot4"], glob_dirs=["%LOCALAPPDATA%/Programs/Godot/*.exe"])
    result = discover_binaries(spec)
    assert result["ok"] and result["source"] == "glob"
    assert Path(result["path"]) == exe
    monkeypatch.delenv("LOCALAPPDATA")
    assert discover_binaries(spec)["ok"] is False


def test_work_root_markers_for_games(tmp_path: Path) -> None:
    from remedy.core.work_roots import discover_work_root

    love = tmp_path / "love"
    (love / "src").mkdir(parents=True)
    (love / "main.lua").write_text("", encoding="utf-8")
    assert discover_work_root(love / "src") == love
    unity = tmp_path / "unity"
    (unity / "ProjectSettings").mkdir(parents=True)
    (unity / "Assets").mkdir()
    assert discover_work_root(unity / "Assets") == unity
    ue = tmp_path / "ue"
    (ue / "Source").mkdir(parents=True)
    (ue / "Game.uproject").write_text("{}", encoding="utf-8")
    assert discover_work_root(ue / "Source") == ue
