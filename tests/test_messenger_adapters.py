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

    from remedy.gateway.channels import jwt_rs256 as jwks_mod
    from remedy.gateway.channels import teams as teams_mod

    monkeypatch.delenv("REMEDY_TEAMS_SKIP_JWT", raising=False)
    # Structure-only path for claim tests (signature covered separately)
    monkeypatch.setenv("REMEDY_TEAMS_SKIP_JWKS", "1")
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

    # Valid structure (JWKS skipped via env for this claim suite)
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
    monkeypatch.delenv("REMEDY_TEAMS_SKIP_JWKS", raising=False)
    _ = jwks_mod  # import used by signature suite


def test_teams_jwt_rs256_signature_required(monkeypatch):
    """Forged unsigned JWT must fail when JWKS verify is on (S-MSG-01)."""
    import json
    import time

    from remedy.gateway.channels.jwt_rs256 import (
        b64url_encode,
        clear_test_rsa_keys,
        inject_test_rsa_key,
        verify_jwt_rs256_jwks,
    )

    monkeypatch.delenv("REMEDY_TEAMS_SKIP_JWT", raising=False)
    monkeypatch.delenv("REMEDY_TEAMS_SKIP_JWKS", raising=False)

    # Deterministic 1024-bit RSA (seed 42) — public only injected for verify
    n_b64 = (
        "bpQFAK6Xu7a1pUYfFGNS_0fqnz9wdIW-_5bCBHXIYvy5kwALgdRY1X31gcyO2nJwCe7tksbMkrHMox1UTIN8GLuqYFmYqBc4f_hrYNA4WoDqCofOcZxOiiVLYPUio1lV-VcQdXs88dMjNy8NbyworNy4uw85O8aq2SHGgv9u8Dc"
    )
    e_b64 = "AQAB"
    # Pre-signed token for aud=my-app-id (matching private d; see jwt_rs256 tests)
    good_token = (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InRlc3QtMSJ9."
        "eyJhdWQiOiJteS1hcHAtaWQiLCJleHAiOjk5OTk5OTk5OTksImlzcyI6Imh0dHBzOi8vYXBpLmJvdGZyYW1ld29yay5jb20ifQ."
        "P_Suow7oRzjfGOPJDZiXNlyyKeWKB0M3er3Yw9VKfkF92XZtngAbvjfeEavwNsoGItr01xo8mu9KkN2-Gq9CO5JBKG6ngGYSTf18UN6phGbAow1Uidh52kt8i6XXxAUpHS0rEioU9jWM0mbumk8V1YNy0M-9QygknSHD33ANfr0"
    )

    clear_test_rsa_keys()
    inject_test_rsa_key(kid="test-1", n_b64=n_b64, e_b64=e_b64)
    assert verify_jwt_rs256_jwks(good_token, allow_network=False) is True

    ch = TeamsChannel(_GW(), app_id="my-app-id", app_password="pw")
    assert ch.verify_inbound_auth(f"Bearer {good_token}") is True

    # Unsigned / alg=none style forgery rejected
    forged = _fake_jwt(
        {
            "aud": "my-app-id",
            "exp": time.time() + 3600,
            "iss": "https://api.botframework.com",
        }
    )
    assert ch.verify_inbound_auth(f"Bearer {forged}") is False

    # Wrong signature (tamper payload)
    parts = good_token.split(".")
    bad_payload = b64url_encode(
        json.dumps(
            {
                "aud": "my-app-id",
                "exp": 9999999999,
                "iss": "https://api.botframework.com",
                "evil": 1,
            },
            separators=(",", ":"),
        ).encode()
    )
    tampered = f"{parts[0]}.{bad_payload}.{parts[2]}"
    assert ch.verify_inbound_auth(f"Bearer {tampered}") is False

    clear_test_rsa_keys()


def test_google_chat_auth_length_mismatch_false_not_raise():
    ch = GoogleChatChannel(_GW(), access_token="long-token-value")
    assert ch.verify_inbound_auth("Bearer short") is False
    assert ch.verify_inbound_auth("Bearer long-token-value") is True
    assert ch.verify_inbound_auth(None) is False


def test_whatsapp_webhook_rejects_oversized_body():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from remedy.interfaces.routes.webhooks import register_webhook_routes
    from remedy.models import ChannelKind

    class _WA:
        def verify_signature(self, *_a, **_k):
            raise AssertionError("must not verify oversized body")

        async def handle_webhook_payload(self, *_a, **_k):
            raise AssertionError("must not handle oversized body")

    class _FakeGW:
        def get_channel(self, kind):
            if kind == ChannelKind.WHATSAPP:
                return _WA()
            return None

    app = FastAPI()
    register_webhook_routes(app, gateway=_FakeGW())
    client = TestClient(app)
    r = client.post("/api/webhooks/whatsapp", content=b"x" * (2 * 1024 * 1024 + 8))
    assert r.status_code == 413


def test_google_chat_webhook_rejects_oversized_content_length():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from remedy.interfaces.routes.webhooks import register_webhook_routes
    from remedy.models import ChannelKind

    class _FakeGW:
        def get_channel(self, kind):
            if kind == ChannelKind.GOOGLE_CHAT:
                return GoogleChatChannel(self, access_token="secret-token", allow_all=True)
            return None

        async def emit(self, event):
            return None

    app = FastAPI()
    register_webhook_routes(app, gateway=_FakeGW())
    client = TestClient(app)
    r = client.post(
        "/api/webhooks/google_chat",
        content=b"{}",
        headers={"Content-Length": str(3 * 1024 * 1024)},
    )
    assert r.status_code == 413


def test_google_chat_webhook_challenge_does_not_skip_auth_on_message():
    """MESSAGE bodies with a challenge key must still require Bearer auth."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from remedy.interfaces.routes.webhooks import register_webhook_routes
    from remedy.models import ChannelKind

    class _FakeGW:
        def __init__(self):
            self.events = []

        def get_channel(self, kind):
            if kind == ChannelKind.GOOGLE_CHAT:
                return GoogleChatChannel(self, access_token="secret-token", allow_all=True)
            return None

        async def emit(self, event):
            self.events.append(event)

    app = FastAPI()
    register_webhook_routes(app, gateway=_FakeGW())
    client = TestClient(app)

    # Explicit verification shape — allowed without auth
    r0 = client.post(
        "/api/webhooks/google_chat",
        json={"type": "URL_VERIFICATION", "challenge": "abc"},
    )
    assert r0.status_code == 200
    assert r0.json().get("challenge") == "abc"

    # MESSAGE with challenge must not skip auth
    r1 = client.post(
        "/api/webhooks/google_chat",
        json={
            "type": "MESSAGE",
            "challenge": "sneaky",
            "message": {
                "text": "hi",
                "sender": {"name": "users/1", "displayName": "A", "type": "HUMAN"},
                "space": {"name": "spaces/abc"},
            },
            "space": {"name": "spaces/abc"},
        },
    )
    assert r1.status_code == 401

    r2 = client.post(
        "/api/webhooks/google_chat",
        json={
            "type": "MESSAGE",
            "message": {
                "text": "hi",
                "sender": {"name": "users/1", "displayName": "A", "type": "HUMAN"},
                "space": {"name": "spaces/abc"},
            },
            "space": {"name": "spaces/abc"},
        },
        headers={"Authorization": "Bearer secret-token"},
    )
    assert r2.status_code == 200

