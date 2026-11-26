"""Closed-loop program: honest outcomes, attention, one memory, quiet metabolism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from remedy.core.retention import run_retention_pass
from remedy.events.bus import EventBus
from remedy.events.types import Event, EventType
from remedy.memory.authority import budget_hits


class _Rt:
    def __init__(self, root: Path) -> None:
        self._root = root

    def effective_project_path(self) -> str:
        return str(self._root)


def test_budget_hits_drops_inferred_when_full() -> None:
    owner = {
        "title": "tea",
        "content": "likes tea",
        "inferred": False,
        "authority": "owner",
    }
    inferred = [
        {
            "title": f"n{i}",
            "content": "x" * 200,
            "inferred": True,
        }
        for i in range(8)
    ]
    out = budget_hits([*inferred, owner], limit=6, max_chars=400)
    assert out[0]["title"] == "tea"
    assert not any(h.get("inferred") and h.get("title") == "tea" for h in out)
    assert sum(1 for h in out if h.get("inferred")) < 8


def test_events_retention_prunes_old_rows(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    bus = EventBus(db_path=db)
    old = Event(
        event_type=EventType.TOOL_PROPOSED,
        session_id="s",
        turn_id="t",
        actor="remedy",
        payload={"n": 1},
        timestamp=datetime.now(UTC) - timedelta(days=40),
    )
    bus.emit(old)
    bus.emit_simple(
        EventType.TOOL_PROPOSED, session_id="s", turn_id="t2", n=2
    )
    n = bus.prune_older_than_days(14)
    assert n >= 1
    remaining = bus.for_turn("t")
    assert remaining == []
    bus.close()


def test_retention_pass_includes_events(tmp_path: Path) -> None:
    home = tmp_path / ".remedy"
    (home / "logs").mkdir(parents=True)
    res = run_retention_pass(
        {
            "home_dir": str(home),
            "retention_log_days": 30,
            "retention_event_days": 14,
        },
        home=home,
    )
    assert "events" in res
    # Prune must not mint events.db on a home that never logged events.
    assert not (home / "events.db").exists()


def test_budget_hits_keeps_unstamped_notes() -> None:
    from remedy.memory.authority import budget_hits

    hits = [
        {"title": "old", "content": "unstamped owner note " * 20, "inferred": False},
        {"title": "guess", "content": "inferred filler " * 40, "inferred": True},
    ]
    out = budget_hits(hits, limit=6, max_chars=80)
    assert out[0]["title"] == "old"
    assert not any(h.get("title") == "guess" for h in out)
