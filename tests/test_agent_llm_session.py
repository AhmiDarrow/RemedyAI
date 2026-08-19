"""The shared aiohttp session behind every LLM call has to actually close.

``close_shared_session`` used to be a bare ``pass`` under a comment admitting
that ``ClientSession.close()`` is async and "the caller should await" — and no
caller ever did, because nothing in the tree imports it. So the connection pool
behind every model call was dereferenced but never closed, and anyone who did
wire the function up would have leaked one pool per shutdown, since dropping the
reference makes the next call build a fresh session.
"""

from __future__ import annotations

import asyncio

import pytest

from remedy.core import agent_llm


@pytest.fixture(autouse=True)
def _clean_session():
    agent_llm._shared_session = None
    yield
    agent_llm._shared_session = None


@pytest.mark.asyncio
async def test_the_session_is_reused_until_it_is_closed():
    first = agent_llm._get_shared_session()
    assert agent_llm._get_shared_session() is first
    await agent_llm.aclose_shared_session()
    assert first.closed
    assert agent_llm._get_shared_session() is not first
    await agent_llm.aclose_shared_session()


@pytest.mark.asyncio
async def test_awaiting_the_close_really_closes_it():
    session = agent_llm._get_shared_session()
    assert not session.closed
    await agent_llm.aclose_shared_session()
    assert session.closed, "the socket pool was left open"
    assert agent_llm._shared_session is None


@pytest.mark.asyncio
async def test_the_sync_close_closes_it_too():
    """It is the name that already exists and is documented as the shutdown
    hook, so it has to work from sync code rather than quietly do nothing."""
    session = agent_llm._get_shared_session()
    agent_llm.close_shared_session()
    assert agent_llm._shared_session is None
    for _ in range(10):
        if session.closed:
            break
        await asyncio.sleep(0)
    assert session.closed


@pytest.mark.asyncio
async def test_closing_twice_is_harmless():
    agent_llm._get_shared_session()
    await agent_llm.aclose_shared_session()
    await agent_llm.aclose_shared_session()
    agent_llm.close_shared_session()
    assert agent_llm._shared_session is None
