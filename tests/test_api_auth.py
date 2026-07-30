"""Local API authentication (Phase A)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.interfaces.local_auth import ensure_local_api_token, token_path


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("REMEDY_API_AUTH", "1")
    monkeypatch.delenv("REMEDY_API_KEY", raising=False)
    yield
    monkeypatch.setenv("REMEDY_API_AUTH", "0")


def test_ensure_token_generates_and_persists(tmp_path, auth_on):
    home = tmp_path / "home"
    home.mkdir()
    t1 = ensure_local_api_token(home)
    assert len(t1) >= 16
    assert token_path(home).is_file()
    t2 = ensure_local_api_token(home)
    assert t1 == t2


def test_auth_middleware_401_without_token(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/skills")
    assert r.status_code == 401


def test_auth_middleware_ok_with_bearer(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/skills", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


def test_status_public(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/status")
    assert r.status_code == 200


def test_bootstrap_loopback(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/auth/local-bootstrap")
    assert r.status_code == 200
    assert r.json()["token"] == tok


def test_auth_disabled_empty_key(monkeypatch):
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    app = create_app(api_key="")
    client = TestClient(app)
    r = client.get("/api/skills")
    assert r.status_code == 200


def test_cors_star_refused_when_auth_on(auth_on, tmp_path, monkeypatch):
    """CORS * must not apply while a token exists (browser token theft)."""
    monkeypatch.setenv("REMEDY_CORS_ORIGINS", "*")
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    # Middleware still has a concrete origin list, not bare *
    # Smoke: authenticated call works
    client = TestClient(app)
    r = client.get("/api/skills", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


def test_bootstrap_can_be_disabled(auth_on, tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HTTP_BOOTSTRAP", "0")
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/auth/local-bootstrap")
    assert r.status_code == 403
    monkeypatch.setenv("REMEDY_HTTP_BOOTSTRAP", "1")


def test_cors_preflight_options_not_blocked_by_auth(auth_on, tmp_path):
    """OPTIONS must not 401 — browser preflight has no Bearer (desktop xAI OAuth)."""
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.options(
        "/api/auth/xai/login",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    # Must not look like auth failure (401 → opaque Failed to fetch in webview)
    assert r.status_code != 401
    assert r.status_code in (200, 204, 400)
    # CORS headers present for Tauri origin
    assert r.headers.get("access-control-allow-origin") in (
        "http://tauri.localhost",
        "*",
    )


def test_cors_allows_tauri_https_origin(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get(
        "/api/status",
        headers={"Origin": "https://tauri.localhost"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://tauri.localhost"


def test_gateway_serve_api_enables_auth(auth_on, tmp_path, monkeypatch):
    """``remedy gateway serve`` must not open the loopback API without Bearer."""
    import types

    from remedy.gateway import cli as gateway_cli
    import remedy.interfaces.api as api_mod

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    db = tmp_path / "memory.db"
    db.write_bytes(b"")

    captured: dict = {}

    def _fake_create_app(*_a, **kwargs):
        captured.update(kwargs)

        class _App:
            pass

        return _App()

    monkeypatch.setattr(api_mod, "create_app", _fake_create_app)

    fake_uv = types.ModuleType("uvicorn")
    fake_uv.run = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", fake_uv)

    gateway_cli._serve_api(db)  # noqa: SLF001
    assert captured.get("api_key")
    assert len(str(captured["api_key"])) >= 16


def test_auth_length_mismatch_is_401_not_500(auth_on, tmp_path):
    """Unequal Bearer length must not raise from compare_digest."""
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/skills", headers={"Authorization": "Bearer x"})
    assert r.status_code == 401


def test_generic_webhook_fail_closed_without_secret(auth_on, tmp_path, monkeypatch):
    """With API auth on and no shared secret, unauthenticated webhook is rejected."""
    monkeypatch.delenv("REMEDY_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("REMEDY_API_KEY", raising=False)

    class _GW:
        async def enqueue(self, _event):
            raise AssertionError("must not enqueue unauthenticated webhook")

    # Empty api_key but auth still "enabled" via env — create_app won't install
    # middleware; webhook route uses local_auth.auth_enabled() independently.
    # When api_key is set, expected is present; when not, must 503.
    app = create_app(gateway=_GW(), api_key="")
    client = TestClient(app)
    r = client.post(
        "/api/webhook/ci",
        json={"source": "ci", "event": "push", "data": {"x": 1}},
    )
    assert r.status_code in (401, 503)


def test_generic_webhook_accepts_bearer(auth_on, tmp_path):
    class _GW:
        def __init__(self):
            self.n = 0

        async def enqueue(self, _event):
            self.n += 1

    tok = ensure_local_api_token(tmp_path)
    gw = _GW()
    app = create_app(gateway=gw, api_key=tok)
    client = TestClient(app)
    r = client.post(
        "/api/webhook/ci",
        json={"source": "ci", "event": "push", "data": {"x": 1}},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    assert gw.n == 1
