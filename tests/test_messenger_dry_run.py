"""Dry-run messenger path without external platform apps."""

from __future__ import annotations

import pytest

from remedy.gateway.messenger_dry_run import dry_run_all_channels, dry_run_inbound


@pytest.mark.asyncio
async def test_dry_run_telegram_creates_session(tmp_path):
    db = tmp_path / "memory.db"
    r = await dry_run_inbound(
        channel="telegram",
        chat_id="test-chat-1",
        message="hello dry run",
        username="tester",
        home=tmp_path,
        db_path=db,
    )
    assert r["ok"] is True
    assert r["session_id"] == "msg:telegram:test-chat-1"
    assert r["origin_channel"] == "telegram"
    assert r["message_count"] >= 1
    assert "user" in r["roles"]


@pytest.mark.asyncio
async def test_dry_run_all_channels(tmp_path):
    db = tmp_path / "memory.db"
    r = await dry_run_all_channels(home=tmp_path, db_path=db)
    assert r["ok"] is True
    assert len(r["results"]) >= 5
    for item in r["results"]:
        assert item.get("ok"), item
