"""Dispatcher guard: a hung tool returns TOOL_TIMEOUT instead of hanging the turn."""

from __future__ import annotations

import asyncio

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.tool_timeouts import (
    DEFAULT_TOOL_TIMEOUT_S,
    TOOL_TIMEOUTS,
    tool_timeout_for,
)
from remedy.models import AgentConfig, ToolCall

PARAMS = {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_hung_tool_times_out_with_formatted_error():
    rt = BasicRuntime(AgentConfig(llm_api_key=""))
    cancelled = asyncio.Event()

    async def sleepy(**kwargs):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    sleepy._remedy_timeout = 0.2  # type: ignore[attr-defined]
    rt.tool_registry.register_builtin_handler("sleepy", "zz", sleepy, parameters=PARAMS)

    result = await asyncio.wait_for(
        rt.call_tool(ToolCall(tool_name="sleepy", arguments={})), timeout=30
    )
    assert not result.success
    assert "TOOL_TIMEOUT" in (result.error or "")
    assert "sleepy" in (result.error or "")
    assert cancelled.is_set(), "the hung coroutine must be cancelled, not leaked"


@pytest.mark.asyncio
async def test_fast_tool_unaffected():
    rt = BasicRuntime(AgentConfig(llm_api_key=""))

    async def quick(**kwargs):
        return "ok"

    rt.tool_registry.register_builtin_handler("quick", "q", quick, parameters=PARAMS)
    result = await rt.call_tool(ToolCall(tool_name="quick", arguments={}))
    assert result.success and result.data == "ok"


@pytest.mark.asyncio
async def test_opt_out_tool_is_not_wrapped():
    rt = BasicRuntime(AgentConfig(llm_api_key=""))

    async def free(**kwargs):
        return "free"

    free._remedy_timeout = None  # type: ignore[attr-defined]
    rt.tool_registry.register_builtin_handler("free", "f", free, parameters=PARAMS)
    assert tool_timeout_for("free", rt.tool_registry) is None
    result = await rt.call_tool(ToolCall(tool_name="free", arguments={}))
    assert result.success


def test_timeout_table_respects_internal_limits():
    # host_run clamps its own timeout to 600 s; the outer guard must sit above it.
    assert tool_timeout_for("host_run") > 600
    assert tool_timeout_for("bash_exec") > 600
    assert tool_timeout_for("build_drive") is None
    assert tool_timeout_for("computer_click") == 300.0
    assert tool_timeout_for("repo_search") == DEFAULT_TOOL_TIMEOUT_S
    for name, secs in TOOL_TIMEOUTS.items():
        assert secs is None or secs >= DEFAULT_TOOL_TIMEOUT_S, name
