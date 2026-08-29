"""Connect proxy must not follow absolute URIs or echo Bearer to the phone."""

from __future__ import annotations

import pytest

from remedy.connect.deny import sanitize_origin_path, settings_write_locked
from remedy.connect.proxy import _filter_request_headers, _filter_response_headers


@pytest.mark.parametrize(
    "path",
    [
        "http://evil.example/api/status",
        "https://127.0.0.1:9/api/status",
        "//evil.example/api/status",
        "/api/status\x00",
        r"\api\status",
    ],
)
def test_absolute_and_opaque_paths_refused(path):
    assert sanitize_origin_path(path) is None


def test_dotdot_normalizes_to_jobs_next_shape():
    assert sanitize_origin_path("/api/foo/../../api/computer/jobs/next") == "/api/computer/jobs/next"
    assert sanitize_origin_path("/api/computer/jobs/../jobs/next") == "/api/computer/jobs/next"


def test_phone_cannot_inject_bearer_or_forwarded():
    got = _filter_request_headers(
        {
            "Authorization": "Bearer stolen",
            "X-Remedy-Token": "stolen",
            "X-Api-Key": "stolen",
            "X-Forwarded-For": "1.2.3.4",
            "Cookie": "a=b",
            "Accept": "application/json",
        }
    )
    keys = {k.lower() for k in got}
    assert "authorization" not in keys
    assert "x-remedy-token" not in keys
    assert "x-api-key" not in keys
    assert "cookie" not in keys
    assert "accept" in keys


def test_response_strips_auth_set_cookie():
    got = _filter_response_headers(
        {
            "Authorization": "Bearer tok",
            "Set-Cookie": "sid=1",
            "WWW-Authenticate": "Bearer",
            "Content-Type": "application/json",
        },
        sse=False,
    )
    keys = {k.lower() for k in got}
    assert "authorization" not in keys
    assert "set-cookie" not in keys
    assert "www-authenticate" not in keys
    assert "content-type" in keys


def test_settings_lock_family():
    assert settings_write_locked(b"") is None
    assert settings_write_locked(b'{"persona":"calm"}') is None
    for raw in (
        b'{"connect_enabled":true}',
        b'{"connect_relay_url":"10.0.0.1:9"}',
        b'{"http_bootstrap":true}',
        b'{"messengers":{}}',
        b'{"assistant":{}}',
        b'{"provider_keys":{}}',
        b'not-json',
    ):
        assert settings_write_locked(raw)
