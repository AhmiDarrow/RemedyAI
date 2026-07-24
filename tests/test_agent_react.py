"""Tests for BasicRuntime ReAct loop and abstract AgentRuntime."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remedy.core.agent import BasicRuntime, _message_wants_tools
from remedy.core.runtime import AgentRuntime
from remedy.models import (
    AgentConfig,
    ChannelKind,
    EventKind,
    GatewayEvent,
    ToolCall,
)


def test_agent_runtime_is_abstract():
    with pytest.raises(TypeError):
        AgentRuntime(AgentConfig())


@pytest.mark.asyncio
async def test_basic_runtime_fallback_greeting(tmp_path):
    cfg = AgentConfig(
        name="TestBot",
        llm_api_key="",
        memory_db_path=str(tmp_path / "m.db"),
        home_dir=str(tmp_path),
    )
    rt = BasicRuntime(cfg)
    await rt.start()
    try:
        event = GatewayEvent(
            kind=EventKind.MESSAGE,
            channel=ChannelKind.CLI,
            source_id="user",
            payload={"message": "hello"},
        )
        parts = [p async for p in rt.handle_event(event)]
        assert any("Hello" in str(p) for p in parts)
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_basic_runtime_call_tool_registered():
    rt = BasicRuntime(AgentConfig(llm_api_key=""))

    async def handler(**kwargs):
        return {"echo": kwargs.get("x")}

    rt.tool_registry.register_builtin_handler(
        "echo_tool", "echo", handler, parameters={"type": "object", "properties": {}}
    )
    result = await rt.call_tool(ToolCall(tool_name="echo_tool", arguments={"x": 1}))
    assert result.success
    assert result.data == {"echo": 1}


@pytest.mark.asyncio
async def test_basic_runtime_call_tool_missing():
    rt = BasicRuntime(AgentConfig(llm_api_key=""))
    result = await rt.call_tool(ToolCall(tool_name="nope", arguments={}))
    assert not result.success
    assert result.error


def _sse_bytes(events: list[dict]) -> list[bytes]:
    chunks: list[bytes] = []
    for ev in events:
        chunks.append(f"data: {json.dumps(ev)}\n\n".encode())
    chunks.append(b"data: [DONE]\n\n")
    return chunks


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeResp:
    def __init__(self, chunks: list[bytes], status: int = 200) -> None:
        self.status = status
        self.content = _FakeContent(chunks)

    async def text(self) -> str:
        return ""

    async def json(self) -> dict:
        return {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, responses: list[_FakeResp]) -> None:
        self._responses = list(responses)
        self.posts = 0
        self.bodies: list[dict] = []

    def post(self, *args, **kwargs):
        self.posts += 1
        body = kwargs.get("json")
        if isinstance(body, dict):
            self.bodies.append(body)
        if not self._responses:
            raise RuntimeError("no more fake responses")
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_react_loop_executes_tool_then_final_answer():
    """OpenAI-compatible stream: tool call step, then final text."""
    rt = BasicRuntime(
        AgentConfig(
            llm_api_key="sk-test",
            llm_model="gpt-test",
            llm_base_url="http://llm/v1",
            llm_provider="openai",
        )
    )

    async def add_handler(**kwargs):
        return {"sum": kwargs.get("a", 0) + kwargs.get("b", 0)}

    rt.tool_registry.register_builtin_handler(
        "add",
        "add two numbers",
        add_handler,
        parameters={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        },
    )

    tool_sse = _sse_bytes(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "add",
                                        "arguments": '{"a": 2, "b": 3}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ]
    )
    final_sse = _sse_bytes(
        [
            {
                "choices": [
                    {
                        "delta": {"content": "The sum is 5."},
                        "finish_reason": "stop",
                    }
                ]
            }
        ]
    )

    session = _FakeSession([_FakeResp(tool_sse), _FakeResp(final_sse)])

    # Force tools on even though the prompt is short math.
    with (
        patch("remedy.core.agent.aiohttp.ClientSession", return_value=session),
        patch("remedy.core.agent._message_wants_tools", return_value=True),
    ):
        text = await rt._call_llm("run add tool a=2 b=3")

    assert "The sum is 5." in text
    assert session.posts == 2


def test_message_wants_tools_policy():
    assert _message_wants_tools("hi") is False
    assert _message_wants_tools("review project") is True


@pytest.mark.asyncio
async def test_react_loop_dedups_identical_tool_calls():
    """Second identical tool call is skipped (fingerprint cache)."""
    rt = BasicRuntime(
        AgentConfig(
            llm_api_key="sk-test",
            llm_model="gpt-test",
            llm_base_url="http://llm/v1",
            llm_provider="openai",
        )
    )
    calls = {"n": 0}

    async def counter(**kwargs):
        calls["n"] += 1
        return {"n": calls["n"]}

    rt.tool_registry.register_builtin_handler(
        "counter",
        "count",
        counter,
        parameters={"type": "object", "properties": {}},
    )

    def _tool_step() -> list[bytes]:
        return _sse_bytes(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_x",
                                        "type": "function",
                                        "function": {
                                            "name": "counter",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ]
        )

    final_sse = _sse_bytes(
        [{"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}]
    )
    session = _FakeSession(
        [_FakeResp(_tool_step()), _FakeResp(_tool_step()), _FakeResp(final_sse)]
    )

    with (
        patch("remedy.core.agent.aiohttp.ClientSession", return_value=session),
        patch("remedy.core.agent._message_wants_tools", return_value=True),
    ):
        text = await rt._call_llm("run counter twice")

    assert calls["n"] == 1
    assert "done" in text


@pytest.mark.asyncio
async def test_react_loop_recovery_nudge_on_tool_error():
    """Failing tool batch injects RECOVERY_NUDGE once before the next LLM turn."""
    from remedy.core.react_policy import RECOVERY_NUDGE

    rt = BasicRuntime(
        AgentConfig(
            llm_api_key="sk-test",
            llm_model="gpt-test",
            llm_base_url="http://llm/v1",
            llm_provider="openai",
        )
    )

    async def boom(**kwargs):
        return (
            "Error [NOT_FOUND:file_read]: file not found: missing.py\n"
            "Suggestion: Call list_dir on parent."
        )

    rt.tool_registry.register_builtin_handler(
        "file_read",
        "read file",
        boom,
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    )

    tool_sse = _sse_bytes(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_fail",
                                    "type": "function",
                                    "function": {
                                        "name": "file_read",
                                        "arguments": '{"path": "missing.py"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ]
    )
    final_sse = _sse_bytes(
        [
            {
                "choices": [
                    {
                        "delta": {"content": "Recovered by listing the directory."},
                        "finish_reason": "stop",
                    }
                ]
            }
        ]
    )
    session = _FakeSession([_FakeResp(tool_sse), _FakeResp(final_sse)])

    with (
        patch("remedy.core.agent.aiohttp.ClientSession", return_value=session),
        patch("remedy.core.agent._message_wants_tools", return_value=True),
    ):
        text = await rt._call_llm("read missing.py")

    assert "Recovered" in text
    assert session.posts == 2
    # Second request must include the recovery nudge user message.
    assert len(session.bodies) >= 2
    second_msgs = session.bodies[1].get("messages") or []
    nudge_msgs = [
        m
        for m in second_msgs
        if m.get("role") == "user" and RECOVERY_NUDGE in str(m.get("content") or "")
    ]
    assert len(nudge_msgs) == 1
