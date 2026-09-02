"""Deny family: host poller, bootstrap, pane-off 403s. Not one-string matchers."""

from __future__ import annotations

import pytest

from remedy.connect.deny import connect_forbidden
from remedy.connect.panes import default_panes, normalize_panes

DEFAULT = default_panes()


@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("GET", "/api/computer/jobs/next", ""),
        ("GET", "/api/computer/jobs/next/", ""),
        ("GET", "/api/computer/jobs/next", "wait_ms=5000"),
        ("GET", "/api/computer/jobs/next", "wait_ms=0&driver=rust"),
        ("GET", "/api/computer/jobs/next", "driver=web"),
        ("GET", "/api/computer/jobs/next", "only=navigate"),
        ("GET", "/api/computer/jobs/next", "only=click&take=1"),
        ("GET", "/api/computer/jobs/next?wait_ms=2500&driver=rust", ""),
        ("POST", "/api/computer/jobs/abc/complete", ""),
        ("POST", "/api/computer/jobs/abc/cancel", ""),
        ("GET", "/api/computer/jobs/next", "take=1"),
        ("GET", "/API/COMPUTER/JOBS/NEXT", ""),
        ("GET", "/api/computer/jobs%2Fnext", ""),
        ("GET", "/%61pi/computer/jobs/next", ""),
        ("GET", "//api/computer/jobs/next", ""),
    ],
)
def test_jobs_next_family_is_hard_403(method, path, query):
    reason = connect_forbidden(method, path, query, DEFAULT)
    assert reason is not None
    # Protocol-relative `//host/...` is refused as `path` (not fetched).
    assert "host-poller" in reason or "jobs" in reason or reason == "path"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/computer/host/hello"),
        ("GET", "/api/computer/host/hello"),
        ("GET", "/api/computer/host/status"),
        ("GET", "/api/computer/host/other"),
        ("GET", "/api/computer/ui/command"),
        ("GET", "/api/computer/ui/command",),
        ("POST", "/api/computer/ui/command/ack"),
        ("POST", "/api/computer/a11y/push"),
    ],
)
def test_host_poller_siblings_are_hard_403(method, path):
    reason = connect_forbidden(method, path, "", DEFAULT)
    assert reason is not None
    assert "host-poller" in reason


@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("GET", "/api/auth/local-bootstrap", ""),
        ("POST", "/api/auth/local-bootstrap", ""),
        ("GET", "/api/auth/local-bootstrap/", ""),
        ("GET", "/api/auth/local-bootstrap", "x=1"),
        ("GET", "/api/auth/local-bootstrap?token=1", ""),
    ],
)
def test_local_bootstrap_family_is_hard_403(method, path, query):
    reason = connect_forbidden(method, path, query, DEFAULT)
    assert reason is not None
    assert "bootstrap" in reason


def test_capture_403_when_preview_pane_off():
    panes = normalize_panes({"computer_preview": False})
    assert connect_forbidden("POST", "/api/computer/capture", "", panes)
    panes_on = normalize_panes({"computer_preview": True})
    assert connect_forbidden("POST", "/api/computer/capture", "", panes_on) is None


def test_settings_body_safe_provider_only_switches_provider_model():
    from remedy.connect.deny import settings_body_safe_provider

    assert settings_body_safe_provider(b'{"llm_provider":"deepseek"}') is True
    assert (
        settings_body_safe_provider(
            b'{"llm_model":"deepseek-v4-flash","llm_provider":"deepseek"}'
        )
        is True
    )
    assert settings_body_safe_provider(b'{"provider":"openai","model":"gpt-4o"}') is True
    assert settings_body_safe_provider(b'{"llm_api_key":"x"}') is False
    assert settings_body_safe_provider(b'{"connect_relay_url":"1.2.3.4:9"}') is False
    assert settings_body_safe_provider(b'{"llm_provider":"deepseek","llm_api_key":"x"}') is False
    assert settings_body_safe_provider(b"") is False
    assert settings_body_safe_provider(b"[]") is False
    assert settings_body_safe_provider(b"{}") is False
    assert settings_body_safe_provider(b'{"llm_provider":123}') is False


def test_settings_write_403_until_opted_in():
    off = normalize_panes({"settings_write": False})
    assert connect_forbidden("PUT", "/api/settings", "", off)
    assert connect_forbidden("PATCH", "/api/settings", "", off)
    assert connect_forbidden("GET", "/api/settings", "", off) is None
    assert connect_forbidden("GET", "/api/status", "", off) is None
    on = normalize_panes({"settings_write": True})
    assert connect_forbidden("PUT", "/api/settings", "", on) is None


