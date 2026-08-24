"""Studio scratch pad — server store the agent can read."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.core.scratchpad_store import read_scratch, scratch_id, write_scratch
from remedy.interfaces.api import create_app


def test_scratch_roundtrip(tmp_path: Path):
    write_scratch("sess-1", "hello notes", home=tmp_path)
    assert read_scratch("sess-1", home=tmp_path) == "hello notes"
    write_scratch("sess-1", " more", home=tmp_path, append=True)
    assert read_scratch("sess-1", home=tmp_path) == "hello notes more"
    assert scratch_id("../evil") == "_evil"
    assert scratch_id("") == "_global"


def test_scratch_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    app = create_app()
    client = TestClient(app)
    empty = client.get("/api/scratch", params={"session_id": "s1"})
    assert empty.status_code == 200
    assert empty.json()["text"] == ""
    put = client.put("/api/scratch", json={"session_id": "s1", "text": "rail notes"})
    assert put.status_code == 200
    assert put.json()["text"] == "rail notes"
    got = client.get("/api/scratch", params={"session_id": "s1"})
    assert got.json()["text"] == "rail notes"


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
