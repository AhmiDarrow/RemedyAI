"""The stream itself: what the ReAct loop must do with the bytes it gets back.

``call_llm_stream`` owns everything between "the POST left" and "the person
sees an answer": SSE deltas that arrive one character at a time, tool calls
assembled across many of those deltas, reasoning content that must never be
mistaken for the answer, hosts that answer 503 *Loading model* for a minute,
connections that die mid-token, and a Stop button pressed while tokens are
still arriving.

The failures this file guards against are the quiet ones:

* a tool call reassembled wrong, so the tool runs with half its arguments;
* thinking text shipped to the person as if it were the answer;
* a Stop that leaves the turn running and keeps spending tokens;
* a local host still loading its weights treated as a dead provider;
* a retry loop with no ceiling, which looks exactly like a hang;
* a broken usage ledger taking the whole answer down with it;
* tool markup written as prose being handed to the person verbatim.

Everything talks to :mod:`tests.harness.fake_llm`; nothing here opens a
socket, drives the desktop, or touches ``~/.remedy``.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.providers import clear_provider_quarantine
from remedy.core.react_loop.loop import (
    call_llm_stream,
    repair_tool_arguments_in_messages,
)
from remedy.core.react_loop.loop import (
    consume_llm_http_response as real_consume,
)
from remedy.core.turn_context import _react_flags, begin_turn, end_turn
from remedy.models import AgentConfig
from tests.harness.fake_llm import (
    FakeLLM,
    FakeToolRegistry,
    RecordedToolCall,
    ToolFailure,
    empty_turn,
    error_turn,
    exception_turn,
    raw_turn,
    text_turn,
    tool_turn,
)

#: The tools-on/tools-off gate; pin it off for turns that must stay chat-only.
NO_TOOLS = "remedy.core.agent._message_wants_tools"
#: Where the loop asks "is this provider a local box?" — several stream
#: recovery paths (wait-and-retry, keep-tools, low thinking) hang off it.
IS_LOCAL = "remedy.core.local_agent_optimize.is_local_binding"
WANTS_BUILD = "remedy.core.local_agent_optimize.message_wants_build_work"
#: The RMB readiness poll. Patched everywhere so no test ever waits 90s.
RMB_WAIT = "remedy.core.react_loop.loop._wait_rmb_ready_abortable"
ABORTED = "remedy.core.turn_context.is_turn_aborted"


@pytest.fixture(autouse=True)
def _clean_provider_breaker():
    """The provider circuit breaker is process-global: a 500 here would
    quarantine the provider for the next test."""
    clear_provider_quarantine()
    yield
    clear_provider_quarantine()


@pytest.fixture(autouse=True)
def _never_really_wait():
    """No test may block on the real RMB readiness poll (90s a call)."""
    with patch(RMB_WAIT, return_value={"ok": False, "ready": False}) as m:
        yield m


def make_local_runtime(tmp_path: Path, **overrides: Any) -> BasicRuntime:
    """A runtime bound to a local host (ollama), which the loop treats very
    differently: it waits for weights to load, keeps tools armed through host
    errors, and is never quarantined by the provider circuit breaker."""
    overrides.setdefault("llm_provider", "ollama")
    overrides.setdefault("llm_model", "qwen-fake")
    overrides.setdefault("llm_base_url", "http://127.0.0.1:11434/v1")
    return make_runtime(tmp_path, **overrides)


def make_runtime(tmp_path: Path, **overrides: Any) -> BasicRuntime:
    """A runtime pointed at a throwaway home and an unroutable provider."""
    kwargs: dict[str, Any] = {
        "name": "test",
        "home_dir": str(tmp_path / "home"),
        "project_path": str(tmp_path / "proj"),
        "llm_provider": "openai",
        "llm_model": "fake-model",
        "llm_api_key": "sk-test",
        "llm_base_url": "http://llm.invalid/v1",
    }
    kwargs.update(overrides)
    (tmp_path / "proj").mkdir(exist_ok=True)
    runtime = BasicRuntime(AgentConfig(**kwargs), memory=None)
    runtime._max_react_steps = 24
    return runtime


async def drain(runtime: BasicRuntime, message: str, **kwargs: Any) -> list[str]:
    kwargs.setdefault("session_id", str(runtime.config.home_dir))
    return [chunk async for chunk in call_llm_stream(runtime, message, **kwargs)]


def answer(chunks: list[str]) -> str:
    return "".join(c for c in chunks if not c.startswith("@@"))


def events(chunks: list[str]) -> list[str]:
    return [c for c in chunks if c.startswith("@@")]


def statuses(chunks: list[str]) -> str:
    return "".join(c for c in chunks if c.startswith("@@status:"))


# --------------------------------------------------------------------------
# SSE assembly: deltas in, one coherent round out
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_answer_split_one_character_per_sse_delta_arrives_whole(tmp_path):
    """Providers chunk at token level; the round must not lose or reorder any."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("Hello there, friend.", chunk_size=1)])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert answer(chunks) == "Hello there, friend."


