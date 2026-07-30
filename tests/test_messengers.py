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
    public_fields_from_section,
    redact_messenger_secrets,
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
    """Token never echoes; enable + secrets land under REMEDY_HOME only."""
    home = (tmp_path / "home").resolve()
    home.mkdir()
    (home / "config.toml").write_text(
        f'name = "Remedy"\nenabled_channels = ["cli"]\nsetup_completed = true\n'
        f'home_dir = "{home.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    from remedy.interfaces import api_support
    from remedy.interfaces.messenger_settings import apply_messengers_update

    api_support.invalidate_config_cache()

    # Unit path: apply_messengers_update is the product truth for enable+token
    cfg: dict = {
        "enabled_channels": ["cli"],
        "setup_completed": True,
        "home_dir": str(home),
    }
    apply_messengers_update(
        cfg,
        {
            "telegram": {
                "enabled": True,
                "bot_token": "secret-token-xyz",
                "allow_chat_ids": "111,222",
            }
        },
        home_path=home,
    )
    assert "telegram" in cfg.get("enabled_channels", [])
    api_support._write_config(home / "config.toml", cfg)
    disk = (home / "config.toml").read_text(encoding="utf-8")
    assert "telegram" in disk
    assert "secret-token-xyz" not in disk

    # API path: GET must not echo token; token_set reflects secret store
    client = TestClient(create_app())
    g = client.get("/api/settings")
    assert g.status_code == 200
    assert "secret-token-xyz" not in g.text
    tg = next(m for m in g.json()["messengers"] if m["id"] == "telegram")
    assert tg.get("token_set") is True
    assert tg.get("enabled") is True

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


def test_redact_messenger_secrets_strips_telegram_url_token():
    tok = "7123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    url = f"https://api.telegram.org/bot{tok}/getUpdates"
    scrubbed = redact_messenger_secrets(f"ClientConnectorError: {url}")
    assert tok not in scrubbed
    assert "api.telegram.org/bot" in scrubbed
    assert "[redacted]" in scrubbed
    assert tok not in redact_messenger_secrets(tok)


def test_redact_messenger_secrets_residual_channels():
    """Slack xapp, Discord webhook/bot, Matrix syt_, Bearer must not leak."""
    samples = {
        "xapp": "xapp-1-A0123456789-1234567890123-abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        "webhook": (
            "https://discord.com/api/webhooks/123456789012345678/"
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        ),
        "discord_bot": "MTIzNDU2Nzg5MDEyMzQ1Njc4.GhIjKl.abcdefghijklmnopqrstuvwxyz0123456789",
        "matrix": "syt_abcdefgh_abcdefghijklmnopqrstuv_abc123",
        "bearer": "Authorization failed: Bearer mfa.abcdefghijklmnopqrstuvwxyz012345",
    }
    for label, secret in samples.items():
        scrubbed = redact_messenger_secrets(f"poll error: {secret}")
        # Token body must not appear verbatim after scrub
        assert secret not in scrubbed, f"{label} leaked: {scrubbed}"
        assert "redacted" in scrubbed.lower(), f"{label} missing redaction: {scrubbed}"


def test_public_fields_never_echo_legacy_plaintext_token():
    fields = public_fields_from_section(
        "telegram",
        {
            "bot_token": "should-never-appear",
            "allow_chat_ids": ["1", "2"],
            "allow_all": False,
        },
    )
    assert "bot_token" not in fields
    assert "should-never-appear" not in str(fields)
    assert fields.get("allow_chat_ids") == ["1", "2"]
    assert fields.get("allow_all") is False


def test_public_fields_unknown_channel_strips_secret_suffixes():
    fields = public_fields_from_section(
        "not_a_real_channel",
        {
            "bot_token": "x",
            "app_password": "y",
            "signing_secret": "z",
            "channel_id": "ok",
        },
    )
    assert fields == {"channel_id": "ok"}

