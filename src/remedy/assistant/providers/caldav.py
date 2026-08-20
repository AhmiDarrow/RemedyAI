"""Calendar over CalDAV with an app password — no cloud project required.

The Google Calendar *API* needs OAuth, which needs a registered Cloud project.
CalDAV does not: the same app password that connects the mailbox also reaches
the calendar, so Remedy can read and change appointments with zero setup.

Verified working against Google (legacy ``/calendar/dav/`` endpoint — the newer
``apidata.googleusercontent.com`` path rejects Basic auth with 401
loginRequired), and the same code serves iCloud and Fastmail.

Standard library only: ``urllib`` for CalDAV/WebDAV, plus a small iCalendar
(VEVENT) builder/parser — the subset a personal calendar actually needs.
"""

from __future__ import annotations

import base64
import datetime as _dt
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remedy.assistant.providers.base import CalendarEvent
from remedy.core.security import urlopen_no_redirect

logger = logging.getLogger(__name__)

# Google's modern CalDAV host refuses Basic auth (401 loginRequired); the legacy
# path still serves app passwords. iCloud/Fastmail use their own hosts.
#: Ceiling on what a calendar server may hand back. Real REPORT responses are
#: kilobytes; this only stops a hostile or broken one exhausting memory.
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
#: Error bodies are quoted into messages, so they stay small.
_MAX_ERROR_BYTES = 64 * 1024

# Same-origin redirects are followed, but only this many deep.
_MAX_REDIRECTS = 3
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

_DEFAULT_PORTS = {"https": 443, "http": 80}


def _origin(parts: urllib.parse.SplitResult) -> tuple[str, str, int | None]:
    """(scheme, host, effective port) — the identity credentials may go to.

    Compared on these, not on the raw netloc: ``https://h`` and
    ``https://h:443/`` are the same server, and ``https://me%40x:pw@h`` in the
    configured URL is still just ``h``. Userinfo is ignored on both sides.
    """
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None:
        port = _DEFAULT_PORTS.get(scheme)
    return scheme, host, port

CALDAV_PRESETS: dict[str, str] = {
    "gmail.com": "https://www.google.com/calendar/dav/{email}/events/",
    "googlemail.com": "https://www.google.com/calendar/dav/{email}/events/",
    "icloud.com": "https://caldav.icloud.com/",
    "me.com": "https://caldav.icloud.com/",
    "fastmail.com": "https://caldav.fastmail.com/dav/calendars/user/{email}/Default/",
}

_TEXT_ESCAPES = ((chr(92), chr(92) * 2), (";", r"\;"), (",", r"\,"), ("\n", r"\n"))


def caldav_url_for(address: str) -> str:
    dom = (address or "").split("@")[-1].strip().lower()
    tpl = CALDAV_PRESETS.get(dom) or ""
    return tpl.format(email=urllib.parse.quote(address)) if tpl else ""


# --- tiny iCalendar helpers -------------------------------------------------


def ics_escape(value: str) -> str:
    out = str(value or "")
    for raw, esc in _TEXT_ESCAPES:
        out = out.replace(raw, esc)
    return out


def ics_unescape(value: str) -> str:
    out = str(value or "")
    for raw, esc in reversed(_TEXT_ESCAPES):
        out = out.replace(esc, raw)
    return out


def unfold(text: str) -> list[str]:
    """iCalendar folds long lines with CRLF + space/tab. Rejoin them."""
    lines: list[str] = []
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def fold(line: str, limit: int = 73) -> str:
    """Fold a content line so servers do not reject an over-long one."""
    s = str(line or "")
    if len(s) <= limit:
        return s
    out = [s[:limit]]
    rest = s[limit:]
    while rest:
        out.append(" " + rest[: limit - 1])
        rest = rest[limit - 1 :]
    return "\r\n".join(out)


def to_ics_dt(value: str) -> tuple[str, bool]:
    """('20260901T200000Z', False) for timed, ('20260901', True) for all-day."""
    s = str(value or "").strip()
    if not s:
        raise ValueError("empty datetime")
    if len(s) == 10 and s[4] == "-" and s[7] == "-":  # YYYY-MM-DD
        return s.replace("-", ""), True
    iso = s.replace("Z", "+00:00")
    dt = _dt.datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # assume the owner's local zone
    return dt.astimezone(_dt.UTC).strftime("%Y%m%dT%H%M%SZ"), False