@pytest.mark.asyncio
async def test_tool_call_arguments_split_across_deltas_reach_the_tool_intact(tmp_path):
    """The name arrives in the first delta and the JSON a fragment at a time.

    Reassembling that wrong is the worst kind of bug: the tool still runs, just
    with the wrong arguments.
    """
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 2, "b": 3, "label": "two and three"}, chunk_size=3),
            text_turn("The sum is 5."),
        ]
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the add tool")

    assert registry.calls_to("add") == [
        RecordedToolCall("add", {"a": 2, "b": 3, "label": "two and three"})
    ]
    assert "The sum is 5." in answer(chunks)


@pytest.mark.asyncio
async def test_two_tool_calls_interleaved_by_index_do_not_bleed_into_each_other(
    tmp_path,
):
    """Deltas carry an ``index``; ignoring it merges both calls into one."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("alpha", description="first", results=["A"])
    registry.add("beta", description="second", results=["B"])
    lines = [
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"c0",'
        b'"type":"function","function":{"name":"alpha","arguments":"{\\"x\\": "}}]}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"c1",'
        b'"type":"function","function":{"name":"beta","arguments":"{\\"y\\": "}}]}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"1}"}}]}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,'
        b'"function":{"arguments":"2}"}}]}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    fake = FakeLLM([raw_turn(lines), text_turn("Both ran.")])

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run alpha and beta")

    assert registry.calls == [
        RecordedToolCall("alpha", {"x": 1}),
        RecordedToolCall("beta", {"y": 2}),
    ]
    assert "Both ran." in answer(chunks)


@pytest.mark.asyncio
async def test_junk_lines_between_good_deltas_are_skipped_not_fatal(tmp_path):
    """Comments, blank keepalives and one bad JSON line must not lose the answer."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    lines = [
        b": keepalive\n\n",
        b"\n",
        b'data: {"choices":[{"index":0,"delta":{"content":"good "}}]}\n\n',
        b"data: {not json at all\n\n",
        b'data: {"choices":[{"index":0,"delta":{"content":"answer"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    fake = FakeLLM([raw_turn(lines)])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert answer(chunks) == "good answer"


@pytest.mark.asyncio
async def test_deltas_after_the_done_sentinel_are_ignored(tmp_path):
    """``[DONE]`` ends the round — trailing bytes are noise, not more answer."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    lines = [
        b'data: {"choices":[{"index":0,"delta":{"content":"kept"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
        b'data: {"choices":[{"index":0,"delta":{"content":" DROPPED"}}]}\n\n',
    ]
    fake = FakeLLM([raw_turn(lines)])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert "DROPPED" not in answer(chunks)
    assert answer(chunks) == "kept"


@pytest.mark.asyncio
async def test_reasoning_deltas_are_thinking_events_and_never_the_answer(tmp_path):
    """A reasoner streams its scratchpad first; shipping it as the reply leaks
    internal monologue into the transcript."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("Four.", reasoning="two plus two is four", chunk_size=2)])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "what is 2+2")

    assert answer(chunks) == "Four."
    thinking = "".join(
        c[len("@@thinking:") :] for c in chunks if c.startswith("@@thinking:")
    )
    assert "two plus two is four" in thinking
    assert "@@thinking_round" in chunks


# --------------------------------------------------------------------------
# non-stream (single JSON completion) rounds
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_provider_that_answers_with_one_json_blob_is_parsed_not_streamed(
    tmp_path,
):
    """Anthropic-class bindings post ``stream=False``; a stream-only parser
    would deliver nothing at all."""
    runtime = make_runtime(
        tmp_path,
        llm_provider="anthropic",
        llm_model="claude-fake",
        llm_base_url="http://anthropic.invalid/v1",
        llm_api_key="sk-ant-test",
    )
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("Non-streamed answer.")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.last_request.stream is False
    assert "Non-streamed answer." in answer(chunks)


# --------------------------------------------------------------------------
# stop pressed while tokens are arriving
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cancel_raised_mid_stream_stops_the_turn_with_a_durable_note(tmp_path):
    """Cancellation must not escape to the caller as a bare CancelledError."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("never delivered")], when_exhausted=text_turn("nor this"))

    async def _cancel(*_a: Any, **_k: Any):
        raise asyncio.CancelledError()
        yield  # pragma: no cover - only here to make this an async generator

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop.consume_llm_http_response", _cancel
    ), patch(ABORTED, side_effect=lambda *a, **k: fake.request_count >= 1):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert "@@aborted\n" in chunks
    assert "History is intact" in answer(chunks)


@pytest.mark.asyncio
async def test_stop_pressed_after_the_stream_finished_still_ends_the_turn(tmp_path):
    """The abort flag is re-read after every round; a late Stop must not be
    overtaken by one more model call."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [text_turn("partial answer")], when_exhausted=text_turn("must not be asked")
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        ABORTED, side_effect=lambda *a, **k: fake.request_count > 0
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert "@@aborted\n" in chunks
    assert "History is intact" in answer(chunks)


