"""The mailbox app password must never leave the calendar host.

``_dav`` puts it in an ``Authorization`` header on every request, and event ids
are the ``<href>`` values the server itself returned — which also arrive from
the model as tool arguments. ``_event_url`` used to return any absolute id
verbatim, so one calendar entry reading ``http://elsewhere/x`` was enough to
hand that credential to a stranger. ``urlopen`` following a 302 did the same.
"""

from __future__ import annotations

import pytest

from remedy.assistant.providers.caldav import CalDavAccount, CalDavCalendarProvider

HOST = "https://caldav.fastmail.com"
BASE = f"{HOST}/dav/calendars/user/me@fastmail.com/Default/"


@pytest.fixture
def provider() -> CalDavCalendarProvider:
    return CalDavCalendarProvider(
        CalDavAccount(address="me@fastmail.com", password="app-password", url=BASE)
    )


@pytest.mark.parametrize(
    ("event_id", "expect"),
    [
        ("abc123", f"{BASE}abc123.ics"),
        ("abc123.ics", f"{BASE}abc123.ics"),
        ("/dav/calendars/user/me@fastmail.com/Default/x.ics",
         f"{HOST}/dav/calendars/user/me@fastmail.com/Default/x.ics"),
        (f"{HOST}/dav/calendars/user/me/x.ics", f"{HOST}/dav/calendars/user/me/x.ics"),
    ],
)
def test_legitimate_ids_still_resolve(provider, event_id, expect):
    assert provider._event_url(event_id) == expect


@pytest.mark.parametrize(
    "hostile",
    [
        "http://attacker.example/collect",
        "https://attacker.example/collect",
        "http://caldav.fastmail.com/x.ics",            # scheme downgrade
        "https://caldav.fastmail.com.evil.tld/x.ics",  # lookalike suffix
        "https://evil.tld/caldav.fastmail.com/x.ics",  # host in the path
        "https://user:pw@attacker.example/x.ics",
    ],
)
def test_an_id_pointing_anywhere_else_is_refused(provider, hostile):
    with pytest.raises(RuntimeError, match="not your calendar host"):
        provider._event_url(hostile)


def test_an_empty_id_is_refused(provider):
    with pytest.raises(ValueError):
        provider._event_url("")


def test_every_request_is_origin_checked(provider):
    """Belt and braces: even a URL that reached ``_dav`` another way is caught
    before the Authorization header is attached."""
    with pytest.raises(RuntimeError, match="not your calendar host"):
        provider._dav("GET", "https://attacker.example/x")


def test_redirects_are_never_followed():
    """urllib carries Authorization across a redirect, so a 302 from the
    calendar host would hand the password to whatever it named."""
    import inspect

    from remedy.assistant.providers import caldav

    src = inspect.getsource(caldav.CalDavCalendarProvider._dav)
    assert "urlopen_no_redirect" in src
    assert "urllib.request.urlopen(" not in src


class TestResponseBounds:
    """A calendar answer is kilobytes. A hostile or broken server must not be
    able to hand back a body larger than memory."""

    def test_the_body_read_is_capped(self):
        import inspect

        from remedy.assistant.providers import caldav

        src = inspect.getsource(caldav.CalDavCalendarProvider._dav)
        assert "_MAX_RESPONSE_BYTES" in src
        assert "resp.read()" not in src, "unbounded read is back"

    def test_error_bodies_are_capped_too(self):
        import inspect

        from remedy.assistant.providers import caldav

        src = inspect.getsource(caldav.CalDavCalendarProvider._dav)
        assert "_MAX_ERROR_BYTES" in src
        assert "e.read()" not in src

    def test_the_caps_are_generous_enough_for_real_calendars(self):
        from remedy.assistant.providers import caldav

        assert caldav._MAX_RESPONSE_BYTES >= 8 * 1024 * 1024
        assert caldav._MAX_ERROR_BYTES >= 4096


# --- origin identity, not raw netloc ------------------------------------------


@pytest.mark.parametrize(
    ("configured", "target"),
    [
        ("https://h/dav/", "https://h:443/dav/x.ics"),
        ("https://h:443/dav/", "https://h/dav/x.ics"),
        ("http://h:80/dav/", "http://h/dav/x.ics"),
        ("https://me%40x.com:app-pw@h/dav/", "https://h/dav/x.ics"),
        ("https://H.Example.COM/dav/", "https://h.example.com/dav/x.ics"),
    ],
)
def test_the_same_server_spelled_differently_is_the_same_origin(configured, target):
    """``https://h`` and ``https://h:443/`` are one server; userinfo in the
    configured URL is not part of where the credential goes. Comparing raw
    ``netloc`` refused all of these."""
    prov = CalDavCalendarProvider(CalDavAccount(address="me@x.com", password="pw", url=configured))
    prov._require_same_origin(target)  # must not raise
    assert prov._event_url(target) == target


