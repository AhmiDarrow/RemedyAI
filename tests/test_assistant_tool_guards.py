"""The guards on the assistant's mail and calendar tools.

These tools act on the owner's real mailbox and real diary: they send mail,
cancel appointments and hand message text to an AI provider. Everything here
is about what must NOT happen — refusing before a mailbox is connected,
refusing before the owner accepted the privacy notice, refusing an empty
event id instead of guessing, turning a provider blow-up into a message
rather than a crash, and never echoing a password or a draft body back into
the model's transcript. If any of that slips, the failure is silent and it is
the owner who pays for it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from remedy.assistant.providers.base import CalendarEvent, MailMessage
from remedy.core.agent_assistant_tools import register_assistant_tools

# ── doubles ───────────────────────────────────────────────────────────────


class _Registry:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.handlers[name] = handler
        self.schemas[name] = parameters or {}

    def __getattr__(self, _name):
        return lambda *a, **kw: None


class _Config:
    def __init__(self, home: str) -> None:
        self.home_dir = home

    def __getattr__(self, _name):
        return None


class _Runtime:
    def __init__(self, home: str, tasks=None) -> None:
        self.tool_registry = _Registry()
        self.config = _Config(home)
        self.tasks = tasks

    def list_tasks(self):
        return self.tasks

    def __getattr__(self, _name):
        return None


class _Task:
    def __init__(self, title, tags=(), status="open") -> None:
        self.title = title
        self.tags = list(tags)
        self.status = status


class _ReadOnlyCalendar:
    """List/create only — no update_event, no delete_event."""

    def __init__(self, provider_id="google", events=None, fail="") -> None:
        self.provider_id = provider_id
        self.events = list(events or [])
        self.fail = fail
        self.calls: list[tuple] = []

    def list_events(self, *, time_min, time_max):
        self.calls.append(("list", time_min, time_max))
        if self.fail == "list":
            raise RuntimeError("calendar backend is down")
        return list(self.events)

    def create_event(self, *, title, start, end, description=""):
        self.calls.append(("create", title, start, end, description))
        if self.fail == "create":
            raise RuntimeError("calendar refused the event")
        return CalendarEvent(id="ev-new", title=title, start=start, end=end)

    def get_event(self, event_id):
        self.calls.append(("get", event_id))
        return CalendarEvent(id=event_id, title="Dentist", start="2026-09-01T09:00:00Z")


class _FullCalendar(_ReadOnlyCalendar):
    def update_event(self, event_id, **fields):
        self.calls.append(("update", event_id, fields))
        if self.fail == "update":
            raise RuntimeError("calendar refused the edit")
        return CalendarEvent(
            id=event_id, title=fields.get("title") or "Dentist", start="2026-09-02T09:00:00Z"
        )

    def delete_event(self, event_id):
        self.calls.append(("delete", event_id))
        if self.fail == "delete":
            raise RuntimeError("calendar refused the delete")
        return {"ok": True}


class _ReadOnlyMail:
    """Read + draft only — no send_message, reply, archive or mark_read."""

    def __init__(self, provider_id="google", messages=None, fail="") -> None:
        self.provider_id = provider_id
        self.messages = list(messages or [])
        self.fail = fail
        self.calls: list[tuple] = []
        self.draft_result: dict = {"draft_id": "d1", "to": "a@b.com", "subject": "hi"}

    def list_messages(self, *, query="", limit=20):
        self.calls.append(("list", query, limit))
        if self.fail == "list":
            raise RuntimeError("imap login failed")
        return list(self.messages)

    def get_message(self, message_id):
        self.calls.append(("get", message_id))
        if self.fail == "get":
            raise RuntimeError("no such message")
        return next(
            (m for m in self.messages if m.id == message_id),
            MailMessage(id=message_id, subject="Subject", snippet="body text"),
        )

    def create_draft(self, *, to, subject, body):
        self.calls.append(("draft", to, subject, body))
        if self.fail == "draft":
            raise RuntimeError("draft rejected")
        return dict(self.draft_result)


class _FullMail(_ReadOnlyMail):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.sent: list[dict] = []
        self.send_result: dict = {"message_id": "m1", "to": "a@b.com", "subject": "hi"}
        self.reply_result: dict = {"message_id": "m2", "thread_id": "t1"}

    def send_message(self, *, to, subject, body):
        if self.fail == "send":
            raise RuntimeError("smtp refused")
        self.sent.append({"to": to, "subject": subject, "body": body})
        return dict(self.send_result)

    def reply_to_message(self, message_id, *, body="", reply_all=False):
        self.calls.append(("reply", message_id, body, reply_all))
        if self.fail == "reply":
            raise RuntimeError("thread is gone")
        return dict(self.reply_result)

    def archive_message(self, message_id):
        self.calls.append(("archive", message_id))
        if self.fail == "archive":
            raise RuntimeError("archive folder missing")
        return {"ok": True, "message_id": message_id}

    def mark_read(self, message_id, *, read=True):
        self.calls.append(("mark", message_id, read))
        if self.fail == "mark":
            raise RuntimeError("flag store refused")
        return {"ok": True, "message_id": message_id, "read": read}


# ── fixtures / helpers ────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway REMEDY_HOME — never the owner's real ~/.remedy."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return str(tmp_path)


@pytest.fixture
def registry(home):
    rt = _Runtime(home)
    register_assistant_tools(rt)
    return rt.tool_registry


@pytest.fixture
def tools(registry):
    return registry.handlers


def _make_tools(home, tasks=None):
    rt = _Runtime(home, tasks=tasks)
    register_assistant_tools(rt)
    return rt.tool_registry.handlers


def _accept_consent(home):
    from remedy.assistant.store import get_assistant_store

    get_assistant_store(home).patch_prefs(
        privacy_ai_accepted=True, account_access_accepted=True
    )


def _install_calendar(monkeypatch, cal, *, caldav=False):
    """Point both calendar factories at *cal* (or at nothing when None)."""
    monkeypatch.setattr(
        "remedy.assistant.providers.caldav.get_caldav_calendar",
        lambda _home=None: cal if caldav else None,
    )
    monkeypatch.setattr(
        "remedy.assistant.providers.google_calendar.get_google_calendar",
        lambda _home=None: None if caldav else cal,
    )


def _install_mail(monkeypatch, mail, *, imap=False):
    monkeypatch.setattr(
        "remedy.assistant.providers.imap_smtp.get_imap_mail",
        lambda _home=None: mail if imap else None,
    )
    monkeypatch.setattr(
        "remedy.assistant.providers.google_gmail.get_google_gmail",
        lambda _home=None: None if imap else mail,
    )


def _no_providers(monkeypatch):
    _install_calendar(monkeypatch, None)
    _install_mail(monkeypatch, None)


async def _json(tools, _tool, **kw):
    return json.loads(await tools[_tool](**kw))


@pytest.fixture
def approvals(monkeypatch):
    """The process-wide approval queue, restored to its previous mode."""
    from remedy.core.approvals import APPROVALS

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    prev = APPROVALS.mode
    try:
        yield APPROVALS
    finally:
        APPROVALS.set_mode(prev)


# ── registration ──────────────────────────────────────────────────────────


def test_every_documented_tool_is_registered_as_a_coroutine(registry):
    import inspect

    expected = {
        "assistant_accounts", "assistant_brief", "budget_get", "budget_set",
        "budget_tx_add", "budget_status", "debt_list", "debt_upsert",
        "debt_scenario", "bill_list", "bill_upsert", "money_disclaimer",
        "calendar_list_events", "calendar_create_event", "calendar_update_event",
        "calendar_cancel_event", "mail_connect", "mail_disconnect", "mail_status",
        "mail_reply", "mail_archive", "mail_mark_read", "mail_list", "mail_get",
        "mail_create_draft", "mail_send",
    }
    assert expected <= set(registry.handlers)
    for name in expected:
        assert inspect.iscoroutinefunction(registry.handlers[name]), name


@pytest.mark.parametrize(
    ("tool", "required"),
    [
        ("calendar_create_event", ["title", "start", "end"]),
        ("calendar_update_event", ["event_id"]),
        ("calendar_cancel_event", ["event_id"]),
        ("mail_connect", ["address", "app_password"]),
        ("mail_reply", ["message_id", "body"]),
        ("mail_get", ["message_id"]),
        ("mail_send", ["to"]),
    ],
)
def test_the_schema_marks_the_arguments_the_tool_refuses_to_guess(registry, tool, required):
    assert registry.schemas[tool].get("required") == required


# ── nothing connected ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("calendar_list_events", {}),
        ("calendar_create_event", {"title": "x", "start": "a", "end": "b"}),
        ("calendar_update_event", {"event_id": "e1"}),
        ("calendar_cancel_event", {"event_id": "e1"}),
        ("mail_list", {}),
        ("mail_get", {"message_id": "m1"}),
        ("mail_create_draft", {"to": "a@b.com"}),
        ("mail_send", {"to": "a@b.com"}),
        ("mail_reply", {"message_id": "m1", "body": "hi"}),
        ("mail_archive", {"message_id": "m1"}),
        ("mail_mark_read", {"message_id": "m1"}),
    ],
)
async def test_with_no_account_connected_the_tool_reports_it_and_does_not_raise(
    tools, monkeypatch, tool, kwargs
):
    _no_providers(monkeypatch)
    out = await _json(tools, tool, **kwargs)
    assert out["ok"] is False
    assert "not connected" in out["message"].lower()


# ── consent gate ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("calendar_list_events", {}),
        ("calendar_create_event", {"title": "x", "start": "a", "end": "b"}),
        ("calendar_update_event", {"event_id": "e1"}),
        ("calendar_cancel_event", {"event_id": "e1"}),
    ],
)
async def test_an_oauth_calendar_is_refused_until_the_privacy_notice_is_accepted(
    tools, monkeypatch, tool, kwargs
):
    cal = _FullCalendar(provider_id="google")
    _install_calendar(monkeypatch, cal)
    out = await _json(tools, tool, **kwargs)
    assert out["ok"] is False
    assert "Settings" in out["message"]
    assert cal.calls == [], "the provider must not be touched before consent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("mail_list", {}),
        ("mail_get", {"message_id": "m1"}),
        ("mail_create_draft", {"to": "a@b.com"}),
        ("mail_send", {"to": "a@b.com"}),
        ("mail_reply", {"message_id": "m1", "body": "hi"}),
        ("mail_archive", {"message_id": "m1"}),
        ("mail_mark_read", {"message_id": "m1"}),
    ],
)
async def test_an_oauth_mailbox_is_refused_until_the_privacy_notice_is_accepted(
    tools, monkeypatch, tool, kwargs
):
    mail = _FullMail(provider_id="google")
    _install_mail(monkeypatch, mail)
    out = await _json(tools, tool, **kwargs)
    assert out["ok"] is False
    assert "Settings" in out["message"]
    assert mail.calls == [] and mail.sent == []


@pytest.mark.asyncio
async def test_an_app_password_mailbox_needs_no_google_consent(tools, monkeypatch):
    """Handing over an app password already IS the consent — do not ask twice."""
    mail = _FullMail(provider_id="imap", messages=[MailMessage(id="1", subject="Hello")])
    _install_mail(monkeypatch, mail, imap=True)
    out = await _json(tools, "mail_list")
    assert out["ok"] is True
    assert out["count"] == 1


@pytest.mark.asyncio
async def test_a_caldav_calendar_needs_no_google_consent(tools, monkeypatch):
    cal = _FullCalendar(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await _json(tools, "calendar_list_events")
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_a_stale_consent_version_reopens_the_gate(tools, monkeypatch, home):
    """Scopes moved on since the owner accepted — that is not still a yes."""
    from remedy.assistant.store import get_assistant_store

    _accept_consent(home)
    get_assistant_store(home).patch_prefs(consent_version="some_older_version_v0")
    _install_mail(monkeypatch, _FullMail(provider_id="google"))
    out = await _json(tools, "mail_list")
    assert out["ok"] is False
    assert "re-accept" in out["message"].lower()


@pytest.mark.asyncio
async def test_logging_an_expense_does_not_hand_over_the_mailbox(tools, monkeypatch):
    """Money tools auto-accept the money disclaimer; that must not leak into
    account access, which is a different consent entirely."""
    await tools["budget_tx_add"](amount=12.5, category="coffee")
    _install_mail(monkeypatch, _FullMail(provider_id="google"))
    out = await _json(tools, "mail_list")
    assert out["ok"] is False
    assert "Settings" in out["message"]


# ── missing arguments ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "", "start": "2026-09-01", "end": "2026-09-02"},
        {"title": "Lunch", "start": "", "end": "2026-09-02"},
        {"title": "Lunch", "start": "2026-09-01", "end": ""},
        {"title": "   ", "start": "2026-09-01", "end": "2026-09-02"},
        {},
    ],
)
async def test_a_half_specified_event_is_refused_rather_than_invented(
    tools, monkeypatch, kwargs
):
    cal = _FullCalendar(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await _json(tools, "calendar_create_event", **kwargs)
    assert out["ok"] is False
    assert "Need title, start, and end" in out["message"]
    assert cal.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["calendar_update_event", "calendar_cancel_event"])
@pytest.mark.parametrize("event_id", ["", "   "])
async def test_an_empty_event_id_is_refused_not_applied_to_something(
    tools, monkeypatch, tool, event_id
):
    cal = _FullCalendar(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await _json(tools, tool, event_id=event_id)
    assert out["ok"] is False
    assert "event_id required" in out["message"]
    assert cal.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool", ["mail_get", "mail_archive", "mail_mark_read", "mail_reply"]
)
@pytest.mark.parametrize("mid", ["", "   "])
async def test_an_empty_message_id_is_refused(tools, monkeypatch, tool, mid):
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    out = await _json(tools, tool, message_id=mid)
    assert out["ok"] is False
    assert "message_id required" in out["message"]
    assert mail.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["mail_create_draft", "mail_send"])
@pytest.mark.parametrize("to", ["", "   "])
async def test_mail_without_a_recipient_is_refused(tools, monkeypatch, tool, to):
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    out = await _json(tools, tool, to=to, subject="s", body="b")
    assert out["ok"] is False
    assert out["message"] == "to address required"
    assert mail.sent == [] and mail.calls == []


# ── providers that cannot do the thing ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs", "fragment"),
    [
        ("calendar_update_event", {"event_id": "e1"}, "cannot update events"),
        ("calendar_cancel_event", {"event_id": "e1"}, "cannot delete events"),
    ],
)
async def test_a_read_only_calendar_says_so_instead_of_pretending(
    tools, monkeypatch, approvals, tool, kwargs, fragment
):
    # About the provider's refusal, not the approval gate — cancel is gated now.
    approvals.set_mode("auto")
    _install_calendar(monkeypatch, _ReadOnlyCalendar(provider_id="caldav"), caldav=True)
    out = await _json(tools, tool, **kwargs)
    assert out["ok"] is False
    assert fragment in out["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs", "fragment"),
    [
        ("mail_reply", {"message_id": "m1", "body": "hi"}, "cannot reply in thread"),
        ("mail_archive", {"message_id": "m1"}, "cannot archive"),
        ("mail_mark_read", {"message_id": "m1"}, "cannot change read state"),
        ("mail_send", {"to": "a@b.com"}, "does not support send"),
    ],
)
async def test_a_draft_only_mailbox_says_so_instead_of_pretending(
    tools, monkeypatch, approvals, tool, kwargs, fragment
):
    approvals.set_mode("auto")
    monkeypatch.setattr(approvals, "take_one_shot", lambda *a, **k: True)
    _install_mail(monkeypatch, _ReadOnlyMail(provider_id="imap"), imap=True)
    out = await _json(tools, tool, **kwargs)
    assert out["ok"] is False
    assert fragment in out["message"]


# ── provider blow-ups become messages, never exceptions ───────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs", "fail", "text"),
    [
        ("calendar_list_events", {}, "list", "calendar backend is down"),
        (
            "calendar_create_event",
            {"title": "x", "start": "2026-09-01", "end": "2026-09-02"},
            "create",
            "calendar refused the event",
        ),
        ("calendar_update_event", {"event_id": "e1"}, "update", "calendar refused the edit"),
        ("calendar_cancel_event", {"event_id": "e1"}, "delete", "calendar refused the delete"),
    ],
)
async def test_a_calendar_failure_is_reported_not_raised(
    tools, monkeypatch, approvals, tool, kwargs, fail, text
):
    approvals.set_mode("auto")
    _install_calendar(monkeypatch, _FullCalendar(provider_id="caldav", fail=fail), caldav=True)
    out = await _json(tools, tool, **kwargs)
    assert out["ok"] is False
    assert out["message"] == text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs", "fail", "text"),
    [
        ("mail_list", {}, "list", "imap login failed"),
        ("mail_get", {"message_id": "m1"}, "get", "no such message"),
        ("mail_create_draft", {"to": "a@b.com"}, "draft", "draft rejected"),
        ("mail_send", {"to": "a@b.com"}, "send", "smtp refused"),
        ("mail_reply", {"message_id": "m1", "body": "b"}, "reply", "thread is gone"),
        ("mail_archive", {"message_id": "m1"}, "archive", "archive folder missing"),
        ("mail_mark_read", {"message_id": "m1"}, "mark", "flag store refused"),
    ],
)
async def test_a_mail_failure_is_reported_not_raised(
    tools, monkeypatch, approvals, tool, kwargs, fail, text
):
    approvals.set_mode("auto")
    monkeypatch.setattr(approvals, "take_one_shot", lambda *a, **k: True)
    _install_mail(monkeypatch, _FullMail(provider_id="imap", fail=fail), imap=True)
    out = await _json(tools, tool, **kwargs)
    assert out["ok"] is False
    assert out["message"] == text


# ── argument handling ─────────────────────────────────────────────────────


def _span_days(payload) -> float:
    lo = datetime.fromisoformat(payload["time_min"].replace("Z", "+00:00"))
    hi = datetime.fromisoformat(payload["time_max"].replace("Z", "+00:00"))
    return (hi - lo).total_seconds() / 86400.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("days", "expected"), [(0, 7), (None, 7), (7, 7), (1, 1), (31, 31), (999, 31), (-5, 1)]
)
async def test_the_calendar_lookahead_is_clamped_to_a_sane_window(
    tools, monkeypatch, days, expected
):
    _install_calendar(monkeypatch, _FullCalendar(provider_id="caldav"), caldav=True)
    out = await _json(tools, "calendar_list_events", days=days)
    assert round(_span_days(out)) == expected


@pytest.mark.asyncio
async def test_an_explicit_window_wins_over_days_and_is_passed_through(tools, monkeypatch):
    cal = _FullCalendar(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await _json(
        tools,
        "calendar_list_events",
        days=30,
        time_min="  2026-01-01T00:00:00Z  ",
        time_max="  2026-01-02T00:00:00Z  ",
    )
    assert out["time_min"] == "2026-01-01T00:00:00Z"
    assert out["time_max"] == "2026-01-02T00:00:00Z"
    assert cal.calls[0][1:] == ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")


@pytest.mark.asyncio
async def test_a_long_event_description_is_truncated_before_the_model_sees_it(
    tools, monkeypatch
):
    ev = CalendarEvent(id="e1", title="t", start="s", description="x" * 900)
    _install_calendar(monkeypatch, _FullCalendar(provider_id="caldav", events=[ev]), caldav=True)
    out = await _json(tools, "calendar_list_events")
    assert len(out["events"][0]["description"]) == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "expected"), [(0, 15), (None, 15), (10, 10), (99, 25)])
async def test_the_mail_page_size_is_capped(tools, monkeypatch, limit, expected):
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    await _json(tools, "mail_list", limit=limit)
    assert mail.calls[0][2] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", None])
async def test_a_blank_mail_query_falls_back_to_the_inbox(tools, monkeypatch, query):
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    await _json(tools, "mail_list", query=query)
    assert mail.calls[0][1] == "in:inbox"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [-3, 0, -1])
async def test_a_nonsense_page_size_is_clamped_before_the_provider_sees_it(
    tools, monkeypatch, limit
):
    """min() alone bounded only the top, so a negative limit went straight
    through — not a smaller page, a nonsense one."""
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    await _json(tools, "mail_list", limit=limit)
    assert mail.calls[0][2] >= 1


# ── data minimisation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_mail_listing_is_clipped_before_it_reaches_the_model(tools, monkeypatch):
    msg = MailMessage(
        id="i" * 200,
        subject="s" * 400,
        from_addr="f" * 200,
        snippet="n" * 900,
        date="d" * 100,
        thread_id="t" * 200,
    )
    _install_mail(monkeypatch, _FullMail(provider_id="imap", messages=[msg]), imap=True)
    row = (await _json(tools, "mail_list"))["messages"][0]
    assert len(row["id"]) == 128
    assert len(row["thread_id"]) == 128
    assert len(row["subject"]) == 120
    assert len(row["from"]) == 80
    assert len(row["snippet"]) == 160
    assert len(row["date"]) == 40


@pytest.mark.asyncio
async def test_a_read_message_body_is_capped(tools, monkeypatch):
    msg = MailMessage(id="m1", subject="s", snippet="b" * 9000)
    _install_mail(monkeypatch, _FullMail(provider_id="imap", messages=[msg]), imap=True)
    out = await _json(tools, "mail_get", message_id="m1")
    assert len(out["message"]["body"]) == 2500


@pytest.mark.asyncio
async def test_a_draft_result_carries_only_the_confirmation_fields(tools, monkeypatch):
    mail = _FullMail(provider_id="imap")
    mail.draft_result = {
        "draft_id": "d1",
        "to": "a@b.com",
        "subject": "hi",
        "body": "the private draft text",
        "access_token": "ya29.super-secret-value",
    }
    _install_mail(monkeypatch, mail, imap=True)
    blob = await tools["mail_create_draft"](to="a@b.com", subject="hi", body="x")
    assert "the private draft text" not in blob
    assert "ya29.super-secret-value" not in blob
    assert json.loads(blob)["draft_id"] == "d1"


@pytest.mark.asyncio
async def test_a_send_result_carries_only_the_confirmation_fields(
    tools, monkeypatch, approvals
):
    approvals.set_mode("auto")
    monkeypatch.setattr(approvals, "take_one_shot", lambda *a, **k: True)
    mail = _FullMail(provider_id="imap")
    mail.send_result = {
        "message_id": "m1",
        "to": "a@b.com",
        "body": "the private message text",
        "refresh_token": "1//leaked",
    }
    _install_mail(monkeypatch, mail, imap=True)
    blob = await tools["mail_send"](to="a@b.com", subject="hi", body="secret body")
    assert "the private message text" not in blob
    assert "1//leaked" not in blob
    assert mail.sent[0]["body"] == "secret body"


@pytest.mark.asyncio
async def test_a_reply_result_carries_only_the_confirmation_fields(
    tools, monkeypatch, approvals
):
    approvals.set_mode("auto")  # about the payload, not the gate
    monkeypatch.setattr(approvals, "take_one_shot", lambda *a, **k: True)
    mail = _FullMail(provider_id="imap")
    mail.reply_result = {
        "message_id": "m2",
        "thread_id": "t1",
        "body": "the private reply text",
        "password": "hunter2",
    }
    _install_mail(monkeypatch, mail, imap=True)
    blob = await tools["mail_reply"](message_id="m1", body="hello")
    assert "the private reply text" not in blob
    assert "hunter2" not in blob
    assert json.loads(blob)["thread_id"] == "t1"


# ── the approval gate on sending ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_mode_stops_a_send_and_never_hands_it_to_the_mailbox(
    tools, monkeypatch, approvals
):
    approvals.set_mode("ask")
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    out = await tools["mail_send"](
        to="boss@example.com", subject="Resignation", body="I quit"
    )
    assert out.startswith("APPROVAL_REQUIRED id=")
    assert "Do not invent success" in out
    assert "I quit" not in out, "the body must not be replayed in the prompt"
    assert mail.sent == [], "nothing may leave the mailbox before approval"


@pytest.mark.asyncio
async def test_auto_mode_still_stops_a_send(
    tools, monkeypatch, approvals
):
    """Sending mail is irreversible — auto/full must not waive the checkpoint."""
    approvals.set_mode("auto")
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    out = await tools["mail_send"](to="a@b.com", subject="hi", body="hello")
    assert out.startswith("APPROVAL_REQUIRED id=")
    assert mail.sent == []


@pytest.mark.asyncio
async def test_the_send_gate_runs_before_the_recipient_is_even_needed(
    tools, monkeypatch, approvals
):
    """Argument validation still comes first — an empty `to` never creates a
    pending approval the owner would have to dismiss."""
    approvals.set_mode("ask")
    _install_mail(monkeypatch, _FullMail(provider_id="imap"), imap=True)
    out = await _json(tools, "mail_send", to="")
    assert out["message"] == "to address required"


@pytest.mark.asyncio
async def test_replying_in_thread_is_gated_in_ask_mode(tools, monkeypatch, approvals):
    """A reply sends mail from the owner's account, so Ask mode stops it.

    It did not: mail_reply builds a full APPROVAL_REQUIRED item, but the name
    was never listed in HIGH_IMPACT_TOOLS, so needs_ask returned None and the
    whole block was dead. Mail left the mailbox with no prompt.
    """
    approvals.set_mode("ask")
    mail = _FullMail(provider_id="imap")
    _install_mail(monkeypatch, mail, imap=True)
    out = await tools["mail_reply"](message_id="m1", body="sure")
    assert "APPROVAL_REQUIRED" in out
    assert not [c for c in mail.calls if c[0] == "reply"], "the reply was sent anyway"


@pytest.mark.asyncio
async def test_cancelling_an_appointment_is_gated_in_ask_mode(
    tools, monkeypatch, approvals
):
    """The tool's own description promises it asks first. It did not.

    Deleting an appointment is not reversible from here, and the name was
    missing from HIGH_IMPACT_TOOLS, so the event went whatever the mode said.
    """
    approvals.set_mode("ask")
    cal = _FullCalendar(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await tools["calendar_cancel_event"](event_id="e1")
    assert "APPROVAL_REQUIRED" in out
    assert ("delete", "e1") not in cal.calls, "the appointment was cancelled anyway"


@pytest.mark.asyncio
async def test_rescheduling_an_appointment_is_gated_in_ask_mode(
    tools, monkeypatch, approvals
):
    """A reschedule notifies every attendee, so Ask mode stops it first.

    calendar_update_event had no gate at all: the edit went straight to the
    provider in every mode.
    """
    approvals.set_mode("ask")
    cal = _FullCalendar(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await tools["calendar_update_event"](event_id="e1", start="2026-09-03T09:00:00Z")
    assert out.startswith("APPROVAL_REQUIRED id=")
    assert "start" in out
    assert not [c for c in cal.calls if c[0] == "update"], "the event was edited anyway"


@pytest.mark.asyncio
async def test_auto_mode_reschedules_without_a_prompt(tools, monkeypatch, approvals):
    approvals.set_mode("auto")
    cal = _FullCalendar(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await _json(tools, "calendar_update_event", event_id="e1", title="Dentist (moved)")
    assert out["ok"] is True
    assert [c for c in cal.calls if c[0] == "update"]


@pytest.mark.asyncio
async def test_disconnecting_a_mailbox_is_gated_in_ask_mode(
    tools, monkeypatch, approvals, home
):
    """mail_disconnect deletes the stored app password; the owner has to go
    and generate a new one. It had no gate: the model could unlink the mailbox
    in any mode."""
    from remedy.interfaces.secret_store import get_provider_secret, set_provider_secret

    approvals.set_mode("ask")
    set_provider_secret("mail_address", "someone@fastmail.com", home)
    set_provider_secret("mail_app_password", "hunter2-app-password", home)
    out = await tools["mail_disconnect"]()
    assert out.startswith("APPROVAL_REQUIRED id=")
    assert "someone@fastmail.com" in out
    assert "hunter2-app-password" not in out
    assert get_provider_secret("mail_app_password", home=home) == "hunter2-app-password"


@pytest.mark.asyncio
async def test_auto_mode_disconnects_without_a_prompt(tools, approvals, home):
    from remedy.interfaces.secret_store import get_provider_secret, set_provider_secret

    approvals.set_mode("auto")
    set_provider_secret("mail_address", "someone@fastmail.com", home)
    set_provider_secret("mail_app_password", "hunter2-app-password", home)
    out = await _json(tools, "mail_disconnect")
    assert out["ok"] is True
    assert not (get_provider_secret("mail_app_password", home=home) or "")


@pytest.mark.asyncio
async def test_a_cancel_survives_a_provider_that_cannot_describe_the_event(
    tools, monkeypatch, approvals
):
    """get_event is best-effort labelling — a failure there must not block the
    cancellation the owner asked for."""
    approvals.set_mode("auto")

    class _NoLookup(_FullCalendar):
        def get_event(self, event_id):
            raise RuntimeError("lookup unsupported")

    cal = _NoLookup(provider_id="caldav")
    _install_calendar(monkeypatch, cal, caldav=True)
    out = await _json(tools, "calendar_cancel_event", event_id="e1")
    assert out["ok"] is True
    assert out["event_id"] == "e1"


# ── mail_connect / disconnect / status ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("address", "password"),
    [("", ""), ("someone@gmail.com", ""), ("", "abcdefghijklmnop")],
)
async def test_connecting_without_both_halves_is_refused(tools, address, password):
    out = await _json(tools, "mail_connect", address=address, app_password=password)
    assert out["ok"] is False
    assert "app password" in out["message"].lower()


@pytest.mark.asyncio
async def test_a_known_provider_is_told_where_to_generate_the_app_password(tools):
    out = await _json(tools, "mail_connect", address="someone@gmail.com")
    assert "https://myaccount.google.com/apppasswords" in out["message"]


@pytest.mark.asyncio
async def test_an_unknown_domain_is_refused_before_any_login_is_attempted(
    tools, monkeypatch
):
    from remedy.assistant.providers import imap_smtp

    def _boom(self):
        raise AssertionError("verify() must not run for an unknown domain")

    monkeypatch.setattr(imap_smtp.ImapSmtpMailProvider, "verify", _boom)
    out = await _json(
        tools, "mail_connect", address="a@example.invalid", app_password="abcd efgh ijkl mnop"
    )
    assert out["ok"] is False
    assert "servers" in out["message"].lower()


@pytest.mark.asyncio
async def test_a_credential_that_fails_verification_is_never_saved(tools, monkeypatch, home):
    from remedy.assistant.providers import imap_smtp
    from remedy.interfaces.secret_store import get_provider_secret

    saved: list = []
    monkeypatch.setattr(
        imap_smtp.ImapSmtpMailProvider,
        "verify",
        lambda self: (_ for _ in ()).throw(RuntimeError("Wrong password for that mailbox")),
    )
    monkeypatch.setattr(
        imap_smtp, "save_mail_credentials", lambda *a, **kw: saved.append(a) or {}
    )
    out = await _json(
        tools, "mail_connect", address="someone@gmail.com", app_password="abcd efgh ijkl mnop"
    )
    assert out["ok"] is False
    assert out["message"] == "Wrong password for that mailbox"
    assert saved == []
    assert not (get_provider_secret("mail_app_password", home=home) or "")


@pytest.mark.asyncio
async def test_a_verified_connect_records_consent_and_strips_the_spaces(
    tools, monkeypatch, home
):
    from remedy.assistant.providers import imap_smtp
    from remedy.assistant.store import get_assistant_store

    seen: dict = {}
    monkeypatch.setattr(imap_smtp.ImapSmtpMailProvider, "verify", lambda self: {"ok": True})

    def _save(address, app_password, home=None):
        seen["address"] = address
        seen["password"] = app_password
        return {"ok": True, "address": address}

    monkeypatch.setattr(imap_smtp, "save_mail_credentials", _save)
    out = await _json(
        tools, "mail_connect", address=" someone@gmail.com ", app_password="abcd efgh ijkl mnop"
    )
    assert out["ok"] is True
    assert seen["password"] == "abcdefghijklmnop"
    assert "never sent to the model" in out["privacy"]
    prefs = get_assistant_store(home).get_prefs()
    assert prefs.privacy_ai_accepted and prefs.account_access_accepted


@pytest.mark.asyncio
async def test_disconnecting_an_unconnected_mailbox_is_not_an_error(tools):
    out = await _json(tools, "mail_disconnect")
    assert out["ok"] is True
    assert "no mailbox" in out["message"].lower()


def test_switching_mailboxes_leaves_no_stale_connected_row(home):
    """Connect Gmail, then Outlook: there is one credential slot, so the
    ``imap_google`` row must go. It used to stay "connected" for ever, and
    disconnect only removed the row for the *current* address."""
    from remedy.assistant.providers.imap_smtp import (
        clear_mail_credentials,
        save_mail_credentials,
    )
    from remedy.assistant.store import get_assistant_store

    store = get_assistant_store(home)
    assert save_mail_credentials("me@gmail.com", "abcdefghijklmnop", home)["ok"]
    assert save_mail_credentials("me@outlook.com", "abcdefghijklmnop", home)["ok"]
    ids = sorted(a.id for a in store.list_accounts() if a.id.startswith("imap_"))
    assert ids == ["imap_microsoft"], ids
    out = clear_mail_credentials(home)
    assert out["ok"] and out["address"] == "me@outlook.com"
    assert [a for a in store.list_accounts() if a.id.startswith("imap_")] == []


def test_disconnect_clears_every_app_password_row_even_a_foreign_one(home):
    """A row from an earlier mailbox whose address no longer matches the
    stored one is cleared too — the credential behind it is gone either way."""
    import time

    from remedy.assistant.models import LinkedAccount
    from remedy.assistant.providers.imap_smtp import clear_mail_credentials
    from remedy.assistant.store import get_assistant_store
    from remedy.interfaces.secret_store import set_provider_secret

    store = get_assistant_store(home)
    store.upsert_account(
        LinkedAccount(
            id="imap_google", provider="google", email="old@gmail.com",
            capabilities=["mail"], status="connected",
            last_sync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    )
    set_provider_secret("mail_address", "me@outlook.com", home)
    set_provider_secret("mail_app_password", "abcdefghijklmnop", home)
    assert clear_mail_credentials(home)["ok"]
    assert [a for a in store.list_accounts() if a.id.startswith("imap_")] == []


@pytest.mark.asyncio
async def test_mail_status_reports_nothing_connected_without_inventing_one(tools):
    out = await _json(tools, "mail_status")
    assert out["ok"] is True
    assert out["connected"] is False
    assert out["method"] == ""


@pytest.mark.asyncio
async def test_mail_status_names_the_mailbox_but_never_the_password(tools, home):
    from remedy.interfaces.secret_store import set_provider_secret

    set_provider_secret("mail_address", "someone@fastmail.com", home)
    set_provider_secret("mail_app_password", "hunter2-app-password", home)
    blob = await tools["mail_status"]()
    out = json.loads(blob)
    assert out["connected"] is True
    assert out["method"] == "app_password"
    assert out["provider"] == "Fastmail"
    assert "hunter2-app-password" not in blob


# ── the brief ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_brief_says_what_it_skipped_rather_than_pretending_it_is_empty(
    tools, monkeypatch
):
    _no_providers(monkeypatch)
    text = await tools["assistant_brief"]()
    assert "## Calendar" in text and "## Mail" in text
    assert text.count("(Skipped —") == 2


@pytest.mark.asyncio
async def test_the_brief_skips_a_caldav_calendar_the_owner_already_connected(
    tools, monkeypatch
):
    """Documents a gap: calendar_list_events exempts CalDAV from the Google
    consent gate, the brief does not — so the section is skipped untouched."""
    cal = _FullCalendar(provider_id="caldav", events=[CalendarEvent(id="1", title="Gym", start="s")])
    _install_calendar(monkeypatch, cal, caldav=True)
    _install_mail(monkeypatch, None)
    text = await tools["assistant_brief"]()
    assert "(Skipped —" in text
    assert cal.calls == []


@pytest.mark.asyncio
async def test_the_brief_does_include_an_app_password_inbox_without_consent(
    tools, monkeypatch
):
    msg = MailMessage(id="1", subject="Invoice", from_addr="acct@x.com", snippet="z" * 300)
    _install_mail(monkeypatch, _FullMail(provider_id="imap", messages=[msg]), imap=True)
    _install_calendar(monkeypatch, None)
    text = await tools["assistant_brief"]()
    assert "## Inbox (recent)" in text
    assert "z" * 300 not in text, "the brief must clip snippets hard"


@pytest.mark.asyncio
async def test_a_calendar_that_blows_up_mid_brief_does_not_lose_the_brief(
    tools, monkeypatch, home
):
    _accept_consent(home)
    _install_calendar(monkeypatch, _FullCalendar(fail="list"), caldav=False)
    _install_mail(monkeypatch, None)
    text = await tools["assistant_brief"]()
    assert "(Could not load calendar: calendar backend is down)" in text
    assert "## Linked accounts" in text, "the rest of the brief must still be produced"


@pytest.mark.asyncio
async def test_a_mailbox_that_blows_up_mid_brief_does_not_lose_the_brief(
    tools, monkeypatch, home
):
    _accept_consent(home)
    _install_mail(monkeypatch, _FullMail(fail="list"), imap=False)
    _install_calendar(monkeypatch, None)
    text = await tools["assistant_brief"]()
    assert "(Could not load mail: imap login failed)" in text
    assert "## Linked accounts" in text


@pytest.mark.asyncio
async def test_an_empty_calendar_and_inbox_are_stated_not_omitted(
    tools, monkeypatch, home
):
    _accept_consent(home)
    _install_calendar(monkeypatch, _FullCalendar(), caldav=False)
    _install_mail(monkeypatch, _FullMail(), imap=False)
    text = await tools["assistant_brief"]()
    assert "No upcoming events on primary calendar." in text
    assert "No recent inbox messages." in text


@pytest.mark.asyncio
async def test_a_users_hint_is_truncated_before_it_is_echoed_back(tools, monkeypatch):
    _no_providers(monkeypatch)
    text = await tools["assistant_brief"](hint="q" * 900)
    assert "q" * 200 in text
    assert "q" * 201 not in text


@pytest.mark.asyncio
async def test_the_brief_always_carries_the_money_disclaimer(tools, monkeypatch):
    from remedy.assistant.disclaimer import MONEY_DISCLAIMER_SHORT

    _no_providers(monkeypatch)
    text = await tools["assistant_brief"]()
    assert MONEY_DISCLAIMER_SHORT in text


@pytest.mark.asyncio
async def test_a_runtime_without_a_task_list_still_produces_a_brief(home, monkeypatch):
    """Goals are optional context — a runtime that cannot list tasks must not
    take the brief down with it."""

    class _Broken(_Runtime):
        def list_tasks(self):
            raise RuntimeError("task store offline")

    rt = _Broken(home)
    register_assistant_tools(rt)
    _no_providers(monkeypatch)
    text = await rt.tool_registry.handlers["assistant_brief"]()
    assert "## Linked accounts" in text
    assert "## Open goals" not in text


@pytest.mark.asyncio
async def test_only_open_goals_reach_the_brief(home, monkeypatch):
    tasks = [
        _Task("Ship the update", tags=["goal"], status="open"),
        _Task("Old goal", tags=["goal"], status="TaskStatus.COMPLETED"),
        _Task("Buy milk", tags=["chore"], status="open"),
    ]
    tools = _make_tools(home, tasks=tasks)
    _no_providers(monkeypatch)
    text = await tools["assistant_brief"]()
    assert "Ship the update" in text
    assert "Old goal" not in text
    assert "Buy milk" not in text


# ── money tool argument handling ──────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["not json", "{", "[{,}]", "[1,2"])
async def test_a_malformed_category_list_is_reported_not_swallowed(tools, raw):
    out = await tools["budget_set"](label="2026-09", categories_json=raw)
    assert out == "categories_json must be a JSON list of {name, planned}"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{"name": "food"}', "null", "42", '"food"'])
async def test_valid_json_that_is_not_a_list_yields_no_categories(tools, raw):
    """Documents current behaviour: only a JSON *list* is read; anything else
    that parses is accepted quietly as 'no categories'."""
    out = await _json(tools, "budget_set", label="2026-09", categories_json=raw)
    assert out["ok"] is True
    assert out["categories"] == []


@pytest.mark.asyncio
async def test_non_object_entries_in_the_category_list_are_dropped(tools):
    out = await _json(
        tools,
        "budget_set",
        label="2026-09",
        categories_json=json.dumps([{"name": "food", "planned": 100}, "rent", 7, None]),
    )
    assert [c["name"] for c in out["categories"]] == ["food"]


@pytest.mark.asyncio
async def test_a_budget_with_no_label_is_stamped_with_the_current_month(tools):
    out = await _json(tools, "budget_set", label="   ")
    assert out["label"] == datetime.now(UTC).strftime("%Y-%m")


@pytest.mark.asyncio
async def test_the_accounts_status_is_json_and_holds_no_stored_secret(tools, home):
    from remedy.interfaces.secret_store import set_provider_secret

    set_provider_secret("mail_app_password", "hunter2-app-password", home)
    blob = await tools["assistant_accounts"]()
    out = json.loads(blob)
    assert "money_disclaimer" in out
    assert "hunter2-app-password" not in blob