@pytest.mark.asyncio
async def test_stop_after_a_tool_ran_promises_the_tool_history_is_kept(tmp_path):
    """The two stopped-notes differ; the one that ran tools must say so."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("never delivered")],
        when_exhausted=text_turn("nor this"),
    )

    with fake.patch(force_tools=True), patch(
        ABORTED, side_effect=lambda *a, **k: fake.request_count > 1
    ):
        chunks = await drain(runtime, "add one")

    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 1})]
    assert "@@aborted\n" in chunks
    reply = answer(chunks)
    assert "kept" in reply and "continue" in reply


# --------------------------------------------------------------------------
# a non-200 arriving where a stream was expected
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_error_body_that_cannot_be_redacted_is_replaced_not_forwarded(
    tmp_path,
):
    """If redaction itself fails the raw body must never be the fallback —
    that body is exactly where providers echo the Authorization header."""
    runtime = make_runtime(tmp_path, llm_api_key="sk-supersecret-98765")
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [error_turn(500, "auth=sk-supersecret-98765 blew up"), text_turn("recovered")]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.metabolism.redact.redact_text",
        side_effect=RuntimeError("redactor broken"),
    ):
        chunks = await drain(runtime, "say hello")

    whole = "".join(chunks)
    assert "sk-supersecret-98765" not in whole
    assert "[redacted provider error]" in whole
    assert "recovered" in answer(chunks)


@pytest.mark.asyncio
async def test_a_local_host_still_loading_its_weights_is_waited_for_not_abandoned(
    tmp_path,
):
    """RMB answers 503 *Loading model* for a minute after a restart. Treating
    that as a dead provider throws away a turn that was about to work."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [error_turn(503, "Loading model, please wait"), text_turn("ready now")]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, return_value={"ok": True, "ready": True}
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    banner = statuses(chunks)
    assert "Local model is loading" in banner
    assert "Model ready" in banner
    assert "ready now" in answer(chunks)


@pytest.mark.asyncio
async def test_a_cloud_503_saying_unavailable_never_waits_on_rmb(tmp_path):
    """xAI's outage body reads "Service temporarily unavailable". That is not
    our local host loading weights; waking RMB on it unloaded the vision model
    and started a GGUF the owner had not asked for (2026-09-03)."""
    runtime = make_runtime(
        tmp_path,
        llm_provider="xai",
        llm_model="grok-4.5",
        llm_base_url="http://xai.invalid/v1",
    )
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            error_turn(
                503,
                '{"code":"unavailable","error":"Service temporarily unavailable. '
                'The model did not respond to this request."}',
            ),
            text_turn("back again"),
        ]
    )

    def _must_not_wait(*a, **k):
        raise AssertionError("cloud 503 must not wait on / wake RMB")

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, side_effect=_must_not_wait
    ):
        chunks = await drain(runtime, "say hello")

    assert "Local model is loading" not in statuses(chunks)
    assert "back again" in answer(chunks)


@pytest.mark.asyncio
async def test_a_host_that_never_becomes_ready_falls_through_to_normal_recovery(
    tmp_path,
):
    """The readiness wait is a shortcut, not a trap: if it times out the turn
    still takes the ordinary soft-recovery path."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [error_turn(503, "Loading model, please wait"), text_turn("answered anyway")]
    )

    # The autouse fixture already reports "never ready".
    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert "Local model is loading" in statuses(chunks)
    assert "Model ready" not in statuses(chunks)
    assert "answered anyway" in answer(chunks)


@pytest.mark.asyncio
async def test_waiting_for_a_loading_model_is_capped_at_three_rounds(tmp_path):
    """An unbounded wait-and-retry is indistinguishable from a hang."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=error_turn(503, "Loading model, please wait"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, return_value={"ok": True, "ready": True}
    ):
        chunks = await drain(runtime, "say hello")

    assert statuses(chunks).count("Model ready") == 3
    assert "Stopped after repeated provider errors" in answer(chunks)


@pytest.mark.asyncio
async def test_a_local_host_error_retries_with_tools_still_armed(tmp_path):
    """Stripping tools on a local blip abandons build work that was underway."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM(
        [
            error_turn(500, "llama.cpp fell over"),
            tool_turn("file_write", {"path": "notes.txt"}),
            text_turn("Wrote notes.txt."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "write notes.txt in the project")

    # (the local pack adds force_full_write of its own — only the path matters)
    written = registry.calls_to("file_write")
    assert [c.arguments["path"] for c in written] == ["notes.txt"]
    assert "Local host error HTTP 500" in statuses(chunks)
    # The retry budget is visible and bounded, and the work still happens.
    assert "(1/6)" in statuses(chunks)
    assert "Wrote notes.txt." in answer(chunks)


@pytest.mark.asyncio
async def test_a_local_host_that_never_recovers_gives_up_inside_one_step(tmp_path):
    """Three readiness waits plus five keep-tools retries is the whole budget
    for one step — after that the person gets told, not spun."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM([], when_exhausted=error_turn(503, "Loading model, please wait"))

    with fake.patch(force_tools=True), patch(
        RMB_WAIT, return_value={"ok": True, "ready": True}
    ):
        chunks = await drain(runtime, "write notes.txt in the project")

    assert fake.request_count == 8
    assert "Provider request failed after retries" in answer(chunks)


