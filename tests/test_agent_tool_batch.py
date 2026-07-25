"""Unit tests for peeled tool-batch helpers (agent_tool_batch)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.agent_tool_batch import execute_tool_calls, progress_marker
from remedy.models import AgentConfig, ToolCall, ToolResult


def test_progress_marker_indeterminate_for_single_job() -> None:
    raw = progress_marker(label="file_read", step=0, total=1)
    assert raw.startswith("@@progress:")
    payload = json.loads(raw.split(":", 1)[1])
    assert payload["label"] == "file_read"
    assert "percent" not in payload


def test_progress_marker_percent_for_multi_and_force() -> None:
    mid = progress_marker(label="tools", step=1, total=4)
    mid_p = json.loads(mid.split(":", 1)[1])
    assert mid_p["percent"] == 25.0

    done = progress_marker(
        label="file_read", step=1, total=1, percent=100.0, force_percent=True
    )
    done_p = json.loads(done.split(":", 1)[1])
    assert done_p["percent"] == 100.0


@pytest.mark.asyncio
async def test_execute_tool_calls_module_pairs_all_ids() -> None:
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
            "id": "a",
            "type": "function",
            "function": {"name": "file_read", "arguments": '{"path":"x.py"}'},
        },
        {
            "id": "b",
            "type": "function",
            "function": {"name": "file_read", "arguments": '{"path":"y.py"}'},
        },
    ]
    seen: set[str] = set()
    cache: dict[str, str] = {}
    tool_msgs = []
    async for event, msg in execute_tool_calls(
        rt, calls, seen_fps=seen, result_cache=cache
    ):
        if event.startswith("@@progress:"):
            assert "label" in json.loads(event.split(":", 1)[1])
        if msg.get("role") == "tool":
            tool_msgs.append(msg)
    assert {m["tool_call_id"] for m in tool_msgs} == {"a", "b"}
    assert len(cache) == 2


@pytest.mark.asyncio
async def test_execute_tool_calls_records_turn_steps() -> None:
    rt = BasicRuntime(AgentConfig(llm_api_key=""))
    rt._turn_tool_steps = []

    async def ok(**_kwargs):
        return "done"

    rt.tool_registry.register_builtin_handler(
        "list_dir",
        "list",
        ok,
        parameters={"type": "object", "properties": {}},
    )
    calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "list_dir", "arguments": "{}"},
        }
    ]
    async for _e, _m in execute_tool_calls(
        rt, calls, seen_fps=set(), result_cache={}
    ):
        pass
    assert len(rt._turn_tool_steps) == 1
    assert rt._turn_tool_steps[0]["tool"] == "list_dir"
    assert rt._turn_tool_steps[0]["success"] is True


@pytest.mark.asyncio
async def test_parallel_cap_patch_on_tool_batch_module() -> None:
    rt = BasicRuntime(AgentConfig(llm_api_key=""))
    n = {"c": 0}

    async def counter(**_kwargs):
        n["c"] += 1
        return {"n": n["c"]}

    rt.tool_registry.register_builtin_handler(
        "counter",
        "count",
        counter,
        parameters={"type": "object", "properties": {"i": {"type": "integer"}}},
    )
    calls = [
        {
            "id": f"call_{i}",
            "type": "function",
            "function": {
                "name": "counter",
                "arguments": json.dumps({"i": i}),
            },
        }
        for i in range(4)
    ]
    with patch("remedy.core.agent_tool_batch._MAX_PARALLEL_TOOLS", 1):
        msgs = []
        async for _e, m in execute_tool_calls(
            rt, calls, seen_fps=set(), result_cache={}
        ):
            if m.get("role") == "tool":
                msgs.append(m)
    assert len(msgs) == 4
    assert n["c"] == 4


@pytest.mark.asyncio
async def test_tool_exception_still_emits_tool_message() -> None:
    rt = BasicRuntime(AgentConfig(llm_api_key=""))

    async def raise_call(_tc: ToolCall) -> ToolResult:
        raise RuntimeError("boom")

    rt.call_tool = raise_call  # type: ignore[method-assign]
    calls = [
        {
            "id": "err1",
            "type": "function",
            "function": {"name": "list_dir", "arguments": "{}"},
        }
    ]
    tool_msgs = []
    async for _e, m in execute_tool_calls(
        rt, calls, seen_fps=set(), result_cache={}
    ):
        if m.get("role") == "tool":
            tool_msgs.append(m)
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "err1"
    assert "boom" in tool_msgs[0]["content"] or "TOOL_" in tool_msgs[0]["content"]
