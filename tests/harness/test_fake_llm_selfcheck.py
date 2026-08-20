"""Proof that the fake LLM harness itself behaves.

Every other test that uses ``tests.harness.fake_llm`` inherits whatever this
file fails to catch: if the fake serves turns out of order, hides a provider
error, or forgets to record the request, the tests built on it will agree with
the bug instead of finding it. So this file checks the harness against the
*real* parser (``consume_llm_http_response``) and the *real* runtime
(``BasicRuntime.call_tool`` / the ReAct loop), never against itself.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.react_loop.stream_consume import consume_llm_http_response
from remedy.core.react_stream import StreamRoundState
from remedy.models import AgentConfig, ToolCall
from tests.harness.fake_llm import (
    FakeLLM,
    FakeToolRegistry,
    RecordedToolCall,
    ScriptExhaustedError,
    ToolFailure,
    empty_turn,
    error_turn,
    exception_turn,
    fake_adapter,
    fake_binding,
    text_turn,
    tool_turn,
    tools_turn,
    truncated_turn,
)


async def _consume(fake: FakeLLM, *, stream: bool = True) -> StreamRoundState:
    """POST once through the fake and run the answer through the real parser."""
    body: dict[str, Any] = {"stream": stream, "model": "fake-model", "messages": []}
    resp = fake.session.post("http://llm.invalid/v1/chat/completions", json=body)
    state = StreamRoundState()
    collected: dict[str, Any] = {}
    async for _token, _is_user_text in consume_llm_http_response(
        resp,
        round_state=state,
        collected=collected,
        adapter=fake_adapter(),
        bind=fake_binding(),
        body=body,
        use_openai_sse=stream,
        stream_live=True,
    ):
        pass
    return state


# -- scripted turns --------------------------------------------------------


@pytest.mark.asyncio
async def test_scripted_turns_are_served_in_the_order_they_were_written():
    fake = FakeLLM([text_turn("first"), text_turn("second"), text_turn("third")])

    seen = [(await _consume(fake)).text_out for _ in range(3)]

    assert seen == ["first", "second", "third"]
    assert fake.turns_remaining == 0


@pytest.mark.parametrize("chunk_size", [0, 1, 3])
@pytest.mark.asyncio
async def test_text_arrives_whole_however_finely_it_was_chunked(chunk_size):
    fake = FakeLLM([text_turn("the whole sentence", chunk_size=chunk_size)])

    state = await _consume(fake)

    assert state.text_out == "the whole sentence"
    assert state.finish_reason == "stop"


@pytest.mark.parametrize("chunk_size", [0, 2])
@pytest.mark.asyncio
async def test_a_scripted_tool_turn_arrives_as_native_tool_calls(chunk_size):
    """Split argument deltas must reassemble -- that is the whole point of them."""
    fake = FakeLLM([tool_turn("add", {"a": 2, "b": 3}, chunk_size=chunk_size)])

    state = await _consume(fake)

    calls = state.tool_calls_list()
    assert [c["function"]["name"] for c in calls] == ["add"]
    assert calls[0]["function"]["arguments"] == '{"a": 2, "b": 3}'
    assert state.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_text_and_tool_calls_can_arrive_in_the_same_turn():
    fake = FakeLLM(
        [tools_turn([("add", {"a": 1}), ("sub", {"b": 2})], text="working on it")]
    )

    state = await _consume(fake)

    assert state.text_out == "working on it"
    assert [c["function"]["name"] for c in state.tool_calls_list()] == ["add", "sub"]


@pytest.mark.asyncio
async def test_an_empty_completion_yields_no_text_but_still_finishes():
    """The empty-answer recovery path only fires if 'empty' really is empty."""
    fake = FakeLLM([empty_turn()])

    state = await _consume(fake)

    assert state.text_out == ""
    assert state.tool_calls_list() == []
    assert state.finish_reason == "stop"


@pytest.mark.asyncio
async def test_a_stream_that_stops_mid_token_keeps_what_arrived_and_never_finishes():
    """No finish_reason at all -- the stream died, it did not decide to stop."""
    fake = FakeLLM([truncated_turn("hello world", keep=5)])

    state = await _consume(fake)

    assert state.text_out == "hello"
    assert state.finish_reason is None


# -- failures must not be swallowed ---------------------------------------


@pytest.mark.parametrize("status", [400, 401, 429, 500])
@pytest.mark.asyncio
async def test_a_scripted_error_turn_surfaces_as_a_non_200_with_its_body(status):
    fake = FakeLLM([error_turn(status, body="upstream exploded")])

    resp = fake.session.post("http://llm.invalid/v1/chat/completions", json={})

    assert resp.status == status
    assert await resp.text() == "upstream exploded"


@pytest.mark.asyncio
async def test_an_exception_turn_raises_out_of_post_instead_of_returning_a_response():
    fake = FakeLLM([exception_turn(ConnectionResetError("server disconnected"))])

    with pytest.raises(ConnectionResetError, match="server disconnected"):
        fake.session.post("http://llm.invalid/v1/chat/completions", json={})


@pytest.mark.asyncio
async def test_running_out_of_scripted_turns_raises_rather_than_repeating_the_last():
    """A silent repeat would let a runaway loop pass as a two-step turn."""
    fake = FakeLLM([text_turn("only one")])
    await _consume(fake)

    with pytest.raises(ScriptExhaustedError, match="completion #2"):
        fake.session.post("http://llm.invalid/v1/chat/completions", json={})


@pytest.mark.asyncio
async def test_when_exhausted_turn_is_served_forever_when_one_is_given():
    fake = FakeLLM([text_turn("scripted")], when_exhausted=text_turn("filler"))

    seen = [(await _consume(fake)).text_out for _ in range(3)]

    assert seen == ["scripted", "filler", "filler"]


# -- the SSE / non-stream fork --------------------------------------------


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.asyncio
async def test_a_turn_renders_for_whichever_transport_the_request_asked_for(stream):
    """Local/RMB rounds post stream=False; the fake must not always speak SSE."""
    fake = FakeLLM([tool_turn("add", {"a": 2}, text="thinking")])

    state = await _consume(fake, stream=stream)

    assert state.text_out == "thinking"
    assert [c["function"]["name"] for c in state.tool_calls_list()] == ["add"]


# -- the recorder ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_recorder_records_url_headers_and_the_whole_request_body():
    fake = FakeLLM([text_turn("ok")])
    body = {
        "model": "fake-model",
        "stream": True,
        "tool_choice": "required",
        "messages": [
            {"role": "system", "content": "be good"},
            {"role": "user", "content": "do it"},
            {"role": "tool", "content": "tool said hi"},
        ],
        "tools": [{"type": "function", "function": {"name": "add"}}],
    }
    fake.session.post("http://llm.invalid/v1/chat/completions", headers={"X": "y"},
                      json=body)

    req = fake.last_request
    assert fake.request_count == 1
    assert req.url == "http://llm.invalid/v1/chat/completions"
    assert req.headers == {"X": "y"}
    assert req.model == "fake-model"
    assert req.stream is True
    assert req.tool_choice == "required"
    assert req.tool_names == ["add"]
    assert req.texts_for_role("user") == ["do it"]
    assert req.tool_result_texts == ["tool said hi"]
    assert req.mentions("be good")


@pytest.mark.asyncio
async def test_the_recorded_body_is_a_snapshot_the_loop_cannot_mutate_afterwards():
    """The loop reuses and edits its messages list between steps in place."""
    fake = FakeLLM([text_turn("ok")])
    messages = [{"role": "user", "content": "original"}]
    fake.session.post("http://llm.invalid/v1", json={"messages": messages})

    messages[0]["content"] = "rewritten later"

    assert fake.last_request.texts_for_role("user") == ["original"]


# -- the fake tool registry -----------------------------------------------


@pytest.mark.asyncio
async def test_a_scripted_tool_result_comes_back_through_the_real_call_tool():
    runtime = BasicRuntime(AgentConfig(llm_api_key=""))
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", results=[{"sum": 5}])

    result = await runtime.call_tool(ToolCall(tool_name="add", arguments={"a": 2}))

    assert result.success
    assert result.data == {"sum": 5}
    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 2})]


@pytest.mark.asyncio
async def test_a_scripted_tool_failure_comes_back_as_a_failed_result_not_an_exception():
    runtime = BasicRuntime(AgentConfig(llm_api_key=""))
    registry = FakeToolRegistry().install(runtime)
    registry.add("flaky", results=[ToolFailure("disk on fire")])

    result = await runtime.call_tool(ToolCall(tool_name="flaky", arguments={}))

    assert result.success is False
    assert "disk on fire" in (result.error or "")


@pytest.mark.asyncio
async def test_scripted_results_are_consumed_in_order_then_the_last_one_repeats():
    runtime = BasicRuntime(AgentConfig(llm_api_key=""))
    registry = FakeToolRegistry().install(runtime)
    registry.add("counter", results=["one", "two"])

    seen = [
        (await runtime.call_tool(ToolCall(tool_name="counter", arguments={}))).data
        for _ in range(3)
    ]

    assert seen == ["one", "two", "two"]
    assert len(registry.calls_to("counter")) == 3


def test_the_fake_registry_produces_a_real_openai_tools_payload():
    from remedy.core.agent_llm import openai_tools_payload

    registry = FakeToolRegistry()
    registry.add("add", description="add numbers",
                 parameters={"type": "object", "properties": {"a": {"type": "number"}}})

    payload = openai_tools_payload(registry)

    assert [t["function"]["name"] for t in payload] == ["add"]
    assert payload[0]["function"]["description"] == "add numbers"
    assert payload[0]["function"]["parameters"]["properties"] == {"a": {"type": "number"}}


# -- end to end through the real ReAct loop -------------------------------


@pytest.mark.asyncio
async def test_the_loop_runs_a_scripted_tool_turn_then_the_scripted_final_answer():
    """The harness has to survive the real loop, not just the real parser."""
    runtime = BasicRuntime(
        AgentConfig(
            llm_api_key="sk-test",
            llm_model="fake-model",
            llm_base_url="http://llm.invalid/v1",
            llm_provider="openai",
        )
    )
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add two numbers", results=[{"sum": 5}])

    fake = FakeLLM([tool_turn("add", {"a": 2, "b": 3}), text_turn("The sum is 5.")])
    with fake.patch(force_tools=True):
        text = await runtime._call_llm("run the add tool with a=2 b=3")

    assert "The sum is 5." in text
    assert fake.request_count == 2
    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 2, "b": 3})]
    # The recorder is the point: assert on what was *asked*, not only answered.
    assert fake.requests[0].model == "fake-model"
    assert "add" in fake.requests[0].tool_names
    assert any("sum" in t for t in fake.requests[1].tool_result_texts)


@pytest.mark.asyncio
async def test_a_scripted_provider_error_reaches_the_caller_rather_than_a_fake_answer():
    runtime = BasicRuntime(
        AgentConfig(
            llm_api_key="sk-test",
            llm_model="fake-model",
            llm_base_url="http://llm.invalid/v1",
            llm_provider="openai",
        )
    )
    FakeToolRegistry().install(runtime)

    fake = FakeLLM([], when_exhausted=error_turn(500, body="upstream exploded"))
    with fake.patch(), patch("remedy.core.agent._message_wants_tools",
                             return_value=False):
        text = await runtime._call_llm("say hello")

    assert fake.request_count >= 1
    assert "upstream exploded" in text or "500" in text
