"""Content-addressed memory middleman — machine-native retrieval experiments."""

from __future__ import annotations

from remedy.memory.middleman import (
    MiddlemanMemory,
    content_key,
    forget_session_middleman,
    get_session_middleman,
    ingest_tool_result,
    is_handle,
    make_handle,
    tokenize,
)


def test_content_addressing_dedups():
    mm = MiddlemanMemory()
    k1 = mm.put("refactor login auth flow into oauth")
    k2 = mm.put("refactor login auth flow into oauth")
    assert k1 == k2
    assert len(mm) == 1
    assert content_key("x") == content_key("x")
    assert content_key("x") != content_key("y")


def test_provenance_filters_by_path():
    mm = MiddlemanMemory()
    mm.put("fixed the cache invalidation bug in token_nanobot", path="token_nanobot.py")
    mm.put("fixed a typo in the readme", path="README.md")
    hits = mm.search("cache invalidation", paths=["token_nanobot.py"])
    assert len(hits) == 1
    assert hits[0].item.path == "token_nanobot.py"
    hits = mm.search("cache invalidation", paths=["README.md"])
    assert hits == []


def test_retrieval_keyed_by_query_not_recency():
    mm = MiddlemanMemory()
    # old but relevant
    mm.put("the OAuth refresh endpoint is /auth/token with a 3600s expiry", path="auth.py")
    # recent but irrelevant
    mm.put("renamed the splash image to hero.png", path="assets.py")
    hits = mm.search("oauth refresh token expiry")
    assert hits and hits[0].item.path == "auth.py"


def test_project_respects_token_budget():
    mm = MiddlemanMemory()
    for i in range(20):
        mm.put(
            f"config option number {i} controls caching behavior in the backend server",
            path="config.py",
            kind="note",
        )
    big = mm.project("config option caching", budget_tokens=10000)
    small = mm.project("config option caching", budget_tokens=120)
    assert len(big) > len(small)
    assert small  # still returns a useful slice


def test_handles_resolve_lazily():
    mm = MiddlemanMemory()
    k = mm.put("the auth secret lives in the secrets store, not the config", kind="fact", path="secrets.md")
    handle = make_handle(k, kind="fact", path="secrets.md")
    assert is_handle(handle)
    # context holds only the cheap handle
    projected = mm.project("auth secret", budget_tokens=60)
    assert "remedy-mm://" in projected
    # resolve pulls the real body back when the model asks
    assert "secrets store" in mm.resolve(handle)


def test_bm25_ranks_relevant_first():
    mm = MiddlemanMemory()
    mm.put("database connection pool size is 20", kind="fact", path="db.py")
    mm.put("database migrations are applied on boot", kind="fact", path="db.py")
    mm.put("the UI theme is dark purple", kind="note", path="ui.py")
    hits = mm.search("database pool size", paths=["db.py"])
    assert hits
    assert hits[0].item.path == "db.py"
    assert "pool size" in hits[0].item.body


# ---- integration seams (session registry / ingest / project / resolve) ----

def test_session_registry_is_per_session_and_idempotent():
    forget_session_middleman("sess-A")
    forget_session_middleman("sess-B")
    a = get_session_middleman("sess-A")
    b = get_session_middleman("sess-B")
    assert a is not b
    assert get_session_middleman("sess-A") is a  # cached, not a new store


def test_ingest_tool_result_bounded_and_retrievable():
    forget_session_middleman("ingest")
    big = "line\n" * 5000  # far over the 2000-char cap
    key = ingest_tool_result(session_id="ingest", content=big, tool="file_read")
    assert key
    mm = get_session_middleman("ingest")
    body = mm.get(key)
    assert body is not None and len(body) < 4000  # bounded in-memory
    hits = mm.search("line", session_id="ingest")
    assert hits


def test_fact_ingest_then_projection_and_resolve():
    forget_session_middleman("flow")
    mm = get_session_middleman("flow")
    mm.put("the auth token refresh happens on /auth/token", kind="fact", session_id="flow")
    ingest_tool_result(session_id="flow", content="cache invalidation fixed in token_nanobot", tool="bash")
    proj = mm.project("auth token refresh", budget_tokens=300, session_id="flow")
    assert "auth token refresh" in proj
    assert "remedy-mm://" in proj  # carries a cheap handle, not the full dump
    # resolve pulls a body back by handle
    handle = proj.split("remedy-mm://", 1)[1].split("#")[0]
    assert mm.get("remedy-mm://" + handle) is not None
