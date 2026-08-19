"""CalDAV calendar over an app password — iCalendar build/parse + CRUD wiring.

Pure logic + a fake transport; no network. The live proof (create → reschedule
→ cancel against a real Google calendar) is recorded in the commit message.
"""

from __future__ import annotations

import datetime as dt

import pytest

import remedy.assistant.providers.caldav as C

# --- URL presets ------------------------------------------------------------


def test_caldav_url_for_known_domains() -> None:
    assert "google.com/calendar/dav" in C.caldav_url_for("me@gmail.com")
    assert "icloud" in C.caldav_url_for("me@icloud.com")
    assert "fastmail" in C.caldav_url_for("me@fastmail.com")


def test_caldav_url_unknown_domain_is_empty() -> None:
    assert C.caldav_url_for("me@unknown.example") == ""


def test_caldav_url_quotes_address() -> None:
    url = C.caldav_url_for("first+tag@gmail.com")
    assert "+" not in url.split("/dav/")[-1].split("/")[0] or "%2B" in url


# --- iCalendar text escaping / folding --------------------------------------


def test_escape_roundtrip() -> None:
    raw = "Dinner; with Bob, then\nmovie \\ popcorn"
    assert C.ics_unescape(C.ics_escape(raw)) == raw


def test_escape_handles_specials() -> None:
    out = C.ics_escape("a,b;c")
    assert r"\," in out and r"\;" in out


def test_fold_long_lines_and_unfold_restores() -> None:
    line = "SUMMARY:" + ("x" * 300)
    folded = C.fold(line)
    assert "\r\n " in folded  # continuation lines start with a space
    assert "".join(C.unfold(folded)) == line


def test_unfold_handles_tab_continuation() -> None:
    assert C.unfold("SUMMARY:ab\r\n\tcd") == ["SUMMARY:abcd"]


# --- datetime conversion ----------------------------------------------------


def test_to_ics_dt_all_day() -> None:
    assert C.to_ics_dt("2026-09-01") == ("20260901", True)


def test_to_ics_dt_utc_and_naive() -> None:
    stamp, all_day = C.to_ics_dt("2026-09-01T20:00:00Z")
    assert stamp == "20260901T200000Z" and all_day is False
    # naive input is treated as local time and normalised to UTC
    naive, _ = C.to_ics_dt("2026-09-01T20:00:00")
    assert naive.endswith("Z") and len(naive) == 16


def test_to_ics_dt_rejects_empty() -> None:
    with pytest.raises(ValueError):
        C.to_ics_dt("")


def test_from_ics_dt_roundtrips() -> None:
    assert C.from_ics_dt("20260901") == "2026-09-01"
    iso = C.from_ics_dt("20260901T200000Z")
    assert dt.datetime.fromisoformat(iso).tzinfo is not None


# --- VEVENT build / parse ---------------------------------------------------


def test_build_vevent_shape() -> None:
    body = C.build_vevent(
        uid="u1",
        title="Dentist",
        start="2026-09-01T15:00:00Z",
        end="2026-09-01T16:00:00Z",
        description="Cleaning",
        location="Main St",
    ).decode()
    for token in (
        "BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:u1", "SUMMARY:Dentist",
        "DTSTART:20260901T150000Z", "DTEND:20260901T160000Z",
        "DESCRIPTION:Cleaning", "LOCATION:Main St", "END:VCALENDAR",
    ):
        assert token in body


def test_build_vevent_all_day_uses_value_date() -> None:
    body = C.build_vevent(
        uid="u2", title="Holiday", start="2026-12-25", end="2026-12-26"
    ).decode()
    assert "DTSTART;VALUE=DATE:20261225" in body


def test_parse_vevents_reads_fields() -> None:
    ics = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:abc\r\n"
        "DTSTART:20260901T150000Z\r\nDTEND:20260901T160000Z\r\n"
        "SUMMARY:Team sync\r\nLOCATION:Room 2\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    rows = C.parse_vevents(ics)
    assert len(rows) == 1
    assert rows[0]["uid"] == "abc"
    assert rows[0]["summary"] == "Team sync"
    assert rows[0]["dtstart"] == "20260901T150000Z"


def test_parse_vevents_unescapes_and_unfolds() -> None:
    ics = (
        "BEGIN:VEVENT\r\nUID:x\r\nSUMMARY:Lunch\\, then\r\n  walk\r\n"
        "DTSTART:20260901T150000Z\r\nEND:VEVENT\r\n"
    )
    assert C.parse_vevents(ics)[0]["summary"] == "Lunch, then walk"


