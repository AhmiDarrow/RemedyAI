"""A crashed Connect gateway thread comes back on its own; a stop does not."""

from __future__ import annotations

import asyncio
import socket
import time

import pytest

from remedy.connect import lifecycle
from remedy.connect.lifecycle import (
    connect_listening_addr,
    gateway_health,
    maybe_start_connect,
    stop_connect,
)


@pytest.fixture(autouse=True)
def _stop_gateway():
    yield
    stop_connect()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _wait(pred, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _serving() -> bool:
    return bool(gateway_health().get("serving")) and connect_listening_addr() is not None


def _start(port: int) -> None:
    maybe_start_connect(
        None,
        {
            "connect_enabled": True,
            "connect_bind_host": "127.0.0.1",
            "connect_bind_port": port,
            "connect_rdv_enabled": False,
        },
        api_key="tok-connect-test-not-a-secret",
        sidecar_port=7400,
    )


def _crash_gateway_loop() -> None:
    loop = lifecycle._loop
    assert loop is not None

    def _boom() -> None:
        raise SystemExit("simulated gateway loop death")

    loop.call_soon_threadsafe(_boom)


def test_gateway_thread_crash_self_heals(home, monkeypatch):
    monkeypatch.setattr(lifecycle, "_HEAL_DELAYS_S", (0.2, 0.2, 0.2, 0.2))
    port = _free_port()
    _start(port)
    assert _wait(lambda: _serving(), 15.0)
    first = lifecycle._thread
    assert first is not None

    _crash_gateway_loop()
    assert _wait(lambda: not first.is_alive())
    # The listener comes back on a new thread with the same bind.
    assert _wait(lambda: _serving() and (connect_listening_addr() or ("", 0))[1] == port, 20.0)
    assert _wait(lambda: lifecycle._thread is not None and lifecycle._thread is not first)
    assert _wait(lambda: gateway_health()["crashes"] >= 1, 10.0)
    health = gateway_health()
    assert health["crashes"] >= 1
    assert "simulated gateway loop death" in health["last_crash"]
    assert health["thread_alive"] is True


def test_stop_connect_is_not_treated_as_a_crash(home, monkeypatch):
    monkeypatch.setattr(lifecycle, "_HEAL_DELAYS_S", (0.2, 0.2, 0.2, 0.2))
    port = _free_port()
    _start(port)
    assert _wait(lambda: _serving(), 15.0)
    stop_connect()
    time.sleep(0.8)
    assert connect_listening_addr() is None
    assert gateway_health()["crashes"] == 0
    assert lifecycle._restart_waiter is None or not lifecycle._restart_waiter.is_alive()


def test_owner_restart_resets_crash_budget(home, monkeypatch):
    monkeypatch.setattr(lifecycle, "_HEAL_DELAYS_S", (0.2, 0.2, 0.2, 0.2))
    port = _free_port()
    _start(port)
    assert _wait(lambda: _serving(), 15.0)
    _crash_gateway_loop()
    assert _wait(lambda: gateway_health()["crashes"] == 1)
    assert _wait(lambda: _serving() and (connect_listening_addr() or ("", 0))[1] == port, 20.0)
    stop_connect()
    _start(port)
    assert _wait(lambda: _serving() and (connect_listening_addr() or ("", 0))[1] == port, 20.0)
    assert gateway_health()["crashes"] == 0


@pytest.mark.asyncio
async def test_gateway_health_shape():
    h = gateway_health()
    assert set(h) >= {"crashes", "last_crash", "healing", "serving", "thread_alive", "listening"}
    await asyncio.sleep(0)
