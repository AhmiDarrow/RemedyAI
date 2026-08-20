"""A real loopback HTTP server for tests, in place of a mocked ``urlopen``.

Remedy's local-model clients — ``runtime/local_infer.py``,
``vision/decoder.py``, ``vision/runtime.py`` — have no injectable client
object. Each builds a ``urllib`` Request and hands it to
``urlopen_no_redirect``. Monkeypatching that call away proves almost nothing:
it skips the loopback guard, the headers, the JSON body on the wire, the
timeout and the redirect handler, which is precisely the machinery that keeps
the owner's screenshots and prompts on this machine.

So this harness answers over a real socket, and the client under test never
learns it is not talking to llama-server.

What breaks if this file is wrong: a test that believes it proved the decoder
survives a 500, a slow reply, a dropped connection or a hang proves nothing at
all — and worse, a server that fails to shut down leaks a thread and a bound
port into whatever test runs next, so the failure surfaces somewhere innocent.

Two rules are structural rather than conventional. The port is always 0
(ephemeral): a fixed port would collide with the real llama-server on the
owner's machine, and two tests running together would fight over it. The host
must be a loopback literal: a fake server bound to 0.0.0.0 answers the LAN.
Neither is configurable, because neither has a legitimate use here.
"""

from __future__ import annotations

import contextlib
import json as _json
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest

# Anything else answers off-machine. 127.0.0.0/8 and ::1 only.
_LOOPBACK_HOSTS = frozenset({"::1", "0:0:0:0:0:0:0:1"})

# A hung handler waits for teardown, but never for ever: a test that forgets to
# stop the server must still let the interpreter exit.
_HANG_CAP_S = 30.0

# serve_forever only notices shutdown() between polls, so the poll interval is
# the floor on every teardown in the suite. 0.5s (the stdlib default) would add
# half a second to every test that touches this fixture.
_POLL_INTERVAL_S = 0.02

_ANY = "ANY"
_WILDCARD = "*"


@dataclass
class Canned:
    """One prepared reply. ``hang`` and ``drop`` are the interesting ones."""

    status: int = 200
    json: Any = None
    body: bytes | str | None = None
    headers: Mapping[str, str] | None = None
    delay_s: float = 0.0
    # Accept the request, record it, then never answer: the client must hit its
    # own timeout rather than wait for ever.
    hang: bool = False
    # Close the connection with no status line at all (llama-server dying
    # mid-request looks like this).
    drop: bool = False

    def payload(self) -> tuple[bytes, str]:
        """Return (bytes on the wire, default content type)."""
        if self.json is not None:
            return _json.dumps(self.json).encode("utf-8"), "application/json"
        if isinstance(self.body, bytes):
            return self.body, "application/octet-stream"
        if isinstance(self.body, str):
            return self.body.encode("utf-8"), "text/plain; charset=utf-8"
        return b"", "text/plain; charset=utf-8"


