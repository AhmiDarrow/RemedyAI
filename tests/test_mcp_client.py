"""MCP client unit tests (mocked transport)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from remedy.models import ToolCall, ToolDefinition, ToolSource
from remedy.tools.mcp_client import MCPClient


@pytest.mark.asyncio
async def test_call_tool_success() -> None:
    client = MCPClient()
    client._servers["srv"] = {"connected": True}
    client._tools["mcp:srv:echo"] = ToolDefinition(
        name="echo",
        description="echo",
        source=ToolSource.MCP,
        uri="mcp://srv/echo",
    )
    client._send_request = AsyncMock(  # type: ignore[method-assign]
        return_value={"content": [{"text": "pong"}]}
    )
    result = await client.call_tool(ToolCall(tool_name="echo", arguments={"q": 1}))
    assert result.success
    assert result.data["result"] == "pong"
    client._send_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_tool_strips_internal_mcp_server_arg() -> None:
    """Routing key _mcp_server must not be forwarded to the MCP server."""
    client = MCPClient()
    client._servers["srv"] = {"connected": True}
    client._tools["mcp:srv:echo"] = ToolDefinition(
        name="echo",
        description="echo",
        source=ToolSource.MCP,
        uri="mcp://srv/echo",
    )
    client._send_request = AsyncMock(  # type: ignore[method-assign]
        return_value={"content": [{"text": "ok"}]}
    )
    await client.call_tool(
        ToolCall(tool_name="echo", arguments={"q": 1, "_mcp_server": "srv"})
    )
    args = client._send_request.await_args
    # tools/call params are positional: server, method, params
    params = args.args[2] if args.args and len(args.args) >= 3 else args.kwargs.get("params")
    if params is None and args.args:
        # signature: (server_name, method, params, timeout=...)
        params = args.args[2]
    assert params is not None
    assert "_mcp_server" not in (params.get("arguments") or {})
    assert (params.get("arguments") or {}).get("q") == 1


@pytest.mark.asyncio
async def test_call_tool_not_found() -> None:
    client = MCPClient()
    result = await client.call_tool(ToolCall(tool_name="missing", arguments={}))
    assert not result.success
    assert "not found" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_call_tool_server_error() -> None:
    client = MCPClient()
    client._servers["srv"] = {"connected": True}
    client._tools["mcp:srv:fail"] = ToolDefinition(
        name="fail",
        description="fail",
        source=ToolSource.MCP,
        uri="mcp://srv/fail",
    )
    client._send_request = AsyncMock(  # type: ignore[method-assign]
        return_value={"error": "boom"}
    )
    result = await client.call_tool(ToolCall(tool_name="fail", arguments={}))
    assert not result.success
    assert "boom" in (result.error or "")


@pytest.mark.asyncio
async def test_discover_tools_registers() -> None:
    client = MCPClient()
    client._servers["srv"] = {"connected": True}
    client._send_request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "tools": [
                {
                    "name": "search",
                    "description": "Search things",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }
    )
    tools = await client.discover_tools("srv")
    assert len(tools) == 1
    assert tools[0].name == "search"
    assert client.get_tool("search", server="srv") is not None
