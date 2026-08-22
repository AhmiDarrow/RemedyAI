"""MCP client bridge — user-listed servers become ``mcp_<server>_<tool>`` tools.

No real server is ever spawned here: ``MCPClient`` is replaced by a fake that
answers ``connect`` / ``discover_tools`` / ``call_tool`` from memory. The
subject is the glue — parsing the setting, naming and sanitising what a
third-party server advertises, surfacing failures in ``mcp_status``, and
killing the children on shutdown.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from remedy.core import agent_mcp_bridge as bridge_mod
from remedy.core.agent_mcp_bridge import (
    ensure_connected,
    parse_mcp_server_entries,
    register_mcp_bridge,
    shutdown_mcp_bridge,
)
from remedy.core.tool_timeouts import tool_timeout_for
from remedy.models import ToolDefinition, ToolResult, ToolSource
from remedy.skills.tool_registry import ToolRegistry


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- parsing the setting ------------------------------------------------------


def test_named_string_entry():
    (spec,) = parse_mcp_server_entries(["godot=npx -y some-godot-mcp --port 7"])
    assert spec.name == "godot"
    assert spec.command == "npx"
    assert spec.args == ["-y", "some-godot-mcp", "--port", "7"]


def test_plain_command_entry_names_after_executable():
    (spec,) = parse_mcp_server_entries([r"C:\tools\Pixel-Lab.exe --stdio"])
    assert spec.name == "pixel_lab"
    assert spec.command == r"C:\tools\Pixel-Lab.exe"
    assert spec.args == ["--stdio"]


def test_dict_entry_with_env_and_cwd():
    (spec,) = parse_mcp_server_entries([
        {
            "name": "Godot MCP",
            "command": "node",
            "args": ["server.js"],
            "env": {"GODOT": "/g"},
            "cwd": "/proj",
        }
    ])
    assert spec.name == "godot_mcp"
    assert spec.args == ["server.js"]
    assert spec.env == {"GODOT": "/g"}
    assert spec.cwd == "/proj"


def test_invalid_entries_are_skipped():
    specs = parse_mcp_server_entries([
        "", "   ", 42, None, {"name": "x"}, {"command": ""}, "name=",
        "ok=python -m srv",
    ])
    assert [s.name for s in specs] == ["ok"]
    assert parse_mcp_server_entries(None) == []
    assert parse_mcp_server_entries("solo=python srv.py")[0].name == "solo"


def test_duplicate_names_keep_first():
    specs = parse_mcp_server_entries(["a=python one.py", "a=python two.py"])
    assert len(specs) == 1
    assert specs[0].args == ["one.py"]


# --- a fake client --------------------------------------------------------------


class FakeMCPClient:
    instances: list[FakeMCPClient] = []
    fail: set[str] = set()

    def __init__(self) -> None:
        self.connected: list[tuple[str, str, list[str], dict | None, str | None]] = []
        self.calls: list[Any] = []
        self.disconnected = False
        FakeMCPClient.instances.append(self)

    async def connect(self, server_name, command, args=None, env=None, cwd=None):
        self.connected.append((server_name, command, list(args or []), env, cwd))
        return server_name not in FakeMCPClient.fail

    async def discover_tools(self, server_name):
        return [
            ToolDefinition(
                name="get_scene_tree",
                description="Returns the\nscene tree\n" + "x" * 600,
                source=ToolSource.MCP,
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                uri=f"mcp://{server_name}/get_scene_tree",
            ),
            ToolDefinition(
                name="run-project",
                description="Run it",
                source=ToolSource.MCP,
                parameters={},
                uri=f"mcp://{server_name}/run-project",
            ),
        ]

    async def call_tool(self, tool_call):
        self.calls.append(tool_call)
        if tool_call.tool_name == "run-project":
            return ToolResult(call_id=tool_call.id, success=False, error="no project")
        raw = {
            "content": [
                {"type": "text", "text": "Node2D"},
                {"type": "text", "text": " Player"},
                {"type": "image", "data": "abc"},
            ]
        }
        return ToolResult(
            call_id=tool_call.id, success=True, data={"result": "Node2D", "raw": raw}
        )

    async def disconnect_all(self):
        self.disconnected = True


@pytest.fixture()
def fake_client(monkeypatch):
    FakeMCPClient.instances = []
    FakeMCPClient.fail = set()
    monkeypatch.setattr(bridge_mod, "MCPClient", FakeMCPClient)
    return FakeMCPClient


def make_runtime(mcp_servers):
    return SimpleNamespace(
        config=SimpleNamespace(mcp_servers=mcp_servers),
        tool_registry=ToolRegistry(),
    )


# --- registration -----------------------------------------------------------------


def test_register_is_lazy_and_adds_status_tool(fake_client):
    rt = make_runtime(["godot=npx -y godot-mcp"])
    register_mcp_bridge(rt)
    assert rt.tool_registry.get("mcp_status") is not None
    assert fake_client.instances == []  # nothing spawned at registration
    assert "not connected yet" in bridge_mod.mcp_status_text(rt._mcp_bridge)


def test_tools_registered_with_namespaced_names(fake_client):
    rt = make_runtime(["godot=npx -y godot-mcp"])
    register_mcp_bridge(rt)
    run(ensure_connected(rt))
    run(ensure_connected(rt))  # idempotent — one connect

    (client,) = fake_client.instances
    assert client.connected == [("godot", "npx", ["-y", "godot-mcp"], None, None)]

    tool = rt.tool_registry.get("mcp_godot_get_scene_tree")
    assert tool is not None
    assert tool.source == ToolSource.BUILTIN
    assert tool.description.startswith("[MCP godot] Returns the scene tree x")
    assert "\n" not in tool.description
    assert len(tool.description) <= len("[MCP godot] ") + 400
    assert tool.parameters["properties"]["path"]["type"] == "string"
    assert rt.tool_registry.get("mcp_godot_run_project") is not None


def test_dict_entry_passes_env_and_cwd(fake_client):
    rt = make_runtime([
        {"name": "godot", "command": "node", "args": ["s.js"], "env": {"A": "1"}, "cwd": "/p"}
    ])
    register_mcp_bridge(rt)
    run(ensure_connected(rt))
    assert fake_client.instances[0].connected == [("godot", "node", ["s.js"], {"A": "1"}, "/p")]


def test_handler_joins_text_parts_and_routes_to_server(fake_client):
    rt = make_runtime(["godot=npx -y godot-mcp"])
    register_mcp_bridge(rt)
    run(ensure_connected(rt))
    out = run(rt.tool_registry.execute("mcp_godot_get_scene_tree", path="/root"))
    assert out.startswith("Node2D\n Player")
    assert '"image"' in out  # non-text part dumped as JSON
    call = fake_client.instances[0].calls[-1]
    assert call.tool_name == "get_scene_tree"
    assert call.arguments == {"path": "/root", "_mcp_server": "godot"}


def test_handler_surfaces_tool_error(fake_client):
    rt = make_runtime(["godot=npx -y godot-mcp"])
    register_mcp_bridge(rt)
    run(ensure_connected(rt))
    out = run(rt.tool_registry.execute("mcp_godot_run_project"))
    assert "MCP_ERROR" in out and "no project" in out


def test_timeout_prefix_covers_bridge_tools():
    assert tool_timeout_for("mcp_godot_x") == 300


def test_failure_shows_in_status(fake_client):
    fake_client.fail = {"broken"}
    rt = make_runtime(["broken=python nope.py", "godot=npx -y godot-mcp"])
    register_mcp_bridge(rt)
    status = run(rt.tool_registry.execute("mcp_status"))
    assert "broken: failed" in status
    assert "handshake failed" in status
    assert "godot: connected, 2 tools" in status
    assert "mcp_godot_get_scene_tree" in status
    assert rt.tool_registry.get("mcp_broken_get_scene_tree") is None


def test_status_without_servers(fake_client):
    rt = SimpleNamespace(config=SimpleNamespace(), tool_registry=ToolRegistry())
    register_mcp_bridge(rt)
    assert "No MCP servers configured" in run(rt.tool_registry.execute("mcp_status"))
    assert fake_client.instances == []


def test_shutdown_disconnects(fake_client):
    rt = make_runtime(["godot=npx -y godot-mcp"])
    register_mcp_bridge(rt)
    run(shutdown_mcp_bridge(rt))  # nothing connected yet — no-op
    run(ensure_connected(rt))
    (client,) = fake_client.instances
    run(shutdown_mcp_bridge(rt))
    assert client.disconnected is True
    assert rt._mcp_bridge.client is None
    assert "not connected" in bridge_mod.mcp_status_text(rt._mcp_bridge)


def test_agent_config_accepts_dict_entries():
    from remedy.models import AgentConfig

    cfg = AgentConfig(mcp_servers=["a=python x.py", {"name": "b", "command": "node"}])
    assert len(cfg.mcp_servers) == 2
