"""Unit tests for streaming ReAct helpers."""

from __future__ import annotations

import json

from remedy.core.react_stream import (
    StreamRoundState,
    accumulate_tool_call_delta,
    apply_openai_sse_chunk,
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
