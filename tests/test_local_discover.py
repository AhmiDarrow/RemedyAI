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
    assert not is_loopback_service_url("http://169.254.169.254/")
    assert not is_loopback_service_url("http://8.8.8.8/")
    assert not is_loopback_service_url("http://10.0.0.1:8188")
    assert not is_loopback_service_url("file:///etc/passwd")
    assert not is_loopback_service_url("http://user:secret@127.0.0.1/")
    assert not is_loopback_service_url("gopher://127.0.0.1/")


def test_http_get_json_refuses_ssrf_targets() -> None:
    """Must not open non-loopback URLs (no network call)."""
    with patch("urllib.request.urlopen") as mock_open:
        assert http_get_json("http://169.254.169.254/latest/meta-data") is None
        assert http_get_json("http://8.8.8.8/") is None
        assert http_get_json("file:///C:/Windows/win.ini") is None
        mock_open.assert_not_called()


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
