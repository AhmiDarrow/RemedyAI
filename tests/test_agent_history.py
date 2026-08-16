"""Session history loader (extracted from agent)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from remedy.core.agent_history import load_session_history
from remedy.models import ChatMessageRole


@pytest.mark.asyncio
async def test_load_history_drops_duplicate_trailing_user():
    rows = [
        SimpleNamespace(role=ChatMessageRole.USER, content="first"),
        SimpleNamespace(role=ChatMessageRole.ASSISTANT, content="reply"),
        SimpleNamespace(role=ChatMessageRole.USER, content="current"),
    ]
    mem = SimpleNamespace(get_chat_messages=AsyncMock(return_value=rows))
    out = await load_session_history(mem, "s1", "current")
    assert len(out) == 2
    assert out[0]["role"] == "user"
    assert out[0]["content"] == "first"
    assert out[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_load_history_skips_internal_assistant_markers():
    rows = [
        SimpleNamespace(role=ChatMessageRole.ASSISTANT, content="@@tool-start"),
        SimpleNamespace(role=ChatMessageRole.ASSISTANT, content="real answer"),
        SimpleNamespace(role=ChatMessageRole.USER, content="hi"),
    ]
    mem = SimpleNamespace(get_chat_messages=AsyncMock(return_value=rows))
    out = await load_session_history(mem, "s1", "other")
    roles = [m["role"] for m in out]
    assert "assistant" in roles
    assert all(not m["content"].startswith("@@") for m in out)
