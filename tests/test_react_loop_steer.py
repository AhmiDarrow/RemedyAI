"""Mid-turn steering: what the owner says while she works joins the turn.

No stop, no restart. The words are queued per session by
``turn_context.push_nudge`` and the ReAct loop drains them between steps
(and before it would otherwise finish), as plain user messages.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from remedy.core import turn_context as tc
from remedy.core.agent import BasicRuntime
from remedy.core.react_loop.loop import call_llm_stream
from remedy.models import AgentConfig
from tests.harness.fake_llm import FakeLLM, FakeToolRegistry, text_turn, tool_turn


def make_runtime(tmp_path: Path) -> BasicRuntime:
    (tmp_path / "proj").mkdir(exist_ok=True)
    runtime = BasicRuntime(
        AgentConfig(
            name="test",
            home_dir=str(tmp_path / "home"),
            project_path=str(tmp_path / "proj"),
            llm_provider="openai",
            llm_model="fake-model",
            llm_api_key="sk-test",
            llm_base_url="http://llm.invalid/v1",
        ),
        memory=None,
    )
    runtime._max_react_steps = 24
    return runtime


async def drain(runtime: BasicRuntime, message: str, *, session_id: str) -> list[str]:
    """One turn, inside a registered turn context (as the stream route does)."""
    tokens = tc.begin_turn(session_id)
    try:
        return [chunk async for chunk in call_llm_stream(runtime, message, session_id=session_id)]
    finally:
        tc.end_turn(*tokens)


def user_texts(req: Any) -> list[str]:
    return [str(m.get("content") or "") for m in req.messages if m.get("role") == "user"]


# -- queue semantics ---------------------------------------------------------


def test_a_nudge_needs_a_running_turn():
    assert tc.push_nudge("nobody-home", "hello") is False
    assert tc.drain_nudges("nobody-home") == []


def test_nudges_are_kept_in_order_and_capped():
    sid = "s-order"
    tokens = tc.begin_turn(sid)
    try:
        for i in range(tc._NUDGE_MAX + 2):
            ok = tc.push_nudge(sid, f"n{i}")
            assert ok is (i < tc._NUDGE_MAX)
        got = tc.drain_nudges(sid)
        assert got == [f"n{i}" for i in range(tc._NUDGE_MAX)]
        assert tc.drain_nudges(sid) == []
    finally:
        tc.end_turn(*tokens)


def test_blank_nudges_are_ignored():
    sid = "s-blank"
    tokens = tc.begin_turn(sid)
    try:
        assert tc.push_nudge(sid, "   ") is False
        assert tc.drain_nudges(sid) == []
    finally:
        tc.end_turn(*tokens)


def test_releasing_the_stream_claim_drops_leftover_nudges():
    sid = "s-claim"
    assert tc.try_claim_session_stream(sid)
    try:
        assert tc.push_nudge(sid, "late words") is True
    finally:
        tc.release_session_stream_claim(sid)
    assert tc.drain_nudges(sid) == []


# -- through the loop --------------------------------------------------------


@pytest.mark.asyncio
async def test_words_said_during_a_tool_reach_the_next_llm_call(tmp_path):
    runtime = make_runtime(tmp_path)
    sid = "steer-session"
    registry = FakeToolRegistry().install(runtime)

    async def slow_tool(**kwargs: Any) -> Any:
        # The owner types while the tool runs.
        assert tc.push_nudge(sid, "actually, make it blue") is True
        return {"ok": True}

    registry.register_builtin_handler(
        "paint", "paint something", slow_tool, parameters={"type": "object", "properties": {}}
    )
    fake = FakeLLM([tool_turn("paint", {"color": "red"}), text_turn("Painted it blue.")])

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "paint the fence", session_id=sid)

    assert fake.request_count == 2
    second = user_texts(fake.requests[1])
    assert any("actually, make it blue" in t for t in second)
    # It reads as the owner speaking, with a note that it landed mid-turn.
    assert any("Said while you were working" in t for t in second)
    # And the owner's words come *after* the tool result, before the next step.
    roles = [m.get("role") for m in fake.requests[1].messages]
    assert roles.index("tool") < len(roles) - 1 - roles[::-1].index("user")
    assert "@@steered\n" in chunks
    assert tc.drain_nudges(sid) == []


@pytest.mark.asyncio
async def test_a_nudge_that_lands_during_the_final_answer_continues_the_turn(tmp_path):
    runtime = make_runtime(tmp_path)
    sid = "steer-late"
    FakeToolRegistry().install(runtime)  # chat-only: no tools registered
    fake = FakeLLM([text_turn("Mochi or Pepper suit a grey cat."), text_turn("Short, then: Mo.")])

    # The words arrive while the first answer is streaming: the top-of-step
    # drain (call 1) sees nothing; the finalise check (call 2) finds them.
    from remedy.core.react_loop import loop as loop_mod

    calls = {"n": 0}
    real_take = loop_mod._take_nudges

    def staged_take(session_id: Any, runtime_: Any) -> list[str]:
        calls["n"] += 1
        if calls["n"] == 2:
            assert tc.push_nudge(sid, "something shorter") is True
        return real_take(session_id, runtime_)

    with fake.patch(), pytest.MonkeyPatch.context() as mp:
        mp.setattr(loop_mod, "_take_nudges", staged_take)
        chunks = await drain(runtime, "what should I name my grey cat?", session_id=sid)

    text = "".join(c for c in chunks if not c.startswith("@@"))
    assert fake.request_count == 2
    # The first answer stayed (she did not start over); the nudge came next.
    msgs = fake.requests[1].messages
    assert any(
        m.get("role") == "assistant" and "Mochi or Pepper" in str(m.get("content")) for m in msgs
    )
    assert any(
        m.get("role") == "user" and "something shorter" in str(m.get("content")) for m in msgs
    )
    # Both answers reached the owner, in order.
    assert text.index("Mochi or Pepper") < text.index("Short, then: Mo.")


# -- HTTP surface --------------------------------------------------------------


def test_steer_route_falls_back_when_no_turn_runs_and_joins_a_live_one(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    monkeypatch.setenv("REMEDY_NO_FIRST_RUN_DOWNLOAD", "1")
    from remedy.interfaces.api import create_app
    from remedy.memory.store import MemoryStore
    from remedy.models import ChatSession

    memory = MemoryStore(tmp_path / "memory.db")
    asyncio.run(memory.initialize())
    client = TestClient(create_app(memory=memory))
    sess = asyncio.run(memory.create_chat_session(ChatSession(title="steer")))
    sid = sess.id

    # Nothing running: the client must send normally.
    r = client.post(f"/api/sessions/{sid}/steer", json={"message": "turn left"})
    assert r.status_code == 200 and r.json() == {"steered": False}
    assert client.post(f"/api/sessions/{sid}/steer", json={"message": "  "}).status_code == 400

    # A turn is live (stream claim held): the words join it and are kept.
    assert tc.try_claim_session_stream(sid)
    try:
        r = client.post(f"/api/sessions/{sid}/steer", json={"message": "turn left"})
        assert r.json() == {"steered": True}
        assert tc.drain_nudges(sid) == ["turn left"]
    finally:
        tc.release_session_stream_claim(sid)
    rows = asyncio.run(memory.get_chat_messages(sid))
    assert any(m.role == "user" and m.content == "turn left" for m in rows)