@pytest.mark.asyncio
async def test_an_error_after_a_tool_ran_is_retried_with_tools_not_summarised(
    tmp_path,
):
    """Work is underway, so "answer from context" would silently drop it."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            error_turn(500, "upstream hiccup"),
            text_turn("The sum is 5."),
        ]
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert fake.request_count == 3
    assert "retrying with tools" in statuses(chunks)
    assert "(1/3)" in statuses(chunks)
    assert fake.requests[2].tool_names == ["add"]
    assert "The sum is 5." in answer(chunks)


@pytest.mark.asyncio
async def test_a_force_answer_rebuild_keeps_the_write_budget_it_was_given(tmp_path):
    """The rebuilt no-tool body used to force a tiny max_tokens, which cut the
    salvage answer off mid-sentence."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([error_turn(500, "hiccup"), text_turn("salvaged answer")])

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        IS_LOCAL, return_value=True
    ), patch("remedy.core.turn_context.turn_write_budget", return_value=4096):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert fake.requests[1].tools == []
    assert any(
        "Do not call tools" in t for t in fake.requests[1].steering_texts()
    )
    assert "salvaged answer" in answer(chunks)


@pytest.mark.asyncio
async def test_a_rebuild_that_itself_explodes_still_posts_the_old_body(tmp_path):
    """Losing the salvage attempt because the rebuild crashed would turn a
    recoverable blip into a dead turn."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([error_turn(500, "hiccup"), text_turn("still answered")])

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop.sanitize_chat_body",
        side_effect=RuntimeError("sanitizer exploded"),
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert "still answered" in answer(chunks)
    assert "sanitizer exploded" not in "".join(chunks)


@pytest.mark.asyncio
async def test_tool_arguments_that_stay_broken_are_dropped_from_context_entirely(
    tmp_path,
):
    """One repair pass is not always enough. The second 400 must strip the
    broken turns instead of re-posting the same rejected payload forever."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", '{"a": 1'),
            error_turn(400, "invalid tool argument: EOF while parsing"),
            error_turn(400, "invalid tool argument: EOF while parsing"),
            text_turn("recovered without them"),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    whole = "".join(chunks)
    assert "Repaired incomplete tool arguments" in whole
    assert "Dropped truncated tool calls from context" in whole
    # Dropping the calls also disarms tools — finishing from context is the
    # only way out once the arguments cannot be replayed.
    assert fake.requests[3].tools == []
    assert registry.calls == []


@pytest.mark.asyncio
async def test_an_xai_refresh_that_throws_is_reported_instead_of_crashing_the_turn(
    tmp_path,
):
    runtime = make_runtime(
        tmp_path,
        llm_provider="xai",
        llm_api_key="old-key",
        llm_base_url="http://xai.invalid/v1",
    )
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=error_turn(401, "expired"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.interfaces.xai_auth.refresh_if_needed",
        side_effect=RuntimeError("keyring locked"),
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    reply = answer(chunks)
    assert "xAI session expired" in reply
    assert "keyring locked" not in "".join(chunks)


# --------------------------------------------------------------------------
# the connection dies mid-stream
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dead_sleev_gateway_is_bypassed_instead_of_retried_through(tmp_path):
    """Retrying through the proxy that just refused the connection cannot
    work. The turn must fail open to the provider and say nothing scary."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            exception_turn(
                aiohttp.ClientConnectionError(
                    "Cannot connect to host 127.0.0.1:17321 ssl:default"
                )
            ),
            text_turn("answered directly"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert "Sleev gateway unreachable" in statuses(chunks)
    assert "no user action needed" in statuses(chunks)
    assert "answered directly" in answer(chunks)


@pytest.mark.asyncio
async def test_a_gateway_that_stays_dead_names_sleev_in_the_final_explanation(
    tmp_path,
):
    """"Connection failed" alone sends the person hunting their own network."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [],
        when_exhausted=exception_turn(
            aiohttp.ClientConnectionError("Cannot connect to host 127.0.0.1:17321")
        ),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "Sleev proxy looked unreachable" in reply
    assert "History is intact" in reply
    # The retry budget is a budget, not a spin.
    assert fake.request_count <= 10


@pytest.mark.asyncio
async def test_a_dropped_local_connection_waits_for_the_model_before_retrying(
    tmp_path,
):
    """A local host drops the socket while it swaps weights; retrying instantly
    just burns the disconnect budget on a box that is not listening yet."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            exception_turn(aiohttp.ClientConnectionError("Server disconnected")),
            text_turn("back after the wait"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, return_value={"ok": True, "ready": True}
    ) as waited:
        chunks = await drain(runtime, "say hello")

    assert waited.await_count == 1
    assert "waiting for local model" in statuses(chunks)
    assert "back after the wait" in answer(chunks)


@pytest.mark.asyncio
async def test_a_disconnect_retry_keeps_the_tools_the_turn_still_needs(tmp_path):
    """The non-stream retry rebuilds the body; dropping the tool schemas there
    would turn a blip into a turn that can never do the work."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            exception_turn(aiohttp.ClientConnectionError("Server disconnected")),
            tool_turn("add", {"a": 1}),
            text_turn("The sum is 5."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert fake.requests[1].stream is False
    assert fake.requests[1].tool_names == ["add"]
    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 1})]
    assert "The sum is 5." in answer(chunks)


# --------------------------------------------------------------------------
# accounting must never outrank the answer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_broken_usage_ledger_never_costs_the_person_their_answer(tmp_path):
    """Token accounting is bookkeeping. If it throws, the turn still ships."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [text_turn("the answer", usage={"prompt_tokens": 9, "completion_tokens": 3})]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.nanoswarm.token_nanobot.get_token_nanobot",
        side_effect=RuntimeError("nanobot exploded"),
    ):
        chunks = await drain(runtime, "say hello")

    assert answer(chunks) == "the answer"
    assert not [e for e in events(chunks) if e.startswith("@@usage:")]
    assert "nanobot exploded" not in "".join(chunks)


@pytest.mark.asyncio
async def test_a_usage_block_of_all_zeroes_is_not_reported_as_a_usage_event(tmp_path):
    """Zero-token usage is a provider quirk, not a measurement."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [text_turn("hi", usage={"prompt_tokens": 0, "completion_tokens": 0})]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert not [e for e in events(chunks) if e.startswith("@@usage:")]


