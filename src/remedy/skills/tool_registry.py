"""Tool registry and MCP client integration.

Manages the catalog of available tools across MCP servers, skills,
and builtins. Tracks invocation history and tool metadata.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from remedy.models import ToolCall, ToolDefinition, ToolResult, ToolSource


class ToolRegistry:
    """Catalog of all available tools across sources (MCP, skills, builtins)."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._by_source: dict[ToolSource, list[str]] = defaultdict(list)
        self._invocation_history: list[dict[str, Any]] = []
        self._mcp_servers: dict[str, dict[str, Any]] = {}

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
        parameters: Optional[dict[str, Any]] = None,
    ) -> ToolDefinition:
        return self.register(ToolDefinition(
            name=name,
            description=description,
            source=ToolSource.BUILTIN,
            parameters=parameters or {},
        ))

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

    def get(self, name: str, source: Optional[ToolSource] = None) -> Optional[ToolDefinition]:
        if source:
            return self._tools.get(f"{source.value}:{name}")
        for src in ToolSource:
            key = f"{src.value}:{name}"
            if key in self._tools:
                return self._tools[key]
        return None

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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def get_stats(self) -> dict[str, Any]:
        total = len(self._invocation_history)
        if total == 0:
            return {"total_calls": 0}

        success_count = sum(1 for h in self._invocation_history if h["success"])
        tool_counts: dict[str, int] = defaultdict(int)
        for h in self._invocation_history:
            tool_counts[h["tool_name"]] += 1

        return {
            "total_calls": total,
            "success_rate": success_count / total if total > 0 else 0,
            "top_tools": dict(
                sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ),
            "registered_tools": len(self._tools),
            "by_source": {s.value: len(t) for s, t in self._by_source.items()},
        }
