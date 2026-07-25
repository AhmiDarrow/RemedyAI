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
    assert estimate_tokens_text("abcd") == 1
    assert estimate_tokens_text("a" * 40) == 10
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
