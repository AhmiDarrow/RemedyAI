"""Game-engine binary discovery — every tier pointed at tmp dirs, never the machine."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from remedy.core import game_engines
from remedy.core.game_engines import (
    engine_summary,
    find_engine_binary,
    find_repo_godot,
    godot_verify_command,
)
from remedy.core.project_fingerprint import StackFingerprint


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No env overrides, nothing on PATH, all install globs under tmp."""
    for key in ("GODOT", "GODOT4_BIN", "LOVE", "UNITY_EDITOR", "UE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(game_engines.shutil, "which", lambda _n: None)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    return tmp_path


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"MZ")
    return p


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = _touch(tmp_path / "custom" / "godot.exe")
    monkeypatch.setenv("GODOT4_BIN", str(exe))
    monkeypatch.setattr(game_engines.shutil, "which", lambda _n: str(tmp_path / "path-godot"))
    _touch(tmp_path / "repo" / "Godot_v4.3_console.exe")
    assert find_engine_binary("godot", project_root=tmp_path / "repo") == exe


def test_env_override_ignored_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GODOT", str(tmp_path / "nope.exe"))
    assert find_engine_binary("godot") is None


def test_path_tier(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    found = _touch(tmp_path / "bin" / "godot4")
    monkeypatch.setattr(
        game_engines.shutil, "which", lambda n: str(found) if n == "godot4" else None
    )
    _touch(tmp_path / "repo" / "Godot_v4.3_console.exe")
    assert find_engine_binary("godot", project_root=tmp_path / "repo") == found


def test_repo_root_prefers_console(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _touch(repo / "Godot_v4.3-stable_win64.exe")
    console = _touch(repo / "Godot_v4.3-stable_win64_console.exe")
    assert find_repo_godot(repo) == console
    assert find_engine_binary("godot", project_root=repo) == console


def test_install_globs_localappdata_programfiles_home(tmp_path: Path) -> None:
    assert find_engine_binary("godot") is None
    pf = _touch(tmp_path / "pf" / "Godot" / "Godot_v4.2.exe")
    assert find_engine_binary("godot") == pf
    local = _touch(tmp_path / "local" / "Programs" / "Godot" / "Godot_v4.3_console.exe")
    assert find_engine_binary("godot") == local


def test_install_glob_home_newest(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _touch(home / "Godot_4.1" / "Godot_v4.1.exe")
    newest = _touch(home / "Godot_4.3" / "Godot_v4.3.exe")
    assert find_engine_binary("godot") == newest


def test_love_and_unity_and_unreal_globs(tmp_path: Path) -> None:
    lovec = _touch(tmp_path / "pf" / "LOVE" / "lovec.exe")
    assert find_engine_binary("love2d") == lovec

    hub = tmp_path / "pf" / "Unity" / "Hub" / "Editor"
    old = _touch(hub / "2022.3.10f1" / "Editor" / "Unity.exe")
    new = _touch(hub / "6000.0.23f1" / "Editor" / "Unity.exe")
    assert find_engine_binary("unity") == new
    assert find_engine_binary("unity", version="2022.3.10f1") == old
    assert find_engine_binary("unity", version="2021.1.0f1") is None

    uat = _touch(
        tmp_path / "pf" / "Epic Games" / "UE_5.4" / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat"
    )
    assert find_engine_binary("unreal", version="5.4") == uat
    assert find_engine_binary("unreal") == uat
    assert find_engine_binary("unreal", version="5.1") is None


def test_unreal_env_root_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "ue"
    uat = _touch(root / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat")
    monkeypatch.setenv("UE_ROOT", str(root))
    assert find_engine_binary("unreal") == uat


def test_unknown_engine() -> None:
    assert find_engine_binary("quake") is None


def test_godot_verify_command_relative_and_absolute(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    exe = _touch(repo / "Godot_v4.3_console.exe")
    cmd = godot_verify_command(repo, exe)
    sep = "\\" if os.name == "nt" else "/"
    assert cmd == f".{sep}Godot_v4.3_console.exe --headless --path . --quit-after 1"

    (repo / "tools").mkdir()
    (repo / "tools" / "smoke_boot.gd").write_text("extends SceneTree\n", encoding="utf-8")
    assert godot_verify_command(repo, exe).endswith("-s tools/smoke_boot.gd")

    outside = _touch(tmp_path / "elsewhere" / "godot.exe")
    cmd = godot_verify_command(repo, outside)
    assert cmd.startswith(f'"{outside}"') and "--headless --path ." in cmd
    assert godot_verify_command(repo, None) is None


def test_engine_summary_lines(tmp_path: Path) -> None:
    fp = StackFingerprint(path=tmp_path, engine={"name": "godot", "version": "4.3", "lang": "gdscript"})
    assert engine_summary(fp) == "godot 4.3 (gdscript) — binary not found: set GODOT or tell me where it is"
    fp.engine["binary"] = "x/godot.exe"
    assert engine_summary(fp) == "godot 4.3 (gdscript) — x/godot.exe"
    assert engine_summary(StackFingerprint(path=tmp_path)) == ""
