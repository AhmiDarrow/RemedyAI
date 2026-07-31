"""skill_reload rescan + refuse bulk skill_activate (anti-loop)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.agent_skill_tools import register_skill_tools
from remedy.skills.registry import SkillRegistry
from remedy.skills.tool_registry import ToolRegistry


def _runtime(tmp_path: Path) -> SimpleNamespace:
    home = tmp_path / "home"
    home.mkdir()
    reg = SkillRegistry()
    reg.discover_defaults(home_dir=home)
    tr = ToolRegistry()
    return SimpleNamespace(
        skills=reg,
        tool_registry=tr,
        config=SimpleNamespace(home_dir=str(home)),
        effective_project_path=lambda: str(tmp_path),
        _session_id="test",
        _get_learning_loop=lambda: None,
    )


@pytest.mark.asyncio
async def test_skill_activate_refuses_bulk_all(tmp_path: Path):
    rt = _runtime(tmp_path)
    register_skill_tools(rt)
    for bulk in ("all", "reload", "*", "every skill"):
        out = await rt.tool_registry.execute("skill_activate", skill=bulk)
        assert "Refusing bulk" in out or "skill_reload" in out
        assert "Self-dev" in out or "self-dev-loop" in out or "skill_search" in out


@pytest.mark.asyncio
async def test_skill_reload_rescans_without_bodies(tmp_path: Path):
    rt = _runtime(tmp_path)
    register_skill_tools(rt)
    out = await rt.tool_registry.execute("skill_reload")
    assert "rescanned" in out.lower() or "Registry count" in out
    assert "skill_activate every" in out.lower() or "Do not skill_activate" in out
    # Seeds curated packs into home
    assert (tmp_path / "home" / "skills" / "change-safety" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_skill_activate_self_dev_loop(tmp_path: Path):
    rt = _runtime(tmp_path)
    register_skill_tools(rt)
    out = await rt.tool_registry.execute("skill_activate", skill="self-dev-loop")
    assert "Self-dev" in out or "dogfood" in out.lower()
    assert "skill_activate" in out.lower() or "gauntlet" in out.lower()
