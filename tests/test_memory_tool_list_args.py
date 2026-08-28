"""memory_search / memory_save join list query/content instead of .strip() crash."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from remedy.core.agent_memory_tools import register_memory_tools


class _Reg:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.handlers[name] = handler


class _Mem:
    def __init__(self) -> None:
        self.saved = []

    async def upsert(self, entry):
        self.saved.append(entry)
        return entry

    async def get_or_create_profile(self):
        return SimpleNamespace(facts=[])

    async def save_user_profile(self, _profile):
        return None


class _RT:
    def __init__(self, home: str, memory) -> None:
        self.tool_registry = _Reg()
        self.config = SimpleNamespace(home_dir=home, project_path="")
        self.memory = memory
        self._session_id = "sess-mem-list"
        self._session_brief = None
        self._project_path = ""


@pytest.fixture
def mem_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    rt = _RT(str(tmp_path), _Mem())
    register_memory_tools(rt)
    return rt


@pytest.mark.asyncio
async def test_memory_search_joins_list_query(mem_tools):
    """memory_search(query=["foo"]) must not .strip() a list."""
    out = await mem_tools.tool_registry.handlers["memory_search"](query=["foo"])
    assert isinstance(out, str)
    assert "attribute 'strip'" not in out
    assert "['foo']" not in out


@pytest.mark.asyncio
async def test_memory_search_empty_list_refuses(mem_tools):
    out = await mem_tools.tool_registry.handlers["memory_search"](query=[])
    assert "Provide a search query" in out


@pytest.mark.asyncio
async def test_memory_save_joins_list_content(mem_tools):
    """memory_save(content=["bar"]) must not .strip() a list."""
    out = await mem_tools.tool_registry.handlers["memory_save"](content=["bar"])
    assert isinstance(out, str)
    assert "attribute 'strip'" not in out
    saved = mem_tools.memory.saved
    assert saved, out
    assert saved[0].content == "bar"
    assert "['bar']" not in out


@pytest.mark.asyncio
async def test_memory_save_empty_list_refuses(mem_tools):
    out = await mem_tools.tool_registry.handlers["memory_save"](content=[])
    assert "Nothing to save" in out
    assert mem_tools.memory.saved == []