def test_rails_off_fails_closed_on_known_prefixes():
    off = normalize_panes({"rails": False})
    for path in (
        "/api/files",
        "/api/files/search",
        "/api/workspace",
        "/api/scratch",
        "/api/terminal",
        "/api/browser",
    ):
        assert connect_forbidden("GET", path, "", off) == "pane:rails"
    on = normalize_panes({"rails": True})
    assert connect_forbidden("GET", "/api/files", "", on) is None


def test_approvals_and_stop_stay_on_even_if_owner_tries_to_disable():
    raw = normalize_panes({"approvals": False, "sessions": False, "chat": False})
    assert raw["approvals"] is True
    assert connect_forbidden("GET", "/api/approvals", "", raw) is None
    assert connect_forbidden("POST", "/api/approvals/abc/resolve", "", raw) is None
    assert connect_forbidden("POST", "/api/sessions/sid/abort", "", raw) is None


def test_adjacent_status_path_is_not_jobs_next():
    assert connect_forbidden("GET", "/api/status", "", DEFAULT) is None
    assert connect_forbidden("GET", "/api/computer/capture", "", DEFAULT) is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/foo/../../api/computer/jobs/next",
        "/api/computer/jobs/../jobs/next",
        "/api/%2e%2e/computer/jobs/next",
        "http://127.0.0.1:7400/api/computer/jobs/next",
        "//evil.example/api/computer/jobs/next",
        "/api/computer/jobs/next/../../jobs/next",
    ],
)
def test_jobs_next_traversal_and_absolute_uri_denied(path):
    reason = connect_forbidden("GET", path, "", DEFAULT)
    assert reason is not None


def test_connect_trace_methods_denied():
    assert connect_forbidden("CONNECT", "/api/status", "", DEFAULT)
    assert connect_forbidden("TRACE", "/api/status", "", DEFAULT)


def test_settings_lock_blocks_connect_retarget_even_when_pane_on():
    from remedy.connect.deny import settings_write_locked

    on = normalize_panes({"settings_write": True})
    assert connect_forbidden("PUT", "/api/settings", "", on) is None
    assert settings_write_locked(b'{"theme":"dark"}') is None
    assert settings_write_locked(b'{"connect_relay_url":"1.2.3.4:9"}') == "settings:locked"
    assert settings_write_locked(b'{"llm_api_key":"x"}') == "settings:locked"
    assert settings_write_locked(b'{"http_bootstrap":true}') == "settings:locked"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/connect/pair/start"),
        ("PUT", "/api/connect"),
        ("GET", "/api/connect"),
        ("GET", "/api/connect/addresses"),
        ("POST", "/api/connect/pause"),
        ("POST", "/api/connect/resume"),
        ("POST", "/api/connect/devices/abc/revoke"),
        ("GET", "/connect/pair/start"),
    ],
)
def test_connect_management_is_hard_403(method, path):
    reason = connect_forbidden(method, path, "", DEFAULT)
    assert reason == "connect:mgmt"


@pytest.mark.parametrize(
    "path",
    ["/connect/me", "/api/connect/me", "/connect/preview", "/api/connect/preview"],
)
def test_connect_me_and_preview_are_not_mgmt_deny(path):
    assert connect_forbidden("GET", path, "", DEFAULT) is None


def test_wipe_import_family_is_settings_write():
    off = normalize_panes({"settings_write": False})
    assert connect_forbidden("POST", "/api/memory/persona-wipe", "", off) == "pane:settings_write"
    assert connect_forbidden("POST", "/api/custom/import", "", off) == "pane:settings_write"
    assert connect_forbidden("GET", "/api/sessions", "", off) is None
    on = normalize_panes({"settings_write": True})
    assert connect_forbidden("POST", "/api/custom/import", "", on) is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/auth/xai/apikey"),
        ("DELETE", "/api/auth/xai"),
        ("POST", "/api/providers/custom"),
        ("DELETE", "/api/providers/custom/p1"),
        ("PUT", "/api/assistant/google/app"),
        ("POST", "/api/memory/persona-wipe"),
        ("POST", "/api/memory/import"),
        ("POST", "/api/sessions/import"),
        ("POST", "/api/webhooks/whatsapp"),
    ],
)
def test_dedicated_credential_routes_403_until_settings_write(method, path):
    off = normalize_panes({"settings_write": False})
    assert connect_forbidden(method, path, "", off) == "pane:settings_write"
    on = normalize_panes({"settings_write": True})
    assert connect_forbidden(method, path, "", on) is None


