"""Usage estimates + time-travel undo log."""

from __future__ import annotations

from pathlib import Path

from remedy.core.time_travel import SessionUndoLog, build_timeline
from remedy.core.usage import (
    estimate_cost_usd,
    estimate_tokens_text,
    estimate_turn_usage,
    merge_usage,
    usage_from_provider_payload,
)
from remedy.models import ChatMessage, ChatMessageRole


def test_estimate_tokens_and_cost():
    # BPE/heuristic estimates — exact counts vary by pack; stay positive & scale.
    n4 = estimate_tokens_text("abcd")
    n40 = estimate_tokens_text("a" * 40)
    assert n4 >= 1
    assert n40 >= n4
    assert estimate_tokens_text("") == 0
    c = estimate_cost_usd(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        model="gpt-4o-mini",
        provider="openai",
    )
    assert 0.1 < c < 0.3


def test_usage_from_provider_payload():
    u = usage_from_provider_payload(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}},
        model="grok-3-mini",
        provider="xai",
    )
    assert u is not None
    assert u["prompt_tokens"] == 100
    assert u["completion_tokens"] == 50
    assert u["source"] == "provider"
    assert u["estimated_cost_usd"] >= 0


def test_merge_usage_prefers_provider_flag():
    a = estimate_turn_usage(user_text="hi", assistant_text="hello", model="demo")
    b = merge_usage(a, {"prompt_tokens": 10, "completion_tokens": 5, "source": "provider"})
    assert b["source"] == "provider"
    assert b["prompt_tokens"] >= 10


def test_file_undo_restore(tmp_path: Path):
    home = tmp_path / "remedy"
    home.mkdir()
    log = SessionUndoLog(home)
    f = tmp_path / "work" / "note.txt"
    f.parent.mkdir()
    f.write_text("v1", encoding="utf-8")
    log.record_file_write(
        session_id="sess1",
        path=f,
        previous_content="v1",
        existed=True,
        new_size=2,
        message_id="m1",
    )
    f.write_text("v2", encoding="utf-8")
    # Second write after cut
    log.record_file_write(
        session_id="sess1",
        path=f,
        previous_content="v2",
        existed=True,
        new_size=2,
        message_id="m2",
    )
    f.write_text("v3", encoding="utf-8")
    # Undo only m2's write → previous content for that mutation is v2
    result = log.restore_after("sess1", cut_message_id="m2")
    assert result["restored"] >= 1
    assert f.read_text(encoding="utf-8") == "v2"
    # Undo from m1 → further back to v1
    f.write_text("v2-again", encoding="utf-8")
    log.record_file_write(
        session_id="sess1",
        path=f,
        previous_content="v2",
        existed=True,
        new_size=8,
        message_id="m3",
    )
    f.write_text("v3-again", encoding="utf-8")
    result2 = log.restore_after("sess1", cut_message_id="m1")
    assert result2["restored"] >= 1
    assert f.read_text(encoding="utf-8") == "v1"


def test_build_timeline_steps():
    from uuid import uuid4

    u1, a1, u2 = uuid4(), uuid4(), uuid4()
    msgs = [
        ChatMessage(
            session_id="s",
            role=ChatMessageRole.USER,
            content="do step 1",
            id=u1,
        ),
        ChatMessage(
            session_id="s",
            role=ChatMessageRole.ASSISTANT,
            content="done 1",
            id=a1,
            tool_calls=[{"name": "file_write", "args": {}}],
        ),
        ChatMessage(
            session_id="s",
            role=ChatMessageRole.USER,
            content="do step 2",
            id=u2,
        ),
    ]
    steps = build_timeline(msgs)
    kinds = [s["kind"] for s in steps]
    assert "user" in kinds
    assert "assistant" in kinds
    users = [s for s in steps if s["kind"] == "user"]
    assert users[0]["step"] == 1
    assert users[1]["step"] == 2