# --------------------------------------------------------------------------
# tool calls the model wrote as text
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_markup_in_a_chat_only_turn_is_stripped_not_shown(tmp_path):
    """No tools are armed, so there is nothing to recover into — but the raw
    ``<tool_call>`` blob must still never reach the person."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            text_turn(
                "Here you go.\n"
                '<tool_call>{"name": "list_dir", "arguments": {"path": "."}}</tool_call>'
            )
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "<tool_call>" not in reply
    assert "list_dir" not in reply
    assert "Here you go." in reply


@pytest.mark.asyncio
async def test_a_recovered_text_tool_call_that_fails_gets_a_recovery_nudge(tmp_path):
    """The call was recovered from prose and then failed. Without a nudge the
    model repeats the same blocked write and the turn spins."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "file_write",
        description="write a file",
        results=[ToolFailure("EMPTY_SOURCE_WRITE: refused to blank notes.txt")],
    )
    fake = FakeLLM(
        [
            text_turn(
                '<tool_call>{"name": "file_write", '
                '"arguments": {"path": "notes.txt", "content": ""}}</tool_call>'
            ),
            text_turn("I stopped after the blocked write."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "write notes.txt in the project")

    assert [c.name for c in registry.calls] == ["file_write"]
    nudges = fake.requests[1].steering_texts()
    assert any("EMPTY/SPAM file_write blocked" in t for t in nudges)
    # The real file was kept — the nudge must say so, not invent a success.
    assert any("kept" in t for t in nudges)
    assert "<tool_call>" not in answer(chunks)


# --------------------------------------------------------------------------
# what gets injected into the next request
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_gathered_by_tools_is_pointed_at_not_dumped_again(tmp_path):
    """Tier-2 turns re-send a compact evidence delta instead of the full tool
    output; sending nothing loses the facts, sending everything blows context."""
    runtime = make_runtime(tmp_path)
    # ``chat()`` pins the session id on the runtime before the loop starts; the
    # evidence ledger is keyed by it on both the write and the read side.
    runtime._session_id = str(tmp_path / "home")
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_read", description="read a file", results=["src/app.py: ok"])
    fake = FakeLLM(
        [tool_turn("file_read", {"path": "src/app.py"}), text_turn("It reads fine.")]
    )

    with fake.patch(force_tools=True), patch(
        "remedy.core.react_loop.loop._turn_tier_of", return_value=2
    ):
        chunks = await drain(runtime, "read src/app.py")

    systems = fake.requests[1].texts_for_role("system")
    assert any("Evidence delta" in t for t in systems)
    assert "It reads fine." in answer(chunks)


@pytest.mark.asyncio
async def test_a_green_verify_asks_for_six_short_lines_not_an_essay(tmp_path):
    """After the machine has already verified the build, the long "be thorough"
    wrap-up prompt sent models into multi-thousand-token DONE loops."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [empty_turn(), text_turn("done: 2 files, verify green")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.turn_context.turn_build_verify_green", return_value=True
    ):
        chunks = await drain(runtime, "say hello")

    followup = fake.requests[1].steering_texts()
    assert any("Verify is GREEN" in t for t in followup)
    assert any("at most 6 short lines" in t for t in followup)
    assert not any("Be thorough" in t for t in followup)
    assert "done: 2 files, verify green" in answer(chunks)


# --------------------------------------------------------------------------
# stop pressed while the loop is waiting on a local host
# --------------------------------------------------------------------------


@contextmanager
def in_turn(session_id: str):
    """Run the block inside a real turn context.

    ``chat()`` opens one before it ever reaches the loop; several counters
    (the RMB load-wait budget among them) live on those per-turn flags rather
    than on the runtime, so a test that skips it exercises the fallback path.
    """
    tokens = begin_turn(session_id)
    try:
        yield
    finally:
        end_turn(session_id, *tokens)


@pytest.mark.asyncio
async def test_the_loading_wait_budget_is_counted_on_the_turn_not_the_runtime(
    tmp_path,
):
    """Two tabs share one runtime. A load-wait counter kept on the runtime is
    spent by whichever turn got there first."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    sid = str(tmp_path / "home")
    fake = FakeLLM(
        [
            error_turn(503, "Loading model, please wait"),
            error_turn(503, "Loading model, please wait"),
            text_turn("ready at last"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, return_value={"ok": True, "ready": True}
    ), in_turn(sid):
        chunks = await drain(runtime, "say hello", session_id=sid)
        flags = _react_flags()
        waits = int(flags.rmb_load_waits)

    assert waits == 2
    assert not hasattr(runtime, "_rmb_load_waits")
    assert "ready at last" in answer(chunks)


@pytest.mark.asyncio
async def test_stop_while_waiting_for_a_loading_model_ends_the_turn(tmp_path):
    """The readiness poll can block for 90 seconds; Stop must cut through it."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [error_turn(503, "Loading model, please wait")],
        when_exhausted=text_turn("must not be asked"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, side_effect=asyncio.CancelledError()
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert "@@aborted\n" in chunks
    assert "History is intact" in answer(chunks)


@pytest.mark.asyncio
async def test_a_readiness_probe_that_throws_counts_as_not_ready(tmp_path):
    """A broken probe must not be read as "ready" — that would re-POST into a
    host that is still loading and burn the retry budget."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [error_turn(503, "Loading model, please wait"), text_turn("answered anyway")]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, side_effect=RuntimeError("probe socket died")
    ):
        chunks = await drain(runtime, "say hello")

    assert "Model ready" not in statuses(chunks)
    assert "answered anyway" in answer(chunks)
    assert "probe socket died" not in "".join(chunks)


@pytest.mark.asyncio
async def test_stop_while_waiting_out_a_local_host_error_ends_the_turn(tmp_path):
    """Same wait, different branch: the keep-tools retry also blocks on RMB."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM(
        [error_turn(500, "llama.cpp fell over")],
        when_exhausted=text_turn("must not be asked"),
    )

    with fake.patch(force_tools=True), patch(
        RMB_WAIT, side_effect=asyncio.CancelledError()
    ):
        chunks = await drain(runtime, "write notes.txt in the project")

    assert fake.request_count == 1
    assert "@@aborted\n" in chunks
    assert registry.calls == []


@pytest.mark.asyncio
async def test_a_failed_wait_after_a_local_host_error_still_retries(tmp_path):
    """The wait is best-effort. Losing it must not lose the retry."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM(
        [
            error_turn(500, "llama.cpp fell over"),
            tool_turn("file_write", {"path": "notes.txt"}),
            text_turn("Wrote notes.txt."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True), patch(
        RMB_WAIT, side_effect=RuntimeError("probe socket died")
    ):
        chunks = await drain(runtime, "write notes.txt in the project")

    assert [c.name for c in registry.calls] == ["file_write"]
    assert "Wrote notes.txt." in answer(chunks)


@pytest.mark.asyncio
async def test_a_cancel_raised_by_the_post_itself_is_not_reported_as_a_crash(
    tmp_path,
):
    """Stop lands between rounds: the POST is cancelled before any response."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([exception_turn(asyncio.CancelledError())])

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        ABORTED, side_effect=lambda *a, **k: fake.request_count >= 1
    ):
        chunks = await drain(runtime, "say hello")

    assert "@@aborted\n" in chunks
    reply = answer(chunks)
    assert "History is intact" in reply
    assert "went wrong on my side" not in reply


@pytest.mark.asyncio
async def test_a_stop_that_lands_after_the_last_token_still_ends_the_turn(tmp_path):
    """The round completed, so nothing raised — only the re-read of the abort
    flag stands between Stop and one more model call."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [empty_turn()], when_exhausted=text_turn("must not be asked for a retry")
    )
    stream_finished: list[bool] = []

    async def _watch(*args: Any, **kwargs: Any):
        async for item in real_consume(*args, **kwargs):
            yield item
        stream_finished.append(True)

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop.consume_llm_http_response", _watch
    ), patch(ABORTED, side_effect=lambda *a, **k: bool(stream_finished)):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert "@@aborted\n" in chunks
    assert "before a final answer" in answer(chunks)


# --------------------------------------------------------------------------
# 400s the loop is expected to repair rather than re-post
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_context_overflow_shrinks_the_request_but_keeps_the_tools(tmp_path):
    """Answering from a shrunk context is fine; losing the ability to call the
    tool the request was about is not."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM(
        [
            error_turn(400, "exceed_context_size: n_prompt_tokens too large"),
            tool_turn("file_write", {"path": "notes.txt"}),
            text_turn("Wrote notes.txt."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "write notes.txt in the project")

    assert "Context full" in statuses(chunks)
    assert fake.requests[1].tool_choice == "required"
    assert [c.name for c in registry.calls] == ["file_write"]


@pytest.mark.asyncio
async def test_a_thinking_mode_400_restores_reasoning_content_and_retries_once(
    tmp_path,
):
    """DeepSeek in thinking mode rejects tool turns whose ``reasoning_content``
    was dropped on the way back. Re-posting the same body loops forever.

    The repair itself only fires for history that predates
    ``build_assistant_api_message`` always emitting the field, so it is forced
    here; what is under test is that a successful repair triggers exactly one
    retry and says so.
    """
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            error_turn(400, "The reasoning_content in thinking mode must be passed back"),
            text_turn("retried and answered"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop.repair_reasoning_content_in_messages",
        return_value=True,
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert "Restored thinking-mode reasoning" in statuses(chunks)
    assert "retried and answered" in answer(chunks)


@pytest.mark.asyncio
async def test_repairs_that_themselves_explode_still_end_the_turn_politely(tmp_path):
    """Both rescue passes for a broken tool-argument 400 can fail. The turn
    must then stop with an explanation — never with a traceback."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", '{"a": 1'),
            error_turn(400, "invalid tool argument: EOF while parsing"),
            text_turn("recovered without them"),
        ],
        when_exhausted=text_turn("kept going"),
    )
    real_repair = repair_tool_arguments_in_messages

    def _repair(messages):
        # Only the rescue call (made while handling the 400) explodes; the
        # ordinary pre-POST pass must keep working or nothing gets posted.
        if fake.request_count == 2:
            raise RuntimeError("repair exploded")
        return real_repair(messages)

    with fake.patch(force_tools=True), patch(
        "remedy.core.react_loop.loop.repair_tool_arguments_in_messages", _repair
    ), patch(
        "remedy.core.react_loop.loop.strip_broken_tool_call_turns",
        side_effect=RuntimeError("strip exploded"),
    ):
        chunks = await drain(runtime, "add one")

    whole = "".join(chunks)
    assert "repair exploded" not in whole
    assert "strip exploded" not in whole
    assert answer(chunks).strip()


