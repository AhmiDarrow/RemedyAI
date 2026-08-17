"""Phase 3 — follow-through: reschedule/cancel events, reply in thread, archive.

These are the verbs that CHANGE something that already exists. The reply path
is the subtle one: without In-Reply-To/References/threadId a "reply" starts a
new conversation in the recipient's client.
"""

from __future__ import annotations

import base64
import email
import json
from typing import Any

import pytest

from remedy.assistant.providers.google_calendar import GoogleCalendarProvider
from remedy.assistant.providers.google_gmail import GoogleGmailProvider


class FakeCalendar(GoogleCalendarProvider):
    """Calendar provider with the HTTP layer replaced by a recorder."""

    def __init__(self) -> None:
        super().__init__(home=None)
        self.calls: list[tuple[str, str, dict | None]] = []

    def _request(self, method, path, *, query=None, body=None):  # type: ignore[override]
        self.calls.append((method, path, body))
        if method == "DELETE":
            return {}
        return {
            "id": "ev123",
            "summary": (body or {}).get("summary", "Dentist"),
            "start": (body or {}).get("start", {"dateTime": "2026-09-01T15:00:00-05:00"}),
            "end": (body or {}).get("end", {"dateTime": "2026-09-01T16:00:00-05:00"}),
            "description": (body or {}).get("description", ""),
            "location": (body or {}).get("location", ""),
        }


class FakeGmail(GoogleGmailProvider):
    def __init__(self, original: dict[str, Any] | None = None) -> None:
        super().__init__(home=None)
        self.calls: list[tuple[str, str, dict | None]] = []
        self._original = original or {
            "id": "m1",
            "threadId": "t42",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<orig@example.com>"},
                    {"name": "From", "value": "Landlord <landlord@example.com>"},
                    {"name": "Subject", "value": "Rent increase notice"},
                    {"name": "Cc", "value": "agent@example.com"},
                ]
            },
        }

    def _request(self, method, path, *, query=None, body=None):  # type: ignore[override]
        self.calls.append((method, path, body))
        if method == "GET":
            return self._original
        if path.endswith("/send"):
            return {"id": "sent1", "threadId": body.get("threadId", "t42")}
        if "/modify" in path:
            return {"id": "m1", "labelIds": ["IMPORTANT"]}
        return {}


def _decode_raw(body: dict[str, Any]):
    raw = base64.urlsafe_b64decode(body["raw"].encode("ascii"))
    return email.message_from_bytes(raw)


# --- calendar follow-through ------------------------------------------------


def test_update_event_patches_only_given_fields() -> None:
    cal = FakeCalendar()
    ev = cal.update_event("ev123", start="2026-09-02T15:00:00", end="2026-09-02T16:00:00")
    method, path, body = cal.calls[-1]
    assert method == "PATCH"
    assert path == "/calendars/primary/events/ev123"
    # only start/end sent — title/description untouched
    assert set(body) == {"start", "end"}
    assert ev.id == "ev123"


def test_update_event_requires_something_to_change() -> None:
    cal = FakeCalendar()
    with pytest.raises(RuntimeError, match="Nothing to update"):
        cal.update_event("ev123")


def test_update_event_adds_timezone_to_naive_iso() -> None:
    cal = FakeCalendar()
    cal.update_event("ev123", start="2026-09-02T15:00:00")
    _m, _p, body = cal.calls[-1]
    dt = body["start"]["dateTime"]
    # Google 400s on a naive datetime; an offset must be appended
    assert dt.endswith(("Z",)) or dt[-6] in "+-"


def test_update_event_all_day_uses_date_field() -> None:
    cal = FakeCalendar()
    cal.update_event("ev123", start="2026-09-02", end="2026-09-03")
    _m, _p, body = cal.calls[-1]
    assert body["start"] == {"date": "2026-09-02"}


def test_delete_event_calls_delete() -> None:
    cal = FakeCalendar()
    out = cal.delete_event("ev123")
    method, path, _b = cal.calls[-1]
    assert method == "DELETE" and path.endswith("/ev123")
    assert out["ok"] is True


# --- mail reply threading ---------------------------------------------------


def test_reply_sets_threading_headers_and_thread_id() -> None:
    gm = FakeGmail()
    out = gm.reply_to_message("m1", body="Sounds good, thanks.")
    _m, path, body = gm.calls[-1]
    assert path.endswith("/send")
    # threadId keeps Gmail's own grouping
    assert body["threadId"] == "t42"
    msg = _decode_raw(body)
    # headers keep every OTHER client threading it too
    assert msg["In-Reply-To"] == "<orig@example.com>"
    assert "<orig@example.com>" in msg["References"]
    assert msg["To"] == "Landlord <landlord@example.com>"
    assert out["thread_id"] == "t42"


