"""Turn context assembly (extracted from agent.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from remedy.core.agent_context import build_turn_context
from remedy.memory.profile import UserProfile


class _FakeTools:
    tools: list = []


@pytest.mark.asyncio
async def test_build_turn_context_includes_partner_and_scope():
    profile = UserProfile(display_name="Sam")
    mem = MagicMock()
    mem.get_or_create_profile = AsyncMock(return_value=profile)
    mem.save_user_profile = AsyncMock()
    mem.list_recent = AsyncMock(return_value=[])

    runtime = SimpleNamespace(
        memory=mem,
        config=SimpleNamespace(project_path=None),
        _project_path=None,
        _last_user_text="hello",
        _session_brief=None,
        tool_registry=_FakeTools(),
        skills=None,
        effective_project_path=lambda: ".",
        access_scope=lambda: "full",
        allowed_roots=lambda: [],
        project_path_is_unset=lambda: True,
    )

    ctx = await build_turn_context(runtime)
    assert "Access scope: full" in ctx
    assert "Call the user: Sam" in ctx or "Sam" in ctx


@pytest.mark.asyncio
async def test_build_turn_context_uses_warm_skills_cache():
    """Warm skill rank cache skips re-ranking on the hot path."""
    from remedy.nanoswarm import get_swarm

    profile = UserProfile(display_name="Sam")
    mem = MagicMock()
    mem.get_or_create_profile = AsyncMock(return_value=profile)
    mem.save_user_profile = AsyncMock()
    mem.list_recent = AsyncMock(return_value=[])

    class _Reg:
        count = 10

        def match_skills(self, *a, **k):
            raise AssertionError("match_skills should not run when warm cache is set")

        def summary_lines(self, *a, **k):
            raise AssertionError("summary_lines should not run when warm cache is set")

    swarm = get_swarm()
    prev = list(getattr(swarm.skill, "_rank_cache", None) or [])
    try:
        swarm.skill._rank_cache = [
            "- **change-safety** [active]: blast radius",
            "- **project-etiquette** [active]: ship gates",
            "- **refactor-safe** [active]: careful edits",
            "_Activate with skill_activate(name=…); rank with skill_search._",
        ]
        runtime = SimpleNamespace(
            memory=mem,
            config=SimpleNamespace(project_path=None),
            _project_path=None,
            _last_user_text="hi",
            _session_brief=None,
            tool_registry=_FakeTools(),
            skills=_Reg(),
            effective_project_path=lambda: ".",
            access_scope=lambda: "project",
            allowed_roots=lambda: [],
            project_path_is_unset=lambda: False,
        )
        ctx = await build_turn_context(runtime)
        assert "change-safety" in ctx
        assert "Skills catalog" in ctx
        # Greets must not auto-inject full procedure bodies
        assert "[Skill auto-suggest]" not in ctx
    finally:
        swarm.skill._rank_cache = prev


@pytest.mark.asyncio
async def test_review_project_auto_suggests_procedure():
    """'review project' re-ranks and injects change-safety procedure into context."""
    from pathlib import Path

    from remedy.skills.registry import SkillRegistry

    profile = UserProfile(display_name="Sam")
    mem = MagicMock()
    mem.get_or_create_profile = AsyncMock(return_value=profile)
    mem.save_user_profile = AsyncMock()
    mem.list_recent = AsyncMock(return_value=[])

    reg = SkillRegistry()
    n = reg.discover(Path("skills"))
    assert n >= 1
    assert reg.get("change-safety") is not None

    runtime = SimpleNamespace(
        memory=mem,
        config=SimpleNamespace(project_path=None),
        _project_path=None,
        _last_user_text="review project",
        _turn_user_text="review project",
        _session_brief=None,
        tool_registry=_FakeTools(),
        skills=reg,
        effective_project_path=lambda: ".",
        access_scope=lambda: "project",
        allowed_roots=lambda: [],
        project_path_is_unset=lambda: False,
    )
    ctx = await build_turn_context(runtime)
    assert "Skills catalog" in ctx
    assert "change-safety" in ctx
    assert "[Skill auto-suggest]" in ctx
    # Procedure body (blast-radius checklist), not catalog-only
    assert "blast radius" in ctx.lower() or "Blast-radius" in ctx
    assert "skill_activate" in ctx