@pytest.mark.asyncio
async def test_a_dropped_local_connection_cancelled_mid_wait_ends_the_turn(tmp_path):
    """Third and last place the loop waits on a local host — Stop must work
    there too, or the person's Stop does nothing for a minute."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [exception_turn(aiohttp.ClientConnectionError("Server disconnected"))],
        when_exhausted=text_turn("must not be asked"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, side_effect=asyncio.CancelledError()
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert "@@aborted\n" in chunks
    assert "History is intact" in answer(chunks)


@pytest.mark.asyncio
async def test_a_failed_wait_after_a_local_disconnect_still_retries_the_round(
    tmp_path,
):
    """If the readiness probe throws while recovering from a dropped socket,
    the retry must happen anyway — the probe is an optimisation, not a gate."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            exception_turn(aiohttp.ClientConnectionError("Server disconnected")),
            text_turn("second try worked"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, side_effect=RuntimeError("probe socket died")
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert fake.requests[1].stream is False
    assert "second try worked" in answer(chunks)
    assert "probe socket died" not in "".join(chunks)


# --------------------------------------------------------------------------
# hosted gateways that rate-limit (429 + Retry-After) and pace requests
# --------------------------------------------------------------------------

SLEEP = "remedy.core.react_loop.loop._sleep_abortable"
CATALOG = "remedy.interfaces.provider_catalog.PROVIDER_CATALOG"


def make_demo_runtime(tmp_path: Path, **overrides: Any) -> BasicRuntime:
    """The free demo gateway: hosted, OpenAI-compatible, ~1 request/second."""
    overrides.setdefault("llm_provider", "demo")
    overrides.setdefault("llm_model", "codestral-latest")
    overrides.setdefault("llm_api_key", "unused")
    overrides.setdefault("llm_base_url", "https://demo.invalid/v1")
    return make_runtime(tmp_path, **overrides)


@pytest.fixture
def _instant_sleep():
    """Retry-After waits are honoured but must not slow the suite down."""
    waits: list[float] = []

    async def fake_sleep(seconds, abort_ev=None):
        waits.append(seconds)

    with patch(SLEEP, fake_sleep):
        yield waits


@pytest.mark.asyncio
async def test_a_429_with_retry_after_header_is_waited_out_and_the_same_request_resent(
    tmp_path, _instant_sleep
):
    """The gateway said *come back in 3s*. Doing exactly that — same body, no
    tool stripping, no force-answer — is what turns a failed turn into a slow one."""
    runtime = make_demo_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 2, "b": 3}),
            error_turn(
                429,
                '{"code":"rate_limit_exceeded","retry_after":1}',
                headers={"Retry-After": "3"},
            ),
            text_turn("The sum is 5."),
        ]
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the add tool")

    assert fake.request_count == 3
    # Header wins over the body; the retry carried the identical request.
    assert _instant_sleep == [3.0]
    assert fake.requests[2].body == fake.requests[1].body
    assert "Rate limited — waiting 3s" in statuses(chunks)
    assert "The sum is 5." in answer(chunks)
    assert "stopped responding" not in answer(chunks)


