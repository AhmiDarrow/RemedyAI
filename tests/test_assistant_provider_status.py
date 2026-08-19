"""What the assistant says it can reach must be what it can reach.

``public_status`` reported Microsoft and Yahoo as "planned" long after
``imap_smtp.PRESETS`` grew working entries for both — so the owner was told a
capability she already had was still coming, and Gmail was listed the same way
while its preset sat right there next to them.
"""

from __future__ import annotations

import pytest

from remedy.assistant.providers.imap_smtp import ADDRESS_KEY, PRESETS
from remedy.assistant.store import AssistantStore


def _rows(home) -> dict[str, dict]:
    return {r["id"]: r for r in AssistantStore(home).public_status()["providers_planned"]}


def test_nothing_with_a_working_preset_is_called_planned(tmp_path):
    for row in _rows(tmp_path).values():
        assert row["status"] != "planned", f"{row['id']} is reachable today"


@pytest.mark.parametrize(
    ("provider", "domain"),
    [
        ("google", "gmail.com"),
        ("microsoft", "outlook.com"),
        ("yahoo", "yahoo.com"),
        ("fastmail", "fastmail.com"),
        ("icloud", "icloud.com"),
    ],
)
def test_every_offered_provider_has_the_preset_behind_it(tmp_path, provider, domain):
    """The list and the preset table must not drift apart again."""
    rows = _rows(tmp_path)
    assert provider in rows, f"{provider} is no longer offered"
    assert domain in PRESETS, f"{domain} lost its IMAP preset"
    assert rows[provider]["status"] in ("ready", "connected")


def test_a_linked_account_reads_as_connected(tmp_path):
    from remedy.interfaces.secret_store import set_provider_secret

    set_provider_secret(ADDRESS_KEY, "someone@yahoo.com", tmp_path)
    rows = _rows(tmp_path)
    assert rows["yahoo"]["status"] == "connected"
    assert rows["microsoft"]["status"] == "ready", "only the linked one is connected"


def test_an_alias_domain_still_matches_its_provider(tmp_path):
    """hotmail.com and live.com are Outlook; me.com is iCloud."""
    from remedy.interfaces.secret_store import set_provider_secret

    set_provider_secret(ADDRESS_KEY, "someone@hotmail.com", tmp_path)
    assert _rows(tmp_path)["microsoft"]["status"] == "connected"


def test_each_ready_provider_says_where_to_get_the_app_password(tmp_path):
    """"Ready" with no next step is a dead end in a conversation."""
    for row in _rows(tmp_path).values():
        if row["status"] == "ready":
            assert row.get("app_password_url", "").startswith("https://"), row


def test_status_never_leaks_the_address_or_password(tmp_path):
    import json

    from remedy.interfaces.secret_store import set_provider_secret

    set_provider_secret(ADDRESS_KEY, "someone@yahoo.com", tmp_path)
    set_provider_secret("mail_app_password", "hunter2-app-password", tmp_path)
    blob = json.dumps(AssistantStore(tmp_path).public_status())
    assert "hunter2-app-password" not in blob
    assert "someone@yahoo.com" not in blob
