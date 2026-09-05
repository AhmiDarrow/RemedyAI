"""Studio scratch pad — server store the agent can read."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.scratchpad_store import read_scratch, scratch_id, write_scratch


def test_scratch_roundtrip(tmp_path: Path):
    write_scratch("sess-1", "hello notes", home=tmp_path)
    assert read_scratch("sess-1", home=tmp_path) == "hello notes"
    write_scratch("sess-1", " more", home=tmp_path, append=True)
    assert read_scratch("sess-1", home=tmp_path) == "hello notes more"
    assert scratch_id("../evil") == "_evil"
    assert scratch_id("") == "_global"


@pytest.mark.asyncio
async def test_scratchpad_tool_reads_what_it_wrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.core.agent_settings_tools import register_settings_tools
    from remedy.core.app_control import app_control_bus
    from remedy.skills.tool_registry import ToolRegistry

    class RT:
        def __init__(self) -> None:
            self.tool_registry = ToolRegistry()
            self._session_id = "tool-sess"

    rt = RT()
    register_settings_tools(rt)
    app_control_bus().clear()
    out = await rt.tool_registry.execute("scratchpad", action="write", text="seen on rail")
    assert "ok" in out.lower()
    body = await rt.tool_registry.execute("scratchpad", action="read")
    assert "seen on rail" in body
