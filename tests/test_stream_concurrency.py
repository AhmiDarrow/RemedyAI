"""Shared runtime: session streaming tracking + LLM turn lock behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from remedy.core.agent import BasicRuntime
from remedy.models import AgentConfig


@pytest.fixture
def runtime(tmp_path: Path) -> BasicRuntime:
    cfg = AgentConfig(
        name="test",
        project_path=str(tmp_path),
        llm_provider="openai",
        llm_model="gpt-test",
        llm_api_key="sk-test",
        llm_base_url="http://127.0.0.1:9/v1",
    )
    return BasicRuntime(cfg, memory=None)


def test_streaming_is_per_session(runtime: BasicRuntime) -> None:
    assert runtime._streaming is False
    runtime._streaming_sessions.add("sess-a")
    assert runtime._streaming is True
    assert runtime.is_session_streaming("sess-a") is True
    assert runtime.is_session_streaming("sess-b") is False
    runtime._streaming = False  # legacy clear-all
    assert runtime._streaming is False
    assert runtime.is_session_streaming("sess-a") is False


@pytest.mark.asyncio
async def test_llm_turn_lock_serializes_streams(runtime: BasicRuntime) -> None:
    """Second stream waits until first releases the LLM lock."""
    order: list[str] = []

    async def fake_stream(*_a, **_k):
        order.append("start")
        await asyncio.sleep(0.05)
        yield "tok"
        order.append("end")

    runtime._call_llm_stream = fake_stream  # type: ignore[method-assign]

    async def run(tag: str) -> None:
        async for _ in runtime.stream_response(f"msg-{tag}", session_id=tag):
            pass
        order.append(f"done-{tag}")

    await asyncio.gather(run("a"), run("b"))
    # Serialized: start/end of one fully before the other (or nested start-end pairs sequential)
    # With lock: start, end, done-X, start, end, done-Y (order of a/b may vary)
    assert order.count("start") == 2
    assert order.count("end") == 2
    # No interleaving of two turns: after first start, next is end (not second start)
    first_start = order.index("start")
    assert order[first_start + 1] == "end"
