"""Tool descriptions are the model's only map of what it can do.

Mail and calendar gained an app-password path (IMAP / CalDAV) that needs no
cloud project, but the descriptions still said "Needs Connect Google (Gmail)".
The model reads those, so an owner on Outlook or Yahoo — already connected and
working — would be told to go and set up Google.
"""

from __future__ import annotations

import contextlib
import tempfile

import pytest


class _Registry:
    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.seen[name] = description

    def __getattr__(self, _name):
        return lambda *a, **kw: None


class _Config:
    home_dir = tempfile.mkdtemp()

    def __getattr__(self, _name):
        return None


class _Runtime:
    def __init__(self) -> None:
        self.tool_registry = _Registry()
        self.config = _Config()

    def __getattr__(self, _name):
        return None


def _descriptions() -> dict[str, str]:
    from remedy.core.agent_assistant_tools import register_assistant_tools

    rt = _Runtime()
    with contextlib.suppress(Exception):
        register_assistant_tools(rt)
    return rt.tool_registry.seen


@pytest.mark.parametrize(
    "tool",
    [
        "mail_list",
        "mail_get",
        "mail_create_draft",
        "mail_send",
        "calendar_list_events",
        "calendar_create_event",
    ],
)
def test_no_tool_claims_google_is_required(tool):
    desc = _descriptions()[tool]
    lowered = desc.lower()
    assert "needs connect google" not in lowered, desc
    assert not lowered.startswith("list gmail"), desc


@pytest.mark.parametrize("tool", ["mail_list", "mail_send", "calendar_list_events"])
def test_the_app_password_route_is_named(tool):
    """The owner has to be told the route that needs no cloud project."""
    desc = _descriptions()[tool].lower()
    assert "app password" in desc or "imap" in desc or "caldav" in desc, desc


def test_connect_and_disconnect_are_both_offered():
    tools = _descriptions()
    assert "mail_connect" in tools
    assert "mail_disconnect" in tools, "linking a mailbox has to be undoable"
