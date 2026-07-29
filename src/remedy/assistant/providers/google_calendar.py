"""Google Calendar API adapter (list / create events)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from remedy.assistant.providers.base import CalendarEvent, CalendarProvider

logger = logging.getLogger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarProvider:
    """CalendarProvider backed by Google Calendar API v3."""

    provider_id = "google"

    def __init__(self, home: Path | str | None = None) -> None:
        self.home = home

    def _bearer(self) -> str:
        from remedy.assistant.google_oauth import get_valid_access_token

        return get_valid_access_token(self.home)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{CALENDAR_API}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {
            "Authorization": f"Bearer {self._bearer()}",
            "Accept": "application/json",
            "User-Agent": "RemedyDesktop-Google-Calendar/1.0",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(err)
                msg = parsed.get("error", {}).get("message") or err
            except json.JSONDecodeError:
                msg = err or str(e)
            raise RuntimeError(f"Google Calendar API {e.code}: {msg}") from e

    def list_events(self, *, time_min: str, time_max: str) -> list[CalendarEvent]:
        data = self._request(
            "GET",
            "/calendars/primary/events",
            query={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "40",
            },
        )
        out: list[CalendarEvent] = []
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            start = item.get("start") or {}
            end = item.get("end") or {}
            start_s = str(start.get("dateTime") or start.get("date") or "")
            end_s = str(end.get("dateTime") or end.get("date") or "")
            out.append(
                CalendarEvent(
                    id=str(item.get("id") or ""),
                    title=str(item.get("summary") or "(no title)"),
                    start=start_s,
                    end=end_s,
                    description=str(item.get("description") or ""),
                    location=str(item.get("location") or ""),
                    raw=item,
                )
            )
        return out

    def create_event(
        self,
        *,
        title: str,
        start: str,
        end: str,
        description: str = "",
    ) -> CalendarEvent:
        body: dict[str, Any] = {
            "summary": title,
            "description": description or "",
            "start": _event_time(start),
            "end": _event_time(end),
        }
        item = self._request("POST", "/calendars/primary/events", body=body)
        start_o = item.get("start") or {}
        end_o = item.get("end") or {}
        return CalendarEvent(
            id=str(item.get("id") or ""),
            title=str(item.get("summary") or title),
            start=str(start_o.get("dateTime") or start_o.get("date") or start),
            end=str(end_o.get("dateTime") or end_o.get("date") or end),
            description=str(item.get("description") or description or ""),
            location=str(item.get("location") or ""),
            raw=item,
        )


def _event_time(iso_or_date: str) -> dict[str, str]:
    s = (iso_or_date or "").strip()
    # All-day: YYYY-MM-DD
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return {"date": s}
    return {"dateTime": s}


def get_google_calendar(home: Path | str | None = None) -> CalendarProvider | None:
    from remedy.assistant.google_oauth import load_tokens

    if not load_tokens(home).connected:
        return None
    return GoogleCalendarProvider(home=home)