def test_reply_prefixes_subject_once() -> None:
    gm = FakeGmail()
    gm.reply_to_message("m1", body="ok")
    _m, _p, body = gm.calls[-1]
    assert _decode_raw(body)["Subject"] == "Re: Rent increase notice"

    already = FakeGmail(
        {
            "id": "m2",
            "threadId": "t9",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<x@e.com>"},
                    {"name": "From", "value": "a@e.com"},
                    {"name": "Subject", "value": "Re: Already replied"},
                ]
            },
        }
    )
    already.reply_to_message("m2", body="ok")
    _m, _p, body2 = already.calls[-1]
    assert _decode_raw(body2)["Subject"] == "Re: Already replied"  # not "Re: Re:"


def test_reply_prefers_reply_to_header() -> None:
    gm = FakeGmail(
        {
            "id": "m3",
            "threadId": "t3",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<y@e.com>"},
                    {"name": "From", "value": "noreply@e.com"},
                    {"name": "Reply-To", "value": "support@e.com"},
                    {"name": "Subject", "value": "Ticket"},
                ]
            },
        }
    )
    gm.reply_to_message("m3", body="hi")
    _m, _p, body = gm.calls[-1]
    assert _decode_raw(body)["To"] == "support@e.com"


def test_reply_all_includes_cc_but_default_does_not() -> None:
    gm = FakeGmail()
    gm.reply_to_message("m1", body="x", reply_all=True)
    _m, _p, body = gm.calls[-1]
    assert _decode_raw(body)["Cc"] == "agent@example.com"

    gm2 = FakeGmail()
    gm2.reply_to_message("m1", body="x")
    _m, _p, body2 = gm2.calls[-1]
    assert _decode_raw(body2)["Cc"] is None


def test_reply_requires_message_id_and_address() -> None:
    gm = FakeGmail()
    with pytest.raises(ValueError):
        gm.reply_to_message("", body="x")
    noaddr = FakeGmail({"id": "m4", "threadId": "t4", "payload": {"headers": []}})
    with pytest.raises(RuntimeError, match="reply address"):
        noaddr.reply_to_message("m4", body="x")


def test_plain_send_still_starts_a_new_thread() -> None:
    """Regression guard: send_message must NOT carry threading headers."""
    gm = FakeGmail()
    gm.send_message(to="a@e.com", subject="New", body="hi")
    _m, _p, body = gm.calls[-1]
    assert "threadId" not in body
    assert _decode_raw(body)["In-Reply-To"] is None


# --- labels / archive -------------------------------------------------------


def test_archive_removes_inbox_label() -> None:
    gm = FakeGmail()
    out = gm.archive_message("m1")
    _m, path, body = gm.calls[-1]
    assert "/modify" in path
    assert body == {"removeLabelIds": ["INBOX"]}
    assert "Archived" in out["message"]


def test_mark_read_and_unread() -> None:
    gm = FakeGmail()
    gm.mark_read("m1")
    assert gm.calls[-1][2] == {"removeLabelIds": ["UNREAD"]}
    gm.mark_read("m1", read=False)
    assert gm.calls[-1][2] == {"addLabelIds": ["UNREAD"]}


def test_modify_labels_needs_something() -> None:
    gm = FakeGmail()
    with pytest.raises(RuntimeError, match="Nothing to change"):
        gm.modify_labels("m1")


# --- tool registration ------------------------------------------------------


def test_followthrough_tools_registered() -> None:
    from types import SimpleNamespace

    from remedy.core.agent_assistant_tools import register_assistant_tools
    from remedy.skills.tool_registry import ToolRegistry

    reg = ToolRegistry()
    rt = SimpleNamespace(tool_registry=reg, config=SimpleNamespace(home_dir=None))
    rt.list_tasks = lambda: []
    register_assistant_tools(rt)
    names = {t.name for t in reg.tools}
    for want in (
        "calendar_update_event",
        "calendar_cancel_event",
        "mail_reply",
        "mail_archive",
        "mail_mark_read",
    ):
        assert want in names, f"{want} not registered"


@pytest.mark.asyncio
async def test_cancel_and_reply_need_ids() -> None:
    from types import SimpleNamespace

    from remedy.core.agent_assistant_tools import register_assistant_tools
    from remedy.skills.tool_registry import ToolRegistry

    reg = ToolRegistry()
    rt = SimpleNamespace(tool_registry=reg, config=SimpleNamespace(home_dir=None))
    rt.list_tasks = lambda: []
    register_assistant_tools(rt)
    # No Google connected in tests → a clear message, never a crash.
    for name, kwargs in (
        ("calendar_cancel_event", {"event_id": ""}),
        ("mail_reply", {"message_id": "", "body": "x"}),
        ("mail_archive", {"message_id": ""}),
    ):
        out = await reg.execute(name, **kwargs)
        assert isinstance(out, str) and out.strip()
        parsed = json.loads(out)
        assert parsed["ok"] is False
