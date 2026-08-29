"""Management routes on :7400. Pair start is loopback-only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remedy.connect.lifecycle import stop_connect
from remedy.interfaces.api import create_app

TOKEN = "tok-connect-test-not-a-secret"


@pytest.fixture(autouse=True)
def _stop_gateway():
    yield
    stop_connect()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_API_AUTH", "1")
    return tmp_path


def _client(api_key: str = TOKEN, **kwargs) -> TestClient:
    app = create_app(api_key=api_key)
    return TestClient(app, **kwargs)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_get_connect_default_off(home):
    client = _client()
    r = client.get("/api/connect", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body.get("enabled") is False
    assert body.get("paused") in (False, None)
    assert "devices" in body
    blob = str(body)
    assert "local_api_token" not in blob
    assert "Bearer" not in blob
    assert "public_hex" not in blob


def test_pair_start_connect_hop_header_403(home):
    client = _client()
    r = client.post(
        "/api/connect/pair/start",
        headers={**_auth(), "X-Remedy-Connect-Hop": "1"},
    )
    assert r.status_code == 403
    assert "ps=" not in r.text


def test_pair_start_non_loopback_403(home):
    client = _client(client=("10.9.8.7", 12345))
    r = client.post(
        "/api/connect/pair/start",
        headers={**_auth(), "Host": "10.9.8.7:7400"},
    )
    assert r.status_code == 403


def test_pair_start_loopback_after_bind(home):
    client = _client()
    put = client.put(
        "/api/connect",
        headers=_auth(),
        json={"enabled": False, "bind_host": "127.0.0.1", "bind_port": 7401},
    )
    assert put.status_code == 200
    r = client.post("/api/connect/pair/start", headers=_auth())
    assert r.status_code == 200
    qr = r.json().get("qr") or ""
    assert "remedy-connect/1" in qr
    assert "local_api_token" not in qr
    assert "Bearer" not in qr
    assert TOKEN not in qr


def test_wildcard_enable_rejected(home):
    client = _client()
    r = client.put(
        "/api/connect",
        headers=_auth(),
        json={"enabled": True, "bind_host": "0.0.0.0", "bind_port": 7401},
    )
    assert r.status_code in (400, 403)
    g = client.get("/api/connect", headers=_auth())
    assert g.json().get("enabled") is False


def test_pause_resume_and_revoke_routes(home):
    client = _client()
    paused = client.post("/api/connect/pause", headers=_auth())
    assert paused.status_code == 200
    assert paused.json().get("paused") is True
    resumed = client.post("/api/connect/resume", headers=_auth())
    assert resumed.status_code == 200
    assert resumed.json().get("paused") is False
    missing = client.post("/api/connect/devices/not-a-real-id/revoke", headers=_auth())
    assert missing.status_code in (400, 404)


def test_addresses_endpoint(home):
    client = _client()
    r = client.get("/api/connect/addresses", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert "addresses" in body
    assert isinstance(body["addresses"], list)


def test_connect_me_not_on_management_surface_as_sidecar_secret(home):
    """GET /api/connect never returns pair secrets or host private material."""
    client = _client()
    r = client.get("/api/connect", headers=_auth())
    text = r.text.lower()
    assert "ps=" not in text
    assert "private" not in text
