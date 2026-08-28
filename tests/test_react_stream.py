"""Unit tests for streaming ReAct helpers."""

from __future__ import annotations

import pytest

from remedy.core.react_stream import (
    StreamRoundState,
    accumulate_tool_call_delta,
    apply_openai_sse_chunk,
    apply_reasoning_piece,
    build_runtime_system_block,
    filter_fresh_tool_calls,
    finalize_round_text,
    parse_sse_data_line,
    should_enable_tools,
    tool_call_fingerprint,
)


def test_parse_sse_data_line() -> None:
    assert parse_sse_data_line(": keep-alive") is None
    assert parse_sse_data_line("data: [DONE]") is None
    chunk = parse_sse_data_line('data: {"choices":[{"delta":{"content":"hi"}}]}')
    assert chunk is not None
    assert chunk["choices"][0]["delta"]["content"] == "hi"


def test_accumulate_tool_call_deltas() -> None:
    acc: dict = {}
    accumulate_tool_call_delta(
        acc,
        {
            "index": 0,
            "id": "c1",
            "function": {"name": "file_read", "arguments": '{"pa'},
        },
    )
    accumulate_tool_call_delta(
        acc,
        {"index": 0, "function": {"arguments": 'th":"a.py"}'}},
    )
    assert acc[0]["function"]["name"] == "file_read"
    assert acc[0]["function"]["arguments"] == '{"path":"a.py"}'
    assert acc[0]["id"] == "c1"


def test_sse_does_not_live_stream_tool_c_prefix() -> None:
    """Live 2026-08-13: DeepSeek streamed 'tool_c' into the chat bubble."""
    state = StreamRoundState()
    live = apply_openai_sse_chunk(
        state,
        {"choices": [{"delta": {"content": "tool_c"}}]},
        stream_live=True,
    )
    assert live is None
    assert state.produced_user_text is False
    assert state.suppressed_tool_markup is True
    live2 = apply_openai_sse_chunk(
        state,
        {"choices": [{"delta": {"content": "alls>"}}]},
        stream_live=True,
    )
    assert live2 is None


def test_apply_openai_sse_chunk_live_and_tools() -> None:
    state = StreamRoundState()
    live = apply_openai_sse_chunk(
        state,
        {"choices": [{"delta": {"content": "Hello"}}]},
        stream_live=True,
    )
    assert live == "Hello"
    assert state.produced_user_text is True

    apply_openai_sse_chunk(
        state,
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "t1",
                                "function": {"name": "list_dir", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        },
        stream_live=False,
    )
    tcs = state.tool_calls_list()
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "list_dir"


def test_reasoning_snapshots_are_not_concatenated() -> None:
    """A proxy that resends the full scratchpad each chunk must not stack it.

    Session 4d89: thinking was ``The user wants…`` twenty times because every
    SSE delta was treated as a suffix.
    """
    state = StreamRoundState()
    apply_openai_sse_chunk(
        state,
        {"choices": [{"delta": {"reasoning_content": "The user wants a bot."}}]},
        stream_live=True,
    )
    apply_openai_sse_chunk(
        state,
        {"choices": [{"delta": {"reasoning_content": "The user wants a bot."}}]},
        stream_live=True,
    )
    apply_openai_sse_chunk(
        state,
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "The user wants a bot. I'll open config."
                    }
                }
            ]
        },
        stream_live=True,
    )
    assert state.reasoning_out == "The user wants a bot. I'll open config."


def test_apply_reasoning_piece_suffix_and_shorter_snapshot() -> None:
    state = StreamRoundState()
    assert apply_reasoning_piece(state, "abc") == "abc"
    assert apply_reasoning_piece(state, "abc") == ""
    assert apply_reasoning_piece(state, "abcd") == "d"
    assert apply_reasoning_piece(state, "ab") == ""
    assert state.reasoning_out == "abcd"


def test_finish_reason_length_detected() -> None:
    state = StreamRoundState()
    apply_openai_sse_chunk(
        state,
        {"choices": [{"delta": {"content": "…"}, "finish_reason": "length"}]},
        stream_live=True,
    )
    assert state.finish_reason == "length"
    assert state.hit_length_limit is True


def test_finalize_prefers_reasoning_without_tools() -> None:
    state = StreamRoundState()
    state.reasoning_parts.append("think…")
    assert finalize_round_text(state, []) == "think…"
    state.content_parts.append("answer")
    assert finalize_round_text(state, []) == "answer"


