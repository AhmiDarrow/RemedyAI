"""Regression tests for review fixes (stats, MCP, allowlist, path jail, etc.)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remedy.core.errors import SecurityError
from remedy.core.learning_loop import LearningLoop
from remedy.core.security import check_dangerous_command, safe_path
from remedy.execution.runtime import ToolRuntime
from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore
from remedy.models import (
    MemoryEntry,
    MemoryEntryType,
    Skill,
    SkillManifest,
    ToolCall,
    ToolDefinition,
    ToolSource,
)
from remedy.skills.tool_registry import ToolRegistry
from remedy.tools.mcp_client import MCPClient


class TestToolRegistryStats:
    def test_empty_stats_has_full_keys(self):
        registry = ToolRegistry()
        registry.register_builtin("echo", "Echo tool")
        stats = registry.get_stats()
        assert stats["total_calls"] == 0
        assert stats["registered_tools"] == 1
        assert stats["success_rate"] == 0.0
        assert "by_source" in stats


class TestToolRuntimeAllowlist:
    def test_rejects_non_allowlisted_sandbox_tool(self):
        runtime = ToolRuntime(sandbox=object())
        with pytest.raises(ValueError, match="not allowlisted"):
            runtime._build_command(ToolCall(tool_name="rm", arguments={"command": "-rf /"}))

    def test_allows_bash_exec(self):
        runtime = ToolRuntime()
        cmd = runtime._build_command(
            ToolCall(tool_name="bash_exec", arguments={"command": "echo hi"})
        )
        assert isinstance(cmd, list)
        assert len(cmd) >= 2


class TestMCPClientHelpers:
    def test_unwrap_jsonrpc_result(self):
        assert MCPClient._unwrap_jsonrpc({"result": {"ok": True}}) == {"ok": True}
        assert MCPClient._unwrap_jsonrpc({"result": 42}) == {"value": 42}
        assert "error" in MCPClient._unwrap_jsonrpc({"error": {"message": "nope"}})

    def test_resolve_tool_by_server_key(self):
        client = MCPClient()
        tool = ToolDefinition(
            name="search",
            description="Search",
            source=ToolSource.MCP,
            uri="mcp://myserver/search",
        )
        client._tools["mcp:myserver:search"] = tool
        call = ToolCall(tool_name="search", arguments={})
        resolved, server = client._resolve_tool(call)
        assert resolved is tool
        assert server == "myserver"

    def test_resolve_tool_with_server_hint(self):
        client = MCPClient()
        tool = ToolDefinition(name="search", description="Search", source=ToolSource.MCP)
        client._tools["mcp:alpha:search"] = tool
        call = ToolCall(tool_name="search", arguments={"_mcp_server": "alpha"})
        resolved, server = client._resolve_tool(call)
        assert resolved is tool
        assert server == "alpha"


class TestProposeRefinement:
    @pytest.mark.asyncio
    async def test_stores_real_memory_entry(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        await store.initialize()
        ll = LearningLoop(skills_dir=tmp_path / "skills", memory=store)
        skill = Skill(
            manifest=SkillManifest(name="demo-skill", description="Demo")
        )
        await ll.propose_refinement(skill, "needs better error handling")
        recent = await store.list_recent(limit=5)
        assert any(
            e.entry_type == MemoryEntryType.SKILL_LEARNED and "demo-skill" in e.title
            for e in recent
        )
        await store.close()


class TestListBySession:
    @pytest.mark.asyncio
    async def test_list_by_session(self, tmp_path):
        store = MemoryStore(tmp_path / "mem.db")
        await store.initialize()
        sid = "sess-review-1"
        await store.upsert(
            MemoryEntry(title="a", content="in session", session_id=sid)
        )
        await store.upsert(MemoryEntry(title="b", content="other"))
        rows = await store.list_by_session(sid)
        assert len(rows) == 1
        assert rows[0].title == "a"
        await store.close()


class TestSecurity:
    def test_blocks_dangerous_rm(self):
        assert check_dangerous_command(["rm", "-rf", "/"]) is not None

    def test_blocks_windows_system_tools(self):
        assert check_dangerous_command(["reg", "add", "HKLM\\x"]) is not None
        assert check_dangerous_command(["icacls", "C:\\Windows"]) is not None

    def test_does_not_flag_stderr_redirect_alone(self):
        warn = check_dangerous_command(["echo", "hi", "2>/dev/null"])
        assert warn is None or "Error output suppression" not in warn

    def test_safe_path_blocks_traversal(self, tmp_path):
        with pytest.raises(SecurityError):
            safe_path("..", base_dir=tmp_path)


class TestApiFilesJail:
    def test_files_endpoint_rejects_escape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REMEDY_FILES_ROOT", str(tmp_path))
        (tmp_path / "ok.txt").write_text("hi", encoding="utf-8")
        app = create_app()
        client = TestClient(app)
        bad = client.get("/api/files", params={"path": ".."})
        assert bad.status_code == 200
        assert bad.json().get("error")
        good = client.get("/api/files", params={"path": "."})
        assert good.status_code == 200
        assert "error" not in good.json() or not good.json().get("error")
