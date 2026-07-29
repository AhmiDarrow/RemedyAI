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
    assert clip("hello", 3) == "he…"
