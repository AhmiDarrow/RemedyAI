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