def from_ics_dt(value: str) -> str:
    """ICS stamp → ISO for the model ('20260901T200000Z' → local ISO)."""
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) == 8 and s.isdigit():  # all-day
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    m = re.match(r"^(\d{8})T(\d{6})(Z?)$", s)
    if not m:
        return s
    d, t, z = m.groups()
    base = f"{d[0:4]}-{d[4:6]}-{d[6:8]}T{t[0:2]}:{t[2:4]}:{t[4:6]}"
    if z:
        return (
            _dt.datetime.fromisoformat(base)
            .replace(tzinfo=_dt.UTC)
            .astimezone()
            .isoformat(timespec="seconds")
        )
    return base


def build_vevent(
    *,
    uid: str,
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
) -> bytes:
    """Minimal but valid VCALENDAR/VEVENT."""
    dtstart, all_day = to_ics_dt(start)
    dtend, _ = to_ics_dt(end) if end else (dtstart, all_day)
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    sd = ";VALUE=DATE" if all_day else ""
    rows = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Remedy//Partner//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART{sd}:{dtstart}",
        f"DTEND{sd}:{dtend}",
        fold(f"SUMMARY:{ics_escape(title)}"),
    ]
    if description:
        rows.append(fold(f"DESCRIPTION:{ics_escape(description)}"))
    if location:
        rows.append(fold(f"LOCATION:{ics_escape(location)}"))
    rows += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(rows) + "\r\n").encode("utf-8")


def parse_vevents(ics_text: str) -> list[dict[str, str]]:
    """Pull the fields we care about out of one or more VEVENTs."""
    out: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    for line in unfold(ics_text):
        s = line.strip()
        if s == "BEGIN:VEVENT":
            cur = {}
            continue
        if s == "END:VEVENT":
            if cur is not None:
                out.append(cur)
            cur = None
            continue
        if cur is None or ":" not in s:
            continue
        name, _, value = s.partition(":")
        key = name.split(";")[0].upper()
        if key in ("UID", "SUMMARY", "DESCRIPTION", "LOCATION"):
            cur[key.lower()] = ics_unescape(value)
        elif key in ("DTSTART", "DTEND"):
            cur[key.lower()] = value.strip()
    return out


# --- provider ---------------------------------------------------------------


@dataclass
class CalDavAccount:
    address: str
    password: str
    url: str = ""

    def is_ready(self) -> bool:
        return bool(self.address and self.password and self.url)


