"""Portable ComfyUI discovery (no machine-specific hardcoding required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.tools import comfyui as comfy


def test_resolve_base_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMFYUI_URL", "http://127.0.0.1:9191")
    assert comfy.resolve_base_url() == "http://127.0.0.1:9191"
    assert comfy.resolve_base_url("http://localhost:7777") == "http://localhost:7777"


def test_resolve_base_url_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
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
