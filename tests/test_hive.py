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
from remedy.core.hive.pulse import (
    resume_posts,
    run_post_pulse,
    stop_all_posts,
)
from remedy.core.hive.runner import cancel_children, run_forager, set_pulse_impl
from remedy.core.hive.store import HiveStore
from remedy.core.hive.types import (
    CADENCE_POST,
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
        {"type": "function", "function": {"name": "browser_click"}},
        {"type": "function", "function": {"name": "computer_drag"}},
        {"type": "function", "function": {"name": "list_dir"}},
    ]
    out = filter_daughter_tools(tools)
    names = [t["function"]["name"] for t in out]
    assert names == ["file_read", "list_dir"]
    assert is_mother_only_tool("hive_collect")
    assert is_mother_only_tool("browser_click")
    assert is_mother_only_tool("computer_drag")
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


def _runtime(hive_home: Path, session_id: str = "owner-sess"):
    from remedy.core.agent_hive_tools import register_hive_tools
    from remedy.skills.tool_registry import ToolRegistry

    rt = SimpleNamespace(
        tool_registry=ToolRegistry(),
        config=SimpleNamespace(home_dir=str(hive_home)),
        _session_id=session_id,
    )
    rt.effective_project_path = lambda: hive_home  # type: ignore[method-assign]
    register_hive_tools(rt)
    return rt


@pytest.mark.asyncio
async def test_post_pulse_once(hive_home: Path):
    async def pulse(_rt, daughter):
        return ReturnPacket(
            goal=daughter.goal,
            done=False,
            outcome="watched the inbox",
            evidence=["inbox"],
            confidence=0.7,
        )

    set_pulse_impl(pulse)
    try:
        store = HiveStore(hive_home)
        d = store.hire("watch mail", cadence=CADENCE_POST, pulse_s=30)
        rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(hive_home)))
        pkt = await run_post_pulse(rt, d)
        assert "inbox" in pkt.outcome or "watched" in pkt.outcome
        fresh = store.get(d.id)
        assert fresh is not None
        assert fresh.status == "asleep"
        assert int((fresh.journal or {}).get("pulse_count") or 0) == 1
        assert fresh.next_pulse_at
        dumped = (hive_home / "hive" / "posts" / f"{d.id}.json").read_text(
            encoding="utf-8"
        )
        assert "tool_calls" not in dumped
        assert "watched the inbox" in dumped
    finally:
        set_pulse_impl(None)


@pytest.mark.asyncio
async def test_journal_survives_restart(hive_home: Path):
    async def pulse(_rt, daughter):
        return ReturnPacket(goal=daughter.goal, done=False, outcome="tick", confidence=0.5)

    set_pulse_impl(pulse)
    try:
        store = HiveStore(hive_home)
        d = store.hire("stand", cadence=CADENCE_POST, pulse_s=30)
        rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(hive_home)))
        await run_post_pulse(rt, d)
        store2 = HiveStore(hive_home)
        got = store2.get(d.id)
        assert got is not None
        assert got.cadence == CADENCE_POST
        assert int((got.journal or {}).get("pulse_count") or 0) == 1
        notes = (got.journal or {}).get("notes") or []
        assert notes and "tick" in str(notes[0].get("outcome"))
        n = resume_posts(rt)
        assert n >= 1
        stop_all_posts()
    finally:
        set_pulse_impl(None)
        stop_all_posts()


@pytest.mark.asyncio
async def test_hive_assign_replaces_charter(hive_home: Path):
    async def pulse(_rt, daughter):
        return ReturnPacket(goal=daughter.goal, done=False, outcome=daughter.goal[:40])

    set_pulse_impl(pulse)
    try:
        rt = _runtime(hive_home)
        out = await rt.tool_registry.execute(
            "hive_spawn", goal="watch logs", cadence="post", pulse_s=120
        )
        assert "HIVE_POST_UNAVAILABLE" not in out
        assert "cadence=post" in out
        hid = out.split("hive_id=", 1)[1].split()[0]
        assigned = await rt.tool_registry.execute(
            "hive_assign", hive_id=hid, goal="watch errors only"
        )
        assert "charter replaced" in assigned
        store = HiveStore(hive_home)
        got = store.get(hid)
        assert got is not None
        assert got.goal == "watch errors only"
        assert "watch errors only" in str((got.journal or {}).get("charter"))
        forager = store.hire("one shot")
        bad = await rt.tool_registry.execute(
            "hive_assign", hive_id=forager.id, goal="nope"
        )
        assert "HIVE_NOT_POST" in bad
    finally:
        set_pulse_impl(None)
        stop_all_posts()


@pytest.mark.asyncio
async def test_stop_does_not_retire_posts(hive_home: Path):
    store = HiveStore(hive_home)
    post = store.hire(
        "stand forever",
        cadence=CADENCE_POST,
        parent_session_id="owner-sess",
        pulse_s=30,
    )
    forager = store.hire("quick", parent_session_id="owner-sess")
    n = cancel_children("owner-sess")
    # Forager is pending so cancel_children aborts it; post is skipped.
    assert n >= 1
    still = store.get(post.id)
    assert still is not None
    assert still.status not in ("cancelled", "retired")
    assert still.cadence == CADENCE_POST
    gone = store.get(forager.id)
    assert gone is not None
    # Forager abort is async via task; status may still be pending if never scheduled.
    assert gone.cadence == "forager"


