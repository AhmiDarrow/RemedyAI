"""Hive daughters report to Remedy, never the owner sidebar."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.hive.policy import (
    filter_daughter_tools,
    hive_depth,
    is_mother_only_tool,
    reset_hive_depth,
    set_hive_depth,
)
from remedy.core.hive.runner import run_forager, set_pulse_impl
from remedy.core.hive.store import HiveStore
from remedy.core.hive.types import (
    PACKET_CHAR_CAP,
    ReturnPacket,
    is_hive_session_id,
    packet_from_outcome,
)


@pytest.fixture
def hive_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


def test_packet_caps_lists_and_chars():
    pkt = ReturnPacket.from_dict(
        {
            "goal": "g",
            "done": True,
            "outcome": "x" * 5000,
            "evidence": [f"e{i}" for i in range(20)],
            "artifacts": [f"a{i}" for i in range(20)],
            "blockers": ["b"] * 12,
        }
    )
    assert len(pkt.evidence) <= 8
    assert len(pkt.as_mother_text()) <= PACKET_CHAR_CAP
    assert len(pkt.to_dict()["artifacts"]) <= 8


def test_packet_from_json_blob():
    raw = '{"goal":"review","done":true,"outcome":"auth is fine","confidence":0.9}'
    pkt = packet_from_outcome("review", raw)
    assert pkt.done is True
    assert "fine" in pkt.outcome


def test_mother_only_tools_stripped():
    tools = [
        {"type": "function", "function": {"name": "file_read"}},
        {"type": "function", "function": {"name": "hive_spawn"}},
        {"type": "function", "function": {"name": "update_settings"}},
        {"type": "function", "function": {"name": "mail_send"}},
        {"type": "function", "function": {"name": "list_dir"}},
    ]
    out = filter_daughter_tools(tools)
    names = [t["function"]["name"] for t in out]
    assert names == ["file_read", "list_dir"]
    assert is_mother_only_tool("hive_collect")
    assert not is_mother_only_tool("file_read")


def test_store_roundtrip(hive_home: Path):
    store = HiveStore(hive_home)
    d = store.hire("review auth.py", parent_session_id="owner-1")
    assert is_hive_session_id(d.session_id)
    got = store.get(d.id)
    assert got is not None
    assert got.goal == "review auth.py"
    assert got.parent_session_id == "owner-1"
    assert (hive_home / "hive" / "episodes" / f"{d.id}.json").is_file()


@pytest.mark.asyncio
async def test_hive_session_id_not_in_owner_list(hive_home: Path):
    from remedy.memory.store import MemoryStore
    from remedy.models import ChatSession

    mem = MemoryStore(hive_home / "memory.db")
    await mem.initialize()
    hidden = ChatSession(id="hive_should_not_list", title="nope")
    visible = ChatSession(id="owner-chat", title="ok")
    await mem.create_chat_session(hidden)
    await mem.create_chat_session(visible)
    listed = await mem.list_chat_sessions(limit=20)
    ids = {s.id for s in listed}
    assert "owner-chat" in ids
    assert "hive_should_not_list" not in ids


@pytest.mark.asyncio
async def test_forager_reports_packet_not_transcript(hive_home: Path):
    async def pulse(_rt, daughter):
        return ReturnPacket(
            goal=daughter.goal,
            done=True,
            outcome="looked at auth.py",
            evidence=["src/auth.py"],
            confidence=0.8,
        )

    set_pulse_impl(pulse)
    try:
        store = HiveStore(hive_home)
        d = store.hire("review auth.py")
        rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(hive_home)))
        pkt = await run_forager(rt, d)
        assert pkt.done is True
        fresh = store.get(d.id)
        assert fresh is not None
        assert fresh.status == "reported"
        assert "looked at auth.py" in (fresh.packet or {}).get("outcome", "")
        dumped = (hive_home / "hive" / "episodes" / f"{d.id}.json").read_text(
            encoding="utf-8"
        )
        assert "tool_calls" not in dumped
    finally:
        set_pulse_impl(None)


@pytest.mark.asyncio
async def test_stop_cancels_forager(hive_home: Path):
    started = asyncio.Event()

    async def pulse(_rt, daughter):
        from remedy.core.turn_context import current_abort_event, is_turn_aborted

        started.set()
        ev = current_abort_event()
        if ev is not None:
            await ev.wait()
        else:
            await asyncio.sleep(30)
        if is_turn_aborted():
            return packet_from_outcome(daughter.goal, "", aborted=True)
        return ReturnPacket(goal=daughter.goal, done=True, outcome="should not finish")

    set_pulse_impl(pulse)
    try:
        from remedy.core.turn_context import abort_session

        store = HiveStore(hive_home)
        d = store.hire("slow")
        rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(hive_home)))
        task = asyncio.create_task(run_forager(rt, d))
        await started.wait()
        abort_session(d.session_id)
        pkt = await task
        assert pkt.done is False
        assert any("cancelled" in b.lower() for b in pkt.blockers)
    finally:
        set_pulse_impl(None)


@pytest.mark.asyncio
async def test_daughter_cannot_hire(hive_home: Path):
    from remedy.core.agent_hive_tools import register_hive_tools
    from remedy.skills.tool_registry import ToolRegistry

    rt = SimpleNamespace(
        tool_registry=ToolRegistry(),
        config=SimpleNamespace(home_dir=str(hive_home)),
        _session_id="owner",
    )
    rt.effective_project_path = lambda: hive_home  # type: ignore[method-assign]
    register_hive_tools(rt)
    tok = set_hive_depth(1)
    try:
        out = await rt.tool_registry.execute("hive_spawn", goal="nope")
        assert "HIVE_DEPTH" in out
    finally:
        reset_hive_depth(tok)


@pytest.mark.asyncio
async def test_hive_spawn_collect_tools(hive_home: Path):
    async def pulse(_rt, daughter):
        return ReturnPacket(goal=daughter.goal, done=True, outcome="ok", confidence=1)

    set_pulse_impl(pulse)
    try:
        from remedy.core.agent_hive_tools import register_hive_tools
        from remedy.skills.tool_registry import ToolRegistry

        rt = SimpleNamespace(
            tool_registry=ToolRegistry(),
            config=SimpleNamespace(home_dir=str(hive_home)),
            _session_id="owner-sess",
        )
        rt.effective_project_path = lambda: hive_home  # type: ignore[method-assign]
        register_hive_tools(rt)
        out = await rt.tool_registry.execute("hive_spawn", goal="review x")
        assert "hive_id=" in out
        hid = out.split("hive_id=", 1)[1].split()[0]
        for _ in range(50):
            col = await rt.tool_registry.execute("hive_collect", hive_id=hid)
            if "still running" not in col:
                assert "outcome=ok" in col
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail(col)
        st = await rt.tool_registry.execute("hive_status")
        assert hid[:8] in st
        ret = await rt.tool_registry.execute("hive_retire", hive_id=hid)
        assert "retired" in ret
    finally:
        set_pulse_impl(None)


def test_hive_depth_default_zero():
    assert hive_depth() == 0
