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
