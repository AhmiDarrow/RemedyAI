"""Review follow-ups: encoded-fragment deny bypass, case-preserving paths,
hello key binding, live pane config, rdv supervisor teardown, revoke on the
gateway loop."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


# --- deny --------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/app/command%23z",
        "/api/computer/host%23x",
        "/api/shutdown%23",
        "/api/quit%3Fx=1",
        "/api/app/command%2523z",
        "/api/connect%23/pair/start",
    ],
)
def test_encoded_fragment_or_query_cannot_bypass_hard_deny(path):
    from remedy.connect.deny import connect_forbidden, sanitize_origin_path

    assert sanitize_origin_path(path) is None
    assert connect_forbidden("GET", path, "", None) is not None


def test_sanitize_keeps_case_and_matching_is_case_insensitive():
    from remedy.connect.deny import connect_forbidden, sanitize_origin_path

    assert sanitize_origin_path("/api/workspace/files/README.md") == "/api/workspace/files/README.md"
    assert sanitize_origin_path("/api/sessions/AbC123/abort") == "/api/sessions/AbC123/abort"
    # Hard denies still match regardless of case.
    assert connect_forbidden("GET", "/API/Computer/Host", "", None) is not None


def test_phone_stop_is_a_turn_abort_not_a_server_kill():
    from remedy.connect.deny import connect_forbidden

    assert connect_forbidden("POST", "/api/stop", "", None) is None
    assert connect_forbidden("POST", "/api/shutdown", "", None) == "server:kill"
    assert connect_forbidden("GET", "/api/app/command", "take=1", None) == "server:kill"


@pytest.mark.parametrize(
    "path",
    ["/api/memory/search", "/api/updates/check", "/api/claimidx/ops", "/api/brand-new-thing"],
)
def test_unlisted_api_families_fail_closed(path):
    from remedy.connect.deny import connect_forbidden

    all_on = {
        "live_ui": True,
        "chat": True,
        "approvals": True,
        "sessions": True,
        "rails": True,
        "computer_preview": True,
        "settings_write": True,
    }
    assert connect_forbidden("GET", path, "", all_on) == "unknown:family"


@pytest.mark.parametrize(
    "path",
    [
        "/api/goals",
        "/api/models",
        "/api/ping",
        "/api/partner/status",
        "/api/providers/connected",
        "/api/settings",
        "/api/sessions?limit=20",
        "/api/terminal",
        "/api/approvals",
    ],
)
def test_every_phone_route_is_allowed_with_panes_on(path):
    from remedy.connect.deny import connect_forbidden

    all_on = dict.fromkeys(("live_ui", "chat", "approvals", "sessions", "rails", "computer_preview", "settings_write"), True)
    p, _, q = path.partition("?")
    assert connect_forbidden("GET", p, q, all_on) is None


def test_hive_is_gated_by_chat_pane():
    from remedy.connect.deny import connect_forbidden

    panes_off = {"chat": False}
    assert connect_forbidden("POST", "/api/hive/spawn", "", panes_off) == "pane:chat"
    assert connect_forbidden("POST", "/api/hive/spawn", "", {"chat": True}) is None


# --- pipe.authenticate_payload -----------------------------------------------


@pytest.mark.asyncio
async def test_hello_requires_stored_public_key(home, monkeypatch):
    from remedy.connect.pipe import authenticate_payload
    from remedy.connect.store import save_device

    save_device({"id": "a" * 32, "name": "p", "public_hex": "", "revoked": False})
    with pytest.raises(ValueError):
        await authenticate_payload(b"hello\0" + b"a" * 32, b"\x22" * 32)


@pytest.mark.asyncio
async def test_hello_with_matching_key_is_accepted(home):
    from remedy.connect.pipe import authenticate_payload
    from remedy.connect.store import save_device

    pub = b"\x33" * 32
    save_device({"id": "b" * 32, "name": "p", "public_hex": pub.hex(), "revoked": False})
    rec = await authenticate_payload(b"hello\0" + b"b" * 32, pub)
    assert rec["id"] == "b" * 32


# --- store -------------------------------------------------------------------


def test_revoke_is_visible_in_memory_and_cleared_on_repair(home):
    from remedy.connect.store import is_revoked_live, revoke_device, save_device

    save_device({"id": "c" * 32, "name": "p", "public_hex": "11" * 32, "revoked": False})
    assert is_revoked_live("c" * 32) is False
    revoke_device("c" * 32)
    assert is_revoked_live("c" * 32) is True
    save_device({"id": "c" * 32, "name": "p", "public_hex": "11" * 32, "revoked": False})
    assert is_revoked_live("c" * 32) is False


def test_pause_cache_follows_set_paused(home):
    from remedy.connect.store import is_paused, set_paused

    assert is_paused() is False
    set_paused(True)
    assert is_paused() is True
    set_paused(False)
    assert is_paused() is False


# --- lifecycle / server -----------------------------------------------------


def test_settings_change_updates_live_config_without_rebind(home, monkeypatch):
    from remedy.connect import lifecycle

    monkeypatch.setattr(lifecycle, "listening_addr", lambda: ("127.0.0.1", 7401))
    monkeypatch.setattr(lifecycle, "stop_connect", lambda: None)
    cfg = {
        "connect_enabled": True,
        "connect_bind_host": "127.0.0.1",
        "connect_bind_port": 7401,
        "connect_panes": {"rails": True},
    }
    lifecycle.maybe_start_connect(None, cfg, api_key="k", sidecar_port=7400)
    live = lifecycle.current_config()
    assert live["connect_panes"] == {"rails": True}
    cfg2 = dict(cfg, connect_panes={"rails": False})
    lifecycle.on_connect_settings_changed(cfg2)
    # Same object the gateway holds, now carrying the new pane flags.
    assert lifecycle.current_config() is live
    assert live["connect_panes"] == {"rails": False}


def test_settings_change_rebinds_when_listener_configuration_changes(home, monkeypatch):
    from remedy.connect import lifecycle

    stops: list[bool] = []
    starts: list[dict[str, object]] = []
    lifecycle._publish_config(
        {
            "connect_enabled": True,
            "connect_bind_host": "127.0.0.1",
            "connect_bind_port": 7401,
            "connect_relay_url": "wss://old.invalid",
        }
    )
    monkeypatch.setattr(lifecycle, "listening_addr", lambda: ("127.0.0.1", 7401))
    monkeypatch.setattr(lifecycle, "stop_connect", lambda: stops.append(True))
    monkeypatch.setattr(
        lifecycle,
        "maybe_start_connect",
        lambda _app, config, **_kwargs: starts.append(dict(config or {})),
    )
    updated = {
        "connect_enabled": True,
        "connect_bind_host": "127.0.0.1",
        "connect_bind_port": 7401,
        "connect_relay_url": "wss://new.invalid",
    }

    lifecycle.on_connect_settings_changed(updated)

    assert stops == [True]
    assert starts == [updated]
    assert lifecycle.current_config()["connect_relay_url"] == "wss://new.invalid"


@pytest.mark.asyncio
async def test_stop_connect_server_cancels_rdv_supervisor(home, monkeypatch):
    from remedy.connect import server

    async def fake_rdv(**_k):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(server, "_rdv_supervisor", fake_rdv)
    monkeypatch.setattr(server, "_start_mdns", lambda *a, **k: None)
    await server.start_connect_server(
        "127.0.0.1", 0, sidecar_port=7400, api_key="k", config={"connect_rdv_enabled": True}
    )
    task = server._rdv_task
    assert task is not None and not task.done()
    await server.stop_connect_server()
    assert task.cancelled() or task.done()
    assert server._rdv_task is None


@pytest.mark.asyncio
async def test_stop_connect_server_reaps_listener_from_closed_loop(home, monkeypatch):
    from remedy.connect import server

    socket_closed: list[bool] = []

    class RawSocket:
        def close(self):
            socket_closed.append(True)

    class WrappedSocket:
        _sock = RawSocket()

    class StaleServer:
        sockets = [WrappedSocket()]

        def close(self):
            raise AttributeError("disposed proactor")

        async def wait_closed(self):
            raise AssertionError("wait_closed must not run after close failed")

    monkeypatch.setattr(server, "_extra_servers", [StaleServer()])
    monkeypatch.setattr(server, "_server", None)

    await server.stop_connect_server()

    assert socket_closed == [True]
    assert server._extra_servers == []


@pytest.mark.asyncio
async def test_start_connect_server_reaps_previous_background_tasks(home, monkeypatch):
    from remedy.connect import server

    started: list[asyncio.Event] = []

    async def fake_rdv(**_k):
        stopped = asyncio.Event()
        started.append(stopped)
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(server, "_rdv_supervisor", fake_rdv)
    monkeypatch.setattr(server, "_start_mdns", lambda *a, **k: None)
    await server.start_connect_server(
        "127.0.0.1", 0, sidecar_port=7400, api_key="k", config={"connect_rdv_enabled": True}
    )
    first = server._rdv_task
    assert first is not None
    await asyncio.sleep(0)

    await server.start_connect_server(
        "127.0.0.1", 0, sidecar_port=7400, api_key="k", config={"connect_rdv_enabled": True}
    )
    try:
        assert first.done()
        assert started[0].is_set()
        assert server._rdv_task is not first
    finally:
        await server.stop_connect_server()


@pytest.mark.asyncio
async def test_rdv_supervisor_awaits_session_cleanup_on_cancel(home, monkeypatch):
    from remedy.connect import rdv, server

    opened = asyncio.Event()
    closed = asyncio.Event()

    class FakeMqtt:
        def __init__(self, *_a, **_k):
            pass

        async def connect(self):
            return None

        async def aclose(self):
            closed.set()

    class FakeSession:
        def __init__(self, mqtt, *_a, **_k):
            self.mqtt = mqtt

        async def open(self):
            opened.set()
            return object(), object()

        async def aclose(self):
            await self.mqtt.aclose()

    async def hold_handle(*_a, **_k):
        await asyncio.Future()

    monkeypatch.setattr(rdv, "PUBLIC_RDV_ENDPOINTS", (("example.invalid", 1883),))
    monkeypatch.setattr(rdv, "MqttSession", FakeMqtt)
    monkeypatch.setattr(rdv, "RendezvousSession", FakeSession)
    monkeypatch.setattr(server, "_rendezvous_sids", lambda: [b"x" * 16])
    monkeypatch.setattr(server, "_handle", hold_handle)

    task = asyncio.create_task(
        server._rdv_supervisor(sidecar_port=7400, api_key="k", config={})
    )
    await asyncio.wait_for(opened.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_drop_sessions_from_another_thread_closes_on_gateway_loop(home):
    from remedy.connect import server

    class _Writer:
        def __init__(self) -> None:
            self.closed_on: threading.Thread | None = None

        def close(self) -> None:
            self.closed_on = threading.current_thread()

    w = _Writer()
    server.register_writer(w, "dev-x")
    loop_thread = threading.current_thread()
    done = threading.Event()

    def _from_other_thread() -> None:
        server.drop_sessions_for_device("dev-x")
        done.set()

    threading.Thread(target=_from_other_thread).start()
    done.wait(2.0)
    # The close is dispatched onto this loop, not run on the caller thread.
    for _ in range(50):
        if w.closed_on is not None:
            break
        await asyncio.sleep(0.01)
    assert w.closed_on is loop_thread
    assert w not in server.live_writers()


def test_deferred_restart_uses_latest_config_after_old_thread_exits(home, monkeypatch):
    from remedy.connect import lifecycle

    release = threading.Event()
    old = threading.Thread(target=release.wait, daemon=True)
    old.start()
    monkeypatch.setattr(lifecycle, "_thread", old)
    monkeypatch.setattr(lifecycle, "_restart_waiter", None)
    monkeypatch.setattr(lifecycle, "stop_connect", lambda: None)
    monkeypatch.setattr(lifecycle, "listening_addr", lambda: None)
    monkeypatch.setattr(lifecycle, "_enabled_chosen", lambda _cfg: (True, "127.0.0.1", 7401))
    monkeypatch.setitem(lifecycle._saved, "app", lifecycle._saved.get("app"))
    monkeypatch.setitem(lifecycle._saved, "api_key", lifecycle._saved.get("api_key", ""))
    monkeypatch.setitem(lifecycle._saved, "sidecar_port", lifecycle._saved.get("sidecar_port", 7400))
    monkeypatch.setitem(lifecycle._saved, "config", dict(lifecycle._saved.get("config") or {}))

    original_start = lifecycle.maybe_start_connect
    first = {"connect_enabled": True, "connect_bind_host": "127.0.0.1", "marker": "first"}
    original_start(None, first, api_key="k", sidecar_port=7400)
    waiter = lifecycle._restart_waiter
    assert waiter is not None and waiter.is_alive()

    calls: list[dict[str, object]] = []

    def fake_start(_app, config, *, api_key, sidecar_port):
        calls.append({"config": dict(config), "api_key": api_key, "sidecar_port": sidecar_port})

    monkeypatch.setattr(lifecycle, "maybe_start_connect", fake_start)
    latest = dict(first, marker="latest")
    lifecycle._saved["config"] = latest
    release.set()
    waiter.join(timeout=2)
    for _ in range(50):
        if calls:
            break
        time.sleep(0.01)

    assert calls == [{"config": latest, "api_key": "k", "sidecar_port": 7400}]
    assert lifecycle._thread is None