@pytest.mark.parametrize(
    ("configured", "target"),
    [
        ("https://h/dav/", "https://h:8443/dav/x.ics"),       # other port
        ("https://h/dav/", "http://h:443/dav/x.ics"),         # other scheme
        ("https://h/dav/", "https://h@attacker.example/x"),   # host as userinfo
        ("https://me:pw@h/dav/", "https://evil.tld/x.ics"),
    ],
)
def test_a_different_origin_is_still_refused(configured, target):
    prov = CalDavCalendarProvider(CalDavAccount(address="me@x.com", password="pw", url=configured))
    with pytest.raises(RuntimeError, match="not your calendar host"):
        prov._require_same_origin(target)


# --- redirects: same-origin followed (bounded), cross-origin refused ----------


class _Redirecting:
    """A ``urlopen_no_redirect`` double: a scripted 3xx chain, then a 207."""

    def __init__(self, hops: list[tuple[int, str]], final_status: int = 207) -> None:
        self.hops = list(hops)
        self.final_status = final_status
        self.requests: list[tuple[str, str, bytes | None, str]] = []

    def __call__(self, req, *, timeout=30):  # noqa: ANN001
        import email.message
        import io
        import urllib.error

        self.requests.append(
            (req.get_method(), req.full_url, req.data, req.get_header("Authorization") or "")
        )
        if self.hops:
            code, location = self.hops.pop(0)
            headers = email.message.Message()
            headers["Location"] = location
            raise urllib.error.HTTPError(req.full_url, code, "moved", headers, io.BytesIO(b""))
        return _Resp(self.final_status)


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):  # noqa: ANN002
        return False

    def read(self, n: int = -1) -> bytes:
        return b"<d:multistatus/>"


@pytest.fixture
def transport(monkeypatch):
    from remedy.assistant.providers import caldav

    def install(hops, final_status=207):
        t = _Redirecting(hops, final_status)
        monkeypatch.setattr(caldav, "urlopen_no_redirect", t)
        return t

    return install


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_same_origin_redirect_is_followed(provider, transport, code):
    """Servers canonicalise (trailing slash, principal path); that used to be a
    hard ``CalDAV 301`` failure."""
    t = transport([(code, f"{HOST}/dav/principals/me/")])
    status, body = provider._dav("PROPFIND", body=b"<q/>")
    assert status == 207 and "multistatus" in body
    assert [r[1] for r in t.requests] == [BASE, f"{HOST}/dav/principals/me/"]
    # Method, body and credential all carry over to the same host.
    assert t.requests[1][0] == "PROPFIND"
    assert t.requests[1][2] == b"<q/>"
    assert t.requests[1][3].startswith("Basic ")


def test_a_relative_location_is_resolved_against_the_request(provider, transport):
    t = transport([(302, "/dav/calendars/user/me@fastmail.com/Default/")])
    provider._dav("PROPFIND")
    assert t.requests[-1][1] == BASE


@pytest.mark.parametrize(
    "elsewhere",
    [
        "https://attacker.example/collect",
        "http://caldav.fastmail.com/dav/",             # downgrade
        "https://caldav.fastmail.com:8443/dav/",       # other port
        "https://caldav.fastmail.com.evil.tld/dav/",
    ],
)
def test_a_cross_origin_redirect_is_refused_before_any_request(provider, transport, elsewhere):
    t = transport([(302, elsewhere)])
    with pytest.raises(RuntimeError, match="not your calendar host"):
        provider._dav("PROPFIND")
    # Exactly one request went out — the original; the hostile hop was never made.
    assert [r[1] for r in t.requests] == [BASE]


def test_a_chain_inside_the_bound_is_followed(provider, transport):
    from remedy.assistant.providers import caldav

    hops = [(302, f"{HOST}/dav/hop{i}/") for i in range(caldav._MAX_REDIRECTS)]
    t = transport(hops)
    status, _ = provider._dav("PROPFIND")
    assert status == 207
    assert len(t.requests) == caldav._MAX_REDIRECTS + 1


def test_a_chain_past_the_bound_is_cut(provider, transport):
    from remedy.assistant.providers import caldav

    hops = [(302, f"{HOST}/dav/hop{i}/") for i in range(caldav._MAX_REDIRECTS + 1)]
    t = transport(hops)
    with pytest.raises(RuntimeError, match="too many redirects"):
        provider._dav("PROPFIND")
    assert len(t.requests) == caldav._MAX_REDIRECTS + 1


def test_a_3xx_without_a_location_is_an_ordinary_error(provider, monkeypatch):
    import io
    import urllib.error

    from remedy.assistant.providers import caldav

    def no_location(req, *, timeout=30):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, 304, "not modified", None, io.BytesIO(b""))

    monkeypatch.setattr(caldav, "urlopen_no_redirect", no_location)
    with pytest.raises(RuntimeError, match="CalDAV 304"):
        provider._dav("PROPFIND")