def test_parse_vevents_multiple() -> None:
    ics = "".join(
        f"BEGIN:VEVENT\r\nUID:u{i}\r\nSUMMARY:E{i}\r\nDTSTART:2026090{i}T150000Z\r\nEND:VEVENT\r\n"
        for i in (1, 2)
    )
    assert [r["uid"] for r in C.parse_vevents(ics)] == ["u1", "u2"]


# --- multistatus → events ---------------------------------------------------


MULTISTATUS = """<?xml version="1.0" encoding="UTF-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
 <D:response>
  <D:href>/calendar/dav/me@gmail.com/events/ev1.ics</D:href>
  <D:propstat><D:prop><C:calendar-data>BEGIN:VCALENDAR
BEGIN:VEVENT
UID:ev1
DTSTART:20260902T150000Z
DTEND:20260902T160000Z
SUMMARY:Second
END:VEVENT
END:VCALENDAR</C:calendar-data></D:prop></D:propstat>
 </D:response>
 <D:response>
  <D:href>/calendar/dav/me@gmail.com/events/ev0.ics</D:href>
  <D:propstat><D:prop><C:calendar-data>BEGIN:VCALENDAR
BEGIN:VEVENT
UID:ev0
DTSTART:20260901T150000Z
DTEND:20260901T160000Z
SUMMARY:First
END:VEVENT
END:VCALENDAR</C:calendar-data></D:prop></D:propstat>
 </D:response>
</D:multistatus>"""


def test_multistatus_parses_and_sorts_by_start() -> None:
    evs = C.CalDavCalendarProvider._events_from_multistatus(MULTISTATUS)
    assert [e.title for e in evs] == ["First", "Second"]  # sorted by start
    assert evs[0].id.endswith("ev0.ics")
    assert evs[0].raw["uid"] == "ev0"


def test_multistatus_empty_is_safe() -> None:
    assert C.CalDavCalendarProvider._events_from_multistatus("<D:multistatus/>") == []


# --- provider wiring (fake transport) ---------------------------------------


class FakeDav(C.CalDavCalendarProvider):
    def __init__(self, get_body: str = "") -> None:
        super().__init__(
            C.CalDavAccount(address="me@gmail.com", password="pw", url="https://x/dav/")
        )
        self.calls: list[tuple[str, str, bytes | None]] = []
        self._get_body = get_body

    def _dav(self, method, url="", body=None, *, depth="0", content_type=""):  # type: ignore[override]
        self.calls.append((method, url or self.account.url, body))
        if method == "GET":
            return 200, self._get_body
        if method == "REPORT":
            return 207, MULTISTATUS
        if method == "DELETE":
            return 204, ""
        return 201, ""


EXISTING = (
    "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:keepme\r\n"
    "DTSTART:20260901T150000Z\r\nDTEND:20260901T160000Z\r\n"
    "SUMMARY:Dentist\r\nDESCRIPTION:Cleaning\r\nLOCATION:Main St\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_create_event_puts_ics() -> None:
    dav = FakeDav()
    ev = dav.create_event(
        title="New", start="2026-09-01T15:00:00Z", end="2026-09-01T16:00:00Z"
    )
    method, url, body = dav.calls[-1]
    assert method == "PUT" and url.endswith(".ics")
    assert b"SUMMARY:New" in body
    assert ev.title == "New"


def test_update_event_preserves_untouched_fields() -> None:
    """Rescheduling must not wipe title/description/location."""
    dav = FakeDav(get_body=EXISTING)
    dav.update_event("keepme.ics", start="2026-09-02T18:00:00Z", end="2026-09-02T19:00:00Z")
    put = [c for c in dav.calls if c[0] == "PUT"][-1][2].decode()
    assert "SUMMARY:Dentist" in put  # kept
    assert "DESCRIPTION:Cleaning" in put  # kept
    assert "LOCATION:Main St" in put  # kept
    assert "DTSTART:20260902T180000Z" in put  # changed
    assert "UID:keepme" in put  # same event, not a duplicate


def test_delete_event_calls_delete() -> None:
    dav = FakeDav()
    out = dav.delete_event("ev1.ics")
    assert dav.calls[-1][0] == "DELETE"
    assert out["ok"] is True


def test_event_url_forms() -> None:
    dav = FakeDav()
    assert dav._event_url("abc").endswith("/abc.ics")
    assert dav._event_url("abc.ics").endswith("/abc.ics")
    assert dav._event_url("https://x/dav/z.ics") == "https://x/dav/z.ics"
    assert dav._event_url("/dav/me/z.ics").startswith("https://x")


def test_list_events_uses_report() -> None:
    dav = FakeDav()
    evs = dav.list_events(time_min="2026-09-01T00:00:00Z", time_max="2026-10-01T00:00:00Z")
    assert dav.calls[-1][0] == "REPORT"
    assert len(evs) == 2