ALL_PANES_ON = normalize_panes(
    {
        "live_ui": True,
        "chat": True,
        "approvals": True,
        "sessions": True,
        "rails": True,
        "computer_preview": True,
        "settings_write": True,
    }
)


@pytest.mark.parametrize(
    ("method", "path", "query"),
    [
        ("POST", "/api/connect/pair/start", ""),
        ("POST", "/api/connect/pair/start/", ""),
        ("GET", "/api/connect", ""),
        ("PUT", "/api/connect", ""),
        ("GET", "/api/connect/addresses", ""),
        ("POST", "/api/connect/pause", ""),
        ("POST", "/api/connect/resume", ""),
        ("POST", "/api/connect/devices/abc/revoke", ""),
        ("POST", "/connect/pair/start", ""),
        ("POST", "/connect/pause", ""),
        ("GET", "/connect/addresses", ""),
        ("GET", "/API/CONNECT/PAIR/START", ""),
        ("POST", "/api/connect%2Fpair%2Fstart", ""),
        ("POST", "/%61pi/connect/pair/start", ""),
        ("POST", "//api/connect/pair/start", ""),
        ("POST", "/api/foo/../../api/connect/pair/start", ""),
        ("POST", "/api/connect/pair/../pair/start", ""),
    ],
)
def test_connect_mgmt_family_is_hard_403(method, path, query):
    reason = connect_forbidden(method, path, query, ALL_PANES_ON)
    assert reason is not None
    assert "connect" in reason or reason == "path"


@pytest.mark.parametrize(
    "path",
    [
        "/connect/me",
        "/api/connect/me",
        "/connect/preview",
        "/api/connect/preview",
    ],
)
def test_connect_me_and_preview_are_not_mgmt_denies(path):
    assert connect_forbidden("GET", path, "", DEFAULT) is None
    assert connect_forbidden("GET", path, "", ALL_PANES_ON) is None


def test_adjacent_connection_path_is_not_connect_mgmt():
    assert connect_forbidden("GET", "/api/status", "", DEFAULT) is None
    # Not a management route (the regex must not over-match)... but also not a
    # family the phone has any pane for, so it fails closed as unknown.
    assert connect_forbidden("GET", "/api/connection", "", DEFAULT) == "unknown:family"
    assert connect_forbidden("GET", "/api/sessions", "", DEFAULT) is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/auth/xai/apikey"),
        ("POST", "/api/auth/xai/apikey/"),
        ("POST", "/API/AUTH/XAI/APIKEY"),
        ("POST", "/api/auth/xai/login"),
        ("DELETE", "/api/auth/xai"),
        ("GET", "/api/auth/xai"),
        ("POST", "/api/providers/custom"),
        ("DELETE", "/api/providers/custom/custom-foo"),
        ("POST", "/api/providers/probe"),
        ("GET", "/api/providers"),
        ("PUT", "/api/assistant/google/app"),
        ("POST", "/api/assistant/google/oauth/start"),
        ("DELETE", "/api/assistant/google"),
        ("GET", "/api/assistant/status"),
        ("POST", "/api/webhooks/whatsapp"),
        ("POST", "/api/webhook/ci"),
        ("POST", "/api/memory/persona-wipe"),
        ("POST", "/api/memory/import"),
        ("POST", "/api/sessions/import"),
        ("POST", "/api/skills/import"),
        ("POST", "/api/partner/identity/import"),
    ],
)
def test_credential_family_403_until_settings_write(method, path):
    off = normalize_panes({"settings_write": False})
    reason = connect_forbidden(method, path, "", off)
    assert reason == "pane:settings_write"
    on = normalize_panes({"settings_write": True})
    assert connect_forbidden(method, path, "", on) is None


def test_dedicated_apikey_route_is_not_only_settings_body():
    """Phone must not POST /api/auth/xai/apikey when settings_write is off."""
    off = normalize_panes({"settings_write": False})
    reason = connect_forbidden("POST", "/api/auth/xai/apikey", "", off)
    assert reason == "pane:settings_write"
    on = normalize_panes({"settings_write": True})
    assert connect_forbidden("POST", "/api/auth/xai/apikey", "", on) is None
    from remedy.connect.deny import settings_write_locked

    assert settings_write_locked(b'{"llm_api_key":"x"}') == "settings:locked"
    assert settings_write_locked(b'{"theme":"dark"}') is None


def test_local_bootstrap_stays_hard_denied_when_settings_write_on():
    reason = connect_forbidden("GET", "/api/auth/local-bootstrap", "", ALL_PANES_ON)
    assert reason is not None
    assert "bootstrap" in reason


