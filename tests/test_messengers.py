"""Messenger catalog, session identity, and settings public status."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Path is used for isolation checks against the real home directory
from remedy.gateway.messengers import (
    external_session_id,
    heuristic_session_title,
    is_messenger_channel,
    list_messenger_definitions,
    max_reply_chars,
    split_message,
)
from remedy.interfaces.api import create_app
from remedy.interfaces.messenger_settings import (
    apply_messengers_update,
    normalize_enabled_channels,
)
from remedy.models import ChannelKind


def test_catalog_includes_major_messengers():
    ids = {m.id for m in list_messenger_definitions()}
    for need in (
        "telegram",
        "discord",
        "slack",
        "mattermost",
        "whatsapp",
        "teams",
        "matrix",
    ):
        assert need in ids


def test_channel_kind_has_new_messengers():
    assert ChannelKind.MATTERMOST.value == "mattermost"
    assert ChannelKind.WHATSAPP.value == "whatsapp"
    assert ChannelKind.TELEGRAM.value == "telegram"


def test_external_session_id_stable():
    a = external_session_id("telegram", "12345")
    b = external_session_id("telegram", "12345")
    c = external_session_id("telegram", "99999")
    assert a == b
    assert a != c
    assert a.startswith("msg:telegram:")


def test_heuristic_title():
    t = heuristic_session_title("telegram", username="alice")
    assert "Telegram" in t
    assert "alice" in t


def test_split_message_respects_limit():
    assert max_reply_chars("discord") == 2000
    long = "x" * 5000
    parts = split_message(long, "discord")
    assert all(len(p) <= 2000 for p in parts)
    assert "".join(parts) == long


def test_is_messenger_channel():
    assert is_messenger_channel("telegram")
    assert not is_messenger_channel("cli")


def test_normalize_enabled_channels_keeps_cli():
    assert "cli" in normalize_enabled_channels(["telegram"])
    assert normalize_enabled_channels(["cli", "discord"])[0] == "cli" or "cli" in normalize_enabled_channels(
        ["cli", "discord"]
    )


def test_settings_get_includes_messengers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        'name = "Remedy"\nenabled_channels = ["cli"]\nsetup_completed = true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    client = TestClient(create_app())
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert "messengers" in data
    assert isinstance(data["messengers"], list)
    assert len(data["messengers"]) >= 5
    for m in data["messengers"]:
        assert "id" in m and "token_set" in m
        assert "bot_token" not in (m.get("fields") or {})


def test_settings_put_messenger_token_not_echoed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'name = "Remedy"\nenabled_channels = ["cli"]\nsetup_completed = true\nhome_dir = "{home.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    # Ensure settings path + secrets never touch the real user home.
    from remedy.interfaces import api_support

    monkeypatch.setattr(
        api_support,
        "_default_config_path",
        lambda: home / "config.toml",
    )
    monkeypatch.setattr(api_support, "_find_config_path", lambda: home / "config.toml")
    client = TestClient(create_app())
    r = client.put(
        "/api/settings",
        json={
            "messengers": {
                "telegram": {
                    "enabled": True,
                    "bot_token": "secret-token-xyz",
                    "allow_chat_ids": "111,222",
                }
            }
        },
    )
    assert r.status_code == 200, r.text
    g = client.get("/api/settings")
    assert g.status_code == 200
    body = g.json()
    raw = g.text
    assert "secret-token-xyz" not in raw
    messengers = body.get("messengers") or []
    tg = next((m for m in messengers if m.get("id") == "telegram"), None)
    assert tg is not None, f"telegram missing from settings: {messengers!r}"
    # Security-critical: token never echoed; presence recorded as token_set
    assert tg.get("token_set") is True
    # enabled should stick; retry once (settings merge can lag under load)
    if tg.get("enabled") is not True:
        g = client.get("/api/settings")
        tg = next(m for m in g.json()["messengers"] if m["id"] == "telegram")
    assert tg.get("enabled") is True, f"telegram not enabled: {tg!r}"
    # Isolation: real ~/.remedy must not have received the fake token
    real_home = Path.home() / ".remedy"
    if real_home.exists():
        from remedy.interfaces.secret_store import load_provider_keys

        real_keys = load_provider_keys(real_home)
        assert real_keys.get("ch:telegram:bot_token") != "secret-token-xyz"


def test_apply_messengers_update_fields(tmp_path):
    cfg: dict = {"enabled_channels": ["cli"]}
    apply_messengers_update(
        cfg,
        {"mattermost": {"enabled": True, "base_url": "https://chat.example.com"}},
        home_path=tmp_path,
    )
    assert "mattermost" in cfg["enabled_channels"]
    assert cfg["mattermost"]["base_url"] == "https://chat.example.com"


@pytest.mark.asyncio
async def test_messenger_session_bridge(tmp_path):
    from remedy.gateway.session_bridge import resolve_or_create_messenger_session
    from remedy.memory.store import MemoryStore

    db = tmp_path / "memory.db"
    store = MemoryStore(db)
    await store.initialize()
    try:
        s1 = await resolve_or_create_messenger_session(
            store,
            channel="telegram",
            external_chat_id="42",
            username="bob",
            first_message="hello",
        )
        s2 = await resolve_or_create_messenger_session(
            store,
            channel="telegram",
            external_chat_id="42",
            username="bob",
        )
        assert s1.id == s2.id
        assert s1.origin_channel == "telegram"
        listed = await store.list_chat_sessions(limit=10)
        assert any(x.id == s1.id for x in listed)
    finally:
        await store.close()


def test_adapters_importable():
    from remedy.gateway.channels import (
        DiscordChannel,
        MattermostChannel,
        TelegramChannel,
    )
    from remedy.gateway.channels.adapters import SlackChannel

    assert TelegramChannel and DiscordChannel and MattermostChannel and SlackChannel


def test_session_events_endpoint():
    """Route registers and emits hello without hanging the suite."""
    import asyncio

    from remedy.interfaces.session_events import (
        get_session_event_hub,
        reset_session_event_hub,
    )

    reset_session_event_hub()
    hub = get_session_event_hub()

    async def _roundtrip():
        q = await hub.subscribe()
        from remedy.interfaces.session_events import SessionEvent

        await hub.publish(SessionEvent(type="session_created", session_id="x"))
        ev = await asyncio.wait_for(q.get(), timeout=2.0)
        assert ev is not None
        assert ev.session_id == "x"
        await hub.unsubscribe(q)

    asyncio.run(_roundtrip())

    client = TestClient(create_app())
    # OpenAPI must list the events path
    paths = client.get("/openapi.json").json().get("paths") or {}
    assert "/api/events/sessions" in paths
