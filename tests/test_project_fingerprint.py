"""Stack fingerprint + orientation + local PATH helpers."""

import json
import shutil
import types
from pathlib import Path

import pytest

from remedy.core import game_engines, project_fingerprint
from remedy.core.build_oracle import discover_verify_command
from remedy.core.project_fingerprint import (
    fingerprint_path,
    local_bin_dirs,
    orientation_block,
    path_env_with_local_bins,
)


def test_fingerprint_godot(tmp_path: Path):
    (tmp_path / "project.godot").write_text("; godot\n", encoding="utf-8")
    (tmp_path / "Godot_v4.7.1-stable_win64_console.exe").write_bytes(b"MZ")
    fp = fingerprint_path(tmp_path)
    assert "godot" in fp.stacks
    assert fp.suggest_verify
    assert any("Godot" in h for h in fp.hints)
    lines = fp.context_lines()
    assert lines and "godot" in "\n".join(lines).lower()


def test_fingerprint_python_pytest(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    fp = fingerprint_path(tmp_path)
    assert "python" in fp.stacks
    assert fp.suggest_verify and "pytest" in fp.suggest_verify


def test_orientation_agents_and_handoff(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Agent notes\nDo not force push.\n", encoding="utf-8")
    mem = tmp_path / "memory"
    mem.mkdir()
    notes = mem / "SESSION_NOTES"
    notes.mkdir()
    (notes / "HANDOFF_test.md").write_text("# Handoff\nDone X\n", encoding="utf-8")
    (mem / "LATEST_HANDOFF.md").write_text("HANDOFF_test.md\n", encoding="utf-8")
    block = orientation_block(tmp_path)
    assert "AGENTS.md" in block
    assert "LATEST_HANDOFF" in block


def test_local_bin_dirs_venv(tmp_path: Path):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    dirs = local_bin_dirs(tmp_path)
    assert any(d.name == "Scripts" for d in dirs)
    env = path_env_with_local_bins(tmp_path, base_env={"PATH": "/usr/bin"})
    assert str(scripts) in env.get("PATH", "")


# --- game engines (binary lookups isolated from the machine) -----------------


@pytest.fixture()
def no_engine_binaries(monkeypatch: pytest.MonkeyPatch):
    """Engine lookup never finds anything; luac absent."""
    monkeypatch.setattr(game_engines, "find_engine_binary", lambda *a, **k: None)
    monkeypatch.setattr(project_fingerprint.shutil, "which", lambda _n: None)


def _bare_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make build_oracle fall through to its marker fallbacks."""
    monkeypatch.setattr(
        project_fingerprint,
        "fingerprint_path",
        lambda r: project_fingerprint.StackFingerprint(path=Path(r)),
    )


def test_godot_version_lang_and_missing_binary(tmp_path: Path, no_engine_binaries):
    (tmp_path / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/features=PackedStringArray("4.3", "Forward Plus")\n',
        encoding="utf-8",
    )
    (tmp_path / "player.gd").write_text("extends Node\n", encoding="utf-8")
    fp = fingerprint_path(tmp_path)
    assert fp.engine == {"name": "godot", "version": "4.3", "lang": "gdscript"}
    assert fp.suggest_verify is None
    text = "\n".join(fp.context_lines())
    assert "engine: godot 4.3 (gdscript) — binary not found: set GODOT" in text
    assert "studio: game project — skills: godot-4, game-dev-studio" in text


def test_godot_csharp_and_mixed(tmp_path: Path, no_engine_binaries):
    (tmp_path / "project.godot").write_text(
        'config_version=5\nconfig/features=PackedStringArray("4.2", "C#")\n', encoding="utf-8"
    )
    (tmp_path / "Game.cs").write_text("class Game {}\n", encoding="utf-8")
    assert fingerprint_path(tmp_path).engine["lang"] == "csharp"
    (tmp_path / "addons" / "x").mkdir(parents=True)
    (tmp_path / "addons" / "x" / "plugin.gd").write_text("", encoding="utf-8")
    assert fingerprint_path(tmp_path).engine["lang"] == "csharp"  # addons skipped
    (tmp_path / "ui.gd").write_text("extends Control\n", encoding="utf-8")
    assert fingerprint_path(tmp_path).engine["lang"] == "mixed"


def test_godot_config_version_only(tmp_path: Path, no_engine_binaries):
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    assert fingerprint_path(tmp_path).engine["version"] == "4"


def test_godot_repo_binary_gives_relative_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(game_engines.shutil, "which", lambda _n: None)
    for k in ("GODOT", "GODOT4_BIN"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    (tmp_path / "Godot_v4.3_win64.exe").write_bytes(b"MZ")
    (tmp_path / "Godot_v4.3_win64_console.exe").write_bytes(b"MZ")
    fp = fingerprint_path(tmp_path)
    assert fp.engine["binary"].endswith("Godot_v4.3_win64_console.exe")
    assert fp.suggest_verify == fp.engine["verify"]
    assert "Godot_v4.3_win64_console.exe --headless --path . --quit-after 1" in fp.suggest_verify
    assert "Godot_v4.3_win64_console.exe" in "\n".join(fp.context_lines())


def test_love2d_without_and_with_luac(tmp_path: Path, no_engine_binaries, monkeypatch):
    (tmp_path / "main.lua").write_text("function love.load() end\n", encoding="utf-8")
    fp = fingerprint_path(tmp_path)
    assert "love2d" in fp.stacks
    assert fp.engine == {"name": "love2d", "lang": "lua"}
    assert fp.suggest_verify is None
    assert any("luac" in h for h in fp.hints)
    monkeypatch.setattr(
        project_fingerprint.shutil, "which", lambda n: "/usr/bin/luac" if n == "luac" else None
    )
    fp = fingerprint_path(tmp_path)
    assert fp.suggest_verify == "luac -p main.lua"
    assert "skills: love2d, game-dev-studio" in "\n".join(fp.context_lines())


def test_phaser_and_pixi(tmp_path: Path, no_engine_binaries):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"phaser": "^3"}}), encoding="utf-8"
    )
    fp = fingerprint_path(tmp_path)
    assert fp.stacks[:2] == ["node", "phaser"]
    assert fp.suggest_verify == "npm test"
    assert any("npm run dev" in h and "background" in h for h in fp.hints)
    assert "skills: web-games, game-dev-studio" in "\n".join(fp.context_lines())
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"pixi.js": "^8"}}), encoding="utf-8"
    )
    assert fingerprint_path(tmp_path).engine["name"] == "pixi"


def test_bevy(tmp_path: Path, no_engine_binaries):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname="g"\n\n[dependencies]\nbevy = { version = "0.14" }\n', encoding="utf-8"
    )
    fp = fingerprint_path(tmp_path)
    assert "bevy" in fp.stacks and fp.engine["name"] == "bevy"
    assert fp.suggest_verify == "cargo check"
    assert any("dynamic_linking" in h for h in fp.hints)
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname="g"\n[dependencies]\nserde = "1"\n', encoding="utf-8"
    )
    fp = fingerprint_path(tmp_path)
    assert "bevy" not in fp.stacks and fp.suggest_verify == "cargo test"


def test_pygame_import_sniff_and_py_compile(tmp_path: Path, no_engine_binaries):
    (tmp_path / "main.py").write_text("import pygame\n\npygame.init()\n", encoding="utf-8")
    fp = fingerprint_path(tmp_path)
    assert "python" in fp.stacks and "pygame" in fp.stacks
    assert fp.suggest_verify == "python -m py_compile main.py"
    assert "skills: pygame-arcade, game-dev-studio" in "\n".join(fp.context_lines())


def test_arcade_from_deps_keeps_pytest_when_tests_exist(tmp_path: Path, no_engine_binaries):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies=["arcade>=3"]\n', encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    fp = fingerprint_path(tmp_path)
    assert "arcade" in fp.stacks
    assert fp.suggest_verify == "pytest -q"


def test_unity(tmp_path: Path, no_engine_binaries):
    ps = tmp_path / "ProjectSettings"
    ps.mkdir()
    (ps / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.10f1\n", encoding="utf-8")
    fp = fingerprint_path(tmp_path)
    assert "unity" in fp.stacks
    assert fp.engine == {"name": "unity", "lang": "csharp", "version": "2022.3.10f1"}
    assert fp.suggest_verify is None
    assert any("-batchmode" in h for h in fp.hints)
    assert "skills: unity, game-dev-studio" in "\n".join(fp.context_lines())


def test_unreal(tmp_path: Path, no_engine_binaries):
    (tmp_path / "MyGame.uproject").write_text(
        json.dumps({"EngineAssociation": "5.4"}), encoding="utf-8"
    )
    fp = fingerprint_path(tmp_path)
    assert "unreal" in fp.stacks
    assert fp.engine == {"name": "unreal", "lang": "cpp", "version": "5.4"}
    assert fp.suggest_verify is None
    assert any("RunUAT" in h for h in fp.hints)
    assert "skills: unreal, game-dev-studio" in "\n".join(fp.context_lines())


# --- build_oracle.discover_verify_command fallbacks ---------------------------


def _runtime(root: Path):
    return types.SimpleNamespace(effective_project_path=lambda: root)


def test_verify_fallback_godot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _bare_fingerprint(monkeypatch)
    (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    exe = tmp_path / "Godot_v4.3_console.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(game_engines, "find_engine_binary", lambda *a, **k: exe)
    cmd = discover_verify_command(_runtime(tmp_path))
    assert "Godot_v4.3_console.exe --headless --path . --quit-after 1" in cmd


def test_verify_fallback_love_and_bevy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _bare_fingerprint(monkeypatch)
    (tmp_path / "main.lua").write_text("", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/luac" if n == "luac" else None)
    assert discover_verify_command(_runtime(tmp_path)) == "luac -p main.lua"
    (tmp_path / "main.lua").unlink()
    (tmp_path / "Cargo.toml").write_text('[dependencies]\nbevy = "0.14"\n', encoding="utf-8")
    assert discover_verify_command(_runtime(tmp_path)) == "cargo check"
