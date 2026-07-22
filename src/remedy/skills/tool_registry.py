"""Tool registry and MCP client integration.

Manages the catalog of available tools across MCP servers, skills,
and builtins. Tracks invocation history and tool metadata.
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from remedy.models import ToolCall, ToolDefinition, ToolResult, ToolSource


class ToolRegistry:
    """Catalog of all available tools across sources (MCP, skills, builtins)."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._by_source: dict[ToolSource, list[str]] = defaultdict(list)
        self._invocation_history: list[dict[str, Any]] = []
        self._mcp_servers: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable] = {}

    @property
    def tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        key = f"{tool.source.value}:{tool.name}"
        self._tools[key] = tool
        self._by_source[tool.source].append(tool.name)
        return tool

    def register_builtin(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
    ) -> ToolDefinition:
        return self.register(ToolDefinition(
            name=name,
            description=description,
            source=ToolSource.BUILTIN,
            parameters=parameters or {},
        ))

    def register_handler(self, name: str, handler: Callable) -> None:
        """Register an async callable handler for a tool."""
        self._handlers[name] = handler

    def register_builtin_handler(
        self,
        name: str,
        description: str,
        handler: Callable,
        parameters: dict[str, Any] | None = None,
    ) -> ToolDefinition:
        """Register a tool definition + handler in one call."""
        self._handlers[name] = handler
        return self.register_builtin(name, description, parameters)

    async def execute(self, name: str, **arguments: Any) -> Any:
        """Invoke a tool by name with its registered handler."""
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"No handler registered for tool: {name}")
        if inspect.iscoroutinefunction(handler):
            return await handler(**arguments)
        return handler(**arguments)

    def register_from_mcp(
        self,
        server_name: str,
        tool_def: dict[str, Any],
    ) -> ToolDefinition:
        tool = ToolDefinition(
            name=tool_def.get("name", "unknown"),
            description=tool_def.get("description", ""),
            source=ToolSource.MCP,
            parameters=tool_def.get("parameters", tool_def.get("inputSchema", {})),
            uri=f"mcp://{server_name}/{tool_def.get('name', 'unknown')}",
        )
        self._mcp_servers[server_name] = self._mcp_servers.get(server_name, {})
        return self.register(tool)

    def register_skill_tool(
        self,
        skill_name: str,
        tool_name: str,
        description: str,
    ) -> ToolDefinition:
        return self.register(ToolDefinition(
            name=tool_name,
            description=description,
            source=ToolSource.SKILL,
            parameters={},
            uri=f"skill://{skill_name}/{tool_name}",
        ))

    def get_definition(self, name: str, source: ToolSource | None = None) -> ToolDefinition | None:
        if source:
            return self._tools.get(f"{source.value}:{name}")
        for src in ToolSource:
            key = f"{src.value}:{name}"
            if key in self._tools:
                return self._tools[key]
        return None

    def get(self, name: str, source: ToolSource | None = None) -> ToolDefinition | None:
        return self.get_definition(name, source=source)

    def list_by_source(self, source: ToolSource) -> list[ToolDefinition]:
        return [
            self._tools[f"{source.value}:{n}"]
            for n in self._by_source.get(source, [])
            if f"{source.value}:{n}" in self._tools
        ]

    def search(self, query: str) -> list[ToolDefinition]:
        q = query.lower()
        return [
            t for t in self._tools.values()
            if q in t.name.lower() or q in t.description.lower()
        ]

    def record_invocation(
        self,
        call: ToolCall,
        result: ToolResult,
    ) -> None:
        self._invocation_history.append({
            "call_id": str(call.id),
            "tool_name": call.tool_name,
            "success": result.success,
            "duration_ms": result.duration_ms,
            "timestamp": datetime.now(UTC).isoformat(),
        })

    def get_stats(self) -> dict[str, Any]:
        total = len(self._invocation_history)
        success_count = sum(1 for h in self._invocation_history if h["success"])
        tool_counts: dict[str, int] = defaultdict(int)
        for h in self._invocation_history:
            tool_counts[h["tool_name"]] += 1

        return {
            "total_calls": total,
            "success_rate": (success_count / total) if total > 0 else 0.0,
            "top_tools": dict(
                sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
            "registered_tools": len(self._tools),
            "by_source": {s.value: len(t) for s, t in self._by_source.items()},
        }
