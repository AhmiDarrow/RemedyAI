"""Eternal CAS — survive restart, dedup, tombstone, cold recall."""

from __future__ import annotations

from remedy.memory.cas import configure_cas, get_cas
from remedy.memory.middleman import (
    MiddlemanMemory,
    content_key,
    forget_session_middleman,
    get_session_middleman,
    reset_middleman_state,
)


def _bind(tmp_path):
    reset_middleman_state()
    configure_cas(tmp_path)
    return get_cas()


def _unbind():
    reset_middleman_state()
    configure_cas(None)


def test_put_dedups_and_survives_reopen(tmp_path):
    cas = _bind(tmp_path)
    assert cas is not None
    mm = get_session_middleman("s1")
    k1 = mm.put("oauth refresh lives at /auth/token", kind="fact", session_id="s1")
    k2 = mm.put("oauth refresh lives at /auth/token", kind="fact", session_id="s1")
    assert k1 == k2 == content_key("oauth refresh lives at /auth/token")
    assert cas.count() == 1

    reset_middleman_state()
    configure_cas(tmp_path)
    mm2 = get_session_middleman("s1")
    assert len(mm2) >= 1
    assert mm2.get(k1) is not None
    assert "oauth refresh" in (mm2.get(k1) or "")
    _unbind()


def test_cross_session_fact_hydrates(tmp_path):
    _bind(tmp_path)
    get_session_middleman("old").put(
        "the piano exam is in March",
        kind="fact",
        session_id="old",
    )
    reset_middleman_state()
    configure_cas(tmp_path)
    fresh = get_session_middleman("new")
    hits = fresh.search("piano exam")
    assert hits
    assert "March" in hits[0].item.body
    _unbind()


def test_tombstone_hides_object(tmp_path):
    cas = _bind(tmp_path)
    assert cas is not None
    mm = get_session_middleman("t")
    key = mm.put("delete me later secret project name", kind="note", session_id="t")
    assert cas.tombstone(key) is True
    reset_middleman_state()
    configure_cas(tmp_path)
    mm2 = get_session_middleman("t")
    assert mm2.get(key) is None
    _unbind()


def test_cold_fts_on_ram_miss(tmp_path):
    _bind(tmp_path)
    mm = MiddlemanMemory()
    mm.put(
        "the cache invalidation lives in token_nanobot",
        kind="fact",
        path="token_nanobot.py",
        session_id="c",
    )
    # New RAM store, not hydrated — search must pull from disk FTS.
    cold = MiddlemanMemory()
    hits = cold.search("cache invalidation token_nanobot")
    assert hits
    assert "token_nanobot" in hits[0].item.body
    _unbind()


def test_compact_drops_old_tools_keeps_facts(tmp_path):
    cas = _bind(tmp_path)
    assert cas is not None
    mm = get_session_middleman("k")
    mm.put("durable preference: dark theme", kind="fact", session_id="k")
    tool_key = mm.put("tool stdout: compiled 12 files", kind="tool", session_id="k")
    # Age the tool row
    cas._db_req().execute(
        "UPDATE objects SET ts = 1 WHERE key = ?",
        (tool_key,),
    )
    cas._db_req().commit()
    out = cas.compact(tool_max_age_days=1)
    assert out["tombstoned"] >= 1 or out["purged"] >= 1
    reset_middleman_state()
    configure_cas(tmp_path)
    mm2 = get_session_middleman("k")
    assert mm2.search("dark theme")
    assert not mm2.search("compiled 12 files")
    _unbind()


def test_snapshot_is_cached_until_write(tmp_path):
    cas = _bind(tmp_path)
    assert cas is not None
    get_session_middleman("c1").put("alpha fact body", kind="fact", session_id="c1")
    s1 = cas.snapshot()
    assert s1["count"] >= 1
    cas._snap["count"] = 4242
    s2 = cas.snapshot()
    assert s2["count"] == 4242
    get_session_middleman("c1").put("beta fact body unique", kind="fact", session_id="c1")
    s3 = cas.snapshot()
    assert s3["count"] != 4242
    _unbind()


def test_pytest_does_not_auto_open_real_cas():
    _unbind()
    assert get_cas() is None
    forget_session_middleman("anon")
    mm = get_session_middleman("anon")
    mm.put("ephemeral only")
    # Still no process-wide CAS in pytest
    assert get_cas() is None