def test_should_enable_tools_and_filter() -> None:
    tools = [{"type": "function", "function": {"name": "x"}}]
    assert should_enable_tools("hi", tools, has_attachments=False) is False
    assert should_enable_tools("read src/", tools, has_attachments=False) is True
    assert should_enable_tools("hi", tools, has_attachments=True) is True

    tc = {"function": {"name": "file_read", "arguments": '{"path":"a"}'}}
    seen = {tool_call_fingerprint(tc)}
    assert filter_fresh_tool_calls([tc], seen) == []
    assert len(filter_fresh_tool_calls([tc], set())) == 1


def test_build_runtime_system_block() -> None:
    block = build_runtime_system_block(
        system_prompt="You are Remedy",
        provider="openai",
        model="gpt-test",
        base_url="http://x/v1",
        max_steps=12,
        context="Workspace: /tmp",
    )
    assert "You are Remedy" in block
    assert "gpt-test" in block
    assert "Workspace: /tmp" in block

def test_finalize_promotes_reasoning_when_no_tools() -> None:
    """DeepSeek often leaves content empty and puts the answer in reasoning."""
    state = StreamRoundState()
    state.reasoning_parts.append("Complete project review text.")
    assert finalize_round_text(state, []) == "Complete project review text."
    # With tool calls this round, keep reasoning separate (API needs it on the msg).
    assert finalize_round_text(state, [{"function": {"name": "list_dir"}}]) == ""


def test_finish_reason_length_stream_live_false() -> None:
    state = StreamRoundState()
    apply_openai_sse_chunk(
        state,
        {"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]},
        stream_live=False,
    )
    assert state.hit_length_limit is True


@pytest.mark.asyncio
async def test_consume_http_cancels_when_turn_aborted() -> None:
    """Stop mid-SSE must cancel the HTTP wait, not only the next ReAct step."""
    import asyncio

    from remedy.core.react_loop.stream_consume import consume_llm_http_response
    from remedy.core.react_stream import StreamRoundState
    from remedy.core.turn_context import abort_session, begin_turn, end_turn

    class _SlowContent:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(30)
            return b'data: {"choices":[{"delta":{"content":"x"}}]}\n'

    class _Resp:
        headers = {"Content-Type": "text/event-stream"}
        content = _SlowContent()
        closed = False

        def close(self):
            self.closed = True

    class _Bind:
        model = "t"
        provider = "t"

    toks = begin_turn("sse-abort", project_raw=None, active_path=".")
    resp = _Resp()
    try:

        async def _abort_soon():
            await asyncio.sleep(0.05)
            abort_session("sse-abort")

        t = asyncio.create_task(_abort_soon())
        with pytest.raises(asyncio.CancelledError):
            async for _ in consume_llm_http_response(
                resp,
                round_state=StreamRoundState(),
                collected={},
                adapter=None,
                bind=_Bind(),
                body={"stream": True},
                use_openai_sse=True,
                stream_live=True,
            ):
                pass
        await t
        assert resp.closed is True
    finally:
        end_turn("sse-abort", *toks)


@pytest.mark.asyncio
async def test_consume_http_drop_without_stop_is_a_disconnect() -> None:
    """A cancelled wait with no owner Stop is a drop, not Generation stopped."""
    import asyncio

    from remedy.core.react_loop.stream_consume import (
        PROVIDER_DROP_ERROR,
        consume_llm_http_response,
    )
    from remedy.core.react_stream import StreamRoundState
    from remedy.core.turn_context import begin_turn, end_turn

    class _DropContent:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise asyncio.CancelledError()

    class _Resp:
        headers = {"Content-Type": "text/event-stream"}
        content = _DropContent()
        closed = False

        def close(self):
            self.closed = True

    class _Bind:
        model = "t"
        provider = "t"

    toks = begin_turn("sse-drop", project_raw=None, active_path=".")
    resp = _Resp()
    try:
        with pytest.raises(ConnectionError, match="connection reset") as ei:
            async for _ in consume_llm_http_response(
                resp,
                round_state=StreamRoundState(),
                collected={},
                adapter=None,
                bind=_Bind(),
                body={"stream": True},
                use_openai_sse=True,
                stream_live=True,
            ):
                pass
        assert PROVIDER_DROP_ERROR in str(ei.value)
        assert resp.closed is True
    finally:
        end_turn("sse-drop", *toks)


def test_synthesis_leftover_emits_json_or_reasoning():
    """JSON completions fill round_state without yielding; leftover is the answer."""
    rs = StreamRoundState()
    assert not (rs.text_out or rs.reasoning_out)
    rs.reasoning_parts.append("  only thinking  ")
    leftover = rs.text_out or rs.reasoning_out
    assert leftover == "only thinking"
    rs.content_parts.append("final answer")
    leftover = rs.text_out or rs.reasoning_out
    assert leftover == "final answer"
