"""Slim unit tests for multi-messenger adapters (no live network)."""

from __future__ import annotations

import pytest

from remedy.gateway.channels.allowlist import is_allowed, parse_ids
from remedy.gateway.channels.discord import DiscordChannel
from remedy.gateway.channels.google_chat import GoogleChatChannel
from remedy.gateway.channels.matrix import MatrixChannel
from remedy.gateway.channels.mattermost import MattermostChannel
from remedy.gateway.channels.signal_cli import SignalChannel
from remedy.gateway.channels.slack import SlackChannel
from remedy.gateway.channels.teams import TeamsChannel
from remedy.gateway.channels.whatsapp import WhatsAppChannel
from remedy.gateway.messengers import get_messenger, list_messenger_definitions
from remedy.models import ChannelKind


class _GW:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def test_catalog_ready_channels():
    ready = {m.id for m in list_messenger_definitions() if m.status == "ready"}
    for mid in ("telegram", "discord", "slack", "mattermost", "matrix"):
        assert mid in ready
        m = get_messenger(mid)
        assert m and m.inbound and m.outbound


def test_allowlist_secure_default():
    assert not is_allowed(allowlist=frozenset(), allow_all=False, candidates=["1"])
    assert is_allowed(allowlist=frozenset({"1"}), allow_all=False, candidates=["1", "2"])
    assert is_allowed(allowlist=frozenset(), allow_all=True, candidates=["x"])


def test_parse_ids():
    assert parse_ids("a, b ;c") == frozenset({"a", "b", "c"})


@pytest.mark.asyncio
async def test_discord_stub_send():
    gw = _GW()
    ch = DiscordChannel(gw, bot_token="")
    await ch.start()
    assert await ch.send("hi") is True
    await ch.stop()


@pytest.mark.asyncio
async def test_slack_stub_and_whatsapp_verify():
    gw = _GW()
    sl = SlackChannel(gw, bot_token="")
    await sl.start()
    assert await sl.send("x") is True
    await sl.stop()

    wa = WhatsAppChannel(gw, verify_token="secret")
    assert wa.verify_webhook_challenge("subscribe", "secret", "42") == "42"
    assert wa.verify_webhook_challenge("subscribe", "wrong", "42") is None


