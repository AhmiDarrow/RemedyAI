"""SLOW warnings skip host pollers blocked behind a fat ReAct turn."""

from remedy.interfaces.api import should_warn_slow


def test_poller_paths_skip_slow_on_200():
    for path in (
        "/api/computer/jobs/next",
        "/api/computer/ui/command",
        "/api/computer/host/hello",
        "/api/computer/host/status",
        "/api/status",
        "/api/ping",
    ):
        assert should_warn_slow("GET", path, 200, 6259) is False
    assert should_warn_slow("POST", "/api/computer/host/hello", 200, 1711) is False


def test_poller_failures_still_slow():
    assert should_warn_slow("GET", "/api/computer/jobs/next", 500, 800) is True


def test_real_endpoints_still_slow():
    assert should_warn_slow("POST", "/api/sessions/abc/messages/stream", 200, 1716) is True
    assert should_warn_slow("PUT", "/api/settings", 200, 500) is True


def test_voice_status_is_a_poller_not_a_slow_warn():
    """Desktop polls /api/voice/status; a fat turn must not SLOW-spam the log."""
    assert should_warn_slow("GET", "/api/voice/status", 200, 2707) is False
    assert should_warn_slow("GET", "/api/voice/status", 500, 800) is True


def test_fast_never_slow():
    assert should_warn_slow("GET", "/api/computer/jobs/next", 200, 20) is False
    assert should_warn_slow("POST", "/api/sessions/abc/messages/stream", 200, 20) is False
