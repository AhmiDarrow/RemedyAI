"""Model Context Protocol (MCP) client integration.

MCP is the primary tool abstraction layer in Remedy. This module provides
a client for connecting to MCP servers, listing their tools/resources/prompts,
and invoking them on behalf of the agent.
"""

from __future__ import annotations

from typing import Any, Optional

from remedy.models import ToolCall, ToolDefinition, ToolResult, ToolSource


class MCPClient:
    """Client for interacting with Model Context Protocol servers.

    MCP (https://modelcontextprotocol.io) provides a standardized way
    to expose tools, resources, and prompts to LLM agents. Remedy uses
    MCP as its primary tool abstraction layer.

    For Phase 0, this is a minimal stub. Full implementation in Phase 1-2.
    """

    def __init__(self) -> None:
        self._servers: dict[str, dict] = {}
        self._tools: dict[str, ToolDefinition] = {}

    async def connect(self, server_name: str, command: str, args: list[str]) -> None:
        """Register an MCP server connection configuration.

        In production, this would spawn a subprocess and communicate
        via stdio/JSON-RPC. For Phase 0, we store the config.
        """
        self._servers[server_name] = {
            "command": command,
            "args": args,
            "connected": True,
        }

    async def list_tools(self) -> list[ToolDefinition]:
        """Return all known tools from connected MCP servers."""
        return list(self._tools.values())

    async def call_tool(self, tool_call: ToolCall) -> ToolResult:
        """Invoke an MCP tool and return the result.

        Stub implementation for Phase 0.
        """
        tool = self._tools.get(tool_call.tool_name)
        if tool is None:
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=f"Tool not found: {tool_call.tool_name}",
            )
        return ToolResult(
            call_id=tool_call.id,
            success=True,
            data={"message": f"Tool '{tool_call.tool_name}' called (stub)"},
        )

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Optional[dict[str, Any]] = None,
        source: ToolSource = ToolSource.MCP,
    ) -> ToolDefinition:
        """Register a tool definition (useful for builtins and testing)."""
        tool = ToolDefinition(
            name=name,
            description=description,
            source=source,
            parameters=parameters or {},
        )
        self._tools[name] = tool
        return tool
