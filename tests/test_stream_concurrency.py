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