@pytest.mark.asyncio
async def test_whatsapp_webhook_emit():
    gw = _GW()
    wa = WhatsAppChannel(gw, allow_from=["15551234567"], allow_all=False)
    n = await wa.handle_webhook_payload(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "15551234567",
                                        "type": "text",
                                        "text": {"body": "hello"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    )
    assert n == 1
    assert len(gw.events) == 1
    assert gw.events[0].payload["message"] == "hello"


@pytest.mark.asyncio
async def test_mattermost_matrix_stub_start():
    gw = _GW()
    mm = MattermostChannel(gw, bot_token="", base_url="")
    await mm.start()
    await mm.stop()
    mx = MatrixChannel(gw, access_token="", homeserver="")
    await mx.start()
    await mx.stop()


def test_channel_kinds_exist():
    for v in (
        "discord",
        "slack",
        "mattermost",
        "matrix",
        "whatsapp",
        "teams",
        "google_chat",
        "signal",
    ):
        assert ChannelKind(v).value == v


@pytest.mark.asyncio
async def test_teams_activity_and_google_chat_event():
    gw = _GW()
    teams = TeamsChannel(gw, app_id="id", app_password="pw", allow_all=True)
    ok = await teams.handle_activity(
        {
            "type": "message",
            "text": "hi teams",
            "serviceUrl": "https://smba.trafficmanager.net/amer/",
            "conversation": {"id": "conv1"},
            "from": {"id": "u1", "name": "User"},
        }
    )
    assert ok is True
    assert gw.events[-1].payload["message"] == "hi teams"
    assert teams._last_conversation_id == "conv1"

    gchat = GoogleChatChannel(gw, access_token="t", allow_all=True)
    ok2 = await gchat.handle_event(
        {
            "type": "MESSAGE",
            "message": {
                "text": "hi gchat",
                "sender": {"name": "users/1", "displayName": "A", "type": "HUMAN"},
                "space": {"name": "spaces/abc"},
            },
            "space": {"name": "spaces/abc"},
        }
    )
    assert ok2 is True
    assert gw.events[-1].payload["message"] == "hi gchat"


@pytest.mark.asyncio
async def test_signal_stub_without_cli():
    gw = _GW()
    sig = SignalChannel(gw, cli_path="signal-cli-not-installed", account="")
    await sig.start()
    assert await sig.send("x", target="+100") is False
    await sig.stop()


def test_catalog_all_have_fields():
    for m in list_messenger_definitions():
        assert m.id and m.name
        assert m.status in ("ready", "partial", "planned")


def test_whatsapp_verify_token_rejects_length_mismatch():
    wa = WhatsAppChannel(_GW(), verify_token="secret")
    assert wa.verify_webhook_challenge("subscribe", "secretx", "42") is None
    assert wa.verify_webhook_challenge("subscribe", "", "42") is None
    assert wa.verify_webhook_challenge("subscribe", "secret", "42") == "42"


def _fake_jwt(payload: dict) -> str:
    import base64
    import json

    def _b64(obj: bytes) -> str:
        return base64.urlsafe_b64encode(obj).rstrip(b"=").decode("ascii")

    header = _b64(b'{"alg":"none","typ":"JWT"}')
    body = _b64(json.dumps(payload).encode("utf-8"))
    return f"{header}.{body}.sig"


def test_teams_jwt_fail_closed_requires_aud_and_exp(monkeypatch):
    import time

    from remedy.gateway.channels import teams as teams_mod

    monkeypatch.delenv("REMEDY_TEAMS_SKIP_JWT", raising=False)
    ch = TeamsChannel(_GW(), app_id="my-app-id", app_password="pw")

    # Missing aud → reject
    tok = _fake_jwt(
        {
            "exp": time.time() + 3600,
            "iss": "https://api.botframework.com",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {tok}") is False

    # Wrong aud → reject
    tok = _fake_jwt(
        {
            "aud": "other-app",
            "exp": time.time() + 3600,
            "iss": "https://api.botframework.com",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {tok}") is False

    # Expired → reject
    tok = _fake_jwt(
        {
            "aud": "my-app-id",
            "exp": time.time() - 120,
            "iss": "https://api.botframework.com",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {tok}") is False

    # Missing exp → reject
    tok = _fake_jwt(
        {
            "aud": "my-app-id",
            "iss": "https://api.botframework.com",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {tok}") is False

    # Valid structure (still no signature verify — residual risk)
    tok = _fake_jwt(
        {
            "aud": "my-app-id",
            "exp": time.time() + 3600,
            "iss": "https://api.botframework.com",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {tok}") is True

    # api:// prefix form of aud
    tok = _fake_jwt(
        {
            "aud": "api://my-app-id",
            "exp": time.time() + 3600,
            "iss": "https://login.microsoftonline.com/tid/v2.0",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {tok}") is True

    # Bad issuer host
    tok = _fake_jwt(
        {
            "aud": "my-app-id",
            "exp": time.time() + 3600,
            "iss": "https://evil.example.com/",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {tok}") is False

    # Helper unit
    assert teams_mod._jwt_claims_structurally_valid(  # noqa: SLF001
        {"aud": "x", "exp": time.time() + 10, "iss": "https://sts.windows.net/t/"},
        app_id="x",
    )


def test_google_chat_auth_length_mismatch_false_not_raise():
    ch = GoogleChatChannel(_GW(), access_token="long-token-value")
    assert ch.verify_inbound_auth("Bearer short") is False
    assert ch.verify_inbound_auth("Bearer long-token-value") is True
    assert ch.verify_inbound_auth(None) is False