@pytest.mark.asyncio
async def test_a_429_retry_after_in_the_json_body_is_honoured_without_a_header(
    tmp_path, _instant_sleep
):
    runtime = make_demo_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            error_turn(429, '{"code":"rate_limit_exceeded","retry_after":1}'),
            text_turn("hello back"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert _instant_sleep == [1.0]
    assert "hello back" in answer(chunks)


@pytest.mark.asyncio
async def test_a_gateway_that_keeps_rate_limiting_stops_and_says_how_long_it_waited(
    tmp_path, _instant_sleep
):
    """Bounded retries: after the budget the person gets a plain explanation
    that names the wait already spent, not an endless spinner."""
    runtime = make_demo_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [],
        when_exhausted=error_turn(
            429,
            '{"code":"rate_limit_exceeded","retry_after":2}',
        ),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    from remedy.core.llm_pacing import RATE_LIMIT_MAX_RETRIES

    assert len(_instant_sleep) == RATE_LIMIT_MAX_RETRIES
    assert all(w == 2.0 for w in _instant_sleep)
    whole = "".join(chunks)
    assert "waited" in whole and "retr" in whole
    assert "Local host error" not in statuses(chunks)


@pytest.mark.asyncio
async def test_a_429_on_a_local_host_takes_the_local_wait_path_not_retry_after(
    tmp_path, _instant_sleep
):
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([error_turn(429, "busy"), text_turn("local answer")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert _instant_sleep == []
    assert "local answer" in answer(chunks)


@pytest.mark.asyncio
async def test_a_quota_429_is_billing_not_a_rate_limit_and_is_not_retried(
    tmp_path, _instant_sleep
):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [error_turn(429, '{"error":{"code":"insufficient_quota","message":"x"}}')]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert _instant_sleep == []
    low = answer(chunks).lower()
    assert "billing" in low or "credit" in low or "quota" in low


@pytest.mark.asyncio
async def test_the_demo_gateway_is_not_treated_as_a_local_host_after_an_error(
    tmp_path, _instant_sleep
):
    """A 500 from the hosted demo gateway must not poll the RMB readiness
    probe six times at 45s each — that is the local-host path."""
    runtime = make_demo_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([error_turn(500, "upstream hiccup"), text_turn("recovered")])

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        RMB_WAIT, return_value={"ok": False, "ready": False}
    ) as rmb_wait:
        chunks = await drain(runtime, "say hello")

    assert rmb_wait.call_count == 0
    assert "Local host error" not in statuses(chunks)
    assert "recovered" in answer(chunks)


@pytest.mark.asyncio
async def test_consecutive_rounds_to_a_paced_provider_are_spaced_apart(tmp_path):
    """Catalog ``min_request_interval_s`` spaces tool-result → next-round POSTs."""
    from remedy.core import llm_pacing

    runtime = make_demo_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM([tool_turn("add", {"a": 2, "b": 3}), text_turn("The sum is 5.")])
    slept: list[float] = []

    async def fake_sleep(seconds, abort_ev=None):
        slept.append(seconds)

    llm_pacing.reset_pacing()
    try:
        with fake.patch(force_tools=True), patch(
            CATALOG, {"demo": {"min_request_interval_s": 1.1}}
        ), patch.object(llm_pacing, "sleep_abortable", fake_sleep):
            chunks = await drain(runtime, "run the add tool")
    finally:
        llm_pacing.reset_pacing()

    assert fake.request_count == 2
    assert len(slept) == 1 and 0.0 < slept[0] <= 1.15
    assert "The sum is 5." in answer(chunks)


@pytest.mark.asyncio
async def test_an_unpaced_provider_never_sleeps_between_rounds(tmp_path):
    from remedy.core import llm_pacing

    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM([tool_turn("add", {"a": 2, "b": 3}), text_turn("The sum is 5.")])
    slept: list[float] = []

    async def fake_sleep(seconds, abort_ev=None):
        slept.append(seconds)

    with fake.patch(force_tools=True), patch(
        CATALOG, {"openai": {"label": "OpenAI"}}
    ), patch.object(llm_pacing, "sleep_abortable", fake_sleep):
        await drain(runtime, "run the add tool")

    assert slept == []


# --------------------------------------------------------------------------
# ``reasoning`` deltas (gpt-oss) are thinking, same as ``reasoning_content``
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpt_oss_style_reasoning_deltas_stream_as_thinking_then_the_answer(
    tmp_path,
):
    """gpt-oss streams ``reasoning`` (not ``reasoning_content``) before any
    content. Ignoring it leaves the UI blank for the whole think phase."""
    runtime = make_demo_runtime(tmp_path, llm_model="gpt-oss:20b")
    FakeToolRegistry().install(runtime)
    lines = [
        b'data: {"choices":[{"index":0,"delta":{"role":"assistant","reasoning":"Let me "}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"reasoning":"think."}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"Four."}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    fake = FakeLLM([raw_turn(lines)])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "what is 2+2")

    thinking = [c for c in chunks if c.startswith("@@thinking:")]
    assert [t[len("@@thinking:") :] for t in thinking] == ["Let me ", "think."]
    assert chunks.count("@@thinking_round") == 1
    assert answer(chunks) == "Four."
    # Thinking arrived before the first answer token.
    assert chunks.index(thinking[0]) < chunks.index("Four.")


def test_reasoning_delta_text_accepts_every_host_shape():
    from remedy.core.react_stream import reasoning_delta_text

    assert reasoning_delta_text({"reasoning_content": "a"}) == "a"
    assert reasoning_delta_text({"reasoning": "b"}) == "b"
    assert reasoning_delta_text({"reasoning": {"text": "c"}}) == "c"
    assert reasoning_delta_text({"reasoning": [{"text": "d"}, "e"]}) == "de"
    assert reasoning_delta_text({"reasoning_content": None, "reasoning": "f"}) == "f"
    assert reasoning_delta_text({"content": "x"}) == ""
    assert reasoning_delta_text(None) == ""


def test_openai_adapter_extracts_reasoning_key_from_a_json_completion():
    from remedy.core.providers import OpenAIProvider

    parsed = OpenAIProvider().extract_response(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "", "reasoning": "why"},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    assert parsed["reasoning_content"] == "why"


@pytest.mark.asyncio
async def test_a_bare_429_without_any_hint_is_not_retried_in_place(
    tmp_path, _instant_sleep
):
    """No Retry-After, no catalog interval: the pre-existing breaker /
    recovery paths own the error — one request, no sleeps."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=error_turn(429, "rate limited"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        CATALOG, {"openai": {"label": "OpenAI"}}
    ):
        chunks = await drain(runtime, "say hello")

    assert _instant_sleep == []
    assert "Rate limited — waiting" not in statuses(chunks)
