"""Phone tools: conversational setup, no wizard, no dialling."""

from __future__ import annotations

from pathlib import Path

import pytest


class _Reg:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.handlers[name] = handler


class _Rt:
    def __init__(self, home: Path) -> None:
        self.tool_registry = _Reg()
        self.config = {"home_dir": str(home)}


@pytest.fixture
def tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.core.agent_telephony_tools import register_telephony_tools

    rt = _Rt(tmp_path)
    register_telephony_tools(rt)
    return rt


@pytest.mark.asyncio
async def test_phone_status_is_plain(tools):
    text = await tools.tool_registry.handlers["phone_status"]()
    assert "Calling a real number is not on this computer yet" in text


@pytest.mark.asyncio
async def test_agree_and_choose(tools, tmp_path: Path):
    await tools.tool_registry.handlers["phone_agree_terms"]()
    from remedy.telephony import consent

    assert consent.read(tmp_path).current is True
    msg = await tools.tool_registry.handlers["phone_choose_line"](name="sip")
    assert "sip" in msg.lower()
    from remedy.telephony.options import chosen

    assert chosen(tmp_path) == "sip"


@pytest.mark.asyncio
async def test_unknown_line_is_plain(tools):
    msg = await tools.tool_registry.handlers["phone_choose_line"](name="tin-cans")
    assert "tin-cans" in msg
    assert "I do not have a line" in msg
