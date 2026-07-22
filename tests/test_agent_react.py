"""Tests for BasicRuntime ReAct loop and abstract AgentRuntime."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from remedy.core.agent import BasicRuntime
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


@pytest.mark.asyncio
async def test_react_loop_executes_tool_then_final_answer():
    rt = BasicRuntime(
        AgentConfig(llm_api_key="sk-test", llm_model="gpt-test", llm_base_url="http://llm")
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

    tool_turn = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "add",
                                "arguments": '{"a": 2, "b": 3}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    final_turn = {
        "choices": [{"message": {"content": "The sum is 5.", "tool_calls": []}}]
    }

    with patch.object(
        rt, "_post_chat", new=AsyncMock(side_effect=[tool_turn, final_turn])
    ) as mock_post:
        text = await rt._call_llm("What is 2+3?")
        assert text == "The sum is 5."
        assert mock_post.await_count == 2


@pytest.mark.asyncio
async def test_telegram_handle_update_emits_to_handlers():
    from remedy.gateway.channels.adapters import TelegramChannel
    from remedy.gateway.router import Gateway

    class StubRuntime(AgentRuntime):
        async def handle_event(self, event):
            yield f"echo:{event.payload.get('message')}"

        async def call_tool(self, tool_call):
            from remedy.models import ToolResult

            return ToolResult(call_id=tool_call.id, success=False, error="n/a")

    received: list[str] = []
    gw = Gateway(StubRuntime(AgentConfig()))

    async def handler(event):
        async for chunk in gw.runtime.handle_event(event):
            received.append(str(chunk))
            yield chunk

    gw.register_handler(handler)
    ch = TelegramChannel(gw, bot_token="tok", chat_ids=["42"])
    ch._running = True

    await ch._handle_update(
        {
            "update_id": 7,
            "message": {
                "text": "hi bot",
                "chat": {"id": 42},
                "from": {"id": 99, "username": "alice"},
            },
        }
    )
    assert ch._last_update_id == 7
    assert any("echo:hi bot" in m for m in received)
