"""Closed-loop program: honest outcomes, attention, one memory, quiet metabolism."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remedy.core.build_gate_tower import run_gate_tower
from remedy.core.retention import run_retention_pass
from remedy.events.bus import EventBus
from remedy.events.types import Event, EventType
from remedy.memory.authority import budget_hits
from remedy.memory.cas import EternalCAS
from remedy.memory.middleman import MemoryItem, content_key
from remedy.memory.persona_wipe import CONFIRM_PHRASE, wipe_persona
from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType


class _Rt:
    def __init__(self, root: Path) -> None:
        self._root = root

    def effective_project_path(self) -> str:
        return str(self._root)


def test_l3_skip_pass_is_not_verified(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("X = 1\n", encoding="utf-8")
    res = run_gate_tower(
        _Rt(tmp_path),
        [str(tmp_path / "mod.py")],
        levels=["L3_unit"],
        stop_at_first_red=True,
    )
    assert res["ok"] is True
    assert res["verified"] is False
    assert "couldn't verify" in (res.get("message") or "").lower() or "didn't run" in (
        res.get("results") or [{}]
    )[0].get("summary", "").lower()


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


@pytest.mark.asyncio
async def test_persona_wipe_forgets_cas_fact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    await store.initialize()
    await store.upsert(
        MemoryEntry(
            title="tea",
            content="likes tea",
            entry_type=MemoryEntryType.NOTE,
        )
    )
    body = "likes tea as a standing fact"
    cas = EternalCAS(tmp_path)
    cas.put_item(
        MemoryItem(key=content_key(body), kind="fact", body=body, session_id="s1")
    )
    assert any(
        "tea" in (getattr(i, "body", "") or "")
        for i in cas.fetch_hot(session_id="", facts_limit=20)
    )
    await wipe_persona(store, home=tmp_path, confirm=CONFIRM_PHRASE)
    leftover = await store.list_by_type(MemoryEntryType.NOTE, limit=10)
    assert leftover == []
    hot = EternalCAS(tmp_path).fetch_hot(session_id="", facts_limit=40)
    assert not any("tea" in (getattr(i, "body", "") or "") for i in hot)
    await store.close()


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


def test_l2_interpreter_skip_is_not_verified(tmp_path: Path) -> None:
    import remedy.core.build_import_graph as big
    from remedy.core.build_gate_tower import gate_l2_import

    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    real = big.dry_run_imports_for_paths

    def fake(_paths, _root, **_k):
        return [
            {
                "ok": False,
                "module": "m",
                "error": "no interpreter",
                "error_class": "interpreter",
            }
        ]

    big.dry_run_imports_for_paths = fake  # type: ignore[assignment]
    try:
        gr = gate_l2_import(tmp_path, [str(tmp_path / "m.py")])
        assert gr.ok is True
        assert gr.verified is False
    finally:
        big.dry_run_imports_for_paths = real  # type: ignore[assignment]


def test_l1_no_linter_is_not_verified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from remedy.core.build_gate_tower import gate_l1_static

    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr("remedy.core.build_gate_tower.shutil.which", lambda _name: None)
    gr = gate_l1_static(tmp_path, [str(tmp_path / "m.py")])
    assert gr.ok is True
    assert gr.verified is False


def test_budget_hits_keeps_unstamped_notes() -> None:
    from remedy.memory.authority import budget_hits

    hits = [
        {"title": "old", "content": "unstamped owner note " * 20, "inferred": False},
        {"title": "guess", "content": "inferred filler " * 40, "inferred": True},
    ]
    out = budget_hits(hits, limit=6, max_chars=80)
    assert out[0]["title"] == "old"
    assert not any(h.get("title") == "guess" for h in out)
