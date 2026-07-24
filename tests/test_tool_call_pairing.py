"""Tool-call / tool-result pairing contract (OpenAI-compatible HTTP 400 guard)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.react_stream import ensure_tool_call_pairings, normalize_tool_calls
from remedy.models import AgentConfig, ToolCall


def test_normalize_tool_calls_assigns_ids_and_skips_empty_names():
    raw = [
        {
            "id": "",
            "type": "function",
            "function": {"name": "list_dir", "arguments": "{}"},
        },
        {
            "id": "keep_me",
            "type": "function",
            "function": {"name": "file_read", "arguments": {"path": "a.py"}},
        },
        {
            "function": {"name": "", "arguments": "{}"},
        },
    ]
    out = normalize_tool_calls(raw)
    assert len(out) == 2
    assert out[0]["id"]
    assert out[0]["function"]["name"] == "list_dir"
    assert out[1]["id"] == "keep_me"
    assert out[1]["function"]["arguments"] == '{"path": "a.py"}'


def test_ensure_tool_call_pairings_fills_missing_results():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "review project"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_a",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                },
                {
                    "id": "call_b",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": '{"path":"x"}'},
                },
            ],
        },
        # Only one result — the classic incomplete-pairing bug.
        {"role": "tool", "tool_call_id": "call_a", "content": "ok"},
        {"role": "user", "content": "continue"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    tool_ids = [
        m["tool_call_id"] for m in fixed if m.get("role") == "tool"
    ]
    assert tool_ids == ["call_a", "call_b"]
    assert any("missing tool result" in (m.get("content") or "") for m in fixed if m.get("tool_call_id") == "call_b")
    # User message after tools is preserved.
    assert fixed[-1]["role"] == "user"


def test_ensure_tool_call_pairings_drops_orphan_tool_messages():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "orphan", "content": "nope"},
        {"role": "assistant", "content": "ok"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    assert [m["role"] for m in fixed] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_execute_tool_calls_emits_result_for_every_id_beyond_parallel_cap():
    """Cap must limit concurrency, not drop tool results (HTTP 400 root cause)."""
    rt = BasicRuntime(AgentConfig(llm_api_key=""))

    async def echo(**kwargs):
        return {"path": kwargs.get("path")}

    rt.tool_registry.register_builtin_handler(
        "file_read",
        "read",
        echo,
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )

    calls = [
        {
            "id": f"call_{i}",
            "type": "function",
            "function": {
                "name": "file_read",
                "arguments": json.dumps({"path": f"f{i}.py"}),
            },
        }
        for i in range(5)
    ]
    seen: set[str] = set()
    cache: dict[str, str] = {}

    with patch("remedy.core.agent._MAX_PARALLEL_TOOLS", 2):
        events: list[tuple[str, dict]] = []
        async for event, msg in rt._execute_tool_calls(
            calls, seen_fps=seen, result_cache=cache
        ):
            events.append((event, msg))

    tool_msgs = [m for e, m in events if m.get("role") == "tool"]
    assert len(tool_msgs) == 5
    assert {m["tool_call_id"] for m in tool_msgs} == {f"call_{i}" for i in range(5)}
    # All five distinct fingerprints executed.
    assert len(cache) == 5


@pytest.mark.asyncio
async def test_execute_tool_calls_dedupes_work_but_pairs_all_ids():
    """Same fingerprint twice → one execution, two tool results with correct ids."""
    rt = BasicRuntime(AgentConfig(llm_api_key=""))
    n = {"c": 0}

    async def counter(**kwargs):
        n["c"] += 1
        return {"n": n["c"]}

    rt.tool_registry.register_builtin_handler(
        "counter",
        "count",
        counter,
        parameters={"type": "object", "properties": {}},
    )

    calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "counter", "arguments": "{}"},
        },
        {
            "id": "call_2",
            "type": "function",
            "function": {"name": "counter", "arguments": "{}"},
        },
    ]
    seen: set[str] = set()
    cache: dict[str, str] = {}
    tool_msgs = []
    async for _event, msg in rt._execute_tool_calls(
        calls, seen_fps=seen, result_cache=cache
    ):
        if msg.get("role") == "tool":
            tool_msgs.append(msg)

    assert n["c"] == 1
    assert len(tool_msgs) == 2
    assert {m["tool_call_id"] for m in tool_msgs} == {"call_1", "call_2"}
    assert tool_msgs[0]["content"] == tool_msgs[1]["content"]


@pytest.mark.asyncio
async def test_execute_tool_calls_exception_uses_matching_tool_call_id():
    rt = BasicRuntime(AgentConfig(llm_api_key=""))

    async def boom(**kwargs):
        raise RuntimeError("kaboom")

    # Bypass ToolRegistry error wrapping: patch call_tool to raise.
    async def raise_call(tool_call: ToolCall):
        raise RuntimeError("kaboom")

    rt.call_tool = raise_call  # type: ignore[method-assign]

    calls = [
        {
            "id": "call_x",
            "type": "function",
            "function": {"name": "file_read", "arguments": "{}"},
        }
    ]
    tool_msgs = []
    async for _e, msg in rt._execute_tool_calls(
        calls, seen_fps=set(), result_cache={}
    ):
        if msg.get("role") == "tool":
            tool_msgs.append(msg)

    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_x"
    assert "kaboom" in tool_msgs[0]["content"] or "TOOL_EXCEPTION" in tool_msgs[0]["content"]
