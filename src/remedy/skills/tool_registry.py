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

# Unscoped lookup prefers first-party tools so MCP/skill same-name registrations
# cannot shadow builtins (policy, approvals, jail rely on the real definition).
_SOURCE_TRUST_ORDER: tuple[ToolSource, ...] = (
    ToolSource.BUILTIN,
    ToolSource.PLUGIN,
    ToolSource.SKILL,
    ToolSource.MCP,
)


def _filter_handler_arguments(
    handler: Callable[..., Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Drop unknown kwargs so LLM extras do not TypeError the handler.

    Models often pass schema-adjacent keys (``description``, ``target``,
    ``timeout``) that are not on the Python signature. Handlers that declare
    ``**kwargs`` keep the full dict. Required missing params still raise at call.
    """
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return arguments
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return arguments
    allowed = {
        name
        for name, p in params.items()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    if not allowed:
        return {}
    return {k: v for k, v in arguments.items() if k in allowed}


class ToolRegistry:
    """Catalog of all available tools across sources (MCP, skills, builtins)."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._by_source: dict[ToolSource, list[str]] = defaultdict(list)
        self._invocation_history: list[dict[str, Any]] = []
        self._mcp_servers: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable] = {}
        # Names whose handlers are owned by builtins — MCP must not clobber.
        self._builtin_handlers: set[str] = set()
        # OpenAI tools payload cache — rebuild only when registry mutates
        self._schema_gen: int = 0
        self._openai_payload_cache: list[dict[str, Any]] | None = None
        self._openai_payload_gen: int = -1

    @property
    def tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def schema_generation(self) -> int:
        """Bumps on every register — used to cache OpenAI tool schemas."""
        return int(self._schema_gen)

    def register(self, tool: ToolDefinition) -> ToolDefinition:
        key = f"{tool.source.value}:{tool.name}"
        # Avoid residual duplicate names in _by_source on re-register
        names = self._by_source[tool.source]
        if tool.name not in names:
            names.append(tool.name)
        self._tools[key] = tool
        self._schema_gen += 1
        self._openai_payload_cache = None
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
        """Register an async callable handler for a tool.

        Builtin-owned handler names cannot be overwritten (MCP residual trust).
        """
        if name in self._builtin_handlers and self._handlers.get(name) is not handler:
            return
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
        self._builtin_handlers.add(name)
        return self.register_builtin(name, description, parameters)

    async def execute(self, tool_name: str, **arguments: Any) -> Any:
        """Invoke a tool by id with its registered handler.

        Parameter is ``tool_name`` (not ``name``) so handlers like
        ``skill_activate(skill=…, name=…)`` can accept a ``name`` alias in
        ``**arguments`` without ``TypeError: multiple values for argument 'name'``.
        """
        handler = self._handlers.get(tool_name)
        if handler is None:
            # Back-compat if anyone still passes the first arg positionally as name=
            raise ValueError(f"No handler registered for tool: {tool_name}")
        # Never forward a conflicting kwargs key that equals the tool id itself
        # when the model mirrored the tool name into arguments.
        if "name" in arguments and arguments.get("name") == tool_name:
            arguments = {k: v for k, v in arguments.items() if k != "name"}
        # LLM tool-calls routinely include extra keys; strip unknowns before invoke
        # so handlers without **kwargs do not raise TypeError mid-turn.
        arguments = _filter_handler_arguments(handler, arguments)
        if inspect.iscoroutinefunction(handler):
            return await handler(**arguments)
        return handler(**arguments)

    def register_from_mcp(
        self,
        server_name: str,
        tool_def: dict[str, Any],
    ) -> ToolDefinition:
        raw_name = str(tool_def.get("name", "unknown") or "unknown")
        # Namespace colliding names so MCP cannot replace builtin catalog identity
        # when both appear; unscoped get() still prefers builtin via trust order.
        tool = ToolDefinition(
            name=raw_name,
            description=tool_def.get("description", ""),
            source=ToolSource.MCP,
            parameters=tool_def.get("parameters", tool_def.get("inputSchema", {})),
            uri=f"mcp://{server_name}/{raw_name}",
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

    def purge_mcp_server(self, server_name: str) -> int:
        """Drop residual MCP tool definitions for a disconnected server."""
        uri_prefix = f"mcp://{server_name}/"
        drop = [
            k
            for k, t in self._tools.items()
            if t.source == ToolSource.MCP
            and (getattr(t, "uri", None) or "").startswith(uri_prefix)
        ]
        for k in drop:
            self._tools.pop(k, None)
        # Rebuild residual name list from tools that remain (other MCP servers)
        self._by_source[ToolSource.MCP] = [
            t.name for t in self._tools.values() if t.source == ToolSource.MCP
        ]
        if server_name in self._mcp_servers:
            del self._mcp_servers[server_name]
        if drop:
            self._schema_gen += 1
            self._openai_payload_cache = None
        return len(drop)

    def get_definition(self, name: str, source: ToolSource | None = None) -> ToolDefinition | None:
        if source:
            return self._tools.get(f"{source.value}:{name}")
        # Trust order: builtin wins over MCP/skill same-name residual registrations.
        for src in _SOURCE_TRUST_ORDER:
            key = f"{src.value}:{name}"
            if key in self._tools:
                return self._tools[key]
        return None

    def get(self, name: str, source: ToolSource | None = None) -> ToolDefinition | None:
        return self.get_definition(name, source=source)

    def list_by_source(self, source: ToolSource) -> list[ToolDefinition]:
        # Dedup residual name list while preserving first-seen order
        seen: set[str] = set()
        out: list[ToolDefinition] = []
        for n in self._by_source.get(source, []):
            if n in seen:
                continue
            seen.add(n)
            t = self._tools.get(f"{source.value}:{n}")
            if t is not None:
                out.append(t)
        return out

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