def test_spawn_continue_hint():
    from remedy.core.hive.mother import SPAWN_CONTINUE_HINT, inject_spawn_continue

    msgs: list[dict] = []
    spawned = [{"function": {"name": "hive_spawn", "arguments": "{}"}}]
    assert inject_spawn_continue(msgs, spawned) is True
    assert msgs[-1]["role"] == "user"
    assert SPAWN_CONTINUE_HINT in str(msgs[-1]["content"])
    assert inject_spawn_continue(msgs, spawned) is False
    other: list[dict] = []
    assert inject_spawn_continue(other, [{"function": {"name": "file_read"}}]) is False
    assigned: list[dict] = []
    assert inject_spawn_continue(
        assigned, [{"function": {"name": "hive_assign"}}]
    ) is True


def test_announce_registers_coordination_beacon(hive_home: Path):
    from remedy.core.coordination import active_beacons
    from remedy.core.hive.mother import announce_daughter, silence_daughter

    store = HiveStore(hive_home)
    d = store.hire("review auth.py", project_path=str(hive_home))
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(hive_home)))
    announce_daughter(d, rt)
    ids = {b.session_id for b in active_beacons(home=hive_home)}
    assert d.session_id in ids
    hive = [b for b in active_beacons(home=hive_home) if b.session_id == d.session_id][0]
    assert hive.muscle == "hive"
    assert "auth" in hive.goal
    silence_daughter(d, rt)
    ids = {b.session_id for b in active_beacons(home=hive_home)}
    assert d.session_id not in ids


@pytest.mark.asyncio
async def test_collect_admits_evidence_to_mother(hive_home: Path):
    from remedy.core.metabolism.evidence import get_evidence_ledger, reset_evidence_ledger

    reset_evidence_ledger("owner-sess")

    async def pulse(_rt, daughter):
        return ReturnPacket(
            goal=daughter.goal,
            done=True,
            outcome="auth is fine",
            evidence=["src/auth.py"],
            confidence=0.9,
        )

    set_pulse_impl(pulse)
    try:
        rt = _runtime(hive_home, "owner-sess")
        out = await rt.tool_registry.execute("hive_spawn", goal="review auth.py")
        hid = out.split("hive_id=", 1)[1].split()[0]
        packet_text = ""
        for _ in range(50):
            col = await rt.tool_registry.execute("hive_collect", hive_id=hid)
            if "still running" not in col:
                packet_text = col
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail(packet_text or "collect never finished")
        assert "auth is fine" in packet_text
        led = get_evidence_ledger("owner-sess")
        assert led.evidence_units >= 1
        blob = " ".join(u.summary for u in led.units)
        assert "hive" in blob.lower() or "auth" in blob.lower()
    finally:
        set_pulse_impl(None)
        stop_all_posts()


def test_hive_roster_api_has_no_transcript(hive_home: Path):
    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app

    store = HiveStore(hive_home)
    d = store.hire("review auth.py", parent_session_id="owner-1")
    app = create_app(api_key="")
    client = TestClient(app)
    r = client.get("/api/hive/roster")
    assert r.status_code == 200
    body = r.json()
    ids = {row["id"] for row in body.get("daughters") or []}
    assert d.id in ids
    for row in body["daughters"]:
        assert "messages" not in row
        assert "tool_calls" not in row
        assert "goal" in row
    retired = client.post("/api/hive/retire", json={"hive_id": d.id})
    assert retired.status_code == 200
    assert retired.json().get("ok") is True
    fresh = store.get(d.id)
    assert fresh is not None
    assert fresh.status == "retired"


@pytest.mark.asyncio
async def test_a_daughters_step_budget_never_lands_on_the_mothers_runtime(
    hive_home: Path,
):
    """The mother keeps working while a daughter forages — her ReAct ceiling
    must not be replaced by the daughter's small budget (nor left behind)."""
    from remedy.core.turn_context import turn_max_react_steps

    seen: dict[str, int] = {}

    async def pulse(runtime, daughter):
        seen["in_turn"] = turn_max_react_steps(runtime)
        seen["on_runtime"] = int(getattr(runtime, "_max_react_steps", 0) or 0)
        return ReturnPacket(goal=daughter.goal, done=True, outcome="ok")

    from remedy.core.hive.runner import _default_llm_pulse

    set_pulse_impl(None)
    try:
        store = HiveStore(hive_home)
        d = store.hire("scan the repo", budget_steps=3)
        rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(hive_home)))
        rt._max_react_steps = 9999

        async def _fake_stream(runtime, charter, session_id=""):
            seen["in_turn"] = turn_max_react_steps(runtime)
            seen["on_runtime"] = int(getattr(runtime, "_max_react_steps", 0) or 0)
            yield "done"

        import remedy.core.react_loop.loop as loop_mod

        real = loop_mod.call_llm_stream
        loop_mod.call_llm_stream = _fake_stream
        try:
            set_pulse_impl(_default_llm_pulse)
            await run_forager(rt, d)
        finally:
            loop_mod.call_llm_stream = real

        # The budget was scoped to the daughter's turn...
        assert seen["in_turn"] == 3
        # ...and the mother's shared runtime was never touched.
        assert seen["on_runtime"] == 9999
        assert rt._max_react_steps == 9999
    finally:
        set_pulse_impl(None)
