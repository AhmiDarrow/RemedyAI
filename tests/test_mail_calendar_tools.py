"""Unit tests for RMDY mail.* / calendar.* bridges (no live Google)."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.runtime import mail_calendar_tools as mct


class _FakeMail:
    provider_id = "fake"

    def list_messages(self, *, query: str = "", limit: int = 20):
        return [
            SimpleNamespace(
                id="1",
                subject="Hello",
                from_addr="a@example.com",
                snippet="hi",
                date="2026-01-01",
            )
        ]

    def send_message(self, *, to: str, subject: str, body: str):
        return {"ok": True, "message_id": "m1", "to": to, "subject": subject}


class _FakeCal:
    provider_id = "fake"

    def list_events(self, *, time_min: str, time_max: str):
        return [
            SimpleNamespace(
                id="e1",
                title="Standup",
                start=time_min,
                end=time_max,
                location="",
                description="",
            )
        ]

    def create_event(self, *, title: str, start: str, end: str, description: str = ""):
        return SimpleNamespace(
            id="e2", title=title, start=start, end=end, location="", description=description
        )


def test_mail_list_no_provider(monkeypatch):
    monkeypatch.setattr(mct, "_mail_provider", lambda home: None)
    out = mct.mail_list({})
    assert out["ok"] is False
    assert out["count"] == 0


def test_mail_list_and_send(monkeypatch):
    monkeypatch.setattr(mct, "_mail_provider", lambda home: _FakeMail())
    listed = mct.mail_list({"query": "in:inbox", "limit": 5})
    assert listed["ok"] is True
    assert listed["count"] == 1
    assert listed["messages"][0]["from"] == "a@example.com"
    sent = mct.mail_send({"to": "b@example.com", "subject": "Hi", "body": "Body"})
    assert sent["ok"] is True
    assert sent["message_id"] == "m1"
    bad = mct.mail_send({"to": "nope", "body": "x"})
    assert bad["ok"] is False


def test_calendar_list_and_create(monkeypatch):
    monkeypatch.setattr(mct, "_calendar_provider", lambda home: _FakeCal())
    listed = mct.calendar_list_events({"days": 3})
    assert listed["ok"] is True
    assert listed["count"] == 1
    created = mct.calendar_create_event(
        {"title": "Sync", "start": "2026-01-02T10:00:00Z", "end": "2026-01-02T11:00:00Z"}
    )
    assert created["ok"] is True
    assert created["id"] == "e2"
