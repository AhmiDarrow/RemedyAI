"""Connect lifecycle: default off; wildcard bind refused when enabling."""

from __future__ import annotations

import pytest

from remedy.connect.lifecycle import (
    connect_listening_addr,
    drop_all_sessions,
    drop_sessions_for_device,
    maybe_start_connect,
    stop_connect,
)
from remedy.connect.server import bind_writer_device, register_writer, unregister_writer


class _SinkWriter:
    def __init__(self) -> None:
        self.closed = False

    def write(self, _data: bytes) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def drain(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stop_gateway():
    yield
    drop_all_sessions()
    stop_connect()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


def test_connect_default_off_is_noop(home):
    maybe_start_connect(None, {}, api_key="k", sidecar_port=7400)
    assert connect_listening_addr() is None
    maybe_start_connect(
        None,
        {"connect_enabled": False, "connect_bind_host": "127.0.0.1"},
        api_key="k",
        sidecar_port=7400,
    )
    assert connect_listening_addr() is None
    maybe_start_connect(None, None, api_key="k", sidecar_port=7400)
    assert connect_listening_addr() is None


def test_wildcard_bind_refused_when_enabling(home):
    maybe_start_connect(
        None,
        {
            "connect_enabled": True,
            "connect_bind_host": "0.0.0.0",
            "connect_bind_port": 7401,
        },
        api_key="k",
        sidecar_port=7400,
    )
    assert connect_listening_addr() is None
    maybe_start_connect(
        None,
        {"connect_enabled": True, "connect_bind_host": "*", "connect_bind_port": 7401},
        api_key="k",
        sidecar_port=7400,
    )
    assert connect_listening_addr() is None


def test_missing_bind_host_does_not_start(home):
    maybe_start_connect(
        None,
        {"connect_enabled": True, "connect_bind_host": "", "connect_bind_port": 7401},
        api_key="k",
        sidecar_port=7400,
    )
    assert connect_listening_addr() is None


def test_settings_apply_wildcard_raises(home):
    from remedy.interfaces.settings_apply import apply_settings_update

    async def _run():
        with pytest.raises(ValueError, match="wildcard|chosen IPv4"):
            await apply_settings_update(
                {"connect_enabled": True, "connect_bind_host": "0.0.0.0"}
            )

    import asyncio

    asyncio.run(_run())


def test_revoke_device_a_does_not_drop_device_b(home):
    a, b = _SinkWriter(), _SinkWriter()
    register_writer(a)
    bind_writer_device(a, "aaaaaaaaaaaaaaaa")
    register_writer(b)
    bind_writer_device(b, "bbbbbbbbbbbbbbbb")
    try:
        drop_sessions_for_device("aaaaaaaaaaaaaaaa")
        assert a.closed is True
        assert b.closed is False
    finally:
        unregister_writer(a)
        unregister_writer(b)


def test_revoke_unmapped_writers_fail_closed(home):
    """No writer→device map: revoke must not leave live sockets up."""
    a, b = _SinkWriter(), _SinkWriter()
    register_writer(a)
    register_writer(b)
    try:
        drop_sessions_for_device("aaaaaaaaaaaaaaaa")
        assert a.closed is True
        assert b.closed is True
    finally:
        unregister_writer(a)
        unregister_writer(b)


def test_revoke_empty_id_fails_closed(home):
    a, b = _SinkWriter(), _SinkWriter()
    register_writer(a)
    bind_writer_device(a, "aaaaaaaaaaaaaaaa")
    register_writer(b)
    bind_writer_device(b, "bbbbbbbbbbbbbbbb")
    try:
        drop_sessions_for_device("")
        assert a.closed is True
        assert b.closed is True
    finally:
        unregister_writer(a)
        unregister_writer(b)


def test_revoke_keeps_other_device_when_one_writer_unmapped(home):
    a, b, unknown = _SinkWriter(), _SinkWriter(), _SinkWriter()
    register_writer(a)
    bind_writer_device(a, "aaaaaaaaaaaaaaaa")
    register_writer(b)
    bind_writer_device(b, "bbbbbbbbbbbbbbbb")
    register_writer(unknown)
    try:
        drop_sessions_for_device("aaaaaaaaaaaaaaaa")
        assert a.closed is True
        assert b.closed is False
        assert unknown.closed is True
    finally:
        unregister_writer(a)
        unregister_writer(b)
        unregister_writer(unknown)


def test_pause_still_drops_every_live_socket(home):
    a, b = _SinkWriter(), _SinkWriter()
    register_writer(a)
    bind_writer_device(a, "aaaaaaaaaaaaaaaa")
    register_writer(b)
    bind_writer_device(b, "bbbbbbbbbbbbbbbb")
    try:
        drop_all_sessions()
        assert a.closed is True
        assert b.closed is True
    finally:
        unregister_writer(a)
        unregister_writer(b)


def test_revoke_route_drops_only_that_device(home, monkeypatch):
    from fastapi.testclient import TestClient

    from remedy.connect.pair import complete_pair, parse_pair_secret, start_pair
    from remedy.connect.store import get_device
    from remedy.interfaces.api import create_app

    token = "tok-connect-test-not-a-secret"
    monkeypatch.setenv("REMEDY_API_AUTH", "1")
    id_a = complete_pair(
        parse_pair_secret(start_pair(loopback=True, bind_host="127.0.0.1", bind_port=7401)),
        b"\x11" * 32,
        "phone-a",
    )
    id_b = complete_pair(
        parse_pair_secret(start_pair(loopback=True, bind_host="127.0.0.1", bind_port=7401)),
        b"\x22" * 32,
        "phone-b",
    )
    a, b = _SinkWriter(), _SinkWriter()
    register_writer(a)
    bind_writer_device(a, id_a)
    register_writer(b)
    bind_writer_device(b, id_b)
    try:
        client = TestClient(create_app(api_key=token))
        r = client.post(
            f"/api/connect/devices/{id_a}/revoke",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json().get("revoked") is True
        assert a.closed is True
        assert b.closed is False
        rec_a = get_device(id_a)
        rec_b = get_device(id_b)
        assert rec_a is not None and rec_a.get("revoked") is True
        assert rec_b is not None and rec_b.get("revoked") is False
    finally:
        unregister_writer(a)
        unregister_writer(b)
