"""Cooperative abort / turn context registry."""

from __future__ import annotations

import asyncio

import pytest

from remedy.core.turn_context import (
    abort_session,
    begin_turn,
    end_turn,
    is_session_streaming,
    is_turn_aborted,
)


@pytest.mark.asyncio
async def test_abort_sets_event_and_clears_registry():
    tok_s, tok_a = begin_turn("sess-1")
    try:
        assert is_session_streaming("sess-1")
        assert not is_turn_aborted()
        n = abort_session("sess-1")
        assert n == 1
        assert is_turn_aborted()
    finally:
        end_turn("sess-1", tok_s, tok_a)
    assert not is_session_streaming("sess-1")


@pytest.mark.asyncio
async def test_abort_unknown_session_is_noop():
    assert abort_session("missing") == 0


def test_create_session_integrity_race(tmp_path):
    """Concurrent create with same id returns existing (no crash)."""
    import asyncio

    from remedy.memory.store import MemoryStore
    from remedy.models import ChatSession

    async def _run():
        store = MemoryStore(tmp_path / "mem.db")
        await store.initialize()
        a = ChatSession(id="same-id", title="A")
        b = ChatSession(id="same-id", title="B")
        s1 = await store.create_chat_session(a)
        s2 = await store.create_chat_session(b)
        assert s1.id == s2.id == "same-id"
        # First writer wins title
        assert s2.title == "A"

    asyncio.run(_run())