def test_pair_start_stays_hard_denied_when_settings_write_on():
    reason = connect_forbidden("POST", "/api/connect/pair/start", "", ALL_PANES_ON)
    assert reason == "connect:mgmt"


@pytest.mark.asyncio
async def test_proxied_pair_start_is_403():
    from remedy.connect.pipe import HttpRequest, iter_request_http

    req = HttpRequest(method="POST", path="/api/connect/pair/start", query="", body=b"")
    chunks: list[bytes] = []
    async for piece in iter_request_http(
        req,
        device={"id": "dev1", "name": "phone"},
        sidecar_port=9,
        api_key="tok-connect-test-not-a-secret",
        config={"connect_panes": dict(ALL_PANES_ON)},
    ):
        chunks.append(piece)
    blob = b"".join(chunks)
    assert b"HTTP/1.1 403" in blob
    assert b"connect:mgmt" in blob


@pytest.mark.asyncio
async def test_proxied_apikey_route_is_403_when_settings_write_off():
    from remedy.connect.pipe import HttpRequest, iter_request_http

    req = HttpRequest(
        method="POST",
        path="/api/auth/xai/apikey",
        query="",
        body=b'{"api_key":"sk-not-a-real-key"}',
    )
    chunks: list[bytes] = []
    async for piece in iter_request_http(
        req,
        device={"id": "dev1", "name": "phone"},
        sidecar_port=9,
        api_key="tok-connect-test-not-a-secret",
        config={"connect_panes": {"settings_write": False}},
    ):
        chunks.append(piece)
    blob = b"".join(chunks)
    assert b"HTTP/1.1 403" in blob
    assert b"settings_write" in blob


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/shutdown"),
        ("POST", "/api/quit"),
        ("POST", "/api/restart"),
        ("POST", "/api/exit"),
        ("POST", "/api/app/command"),
        ("GET", "/api/app/command/restart"),
        ("POST", "/api/app/command/quit"),
        ("POST", "/API/SHUTDOWN"),
    ],
)
def test_server_kill_paths_are_hard_denied_even_with_all_panes_on(method, path):
    """A phone must NEVER end the Remedy server, whatever panes are on."""
    reason = connect_forbidden(method, path, "", ALL_PANES_ON)
    assert reason == "server:kill"


def test_phone_stop_is_a_turn_abort_and_stays_reachable():
    """``POST /api/stop`` aborts only the phone's own /connect/me session
    (routes/connect.py); the phone's Stop button falls back to it, so it must
    not be lumped in with the server-kill family."""
    assert connect_forbidden("POST", "/api/stop", "", ALL_PANES_ON) is None
    assert connect_forbidden("POST", "/api/stop", "", {}) is None


@pytest.mark.asyncio
async def test_proxied_shutdown_is_403():
    from remedy.connect.pipe import HttpRequest, iter_request_http

    req = HttpRequest(method="POST", path="/api/shutdown", query="", body=b"{}")
    chunks: list[bytes] = []
    async for piece in iter_request_http(
        req,
        device={"id": "dev1", "name": "phone"},
        sidecar_port=9,
        api_key="[redacted]",
        config={"connect_panes": dict(ALL_PANES_ON)},
    ):
        chunks.append(piece)
    blob = b"".join(chunks)
    assert b"HTTP/1.1 403" in blob
    assert b"server:kill" in blob


@pytest.mark.asyncio
async def test_proxied_turn_abort_still_allowed():
    """Aborting a running turn stays reachable — only server control is cut."""
    from remedy.connect.pipe import HttpRequest, iter_request_http

    # Deny gate: abort is NOT blocked (no server:kill / pane reason).
    assert connect_forbidden("POST", "/api/sessions/sess_abc/abort", "", ALL_PANES_ON) is None
    # A valid session-abort request passes the pipe gate (reaches the real
    # server instead of being cut with 403). Port 9 refuses, so the proxy
    # attempt errors — but that error is NOT a deny 403.
    req = HttpRequest(
        method="POST",
        path="/api/sessions/sess_abc/abort",
        query="",
        body=b"{}",
    )
    chunks: list[bytes] = []
    try:
        async for piece in iter_request_http(
            req,
            device={"id": "dev1", "name": "phone"},
            sidecar_port=9,
            api_key="[redacted]",
            config={"connect_panes": dict(ALL_PANES_ON)},
        ):
            chunks.append(piece)
    except OSError:
        pass  # dead proxy port — expected without a live server
    blob = b"".join(chunks)
    assert b"server:kill" not in blob
