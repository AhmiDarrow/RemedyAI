"""MCP *client* bridge — the servers the owner lists in ``mcp_servers``.

Remedy has long been an MCP host; this is the other direction. Each entry in
``config.mcp_servers`` (``"godot=npx -y some-godot-mcp"``, a bare command, or
a ``{name, command, args, env, cwd}`` dict) becomes a stdio subprocess via
:class:`remedy.tools.mcp_client.MCPClient`, and every tool it advertises is
registered as ``mcp_<server>_<tool>`` through ``register_builtin_handler`` so
the usual builtin protection, approvals and the ``("mcp_", 300)`` timeout
prefix all apply.

Connection is lazy: ``register_mcp_bridge`` only parses and stores the specs
(it runs where no event loop may exist). ``ensure_connected`` does the actual
spawn + discovery once, the first time something asks — the API lifespan at
startup, or the ``mcp_status`` tool. Failures are remembered per server so
"is my godot MCP up?" has a real answer instead of a stack trace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.models import ToolCall
from remedy.tools.mcp_client import MCPClient

logger = logging.getLogger(__name__)

_DESC_MAX = 400
_NAME_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class McpServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


@dataclass
class _ServerState:
    spec: McpServerSpec
    attempted: bool = False
    connected: bool = False
    tools: list[str] = field(default_factory=list)
    error: str | None = None


class McpBridge:
    def __init__(self, specs: list[McpServerSpec]) -> None:
        self.specs = specs
        self.client: MCPClient | None = None
        self.states: dict[str, _ServerState] = {s.name: _ServerState(s) for s in specs}
        self._lock: asyncio.Lock | None = None


# -- entry parsing ------------------------------------------------------------


def _safe_name(raw: str) -> str:
    return _NAME_RE.sub("_", str(raw).strip().lower()).strip("_")


def _split_command(text: str) -> list[str]:
    """Split a command line; keep Windows backslashes intact.

    POSIX shlex treats ``\\`` as escape (``C:\\tools\\Pixel-Lab`` becomes
    ``C:<tab>oolsPixel-Lab``). MCP entries are often Windows paths even when
    Remedy itself is running on Linux, so always split with posix=False.
    """
    try:
        return [t.strip("\"'") for t in shlex.split(text, posix=False)]
    except ValueError:
        return text.split()


def _spec_from_string(raw: str) -> McpServerSpec | None:
    text = raw.strip()
    if not text:
        return None
    name = ""
    head, sep, tail = text.partition("=")
    # "name=command args…" — the left side must look like a label, not a path.
    if sep and head.strip() and not any(c in head for c in " \\/:."):
        if not tail.strip():
            return None
        name = head.strip()
        text = tail.strip()
    parts = _split_command(text)
    if not parts:
        return None
    command, args = parts[0], parts[1:]
    if not name:
        name = os.path.splitext(os.path.basename(command.replace("\\", "/")))[0]
    safe = _safe_name(name)
    if not safe:
        return None
    return McpServerSpec(name=safe, command=command, args=args)


def _spec_from_dict(raw: dict[str, Any]) -> McpServerSpec | None:
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    command = command.strip()
    args_raw = raw.get("args") or []
    if isinstance(args_raw, str):
        args = _split_command(args_raw)
    elif isinstance(args_raw, list):
        args = [str(a) for a in args_raw]
    else:
        return None
    if not args and " " in command:
        parts = _split_command(command)
        command, args = parts[0], parts[1:]
    name = raw.get("name") or os.path.splitext(os.path.basename(command.replace("\\", "/")))[0]
    safe = _safe_name(str(name))
    if not safe:
        return None
    env_raw = raw.get("env")
    env = (
        {str(k): str(v) for k, v in env_raw.items()}
        if isinstance(env_raw, dict) and env_raw
        else None
    )
    cwd_raw = raw.get("cwd")
    cwd = str(cwd_raw).strip() if isinstance(cwd_raw, str) and cwd_raw.strip() else None
    return McpServerSpec(name=safe, command=command, args=args, env=env, cwd=cwd)


def parse_mcp_server_entries(raw: Any) -> list[McpServerSpec]:
    """Turn the ``mcp_servers`` setting into specs; bad entries are skipped."""
    if raw is None:
        return []
    if isinstance(raw, str | dict):
        raw = [raw]
    if not isinstance(raw, list | tuple):
        logger.debug("mcp_servers: unsupported type %s", type(raw).__name__)
        return []
    specs: list[McpServerSpec] = []
    seen: set[str] = set()
    for entry in raw:
        spec: McpServerSpec | None = None
        if isinstance(entry, str):
            spec = _spec_from_string(entry)
        elif isinstance(entry, dict):
            spec = _spec_from_dict(entry)
        if spec is None:
            logger.debug("mcp_servers: skipping invalid entry %r", entry)
            continue
        if spec.name in seen:
            logger.debug("mcp_servers: duplicate server name %s skipped", spec.name)
            continue
        seen.add(spec.name)
        specs.append(spec)
    return specs


# -- text handling -------------------------------------------------------------


def _sanitise_description(server: str, text: Any) -> str:
    """Third-party text: one line, bounded, and labelled with its origin."""
    flat = " ".join(str(text or "").split())
    if len(flat) > _DESC_MAX:
        flat = flat[: _DESC_MAX - 1].rstrip() + "…"
    return f"[MCP {server}] {flat}" if flat else f"[MCP {server}] {server} tool"


def _result_text(result: Any) -> str:
    """Join text parts of an MCP content list; anything else is JSON."""
    data = getattr(result, "data", None) or {}
    raw = data.get("raw") if isinstance(data, dict) else None
    content = raw.get("content") if isinstance(raw, dict) else None
    if isinstance(content, list):
        texts: list[str] = []
        other: list[Any] = []
        for part in content:
            if isinstance(part, dict) and part.get("type", "text") == "text" and "text" in part:
                texts.append(str(part["text"]))
            else:
                other.append(part)
        if texts and not other:
            return "\n".join(texts)
        if texts or other:
            tail = json.dumps(other, ensure_ascii=False, default=str) if other else ""
            return "\n".join([*texts, tail]).strip()
        return ""
    if isinstance(data, dict) and isinstance(data.get("result"), str):
        return data["result"]
    return json.dumps(data, ensure_ascii=False, default=str)


# -- registration ----------------------------------------------------------------


def _bridge_of(runtime: Any) -> McpBridge | None:
    b = getattr(runtime, "_mcp_bridge", None)
    return b if isinstance(b, McpBridge) else None


def _make_handler(bridge: McpBridge, server: str, tool: str):
    async def _call(**kwargs: Any) -> str:
        client = bridge.client
        if client is None:
            return format_tool_error(
                f"MCP server {server} is not connected", code="MCP_OFFLINE", tool_name=tool
            )
        arguments = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        arguments["_mcp_server"] = server
        try:
            result = await client.call_tool(ToolCall(tool_name=tool, arguments=arguments))
        except Exception as e:
            return format_tool_error(str(e), code="MCP_ERROR", tool_name=tool)
        if not getattr(result, "success", False):
            err = getattr(result, "error", None) or "MCP tool error"
            return format_tool_error(str(err), code="MCP_ERROR", tool_name=tool)
        return _result_text(result)

    return _call


def _register_tools(runtime: Any, bridge: McpBridge, server: str, tools: list[Any]) -> list[str]:
    registry = runtime.tool_registry
    names: list[str] = []
    for t in tools:
        raw_name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
        if not raw_name:
            continue
        safe_tool = _safe_name(str(raw_name))
        if not safe_tool:
            continue
        full = f"mcp_{server}_{safe_tool}"
        desc = getattr(t, "description", None)
        if desc is None and isinstance(t, dict):
            desc = t.get("description")
        params = getattr(t, "parameters", None)
        if params is None and isinstance(t, dict):
            params = t.get("inputSchema") or t.get("parameters")
        if not isinstance(params, dict) or not params:
            params = {"type": "object", "properties": {}}
        registry.register_builtin_handler(
            full,
            _sanitise_description(server, desc),
            _make_handler(bridge, server, str(raw_name)),
            params,
        )
        names.append(full)
    return names


async def _connect_one(runtime: Any, bridge: McpBridge, state: _ServerState) -> None:
    spec = state.spec
    state.attempted = True
    client = bridge.client
    assert client is not None
    try:
        ok = await client.connect(
            spec.name, spec.command, spec.args, env=spec.env, cwd=spec.cwd
        )
        if not ok:
            raise ConnectionError(f"handshake failed for '{spec.command}'")
        tools = await client.discover_tools(spec.name)
        state.tools = _register_tools(runtime, bridge, spec.name, list(tools))
        state.connected = True
        state.error = None
        logger.info("MCP bridge: %s up with %d tools", spec.name, len(state.tools))
    except Exception as e:
        state.connected = False
        state.error = str(e) or e.__class__.__name__
        logger.warning("MCP bridge: %s failed — %s", spec.name, state.error)


async def ensure_connected(runtime: Any) -> McpBridge | None:
    """Connect every configured server once (idempotent, safe to call often)."""
    bridge = _bridge_of(runtime)
    if bridge is None or not bridge.specs:
        return bridge
    if bridge._lock is None:
        bridge._lock = asyncio.Lock()
    async with bridge._lock:
        pending = [s for s in bridge.states.values() if not s.attempted]
        if not pending:
            return bridge
        if bridge.client is None:
            bridge.client = MCPClient()
        for state in pending:
            await _connect_one(runtime, bridge, state)
    return bridge


async def shutdown_mcp_bridge(runtime: Any) -> None:
    """Kill spawned MCP servers; harmless when nothing was ever connected."""
    bridge = _bridge_of(runtime)
    if bridge is None or bridge.client is None:
        return
    client, bridge.client = bridge.client, None
    for state in bridge.states.values():
        # Back to "not connected yet": a later ensure_connected may respawn.
        state.connected = False
        state.attempted = False
        state.error = None
    try:
        await client.disconnect_all()
    except Exception:
        logger.debug("MCP bridge: disconnect_all failed", exc_info=True)


def mcp_status_text(bridge: McpBridge | None) -> str:
    if bridge is None or not bridge.specs:
        return "No MCP servers configured (settings: mcp_servers)."
    lines = [f"MCP servers ({len(bridge.specs)}):"]
    for state in bridge.states.values():
        spec = state.spec
        cmd = " ".join([spec.command, *spec.args])
        if state.connected:
            status = f"connected, {len(state.tools)} tools"
        elif state.attempted:
            status = f"failed — {state.error or 'unknown error'}"
        else:
            status = "not connected yet"
        lines.append(f"- {spec.name}: {status} ({cmd})")
        if state.tools:
            lines.append("  tools: " + ", ".join(state.tools))
    return "\n".join(lines)


def register_mcp_bridge(runtime: Any) -> None:
    """Parse ``config.mcp_servers`` and expose ``mcp_status``; connect later."""
    cfg = getattr(runtime, "config", None)
    raw = cfg.get("mcp_servers") if isinstance(cfg, dict) else getattr(cfg, "mcp_servers", None)
    bridge = McpBridge(parse_mcp_server_entries(raw))
    runtime._mcp_bridge = bridge

    async def mcp_status() -> str:
        try:
            await ensure_connected(runtime)
        except Exception as e:
            return format_tool_error(str(e), code="MCP_ERROR", tool_name="mcp_status")
        return mcp_status_text(_bridge_of(runtime))

    runtime.tool_registry.register_builtin_handler(
        "mcp_status",
        "Which external MCP servers are configured (settings: mcp_servers), "
        "whether each is connected, how many tools it exposes and the last "
        "error if it failed. Use when asked whether an MCP server is up.",
        mcp_status,
        {"type": "object", "properties": {}},
    )
