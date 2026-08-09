"""Shared runtime: session streaming tracking + parallel multi-provider turns."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.llm_binding import LlmBinding, get_llm_binding, set_llm_binding
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
async def test_parallel_streams_interleave(runtime: BasicRuntime) -> None:
    """Full multi-provider: two turns stream concurrently (not whole-turn lock)."""
    order: list[str] = []
    barrier = asyncio.Event()
    started = 0

    async def fake_stream(*_a, **_k):
        nonlocal started
        started += 1
        order.append("start")
        if started >= 2:
            barrier.set()
        # Wait until both turns have entered the stream body
        await asyncio.wait_for(barrier.wait(), timeout=2.0)
        await asyncio.sleep(0.02)
        yield "tok"
        order.append("end")

    runtime._call_llm_stream = fake_stream  # type: ignore[method-assign]

    async def run(tag: str) -> None:
        async for _ in runtime.stream_response(f"msg-{tag}", session_id=tag):
            pass
        order.append(f"done-{tag}")

    await asyncio.gather(run("a"), run("b"))
    assert order.count("start") == 2
    assert order.count("end") == 2
    # Both starts before either end → parallel (serialized would be start,end,start,end)
    first_end = min(i for i, x in enumerate(order) if x == "end")
    assert order[:first_end].count("start") == 2


@pytest.mark.asyncio
async def test_binding_isolated_per_turn(runtime: BasicRuntime) -> None:
    """Each concurrent turn keeps its own provider/model ContextVar binding."""
    seen: dict[str, tuple[str, str]] = {}

    async def fake_stream(message: str, **kwargs):
        bind = get_llm_binding(runtime)
        # message is "msg-{tag}" from stream_response; session_id is more reliable
        sid = str(kwargs.get("session_id") or "")
        seen[sid] = (bind.provider, bind.model)
        await asyncio.sleep(0.03)
        yield "ok"

    runtime._call_llm_stream = fake_stream  # type: ignore[method-assign]

    # Bypass credential resolve; inject distinct binds via short-circuited stream path
    # by temporarily patching the lock section: pass provider/model and mock sync.
    async def run(sid: str, prov: str, mod: str) -> None:
        async for _ in runtime.stream_response(
            f"msg-{sid}",
            session_id=sid,
            provider=prov,
            model=mod,
        ):
            pass

    # Avoid real config sync overwriting keys — set attrs under lock is fine;
    # stream_response freezes LlmBinding after _sync which may no-op without config.
    from unittest.mock import patch

    def fake_sync(rt, model_override=None, provider_override=None, llm_only=True):
        if provider_override:
            rt._llm_provider = provider_override
            rt._provider = type(rt._provider)  # keep object; adapter resolved via get_provider
            from remedy.core.providers import get_provider

            rt._provider = get_provider(provider_override)
        if model_override:
            rt._llm_model = model_override

    with patch(
        "remedy.interfaces.api_support._sync_runtime_llm_from_config",
        side_effect=fake_sync,
    ):
        await asyncio.gather(
            run("sess-xai", "xai", "grok-4.5"),
            run("sess-ds", "deepseek", "deepseek-chat"),
        )

    assert seen.get("sess-xai") == ("xai", "grok-4.5")
    assert seen.get("sess-ds") == ("deepseek", "deepseek-chat")


def test_llm_binding_contextvar_stack() -> None:
    a = LlmBinding(provider="xai", model="g", base_url="https://a", api_key="k1")
    b = LlmBinding(provider="deepseek", model="d", base_url="https://b", api_key="k2")
    tok_a = set_llm_binding(a)
    assert get_llm_binding().provider == "xai"
    tok_b = set_llm_binding(b)
    assert get_llm_binding().model == "d"
    from remedy.core.llm_binding import reset_llm_binding

    reset_llm_binding(tok_b)
    assert get_llm_binding().provider == "xai"
    reset_llm_binding(tok_a)


@pytest.mark.asyncio
async def test_abort_one_session_leaves_other_streaming(runtime: BasicRuntime) -> None:
    """Concurrent multi-provider: Stop on sess-a must not abort sess-b."""
    from remedy.core.turn_context import abort_session, is_turn_aborted

    order: list[str] = []
    both_started = asyncio.Event()
    started = 0
    hold_b = asyncio.Event()

    async def fake_stream(*_a, **kwargs):
        nonlocal started
        sid = str(kwargs.get("session_id") or "")
        started += 1
        order.append(f"start-{sid}")
        if started >= 2:
            both_started.set()
        # Stay in body until aborted (a) or released (b).
        for _ in range(200):
            if is_turn_aborted():
                order.append(f"aborted-{sid}")
                yield "@@aborted\n"
                return
            if sid == "sess-b" and hold_b.is_set():
                break
            await asyncio.sleep(0.01)
        order.append(f"tok-{sid}")
        yield "ok"

    runtime._call_llm_stream = fake_stream  # type: ignore[method-assign]

    async def run_a() -> None:
        async for tok in runtime.stream_response("msg-a", session_id="sess-a"):
            if isinstance(tok, str) and tok.startswith("@@aborted"):
                order.append("got-abort-a")

    async def run_b() -> None:
        async for tok in runtime.stream_response("msg-b", session_id="sess-b"):
            order.append(f"b-saw:{tok!r}" if not str(tok).startswith("@@") else "b-ctl")
        order.append("done-b")

    t_a = asyncio.create_task(run_a())
    t_b = asyncio.create_task(run_b())
    await asyncio.wait_for(both_started.wait(), timeout=2.0)
    # Abort only A while B is mid-stream.
    n = abort_session("sess-a")
    assert n >= 1
    await asyncio.wait_for(t_a, timeout=2.0)
    assert "got-abort-a" in order or "aborted-sess-a" in order
    # B still streaming — release and finish cleanly.
    assert runtime.is_session_streaming("sess-b") or "done-b" not in order
    hold_b.set()
    await asyncio.wait_for(t_b, timeout=2.0)
    assert "done-b" in order
    assert "aborted-sess-b" not in order
    assert "got-abort-a" in order or any(x.startswith("aborted-sess-a") for x in order)


@pytest.mark.asyncio
async def test_parallel_abort_registry_isolated() -> None:
    """begin_turn registry: abort sess-1 never sets sess-2's event."""
    from remedy.core.turn_context import (
        abort_session,
        begin_turn,
        end_turn,
        is_turn_aborted,
    )

    # Nested-style sequential ContextVars cannot both be current; use registry
    # notify count + is_session_streaming instead of dual is_turn_aborted.
    t1 = begin_turn("iso-1", project_raw=None, active_path=".")
    try:
        t2_tokens = None
        # Register second session on a nested begin (overwrites ContextVar but
        # keeps both abort Events in the registry).
        t2_tokens = begin_turn("iso-2", project_raw=None, active_path=".")
        try:
            from remedy.core.turn_context import is_session_streaming

            assert is_session_streaming("iso-1")
            assert is_session_streaming("iso-2")
            n = abort_session("iso-1")
            assert n == 1
            # Abort clears registry immediately so UI Stop unblocks 409 resend
            assert is_session_streaming("iso-1") is False
            assert is_session_streaming("iso-2")
            # Current ContextVar is iso-2 — must NOT be aborted.
            assert is_turn_aborted() is False
        finally:
            if t2_tokens is not None:
                end_turn("iso-2", *t2_tokens)
    finally:
        end_turn("iso-1", *t1)
