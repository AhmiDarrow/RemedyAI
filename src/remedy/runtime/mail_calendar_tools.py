"""RMDY Tool ABI bridges for mail.* / calendar.* (Google + IMAP/CalDAV)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any


def _home(inp: Mapping[str, Any]) -> Any:
    return inp.get("home_dir") or None


def _mail_provider(home: Any):
    from remedy.assistant.providers.google_gmail import get_google_gmail
    from remedy.assistant.providers.imap_smtp import get_imap_mail

    return get_google_gmail(home) or get_imap_mail(home)


def _calendar_provider(home: Any):
    from remedy.assistant.providers.google_calendar import get_google_calendar

    try:
        from remedy.assistant.providers.caldav import get_caldav_calendar

        cal = get_google_calendar(home) or get_caldav_calendar(home)
    except Exception:
        cal = get_google_calendar(home)
    return cal


def mail_list(inp: Mapping[str, Any]) -> dict[str, Any]:
    home = _home(inp)
    provider = _mail_provider(home)
    if provider is None:
        return {
            "ok": False,
            "messages": [],
            "count": 0,
            "error": "No mail account connected. Connect Google or IMAP in Settings → Personal assistant.",
        }
    query = str(inp.get("query") or "in:inbox").strip() or "in:inbox"
    limit = int(inp.get("limit") or 12)
    limit = max(1, min(limit, 50))
    messages = provider.list_messages(query=query, limit=limit)
    rows: list[dict[str, Any]] = []
    for m in messages:
        rows.append(
            {
                "id": getattr(m, "id", "") or "",
                "from": getattr(m, "from_addr", "") or "",
                "subject": getattr(m, "subject", "") or "",
                "snippet": getattr(m, "snippet", "") or "",
                "date": getattr(m, "date", "") or "",
            }
        )
    return {"ok": True, "messages": rows, "count": len(rows), "query": query}


def mail_send(inp: Mapping[str, Any]) -> dict[str, Any]:
    home = _home(inp)
    provider = _mail_provider(home)
    if provider is None:
        return {
            "ok": False,
            "error": "No mail account connected. Connect Google or IMAP in Settings → Personal assistant.",
        }
    to = str(inp.get("to") or "").strip()
    subject = str(inp.get("subject") or "").strip()
    body = str(inp.get("body") or inp.get("text") or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "error": "to must be a valid email address"}
    if not body and not subject:
        return {"ok": False, "error": "subject or body is required"}
    result = provider.send_message(to=to, subject=subject, body=body)
    if isinstance(result, dict):
        out = dict(result)
        out.setdefault("ok", True)
        return out
    return {"ok": True, "to": to, "subject": subject, "message": "sent"}


def calendar_list_events(inp: Mapping[str, Any]) -> dict[str, Any]:
    home = _home(inp)
    provider = _calendar_provider(home)
    if provider is None:
        return {
            "ok": False,
            "events": [],
            "count": 0,
            "error": "No calendar connected. Connect Google Calendar in Settings → Personal assistant.",
        }
    days = int(inp.get("days") or 7)
    days = max(1, min(days, 60))
    now = datetime.now(UTC)
    time_min = now.isoformat().replace("+00:00", "Z")
    time_max = (now + timedelta(days=days)).isoformat().replace("+00:00", "Z")
    if inp.get("time_min"):
        time_min = str(inp["time_min"])
    if inp.get("time_max"):
        time_max = str(inp["time_max"])
    events = provider.list_events(time_min=time_min, time_max=time_max)
    rows: list[dict[str, Any]] = []
    for e in events:
        rows.append(
            {
                "id": getattr(e, "id", "") or "",
                "title": getattr(e, "title", "") or "",
                "start": getattr(e, "start", "") or "",
                "end": getattr(e, "end", "") or "",
                "location": getattr(e, "location", "") or "",
                "description": (getattr(e, "description", "") or "")[:500],
            }
        )
    return {"ok": True, "events": rows, "count": len(rows), "days": days}


def calendar_create_event(inp: Mapping[str, Any]) -> dict[str, Any]:
    home = _home(inp)
    provider = _calendar_provider(home)
    if provider is None:
        return {
            "ok": False,
            "error": "No calendar connected. Connect Google Calendar in Settings → Personal assistant.",
        }
    title = str(inp.get("title") or "").strip()
    start = str(inp.get("start") or "").strip()
    end = str(inp.get("end") or "").strip()
    description = str(inp.get("description") or "").strip()
    if not title or not start or not end:
        return {"ok": False, "error": "title, start, and end are required"}
    event = provider.create_event(
        title=title, start=start, end=end, description=description
    )
    return {
        "ok": True,
        "id": getattr(event, "id", "") or "",
        "title": getattr(event, "title", "") or title,
        "start": getattr(event, "start", "") or start,
        "end": getattr(event, "end", "") or end,
        "message": f"Created calendar event: {title}",
    }
