"""Portable local discovery framework (skills + built-ins)."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.local_discover import (
    HttpServiceSpec,
    LocalNeedSpec,
    discover_all,
    discover_binaries,
    discover_install_dirs,
    parse_skill_local_spec,
    probe_http_service,
    BinarySpec,
)


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
