"""Connect lifecycle: default off; wildcard bind refused when enabling."""

from __future__ import annotations

import pytest

from remedy.connect.lifecycle import connect_listening_addr, maybe_start_connect, stop_connect


@pytest.fixture(autouse=True)
def _stop_gateway():
    yield
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
