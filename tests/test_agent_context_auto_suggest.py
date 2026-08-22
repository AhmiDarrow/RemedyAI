"""The auto-suggest injection closes the loop: it is an activation, and it is
remembered for end-of-turn grading.

Before this, a skill whose procedure was injected into context never showed
up in ``skill_stats.json`` — only explicit ``skill_activate`` calls did — so
the lifecycle could not tell a useful learned skill from dead weight.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from remedy.core.agent_context import build_turn_context
from remedy.memory.profile import UserProfile


class _FakeTools:
    tools: list = []


class _Loop:
    def __init__(self) -> None:
        self.activations: list[tuple[str, str]] = []

    def record_skill_activation(self, name: str, session_id: str = "") -> None:
        self.activations.append((name, session_id))


def _runtime(text: str, reg, loop):
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
        effective_project_path=lambda: ".",
        access_scope=lambda: "project",
        allowed_roots=lambda: [],
        project_path_is_unset=lambda: False,
    )
    rt._get_learning_loop = lambda: loop
    return rt


@pytest.mark.asyncio
async def test_auto_suggest_records_activation_and_remembers_skill():
    from remedy.skills.registry import SkillRegistry

    reg = SkillRegistry()
    assert reg.discover(Path("skills")) >= 1
    assert reg.get("change-safety") is not None

    loop = _Loop()
    rt = _runtime("review this", reg, loop)
    ctx = await build_turn_context(rt)

    assert "[Skill auto-suggest]" in ctx
    assert "change-safety" in ctx
    assert loop.activations == [("change-safety", "sess-42")]
    assert rt._turn_auto_suggested_skill == "change-safety"


@pytest.mark.asyncio
async def test_auto_suggest_survives_a_broken_learning_loop():
    from remedy.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.discover(Path("skills"))

    class _Boom:
        def record_skill_activation(self, *a, **k):
            raise RuntimeError("stats file locked")

    rt = _runtime("review this", reg, _Boom())
    ctx = await build_turn_context(rt)
    assert "[Skill auto-suggest]" in ctx
    # Still remembered for grading even though the stats write failed
    assert rt._turn_auto_suggested_skill == "change-safety"


@pytest.mark.asyncio
async def test_no_learning_loop_is_fine():
    from remedy.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.discover(Path("skills"))
    rt = _runtime("review this", reg, None)
    ctx = await build_turn_context(rt)
    assert "[Skill auto-suggest]" in ctx
    assert rt._turn_auto_suggested_skill == "change-safety"