def test_time_travel_skips_truncated_prior_content(tmp_path: Path):
    """Oversized priors must not rewrite source with the truncation stub."""
    home = tmp_path / "remedy"
    home.mkdir()
    log = SessionUndoLog(home)
    f = tmp_path / "big.txt"
    huge = "Z" * (SessionUndoLog.MAX_PREV_CHARS + 5000)
    f.write_text(huge, encoding="utf-8")
    entry = log.record_file_write(
        session_id="sess-big",
        path=f,
        previous_content=huge,
        existed=True,
        new_size=10,
        message_id="m-big",
    )
    assert entry is not None
    assert entry.kind == "file_write_incomplete"
    f.write_text("after-agent-edit", encoding="utf-8")
    result = log.restore_after("sess-big", cut_message_id="m-big")
    assert result["restored"] == 0
    assert result["skipped"] >= 1
    # Disk still has agent edit — not a truncated stub
    body = f.read_text(encoding="utf-8")
    assert body == "after-agent-edit"
    assert "truncated for undo log" not in body


def test_time_travel_blocks_auth_paths(tmp_path: Path, monkeypatch):
    """Undo log must not record or restore under auth secrets."""
    home = tmp_path / "remedy"
    home.mkdir()
    auth = home / "auth"
    auth.mkdir()
    secret_file = auth / "keys.json"
    secret_file.write_text('{"k":"v1"}', encoding="utf-8")
    monkeypatch.setenv("REMEDY_HOME", str(home))
    from remedy.core.security import clear_protected_auth_roots_cache

    clear_protected_auth_roots_cache()

    log = SessionUndoLog(home)
    # record should refuse
    entry = log.record_file_write(
        session_id="sess-auth",
        path=secret_file,
        previous_content='{"k":"v1"}',
        existed=True,
        new_size=10,
        message_id="m1",
    )
    assert entry is None
    assert log.list_entries("sess-auth") == []

    # Even a hand-injected residual entry must not restore into auth
    p = log._path("sess-auth")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        __import__("json").dumps(
            {
                "id": "x",
                "session_id": "sess-auth",
                "message_id": "m1",
                "path": str(secret_file),
                "existed": True,
                "previous_content": '{"k":"evil"}',
                "new_size": 10,
                "created_at": "2020-01-01T00:00:00+00:00",
                "kind": "file_write",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    secret_file.write_text('{"k":"v2"}', encoding="utf-8")
    result = log.restore_after("sess-auth", cut_message_id="m1")
    assert result.get("blocked", 0) >= 1
    assert secret_file.read_text(encoding="utf-8") == '{"k":"v2"}'


def test_time_travel_message_id_fallback_to_timestamp(tmp_path: Path):
    """API passes both cuts — missing message tag still undoes by time."""
    home = tmp_path / "remedy"
    home.mkdir()
    log = SessionUndoLog(home)
    f = tmp_path / "note.txt"
    f.write_text("v1", encoding="utf-8")
    log.record_file_write(
        session_id="s-fb",
        path=f,
        previous_content="v1",
        existed=True,
        new_size=2,
        message_id="tagged-later",
    )
    f.write_text("v2", encoding="utf-8")
    entries = log.list_entries("s-fb")
    cut_at = entries[0].created_at
    # Unknown message_id with timestamp fallback
    result = log.restore_after(
        "s-fb",
        cut_message_id="user-step-without-writes",
        cut_created_at=cut_at,
    )
    assert result["restored"] >= 1
    assert f.read_text(encoding="utf-8") == "v1"


def test_a_failing_secret_path_check_refuses_the_restore(tmp_path, monkeypatch):
    """The protected-path guard was wrapped in ``except Exception: pass``, so a
    guard that could not run fell through to "allowed" — and the only thing
    between a restore and ~/.remedy/auth was the weaker path-parts check."""
    from remedy.core import security

    def boom(path):
        raise RuntimeError("auth roots unavailable")

    monkeypatch.setattr(security, "is_protected_secret_path", boom)
    home = tmp_path / "home"
    home.mkdir()
    log = SessionUndoLog(home)
    ordinary = tmp_path / "work" / "notes.txt"
    assert log._is_restore_forbidden(ordinary) is True

    ordinary.parent.mkdir(parents=True)
    ordinary.write_text("v1", encoding="utf-8")
    assert (
        log.record_file_write(
            session_id="s-strict",
            path=ordinary,
            previous_content="v1",
            existed=True,
            new_size=2,
        )
        is None
    )
