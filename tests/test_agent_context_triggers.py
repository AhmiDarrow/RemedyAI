"""Frontmatter ``triggers:`` + the project's engine drive skill auto-suggest."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from remedy.core.agent_context import build_turn_context
from remedy.memory.profile import UserProfile
from remedy.skills.loader import load_skill_from_dir
from remedy.skills.registry import SkillRegistry


class _FakeTools:
    tools: list = []


class _Loop:
    def __init__(self) -> None:
        self.activations: list[tuple[str, str]] = []

    def record_skill_activation(self, name: str, session_id: str = "") -> None:
        self.activations.append((name, session_id))


def _runtime(text: str, reg, loop, project: str = "."):
    profile = UserProfile(display_name="Sam")
    mem = MagicMock()
    mem.get_or_create_profile = AsyncMock(return_value=profile)
    mem.save_user_profile = AsyncMock()
    mem.list_recent = AsyncMock(return_value=[])
    rt = SimpleNamespace(
        memory=mem,
        config=SimpleNamespace(project_path=None),
        _project_path=None,
        _last_user_text=text,
        _turn_user_text=text,
        _session_brief=None,
        _session_id="sess-42",
        tool_registry=_FakeTools(),
        skills=reg,
        effective_project_path=lambda: project,
        access_scope=lambda: "project",
        allowed_roots=lambda: [],
        project_path_is_unset=lambda: False,
    )
    rt._get_learning_loop = lambda: loop
    return rt


def _mk(root: Path, name: str, triggers: list[str]):
    d = root / "skills" / name
    d.mkdir(parents=True)
    lines = [
        "---",
        f"name: {name}",
        f"description: the {name} skill",
        "version: 1.0.0",
        "status: active",
        "triggers:",
    ]
    # Single-quoted YAML keeps regex backslashes verbatim.
    lines += [f"  - '{t}'" for t in triggers]
    lines += ["---", "", f"# {name}", "", f"BODY-OF-{name}", ""]
    (d / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return load_skill_from_dir(d)


@pytest.fixture()
def reg(tmp_path):
    r = SkillRegistry()
    r.register(_mk(tmp_path, "godot-4", [r"\bgodot\b"]))
    r.register(_mk(tmp_path, "game-dev-studio", [r"\bplatformer\b"]))
    r.register(_mk(tmp_path, "change-safety", [r"\bblast.?radius\b"]))
    return r


@pytest.mark.asyncio
async def test_the_project_engine_skill_wins_for_a_game_ish_ask(tmp_path, reg):
    proj = tmp_path / "game"
    proj.mkdir()
    (proj / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/features=PackedStringArray("4.3")\n',
        encoding="utf-8",
    )
    loop = _Loop()
    rt = _runtime("add a double jump for the player", reg, loop, project=str(proj))
    ctx = await build_turn_context(rt)
    assert "BODY-OF-godot-4" in ctx
    assert loop.activations == [("godot-4", "sess-42")]


@pytest.mark.asyncio
async def test_a_trigger_routes_outside_any_game_project(reg):
    ctx = await build_turn_context(_runtime("make a platformer", reg, _Loop()))
    assert "BODY-OF-game-dev-studio" in ctx
    ctx = await build_turn_context(_runtime("check the blast radius of this", reg, _Loop()))
    assert "BODY-OF-change-safety" in ctx


@pytest.mark.asyncio
async def test_no_trigger_and_no_engine_injects_nothing_from_these(reg):
    ctx = await build_turn_context(_runtime("what time is it", reg, _Loop()))
    assert "BODY-OF-" not in ctx
