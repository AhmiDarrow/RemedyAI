"""PA privacy consent + redaction."""

from __future__ import annotations

import pytest

from remedy.assistant.privacy import (
    clip,
    consent_ok,
    redact_secrets,
    require_consent,
    sanitize_mail_list_item,
)
from remedy.assistant.store import AssistantStore, reset_assistant_store


@pytest.fixture(autouse=True)
def _reset():
    reset_assistant_store()
    yield
    reset_assistant_store()


def test_consent_blocks_without_flags(tmp_path):
    store = AssistantStore(home_dir=tmp_path)
    store.patch_prefs(privacy_ai_accepted=False, account_access_accepted=False)
    ok, reason = consent_ok(tmp_path)
    assert ok is False
    assert "privacy" in reason.lower() or "Accept" in reason
    with pytest.raises(PermissionError):
        require_consent(tmp_path)


def test_consent_ok_when_both_accepted(tmp_path):
    store = AssistantStore(home_dir=tmp_path)
    store.patch_prefs(privacy_ai_accepted=True, account_access_accepted=True)
    ok, reason = consent_ok(tmp_path)
    assert ok is True
    assert reason == ""
    prefs = store.get_prefs()
    assert prefs.consent_version  # stamped on accept


def test_consent_blocks_stale_version(tmp_path):
    from remedy.assistant.privacy import CURRENT_CONSENT_VERSION
    from remedy.assistant.store import get_assistant_store

    # Use process singleton so consent_ok reads the same store instance.
    store = get_assistant_store(tmp_path)
    store.patch_prefs(privacy_ai_accepted=True, account_access_accepted=True)
    # Force a stale version without re-stamping via accept flags
    store.patch_prefs(consent_version="ancient_scopes_v0")
    ok, reason = consent_ok(tmp_path)
    assert ok is False
    assert "updated" in reason.lower() or "re-accept" in reason.lower()
    # Re-accept stamps current version
    store.patch_prefs(privacy_ai_accepted=True, account_access_accepted=True)
    assert store.get_prefs().consent_version == CURRENT_CONSENT_VERSION
    ok2, _ = consent_ok(tmp_path)
    assert ok2 is True


def test_redact_secrets():
    raw = {
        "access_token": "ya29.secret",
        "subject": "hello",
        "nested": {"refresh_token": "1//x", "ok": True},
    }
    out = redact_secrets(raw)
    assert out["access_token"] == "[redacted]"
    assert out["nested"]["refresh_token"] == "[redacted]"
    assert out["subject"] == "hello"


def test_sanitize_mail_list_item_clips():
    row = sanitize_mail_list_item(
        {
            "id": "m1",
            "subject": "x" * 200,
            "from": "a@b.com",
            "snippet": "y" * 500,
            "date": "today",
        }
    )
    assert len(row["subject"]) <= 121
    assert len(row["snippet"]) <= 161
    assert "access_token" not in row


def test_public_status_includes_privacy(tmp_path):
    store = AssistantStore(home_dir=tmp_path)
    pub = store.public_status()
    assert "privacy" in pub
    assert pub["tokens_to_model"] is False
    assert pub["data_residency"] == "local"
    assert "privacy_ai_full" in pub["privacy"]
    assert "consent_version" in pub
    assert "current_consent_version" in pub
    assert pub["consent_ok"] is False
    assert pub["needs_reaccept"] is False  # never accepted → not "re"-accept
    assert clip("hello", 3) == "he…"


def test_public_status_needs_reaccept_on_stale_version(tmp_path):
    from remedy.assistant.privacy import CURRENT_CONSENT_VERSION
    from remedy.assistant.store import get_assistant_store

    store = get_assistant_store(tmp_path)
    store.patch_prefs(privacy_ai_accepted=True, account_access_accepted=True)
    store.patch_prefs(consent_version="ancient_scopes_v0")
    pub = store.public_status()
    assert pub["needs_reaccept"] is True
    assert pub["consent_ok"] is False
    assert pub["current_consent_version"] == CURRENT_CONSENT_VERSION
    assert "updated" in (pub.get("consent_reason") or "").lower() or "re-accept" in (
        pub.get("consent_reason") or ""
    ).lower()
    # Fresh accept clears the flag
    store.patch_prefs(privacy_ai_accepted=True, account_access_accepted=True)
    pub2 = store.public_status()
    assert pub2["needs_reaccept"] is False
    assert pub2["consent_ok"] is True


@pytest.mark.asyncio
async def test_assistant_brief_skips_google_without_consent(tmp_path, monkeypatch):
    """Mail/calendar sections must not load without consent_ok (even if tokens exist)."""
    from unittest.mock import MagicMock

    from remedy.assistant.store import get_assistant_store
    from remedy.core.agent_assistant_tools import register_assistant_tools

    store = get_assistant_store(tmp_path)
    store.patch_prefs(
        privacy_ai_accepted=False,
        account_access_accepted=False,
        brief={"include_mail": True, "include_calendar": True, "include_budget": False},
    )

    handlers: dict = {}

    class FakeReg:
        def register_builtin_handler(self, name, desc, fn, params=None):
            handlers[name] = fn

    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()
        tool_registry = FakeReg()

        def list_tasks(self):
            return []

    # If brief ignored consent, these would be called
    monkeypatch.setattr(
        "remedy.assistant.providers.google_gmail.get_google_gmail",
        MagicMock(side_effect=AssertionError("mail should not load")),
    )
    monkeypatch.setattr(
        "remedy.assistant.providers.google_calendar.get_google_calendar",
        MagicMock(side_effect=AssertionError("calendar should not load")),
    )

    register_assistant_tools(RT())
    assert "assistant_brief" in handlers
    out = await handlers["assistant_brief"]("")
    assert "Skipped" in out
    assert "Calendar" in out or "Mail" in out