class CalDavCalendarProvider:
    """CalendarProvider over CalDAV (app password, no OAuth)."""

    provider_id = "caldav"

    def __init__(self, account: CalDavAccount) -> None:
        self.account = account

    # -- transport ----------------------------------------------------------

    def _auth_header(self) -> str:
        raw = f"{self.account.address}:{self.account.password}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _require_same_origin(self, target: str) -> None:
        """Every request carries the app password; none may leave the host."""
        got = urllib.parse.urlsplit(str(target or ""))
        if _origin(got) != _origin(urllib.parse.urlsplit(self.account.url)):
            raise RuntimeError(
                "Refusing to send calendar credentials to "
                f"{got.netloc or str(target)[:40]!r} — that is not your calendar host."
            )

    def _dav(
        self,
        method: str,
        url: str = "",
        body: bytes | None = None,
        *,
        depth: str = "0",
        content_type: str = "application/xml; charset=utf-8",
    ) -> tuple[int, str]:
        target = url or self.account.url
        # Bounded redirect chase. urllib carries the Authorization header
        # across a redirect, so ``urlopen_no_redirect`` refuses to follow any
        # Location on its own; we follow it here only after the new URL has
        # passed the same-origin check the first one did. Calendar servers do
        # redirect (a PROPFIND on the bare host to the principal path, a
        # trailing-slash canonicalisation), so "every 3xx is fatal" broke
        # legitimate accounts; "follow anything" leaked the app password.
        for _hop in range(_MAX_REDIRECTS + 1):
            self._require_same_origin(target)
            req = urllib.request.Request(
                target,
                data=body,
                method=method,
                headers={
                    "Authorization": self._auth_header(),
                    "Depth": depth,
                    "Content-Type": content_type,
                    "User-Agent": "Remedy-Calendar/1.0",
                },
            )
            try:
                with urlopen_no_redirect(req, timeout=30) as resp:
                    # Bounded: a calendar answer is kilobytes, and a hostile or
                    # broken server should not be able to hand us a body larger
                    # than memory. 32 MiB is far past any real REPORT.
                    data = resp.read(_MAX_RESPONSE_BYTES)
                    return resp.status, data.decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                detail = e.read(_MAX_ERROR_BYTES).decode("utf-8", "replace")
                location = e.headers.get("Location") if e.headers is not None else None
                if e.code in _REDIRECT_CODES and location:
                    if _hop >= _MAX_REDIRECTS:
                        raise RuntimeError(
                            f"CalDAV {e.code}: too many redirects (more than {_MAX_REDIRECTS})"
                        ) from e
                    target = urllib.parse.urljoin(target, location)
                    continue  # re-checked at the top of the loop
                if e.code in (401, 403):
                    raise RuntimeError(
                        "The calendar rejected that app password. Same password as "
                        "mail; make sure 2-step verification is on and the calendar "
                        "is enabled for this account."
                    ) from e
                if e.code == 404:
                    raise RuntimeError(
                        "Calendar not found at that address — check the account."
                    ) from e
                raise RuntimeError(f"CalDAV {e.code}: {detail[:200]}") from e
            except Exception as e:  # network / TLS
                raise RuntimeError(f"Calendar unreachable: {e}") from e
        raise RuntimeError("CalDAV: redirect loop")  # pragma: no cover

    def verify(self) -> dict[str, Any]:
        status, _ = self._dav(
            "PROPFIND",
            body=(
                b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
                b"<d:prop><d:displayname/></d:prop></d:propfind>"
            ),
        )
        return {
            "ok": status in (200, 207),
            "address": self.account.address,
            "url": self.account.url,
            "message": f"Calendar reachable for {self.account.address}",
        }

    def _event_url(self, event_id: str) -> str:
        """Build an event URL that can only ever point at the owner's calendar.

        Event ids are the ``<href>`` values from the server's own REPORT
        response, and they also arrive from the model as tool arguments. An
        absolute one used to be returned verbatim — and ``_dav`` puts the
        mailbox app password in an ``Authorization`` header — so a calendar
        entry (or a prompt-injected id) reading ``http://elsewhere/x`` was
        enough to send that credential to a stranger. Same origin or nothing.
        """
        eid = str(event_id or "").strip()
        base = urllib.parse.urlsplit(self.account.url)
        if not eid:
            raise ValueError("An event id is required")
        if "://" in eid or eid.lower().startswith(("http:", "https:")):
            got = urllib.parse.urlsplit(eid)
            if _origin(got) != _origin(base):
                raise RuntimeError(
                    "Refusing to send calendar credentials to "
                    f"{got.netloc or eid[:40]!r} — that is not your calendar host."
                )
            return eid
        if eid.startswith("/"):
            return f"{base.scheme}://{base.netloc}{eid}"
        if not eid.endswith(".ics"):
            eid = f"{eid}.ics"
        return self.account.url.rstrip("/") + "/" + eid

    # -- read ---------------------------------------------------------------

    def list_events(self, *, time_min: str, time_max: str) -> list[CalendarEvent]:
        start, _ = to_ics_dt(time_min)
        end, _ = to_ics_dt(time_max)
        query = (
            '<?xml version="1.0"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            '<c:filter><c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT">'
            f'<c:time-range start="{start}" end="{end}"/>'
            "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
        ).encode()
        _st, text = self._dav("REPORT", body=query, depth="1")
        return self._events_from_multistatus(text)

    @staticmethod
    def _events_from_multistatus(text: str) -> list[CalendarEvent]:
        out: list[CalendarEvent] = []
        # Pair each <response> href with the VEVENT inside it.
        for block in re.split(r"(?i)<D?:?response[ >]", text)[1:]:
            href_m = re.search(r"(?i)<D?:?href>([^<]+)</D?:?href>", block)
            if not href_m:
                continue
            href = href_m.group(1).strip()
            for ev in parse_vevents(block):
                out.append(
                    CalendarEvent(
                        id=href,
                        title=ev.get("summary", "(no title)"),
                        start=from_ics_dt(ev.get("dtstart", "")),
                        end=from_ics_dt(ev.get("dtend", "")),
                        description=ev.get("description", ""),
                        location=ev.get("location", ""),
                        raw={"href": href, "uid": ev.get("uid", "")},
                    )
                )
        out.sort(key=lambda e: e.start or "")
        return out

    def get_event(self, event_id: str) -> CalendarEvent:
        url = self._event_url(event_id)
        _st, text = self._dav("GET", url, content_type="text/calendar")
        rows = parse_vevents(text)
        if not rows:
            raise RuntimeError(f"No event found at {event_id}")
        ev = rows[0]
        return CalendarEvent(
            id=event_id,
            title=ev.get("summary", "(no title)"),
            start=from_ics_dt(ev.get("dtstart", "")),
            end=from_ics_dt(ev.get("dtend", "")),
            description=ev.get("description", ""),
            location=ev.get("location", ""),
            raw={"uid": ev.get("uid", ""), "ics": text},
        )

    # -- write --------------------------------------------------------------

    def create_event(
        self, *, title: str, start: str, end: str, description: str = ""
    ) -> CalendarEvent:
        uid = f"remedy-{uuid.uuid4().hex[:16]}"
        body = build_vevent(
            uid=uid, title=title, start=start, end=end, description=description
        )
        url = self._event_url(uid)
        status, _ = self._dav(
            "PUT", url, body, content_type="text/calendar; charset=utf-8"
        )
        if status not in (200, 201, 204):
            raise RuntimeError(f"Calendar refused the new event (HTTP {status})")
        return CalendarEvent(
            id=url,
            title=title,
            start=from_ics_dt(to_ics_dt(start)[0]),
            end=from_ics_dt(to_ics_dt(end)[0]) if end else "",
            description=description,
            location="",
            raw={"uid": uid},
        )

    def update_event(
        self,
        event_id: str,
        *,
        title: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
        location: str = "",
    ) -> CalendarEvent:
        """Reschedule/edit — reads the event first so untouched fields survive."""
        current = self.get_event(event_id)
        uid = (
            str((current.raw or {}).get("uid") or "")
            or f"remedy-{uuid.uuid4().hex[:16]}"
        )
        body = build_vevent(
            uid=uid,
            title=title or current.title,
            start=start or current.start,
            end=end or current.end,
            description=description or current.description,
            location=location or current.location,
        )
        url = self._event_url(event_id)
        status, _ = self._dav(
            "PUT", url, body, content_type="text/calendar; charset=utf-8"
        )
        if status not in (200, 201, 204):
            raise RuntimeError(f"Calendar refused the update (HTTP {status})")
        return self.get_event(event_id)

    def delete_event(self, event_id: str) -> dict[str, Any]:
        url = self._event_url(event_id)
        status, _ = self._dav("DELETE", url)
        if status not in (200, 204, 404):
            raise RuntimeError(f"Calendar refused the delete (HTTP {status})")
        return {"ok": True, "event_id": event_id, "message": "Event cancelled"}


# --- wiring -----------------------------------------------------------------


def load_caldav_account(home: Path | str | None = None) -> CalDavAccount | None:
    """Reuse the mailbox app password — same account, same credential."""
    from remedy.assistant.providers.imap_smtp import load_mail_account

    acct = load_mail_account(home)
    if acct is None:
        return None
    url = caldav_url_for(acct.address)
    cal = CalDavAccount(address=acct.address, password=acct.password, url=url)
    return cal if cal.is_ready() else None


def get_caldav_calendar(home: Path | str | None = None) -> CalDavCalendarProvider | None:
    acct = load_caldav_account(home)
    return CalDavCalendarProvider(acct) if acct is not None else None
