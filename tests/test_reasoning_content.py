"""DeepSeek thinking-mode reasoning_content must round-trip on tool turns."""

from __future__ import annotations

from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_sse_chunk,
    build_assistant_api_message,
    repair_reasoning_content_in_messages,
)


def test_build_assistant_includes_reasoning_on_tool_calls():
    msg = build_assistant_api_message(
        content=None,
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": "{}"},
            }
        ],
        reasoning_content="I should list the project root first.",
    )
    assert msg["role"] == "assistant"
    assert msg["tool_calls"]
    assert msg["reasoning_content"] == "I should list the project root first."


def test_build_assistant_empty_reasoning_on_tool_calls():
    """Missing reasoning still gets the field so DeepSeek won't 400."""
    msg = build_assistant_api_message(
        content="ok",
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "list_dir", "arguments": "{}"},
            }
        ],
        reasoning_content=None,
    )
    assert "reasoning_content" in msg
    assert msg["reasoning_content"] == ""


def test_repair_reasoning_content_in_messages():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "1", "content": "ok"},
    ]
    assert repair_reasoning_content_in_messages(messages) is True
    assert messages[1]["reasoning_content"] == ""
    assert repair_reasoning_content_in_messages(messages) is False


def test_sse_accumulates_reasoning_alongside_content():
    state = StreamRoundState()
    apply_openai_sse_chunk(
        state,
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "think…",
                        "content": "hello",
                    }
                }
            ]
        },
        stream_live=False,
    )
    assert "think" in state.reasoning_out
    assert "hello" in state.text_out