@dataclass(frozen=True)
class RecordedRequest:
    """Exactly what arrived, before any canned reply was chosen."""

    method: str
    path: str
    query: str
    headers: Mapping[str, str]  # keys lowercased
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json_body(self) -> Any:
        """Parse the body as JSON. Raises if it is not JSON — say so loudly."""
        return _json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class _Server(ThreadingHTTPServer):
    # daemon_threads also clears block_on_close, so server_close() does not
    # join a deliberately hung handler thread and deadlock teardown.
    daemon_threads = True
    allow_reuse_address = False
    fake: FakeHTTPServer


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeHTTP/1.0"
    sys_version = ""

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")

    def log_message(self, *_args: object) -> None:
        """Silence: pytest output is not an access log."""
        return

    def _handle(self, method: str) -> None:
        fake: FakeHTTPServer = self.server.fake  # type: ignore[attr-defined]
        split = urlsplit(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        # Record before answering, so a hung or dropped request is still visible
        # to the test that is asserting about it.
        fake._record(
            RecordedRequest(
                method=method,
                path=split.path,
                query=split.query,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
            )
        )

        canned = fake._match(method, split.path)
        if canned is None:
            self._send(
                Canned(
                    status=404,
                    json={
                        "error": "no canned response registered",
                        "method": method,
                        "path": split.path,
                    },
                )
            )
            return

        if canned.hang:
            fake.closing.wait(_HANG_CAP_S)
            self.close_connection = True
            return

        if canned.drop:
            self.close_connection = True
            with contextlib.suppress(OSError):
                self.connection.close()
            return

        if canned.delay_s > 0:
            # Wake early on teardown: a failed test should not also wait out the
            # delay of every request still in flight.
            fake.closing.wait(canned.delay_s)

        self._send(canned)

    def _send(self, canned: Canned) -> None:
        payload, content_type = canned.payload()
        extra = dict(canned.headers or {})
        try:
            self.send_response(int(canned.status))
            if not any(k.lower() == "content-type" for k in extra):
                self.send_header("Content-Type", content_type)
            for key, value in extra.items():
                self.send_header(str(key), str(value))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD" and payload:
                self.wfile.write(payload)
        except OSError:
            # Client gave up (timeout test) — not this server's problem.
            self.close_connection = True


class FakeHTTPServer:
    """A loopback HTTP server with canned replies and a request log.

    Never binds a fixed port: the port is chosen by the OS and read back from
    :attr:`base_url`.
    """

    def __init__(self, host: str = "127.0.0.1") -> None:
        if not (host.startswith("127.") or host in _LOOPBACK_HOSTS):
            raise ValueError(
                f"fake_http is loopback-only; refusing to bind {host!r}"
            )
        self._host = host
        self._routes: dict[tuple[str, str], list[Canned]] = {}
        self._requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self.closing = threading.Event()
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    # --- lifecycle ------------------------------------------------------------

    def start(self) -> FakeHTTPServer:
        if self._httpd is not None:
            return self
        httpd = _Server((self._host, 0), _Handler)  # port 0: always ephemeral
        httpd.fake = self
        self._httpd = httpd
        self._thread = threading.Thread(
            target=lambda: httpd.serve_forever(poll_interval=_POLL_INTERVAL_S),
            name=f"fake-http-{httpd.server_address[1]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Idempotent. Safe to call from a finally: even if start() failed."""
        self.closing.set()
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError("fake_http server thread did not stop")

    def __enter__(self) -> FakeHTTPServer:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # --- addressing -----------------------------------------------------------

    @property
    def port(self) -> int:
        if self._httpd is None:
            raise RuntimeError("fake_http server is not running")
        return int(self._httpd.server_address[1])

    @property
    def host(self) -> str:
        return self._host

    @property
    def base_url(self) -> str:
        """``http://127.0.0.1:<ephemeral>`` — no trailing slash, no path."""
        host = f"[{self._host}]" if ":" in self._host else self._host
        return f"http://{host}:{self.port}"

    def url(self, path: str = "/") -> str:
        return self.base_url + ("/" + path.lstrip("/") if path else "")

    # --- canned responses -----------------------------------------------------

    def route(
        self,
        path: str,
        *,
        method: str = _ANY,
        status: int = 200,
        json: Any = None,
        body: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        delay_s: float = 0.0,
        hang: bool = False,
        drop: bool = False,
    ) -> None:
        """Answer *path* with this reply, replacing any earlier route for it.

        ``path`` is matched exactly (query string ignored); ``"*"`` catches
        everything unmatched.
        """
        self.route_sequence(
            path,
            [
                Canned(
                    status=status,
                    json=json,
                    body=body,
                    headers=headers,
                    delay_s=delay_s,
                    hang=hang,
                    drop=drop,
                )
            ],
            method=method,
        )

    def route_sequence(
        self,
        path: str,
        responses: Sequence[Canned],
        *,
        method: str = _ANY,
    ) -> None:
        """Answer *path* with each reply in turn; the last one then repeats."""
        if not responses:
            raise ValueError("route_sequence needs at least one response")
        key = (method.upper(), path if path == _WILDCARD else "/" + path.lstrip("/"))
        with self._lock:
            self._routes[key] = list(responses)

    def _match(self, method: str, path: str) -> Canned | None:
        keys = [
            (method.upper(), path),
            (_ANY, path),
            (method.upper(), _WILDCARD),
            (_ANY, _WILDCARD),
        ]
        with self._lock:
            for key in keys:
                queue = self._routes.get(key)
                if not queue:
                    continue
                # Keep the final reply in place so it repeats.
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return None

    # --- request log ----------------------------------------------------------

    def _record(self, request: RecordedRequest) -> None:
        with self._lock:
            self._requests.append(request)

    @property
    def requests(self) -> list[RecordedRequest]:
        with self._lock:
            return list(self._requests)

    @property
    def last_request(self) -> RecordedRequest | None:
        with self._lock:
            return self._requests[-1] if self._requests else None

    def requests_for(self, path: str, *, method: str | None = None) -> list[RecordedRequest]:
        want = "/" + path.lstrip("/")
        return [
            r
            for r in self.requests
            if r.path == want and (method is None or r.method == method.upper())
        ]

    def reset(self) -> None:
        """Forget routes and recorded requests; keep the port."""
        with self._lock:
            self._routes.clear()
            self._requests.clear()


@contextmanager
def fake_http_server(host: str = "127.0.0.1") -> Iterator[FakeHTTPServer]:
    """Start a server, and stop it however the block ends."""
    server = FakeHTTPServer(host)
    try:
        server.start()
        yield server
    finally:
        server.stop()


@pytest.fixture(name="fake_http")
def fake_http_fixture() -> Iterator[FakeHTTPServer]:
    """A running loopback HTTP server, torn down even when the test fails.

    The function is deliberately *not* called ``fake_http``: a test module does
    ``from tests.harness.fake_http import fake_http_fixture  # noqa: F401`` and
    then takes ``fake_http`` as an argument, with no shadowed name for the
    linter — or pytest — to trip over.
    """
    with fake_http_server() as server:
        yield server
