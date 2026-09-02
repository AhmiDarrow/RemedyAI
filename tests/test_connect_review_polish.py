"""Review follow-ups: encoded-fragment deny bypass, case-preserving paths,
hello key binding, live pane config, rdv supervisor teardown, revoke on the
gateway loop."""

from __future__ import annotations

import asyncio
import threading

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
