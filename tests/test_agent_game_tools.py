"""Native game tools: Godot run/check/export/import + playtest loop (no real engine)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from remedy.core import agent_game_tools as gt
from remedy.skills.tool_registry import ToolRegistry


@dataclass
class _Res:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


class _Rt:
    def __init__(self, root: Path) -> None:
        self.tool_registry = ToolRegistry()
        self._root = root

    def effective_project_path(self) -> Path:
        return self._root

    def resolve_tool_path(self, path: str, *, for_write: bool = False) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self._root / p

    def allowed_roots(self) -> list[Path]:
        return []

    def write_roots(self) -> list[Path]:
        return [self._root]

    def access_scope(self) -> str:
        return "project"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GODOT", raising=False)
    monkeypatch.delenv("GODOT4_BIN", raising=False)
    root = tmp_path / "game"
    root.mkdir()
    (root / "project.godot").write_text("[application]\nconfig/name=\"G\"\n", encoding="utf-8")
    (root / "main.gd").write_text("extends Node\n\nfunc _ready():\n\tpass\n", encoding="utf-8")
    (root / "bad.gd").write_text("extends Node\nfunc _ready()\n\tprint((\n", encoding="utf-8")
    (root / "main.tscn").write_text(
        '[gd_scene load_steps=2 format=3]\n\n[node name="Main" type="Node2D"]\n',
        encoding="utf-8",
    )
    (root / ".godot").mkdir()
    (root / ".godot" / "skip.gd").write_text("x(\n", encoding="utf-8")
    return root


@pytest.fixture
def rt(project: Path, monkeypatch) -> _Rt:
    monkeypatch.setattr(gt, "_approval_block", lambda *a, **k: None)
    r = _Rt(project)
    gt.register_game_tools(r)
    return r


@pytest.fixture
def fake_godot(tmp_path: Path, monkeypatch) -> Path:
    exe = tmp_path / "Godot_v4_console.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(gt.game_engines, "find_engine_binary", lambda *a, **k: exe)
    return exe


@pytest.fixture
def runs(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_run(runtime, argv, *, cwd, timeout):
        calls.append({"argv": list(argv), "cwd": cwd, "timeout": timeout})
        return _Res(0, "Godot Engine v4\nok", "")

    monkeypatch.setattr(gt, "_sandbox_run", fake_run)
    return calls


def test_registration_names(rt: _Rt):
    names = set(rt.tool_registry._handlers)
    assert {
        "game_project_info",
        "godot_run",
        "godot_check",
        "godot_export",
        "godot_import",
        "game_playtest",
    } <= names


@pytest.mark.asyncio
async def test_game_project_info(rt: _Rt, project: Path, monkeypatch):
    monkeypatch.setattr(gt.game_engines, "find_engine_binary", lambda *a, **k: None)
    data = json.loads(await rt.tool_registry.execute("game_project_info"))
    assert data["engine"].get("name") == "godot"
    assert data["counts"][".gd"] == 2  # .godot/ skipped
    assert data["counts"][".tscn"] == 1
    assert data["skill"] == "godot-4"


@pytest.mark.asyncio
async def test_godot_run_headless_argv_and_clamp(rt: _Rt, project: Path, fake_godot, runs):
    out = await rt.tool_registry.execute(
        "godot_run", script="tools/smoke.gd", timeout_seconds=1, quit_after=3, extra_args="--verbose"
    )
    assert "exit_code=0" in out
    call = runs[0]
    assert call["argv"] == [
        str(fake_godot),
        "--path",
        str(project),
        "--headless",
        "-s",
        "tools/smoke.gd",
        "--quit-after",
        "3",
        "--verbose",
    ]
    assert call["timeout"] == 5.0
    assert call["cwd"] == project

    out = await rt.tool_registry.execute("godot_run", scene="main.tscn", timeout_seconds=9999)
    assert runs[1]["argv"][-1] == "main.tscn"
    assert runs[1]["timeout"] == 600.0
    assert "timeout_s=600" in out


@pytest.mark.asyncio
async def test_godot_run_no_binary(rt: _Rt, monkeypatch, runs):
    monkeypatch.setattr(gt.game_engines, "find_engine_binary", lambda *a, **k: None)
    out = await rt.tool_registry.execute("godot_run")
    assert "NO_ENGINE" in out
    assert "GODOT" in out and "Godot*.exe" in out and "tell me where Godot is" in out
    assert runs == []


@pytest.mark.asyncio
async def test_godot_run_no_project(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gt, "_approval_block", lambda *a, **k: None)
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _Rt(empty)
    gt.register_game_tools(r)
    out = await r.tool_registry.execute("godot_run")
    assert "NO_GODOT_PROJECT" in out


@pytest.mark.asyncio
async def test_godot_run_windowed_spawns_background(rt: _Rt, project: Path, fake_godot, runs, monkeypatch):
    spawned: list[dict] = []

    def fake_spawn(argv, *, cwd, command):
        spawned.append({"argv": argv, "cwd": cwd})
        return f"started background pid=4242 cwd={cwd}\ncommand={command}"

    monkeypatch.setattr(gt, "_spawn", fake_spawn)
    out = await rt.tool_registry.execute("godot_run", headless=False, scene="main.tscn")
    assert "pid=4242" in out and "game_playtest" in out
    assert "--headless" not in spawned[0]["argv"]
    assert runs == []


@pytest.mark.asyncio
async def test_godot_check_without_binary_uses_tokenizer(rt: _Rt, monkeypatch, runs):
    monkeypatch.setattr(gt.game_engines, "find_engine_binary", lambda *a, **k: None)
    data = json.loads(await rt.tool_registry.execute("godot_check"))
    by_path = {r["path"]: r for r in data["results"]}
    assert set(by_path) == {"main.gd", "bad.gd", "main.tscn"}
    assert by_path["main.gd"]["ok"] and by_path["main.gd"]["engine"] == "tokenizer"
    assert not by_path["bad.gd"]["ok"]
    assert by_path["main.tscn"]["ok"]
    assert data["ok"] is False
    assert "tokenizer" in data["summary"]
    assert runs == []


@pytest.mark.asyncio
async def test_godot_check_with_binary_runs_check_only(rt: _Rt, project: Path, fake_godot, monkeypatch):
    async def fake_run(runtime, argv, *, cwd, timeout):
        if argv[-1] == "bad.gd":
            return _Res(1, "", "SCRIPT ERROR: Parse Error: Expected ':'")
        return _Res(0, "", "")

    monkeypatch.setattr(gt, "_sandbox_run", fake_run)
    data = json.loads(await rt.tool_registry.execute("godot_check", paths="main.gd,bad.gd,*.tscn"))
    by_path = {r["path"]: r for r in data["results"]}
    assert by_path["main.gd"] == {"path": "main.gd", "ok": True, "engine": "godot", "error": ""}
    assert "Parse Error" in by_path["bad.gd"]["error"]
    assert by_path["main.tscn"]["ok"]
    assert data["summary"].startswith("3 checked, 1 failed")


@pytest.mark.asyncio
async def test_godot_check_argv_shape(rt: _Rt, project: Path, fake_godot, runs):
    await rt.tool_registry.execute("godot_check", paths="main.gd", scenes=False)
    assert runs[0]["argv"] == [
        str(fake_godot), "--headless", "--path", str(project), "--check-only", "-s", "main.gd"
    ]


def test_parse_export_presets():
    text = (
        "[preset.0]\n\nname=\"Windows\"\nplatform=\"Windows Desktop\"\n"
        "export_path=\"build/game.exe\"\n\n[preset.0.options]\n\nname=\"nope\"\n\n"
        "[preset.1]\n\nname=\"Web\"\nplatform=\"Web\"\nexport_path=\"build/web/index.html\"\n"
    )
    presets = gt.parse_export_presets(text)
    assert [p["name"] for p in presets] == ["Windows", "Web"]
    assert presets[0]["platform"] == "Windows Desktop"
    assert presets[1]["export_path"] == "build/web/index.html"


@pytest.mark.asyncio
async def test_godot_export_list_jail_and_argv(rt: _Rt, project: Path, fake_godot, runs, tmp_path):
    (project / "export_presets.cfg").write_text(
        "[preset.0]\nname=\"Windows\"\nplatform=\"Windows Desktop\"\nexport_path=\"build/g.exe\"\n",
        encoding="utf-8",
    )
    data = json.loads(await rt.tool_registry.execute("godot_export", list=True))
    assert data["presets"][0]["name"] == "Windows"

    out = await rt.tool_registry.execute(
        "godot_export", preset="Windows", output=str(tmp_path / "elsewhere" / "g.exe")
    )
    assert "WRITE_JAIL" in out
    assert runs == []

    out = await rt.tool_registry.execute("godot_export", preset="Windows", output="build/g.exe", debug=True)
    assert "exit_code=0" in out
    assert runs[0]["argv"] == [
        str(fake_godot),
        "--headless",
        "--path",
        str(project),
        "--export-debug",
        "Windows",
        str(project / "build" / "g.exe"),
    ]
    assert (project / "build").is_dir()


@pytest.mark.asyncio
async def test_godot_import(rt: _Rt, project: Path, fake_godot, runs):
    imported = project / ".godot" / "imported"
    imported.mkdir()
    (imported / "a.ctex").write_bytes(b"")
    (imported / "b.ctex").write_bytes(b"")
    out = await rt.tool_registry.execute("godot_import")
    assert runs[0]["argv"] == [str(fake_godot), "--headless", "--path", str(project), "--import"]
    assert "imported_entries=2" in out


@pytest.mark.asyncio
async def test_game_playtest_loop(rt: _Rt, project: Path, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(gt.asyncio, "sleep", fake_sleep)
    spawned: list[str] = []
    monkeypatch.setattr(
        gt, "_spawn", lambda argv, *, cwd, command: spawned.append(command) or "started background pid=7 cwd=x"
    )
    shots = iter([project / f"shot{i}.png" for i in range(10)])
    keys: list[str] = []
    reg = rt.tool_registry

    async def computer_screenshot(target="auto", **kw):
        return f"Desktop capture (800x600)\npath={next(shots)}"

    async def computer_key(key="", target="auto", **kw):
        keys.append(key)
        return "ok"

    async def vision_decode(action="status", path="", question="", **kw):
        return f"saw {Path(path).name}: {question}"

    reg.register_builtin_handler("computer_screenshot", "", computer_screenshot)
    reg.register_builtin_handler("computer_key", "", computer_key)
    reg.register_builtin_handler("vision_decode", "", vision_decode)
    log = project / "run.log"
    log.write_text("\n".join(f"line{i}" for i in range(50)), encoding="utf-8")

    out = await reg.execute(
        "game_playtest",
        command=f'godot --path . --log-file "{log}"',
        seconds=10,
        interval=5,
        keys="right:300,space",
        question="is the player visible?",
    )
    assert spawned and "--log-file" in spawned[0]
    assert sleeps == [3, 5]  # launch settle + one interval between the two shots
    assert "shot0.png" in out and "shot1.png" in out and "shot2.png" not in out
    assert keys == ["right", "right", "right", "space"] * 2
    assert "line49" in out and "line9\n" not in out
    assert "vision: saw shot1.png: is the player visible?" in out


@pytest.mark.asyncio
async def test_game_playtest_returns_approval_block(rt: _Rt, monkeypatch):
    monkeypatch.setattr(gt.asyncio, "sleep", lambda s: asyncio.sleep(0))

    async def computer_screenshot(**kw):
        return "APPROVAL_REQUIRED id=ap1\nreason=computer"

    rt.tool_registry.register_builtin_handler("computer_screenshot", "", computer_screenshot)
    out = await rt.tool_registry.execute("game_playtest", pid=5, seconds=5)
    assert "APPROVAL_REQUIRED id=ap1" in out


def test_timeouts_resolve(rt: _Rt):
    from remedy.core.tool_timeouts import tool_timeout_for

    reg = rt.tool_registry
    assert tool_timeout_for("godot_run", reg) == 900.0
    assert tool_timeout_for("godot_check", reg) == 900.0
    assert tool_timeout_for("godot_export", reg) == 900.0
    assert tool_timeout_for("game_playtest", reg) == 300.0
    assert tool_timeout_for("game_project_info", reg) == 30.0


def test_phase_lists_include_game_tools():
    from remedy.core.endless_context import EXPAND_TOOL_PACK
    from remedy.core.react_turn import _VERIFY_TOOLS

    assert {"godot_run", "godot_check", "game_playtest"} <= set(EXPAND_TOOL_PACK)
    assert {"godot_run", "godot_check"} <= _VERIFY_TOOLS


def test_registered_in_workspace_tools():
    import inspect

    from remedy.core import agent_workspace_tools

    assert "register_game_tools" in inspect.getsource(agent_workspace_tools)
