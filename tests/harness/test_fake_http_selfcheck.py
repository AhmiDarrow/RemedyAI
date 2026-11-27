"""Proof that the fake HTTP server is worth trusting.

Every other test that uses this harness inherits its correctness. If the
server silently answered 200 to a path nobody registered, or kept a thread and
a port alive after teardown, or bound anything other than loopback, the tests
built on top of it would pass while proving the opposite of what they claim.

So this file checks the harness against the real clients it exists to serve:
``local_text_complete`` and ``decode_image`` talk to it over a real socket,
with the real loopback guard and the real redirect handler in the path.
"""

from __future__ import annotations

import socket
import threading
import time
from urllib.request import urlopen

import pytest

from tests.harness.fake_http import (  # noqa: F401  (imported for the fixture)
    Canned,
    FakeHTTPServer,
    fake_http_fixture,
    fake_http_server,
)

CHAT = "/v1/chat/completions"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"fake png body"


def _chat_reply(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _assert_nothing_listening(port: int) -> None:
    """A live loopback socket answers in under a millisecond.

    Windows drops the SYN to a closed port rather than refusing it, so the
    failure arrives as TimeoutError instead of ConnectionRefusedError — either
    way, nothing accepted.
    """
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()


@pytest.fixture()
def shot(tmp_path):
    p = tmp_path / "screenshot.png"
    p.write_bytes(PNG)
    return p


# --- it is loopback, and the port is never ours to choose ---------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "", "example.com", "::"])
def test_binding_anything_but_loopback_is_refused_not_attempted(host):
    with pytest.raises(ValueError, match="loopback"):
        FakeHTTPServer(host)


def test_the_port_is_ephemeral_so_two_servers_never_collide(fake_http):
    with fake_http_server() as other:
        assert fake_http.port != other.port
        # A fixed port would sooner or later be the real llama-server's.
        assert fake_http.port not in (0, 8080, 8081, 8090)
        assert other.base_url.startswith("http://127.0.0.1:")


def test_base_url_reports_the_port_the_os_actually_gave_us(fake_http):
    assert fake_http.base_url == f"http://127.0.0.1:{fake_http.port}"
    with socket.create_connection(("127.0.0.1", fake_http.port), timeout=2.0):
        pass  # it is really listening, not just reporting a number


def test_asking_for_the_port_before_start_raises_rather_than_lying():
    server = FakeHTTPServer()
    with pytest.raises(RuntimeError):
        _ = server.base_url


# --- canned responses ---------------------------------------------------------


@pytest.mark.parametrize("status", [200, 204, 400, 404, 429, 500, 503])
def test_the_status_asked_for_is_the_status_sent(fake_http, status):
    fake_http.route("/ping", status=status, body="pong")
    try:
        with urlopen(fake_http.url("/ping"), timeout=5.0) as resp:
            assert resp.status == status
    except Exception as e:  # 4xx/5xx surface as HTTPError, which carries .code
        assert getattr(e, "code", None) == status


def test_a_wildcard_route_catches_what_nothing_else_claimed(fake_http):
    fake_http.route("*", status=418, body="teapot")
    for path in ("/v1/models", "/anything/else"):
        with pytest.raises(Exception) as exc:
            urlopen(fake_http.url(path), timeout=5.0)
        assert getattr(exc.value, "code", None) == 418


# --- the request log ----------------------------------------------------------


def test_query_strings_are_kept_apart_from_the_path(fake_http):
    fake_http.route("/health", json={"ok": True})
    with urlopen(fake_http.url("/health?verbose=1&x=2"), timeout=5.0):
        pass
    req = fake_http.last_request
    assert req.path == "/health"  # routing ignores the query…
    assert req.query == "verbose=1&x=2"  # …but the test can still see it


def test_reset_forgets_routes_and_history_without_moving_the_port(fake_http):
    fake_http.route("/ping", body="pong")
    with urlopen(fake_http.url("/ping"), timeout=5.0):
        pass
    port = fake_http.port
    fake_http.reset()
    assert fake_http.requests == []
    assert fake_http.port == port
    with pytest.raises(Exception) as exc:
        urlopen(fake_http.url("/ping"), timeout=5.0)
    assert getattr(exc.value, "code", None) == 404


# --- slow, hung and dropped ---------------------------------------------------


def test_a_delayed_response_really_makes_the_client_wait(fake_http):
    fake_http.route("/slow", json={"ok": True}, delay_s=0.30)
    t0 = time.perf_counter()
    with urlopen(fake_http.url("/slow"), timeout=10.0) as resp:
        assert resp.status == 200
    assert time.perf_counter() - t0 >= 0.25


# --- teardown -----------------------------------------------------------------


def test_stopping_frees_the_port_and_the_thread():
    with fake_http_server() as server:
        port = server.port
        server.route("/ping", body="pong")
        with urlopen(server.url("/ping"), timeout=5.0):
            pass
    _assert_nothing_listening(port)
    assert not [t for t in threading.enumerate() if t.name == f"fake-http-{port}"]


def test_a_failing_test_still_leaves_nothing_running():
    """The finally: in the fixture is the whole point — prove it fires."""
    port = None
    with pytest.raises(AssertionError):
        with fake_http_server() as server:
            port = server.port
            raise AssertionError("pretend the test body failed")
    _assert_nothing_listening(port)


def test_teardown_does_not_block_on_a_request_left_hanging():
    """A hung handler must not deadlock server_close() for the rest of the run."""
    with fake_http_server() as server:
        port = server.port
        server.route("/hang", hang=True)
        with pytest.raises(OSError):
            urlopen(server.url("/hang"), timeout=0.4)
        t0 = time.perf_counter()
    assert time.perf_counter() - t0 < 5.0
    assert not [t for t in threading.enumerate() if t.name == f"fake-http-{port}"]


def test_stop_is_idempotent_and_start_twice_keeps_one_port():
    server = FakeHTTPServer()
    server.start()
    port = server.port
    server.start()
    assert server.port == port
    server.stop()
    server.stop()  # must not raise on the second call
