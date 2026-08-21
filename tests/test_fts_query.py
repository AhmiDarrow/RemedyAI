"""FTS5 MATCH query quoting: no syntax errors, no LIKE fallback."""

from __future__ import annotations

import sqlite3

import pytest

from remedy.memory.fts_query import fts5_match_query, fts5_tokens

NASTY = [
    "remedy.core.agent_llm; drop",
    "what's up: 15:36?",
    "foo-bar (baz)",
    "C:\\Users\\x\\.remedy",
    'he said "hello"',
    "cats AND dogs OR NOT birds",
    "*",
    "...",
    "",
    "NEAR(a b)",
]


def test_tokens_drop_operators_and_punctuation():
    assert fts5_tokens("cats AND dogs OR NOT birds") == ["cats", "dogs", "birds"]
    assert fts5_tokens("remedy.core.agent_llm; drop") == ["remedy", "core", "agent_llm", "drop"]
    assert fts5_tokens("...") == []


def test_match_query_shape():
    assert fts5_match_query("foo bar") == '"foo" "bar"'
    assert fts5_match_query("") == ""
    assert fts5_match_query("mem sto", prefix_last=True) == '"mem" "sto"*'


@pytest.mark.parametrize("raw", NASTY)
def test_quoted_query_never_raises_in_fts5(raw: str):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE t USING fts5(title, content)")
    db.execute(
        "INSERT INTO t VALUES (?, ?)",
        ("agent_llm", "remedy core agent_llm drop; cats dogs birds hello 15 36"),
    )
    q = fts5_match_query(raw)
    if not q:
        return
    # The raw text would raise on most of these; the quoted form never does.
    rows = db.execute("SELECT title FROM t WHERE t MATCH ?", (q,)).fetchall()
    assert isinstance(rows, list)


def test_raw_query_does_raise_so_the_helper_matters():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    with pytest.raises(sqlite3.OperationalError):
        db.execute("SELECT * FROM t WHERE t MATCH ?", ("remedy.core; x",)).fetchall()
    db.execute("INSERT INTO t VALUES ('remedy core x')")
    rows = db.execute(
        "SELECT * FROM t WHERE t MATCH ?", (fts5_match_query("remedy.core; x"),)
    ).fetchall()
    assert len(rows) == 1
