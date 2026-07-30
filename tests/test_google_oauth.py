"""Google OAuth + Calendar Phase 1 (offline unit tests)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from remedy.assistant import google_oauth as go
from remedy.assistant.providers.google_calendar import GoogleCalendarProvider, _event_time
from remedy.assistant.store import reset_assistant_store
from remedy.interfaces.api import create_app


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    reset_assistant_store()
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    # Clear any process pending oauth
    with go._pending_lock:
        go._pending.clear()
    yield
    reset_assistant_store()
    with go._pending_lock:
        go._pending.clear()


def test_event_time_all_day_vs_datetime():
    from remedy.assistant.providers.google_calendar import _has_tz

    assert _event_time("2026-08-01") == {"date": "2026-08-01"}
    assert _event_time("2026-08-01T10:00:00Z") == {"dateTime": "2026-08-01T10:00:00Z"}
    assert _event_time("2026-08-01T10:00:00-07:00") == {
        "dateTime": "2026-08-01T10:00:00-07:00"
    }
    # Naive local wall time → offset attached (no Google "Missing time zone" 400)
    naive = _event_time("2026-08-01T10:00:00")
    assert "dateTime" in naive
    assert _has_tz(naive["dateTime"])
    assert naive["dateTime"].startswith("2026-08-01T10:00:00")


def test_app_config_save_load(tmp_path):
    go.save_app_config(
        client_id="cid-test",
        client_secret="sec-test",
        home=tmp_path,
    )
    cfg = go.load_app_config(tmp_path)
    assert cfg.client_id == "cid-test"
    assert cfg.client_secret == "sec-test"
    assert cfg.configured()
    pub = cfg.to_public()
    assert pub["client_id_set"] is True
    assert pub["client_secret_set"] is True
    assert "client_id" not in pub  # never expose raw id in public? actually we only expose booleans
    assert pub["redirect_uri"].endswith("/api/assistant/google/callback")


def test_tokens_atomic_write_no_tmp_left(tmp_path):
    """Google token seal must use temp+replace (no half-written final file / leftover .tmp)."""
    go.save_tokens(
        go.GoogleTokens(
            access_token="ya29.atomic",
            refresh_token="1//rt",
            expires_at=9e12,
            email="a@b.com",
        ),
        home=tmp_path,
    )
    path = go.tokens_path(tmp_path)
    assert path.is_file()
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    loaded = go.load_tokens(tmp_path)
    assert loaded.access_token == "ya29.atomic"
    assert loaded.refresh_token == "1//rt"
    enc = go.tokens_encoding(tmp_path)
    assert enc in ("dpapi", "plain")
    raw = path.read_text(encoding="utf-8")
    if enc == "dpapi":
        assert "ya29.atomic" not in raw



def test_start_oauth_requires_client(tmp_path, monkeypatch):
    from remedy.assistant.store import get_assistant_store, reset_assistant_store

    monkeypatch.delenv("REMEDY_GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("REMEDY_GOOGLE_OAUTH_DEFAULT_CLIENT_ID", raising=False)
    monkeypatch.setattr(go, "DEFAULT_GOOGLE_CLIENT_ID", "")
    reset_assistant_store()
    get_assistant_store(tmp_path).patch_prefs(
        privacy_ai_accepted=True, account_access_accepted=True
    )
    with pytest.raises(ValueError, match="not configured"):
        go.start_oauth(home=tmp_path)


def test_start_oauth_pkce_and_complete(tmp_path):
    from remedy.assistant.store import get_assistant_store, reset_assistant_store

    reset_assistant_store()
    get_assistant_store(tmp_path).patch_prefs(
        privacy_ai_accepted=True, account_access_accepted=True
    )
    go.save_app_config(client_id="my-client", client_secret="s", home=tmp_path)
    start = go.start_oauth(home=tmp_path)
    assert start["status"] == "pending"
    assert "accounts.google.com" in start["auth_url"]
    assert "code_challenge" in start["auth_url"]
    state = start["state"]
    assert go.pending_status(state)["status"] == "pending"

    token_payload = {
        "access_token": "ya29.access",
        "refresh_token": "1//refresh",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": " ".join(go.SCOPES),
    }

    def fake_form(url, data, timeout=30.0):
        assert "oauth2.googleapis.com/token" in url
        assert data.get("code") == "auth-code"
        assert data.get("code_verifier")
        assert data.get("client_id") == "my-client"
        return token_payload

    def fake_userinfo(url, bearer, timeout=30.0):
        assert bearer == "ya29.access"
        return {"email": "user@gmail.com"}

    with (
        patch.object(go, "_http_form", side_effect=fake_form),
        patch.object(go, "_http_get_json", side_effect=fake_userinfo),
    ):
        tokens = go.complete_oauth(code="auth-code", state=state, home=tmp_path)

    assert tokens.connected
    assert tokens.email == "user@gmail.com"
    assert go.load_tokens(tmp_path).refresh_token == "1//refresh"
    assert go.pending_status(state)["status"] == "connected"
    pub = go.public_status(tmp_path)
    assert pub.get("tokens_encoding") in ("dpapi", "plain")
    assert "tokens_encoding" in pub
    # State is single-use — no code_verifier left; second exchange must fail.
    with (
        patch.object(go, "_http_form", side_effect=fake_form),
        patch.object(go, "_http_get_json", side_effect=fake_userinfo),
        pytest.raises(ValueError, match="already used|Invalid or expired"),
    ):
        go.complete_oauth(code="auth-code-2", state=state, home=tmp_path)
    # Linked account
    from remedy.assistant.store import get_assistant_store

    store = get_assistant_store(tmp_path)
    accts = store.accounts_public()
    assert any(a.get("provider") == "google" and a.get("status") == "connected" for a in accts)


def test_refresh_access_token(tmp_path):
    go.save_app_config(client_id="c", client_secret="s", home=tmp_path)
    go.save_tokens(
        go.GoogleTokens(
            access_token="old",
            refresh_token="rt",
            expires_at=1.0,
            email="a@b.com",
        ),
        home=tmp_path,
    )

    def fake_form(url, data, timeout=30.0):
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "rt"
        return {"access_token": "new-access", "expires_in": 3600}

    with patch.object(go, "_http_form", side_effect=fake_form):
        t = go.refresh_access_token(tmp_path)
    assert t.access_token == "new-access"
    assert go.get_valid_access_token(tmp_path) == "new-access"


def test_calendar_list_uses_bearer(tmp_path):
    go.save_tokens(
        go.GoogleTokens(
            access_token="tok",
            refresh_token="rt",
            expires_at=9e12,
            email="u@g.com",
        ),
        home=tmp_path,
    )
    cal = GoogleCalendarProvider(home=tmp_path)

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {
                    "items": [
                        {
                            "id": "e1",
                            "summary": "Standup",
                            "start": {"dateTime": "2026-08-01T09:00:00Z"},
                            "end": {"dateTime": "2026-08-01T09:30:00Z"},
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(req, timeout=30):
        assert "Bearer tok" in req.headers.get("Authorization", "")
        return FakeResp()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        events = cal.list_events(
            time_min="2026-08-01T00:00:00Z",
            time_max="2026-08-02T00:00:00Z",
        )
    assert len(events) == 1
    assert events[0].title == "Standup"


def test_api_google_routes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'name = "Remedy"\nsetup_completed = true\nhome_dir = "{home.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    from remedy.interfaces import api_support

    monkeypatch.setattr(api_support, "_default_config_path", lambda: home / "config.toml")
    monkeypatch.setattr(api_support, "_find_config_path", lambda: home / "config.toml")
    reset_assistant_store()

    client = TestClient(create_app())
    r = client.get("/api/assistant/google")
    assert r.status_code == 200
    assert r.json()["connected"] is False

    r2 = client.put(
        "/api/assistant/google/app",
        json={"client_id": "cid-from-api", "client_secret": "sec"},
    )
    assert r2.status_code == 200
    assert r2.json()["app"]["client_id_set"] is True

    # Consent required before OAuth start
    r3_block = client.post("/api/assistant/google/oauth/start", json={})
    assert r3_block.status_code == 403

    client.put(
        "/api/settings",
        json={
            "assistant": {
                "privacy_ai_accepted": True,
                "account_access_accepted": True,
            }
        },
    )
    r3 = client.post("/api/assistant/google/oauth/start", json={})
    assert r3.status_code == 200, r3.text
    body = r3.json()
    assert body["auth_url"]
    assert body["state"]

    # Callback without pending state fails gracefully
    r4 = client.get("/api/assistant/google/callback?code=x&state=bogus")
    assert r4.status_code == 400
    assert "text/html" in r4.headers.get("content-type", "")
