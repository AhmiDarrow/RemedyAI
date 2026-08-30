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


def test_connect_me_includes_null_session_id_when_idle(home):
    from remedy.core.computer.host_bridge import get_host_bridge

    bridge = get_host_bridge()
    prev = bridge.focused_session_id()
    try:
        bridge.set_focused_session(None)
        client = _client()
        r = client.get("/connect/me", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert "session_id" in body
        assert body.get("session_id") in (None, "")
        assert "turn_active" in body
        assert body.get("turn_active") is False
        blob = str(body)
        assert "local_api_token" not in blob
        assert "Bearer" not in blob
        assert TOKEN not in blob
        assert "ps=" not in blob
    finally:
        bridge.set_focused_session(prev)


def test_connect_me_includes_streaming_session_id(home):
    from remedy.core.stream_lock import acquire_stream_lock, release_stream_lock

    client = _client()
    acquire_stream_lock(home, "sid-connect-me-stream")
    try:
        r = client.get("/connect/me", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert body.get("session_id") == "sid-connect-me-stream"
        assert body.get("turn_active") is True
        alias = client.get("/api/connect/me", headers=_auth())
        assert alias.status_code == 200
        assert alias.json().get("session_id") == "sid-connect-me-stream"
    finally:
        release_stream_lock(home, "sid-connect-me-stream")


def test_connect_me_prefers_focused_session_when_it_is_streaming(home):
    from remedy.core.computer.host_bridge import get_host_bridge
    from remedy.core.stream_lock import acquire_stream_lock, release_stream_lock

    bridge = get_host_bridge()
    prev = bridge.focused_session_id()
    client = _client()
    acquire_stream_lock(home, "sid-a")
    acquire_stream_lock(home, "sid-b")
    try:
        bridge.set_focused_session("sid-b")
        r = client.get("/connect/me", headers=_auth())
        assert r.status_code == 200
        assert r.json().get("session_id") == "sid-b"
    finally:
        release_stream_lock(home, "sid-a")
        release_stream_lock(home, "sid-b")
        bridge.set_focused_session(prev)


def test_connect_me_falls_back_to_focused_when_idle(home):
    from remedy.core.computer.host_bridge import get_host_bridge

    bridge = get_host_bridge()
    prev = bridge.focused_session_id()
    try:
        bridge.set_focused_session("sid-focused-idle")
        r = _client().get("/connect/me", headers=_auth())
        assert r.status_code == 200
        assert r.json().get("session_id") == "sid-focused-idle"
    finally:
        bridge.set_focused_session(prev)


def test_connect_stop_aborts_connect_me_session_not_list_row(home):
    from remedy.core.stream_lock import acquire_stream_lock, release_stream_lock

    client = _client()
    idle = client.post("/api/stop", headers=_auth())
    assert idle.status_code == 200
    assert idle.json().get("status") == "idle"
    assert idle.json().get("session_id") in (None, "")

    acquire_stream_lock(home, "sid-connect-stop")
    try:
        live = client.post("/api/stop", headers=_auth())
        assert live.status_code == 200
        body = live.json()
        assert body.get("session_id") == "sid-connect-stop"
        assert body.get("status") == "aborted"
        assert body.get("reason") == "stop"
    finally:
        release_stream_lock(home, "sid-connect-stop")
