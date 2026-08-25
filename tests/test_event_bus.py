"""M1.7 in-process event bus + SQLite."""

from __future__ import annotations

from pathlib import Path

from remedy.core.context import TurnFactory
from remedy.core.turn_context import begin_turn, end_turn
from remedy.events.bus import EventBus, default_bus
from remedy.events.types import Event, EventType
from remedy.policy.decisions import ToolRequest
from remedy.policy.engine import PolicyEngine


def test_emit_and_query(tmp_path: Path):
    bus = EventBus(db_path=tmp_path / "events.db")
    seen: list[Event] = []
    bus.subscribe(seen.append)
    ev = bus.emit_simple(
        EventType.GOAL_STARTED,
        session_id="s1",
        turn_id="t1",
        goal="demo",
    )
    assert ev.event_id
    assert seen and seen[0].event_type == EventType.GOAL_STARTED
    rows = bus.for_turn("t1")
    assert len(rows) == 1
    assert rows[0].payload.get("goal") == "demo"
    bus.close()


def test_sqlite_survives_new_bus(tmp_path: Path):
    db = tmp_path / "events.db"
    first = EventBus(db_path=db)
    first.emit_simple(EventType.GOAL_STARTED, session_id="s", turn_id="t", goal="persist")
    first.close()
    second = EventBus(db_path=db)
    rows = second.for_turn("t")
    assert len(rows) == 1
    assert rows[0].payload.get("goal") == "persist"
    second.close()


def test_subscriber_error_does_not_block_emit(tmp_path: Path):
    bus = EventBus(db_path=tmp_path / "events.db")
    seen: list[str] = []

    def boom(_event: Event) -> None:
        raise RuntimeError("subscriber failed")

    def ok(event: Event) -> None:
        seen.append(event.event_id)

    bus.subscribe(boom)
    bus.subscribe(ok)
    ev = bus.emit_simple(EventType.TOOL_COMPLETED, session_id="s", turn_id="t", tool="file_read")
    assert seen == [ev.event_id]
    bus.close()


def test_turn_factory_emits_goal_started(tmp_path: Path):
    tokens = begin_turn("s-ev", project_raw=None, active_path=str(tmp_path))
    try:
        ctx = TurnFactory.create()
        rows = default_bus().for_turn(ctx.turn_id)
        assert any(r.event_type == EventType.GOAL_STARTED for r in rows)
    finally:
        end_turn("s-ev", *tokens)


def test_policy_engine_emits_tool_proposed(tmp_path: Path):
    tokens = begin_turn("s-pol", project_raw=None, active_path=str(tmp_path))
    try:
        ctx = TurnFactory.create()
        PolicyEngine().evaluate(
            ctx, "file_read", ToolRequest(name="file_read", command="README.md")
        )
        kinds = {r.event_type for r in default_bus().for_turn(ctx.turn_id)}
        assert EventType.GOAL_STARTED in kinds
        assert EventType.TOOL_PROPOSED in kinds
    finally:
        end_turn("s-pol", *tokens)
