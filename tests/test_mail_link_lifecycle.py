"""Connecting a mailbox has to be visible, and undoable.

The Google OAuth flow records a ``LinkedAccount``; the app-password flow stored
the credential and stopped. So a mailbox could be fully working while Settings
and ``assistant_accounts`` both showed nothing linked. And there was no unlink
at all — connecting was a one-way door whose only exit was deleting keys by hand.
"""

from __future__ import annotations

import pytest

from remedy.assistant.providers.imap_smtp import (
    clear_mail_credentials,
    save_mail_credentials,
)
from remedy.assistant.store import AssistantStore


def _accounts(home):
    return AssistantStore(home).accounts_public()


def test_connecting_puts_the_mailbox_on_the_linked_list(tmp_path):
    out = save_mail_credentials("someone@fastmail.com", "abcd efgh ijkl mnop", home=tmp_path)
    assert out["ok"]

    accounts = _accounts(tmp_path)
    assert len(accounts) == 1
    assert accounts[0]["email"] == "someone@fastmail.com"
    assert accounts[0]["provider"] == "fastmail"
    assert accounts[0]["status"] == "connected"


@pytest.mark.parametrize(
    ("address", "provider", "calendar"),
    [
        ("a@gmail.com", "google", True),
        ("a@icloud.com", "icloud", True),
        ("a@fastmail.com", "fastmail", True),
        ("a@outlook.com", "microsoft", False),
        ("a@yahoo.com", "yahoo", False),
        ("a@hotmail.com", "microsoft", False),  # alias of outlook.com
    ],
)
def test_the_provider_and_capabilities_match_the_address(tmp_path, address, provider, calendar):
    """Calendar only where a CalDAV preset actually exists — claiming it
    elsewhere would offer the owner something that cannot work."""
    out = save_mail_credentials(address, "abcd efgh ijkl mnop", home=tmp_path)
    assert out["capabilities"] == (["mail", "calendar"] if calendar else ["mail"])
    assert _accounts(tmp_path)[0]["provider"] == provider


def test_reconnecting_the_same_address_does_not_duplicate(tmp_path):
    for _ in range(3):
        save_mail_credentials("a@yahoo.com", "abcd efgh ijkl mnop", home=tmp_path)
    assert len(_accounts(tmp_path)) == 1


def test_disconnecting_forgets_the_password_and_the_account(tmp_path):
    from remedy.assistant.providers.imap_smtp import ADDRESS_KEY, SECRET_KEY
    from remedy.interfaces.secret_store import get_provider_secret

    save_mail_credentials("a@yahoo.com", "abcd efgh ijkl mnop", home=tmp_path)
    out = clear_mail_credentials(home=tmp_path)

    assert out["ok"] and out["address"] == "a@yahoo.com"
    assert get_provider_secret(SECRET_KEY, tmp_path) is None
    assert get_provider_secret(ADDRESS_KEY, tmp_path) is None
    assert _accounts(tmp_path) == []


def test_disconnecting_twice_is_harmless(tmp_path):
    assert clear_mail_credentials(home=tmp_path)["ok"]
    save_mail_credentials("a@yahoo.com", "abcd efgh ijkl mnop", home=tmp_path)
    assert clear_mail_credentials(home=tmp_path)["ok"]
    assert clear_mail_credentials(home=tmp_path)["ok"]


def test_switching_mailboxes_leaves_exactly_one_linked(tmp_path):
    save_mail_credentials("a@yahoo.com", "abcd efgh ijkl mnop", home=tmp_path)
    clear_mail_credentials(home=tmp_path)
    save_mail_credentials("b@fastmail.com", "abcd efgh ijkl mnop", home=tmp_path)

    accounts = _accounts(tmp_path)
    assert len(accounts) == 1
    assert accounts[0]["email"] == "b@fastmail.com"


def test_removing_an_account_that_is_not_there_reports_false(tmp_path):
    assert AssistantStore(tmp_path).remove_account("imap_nobody") is False
    assert AssistantStore(tmp_path).remove_account("") is False


def test_an_unknown_domain_is_refused_before_anything_is_stored(tmp_path):
    out = save_mail_credentials("a@example.invalid", "abcd efgh ijkl mnop", home=tmp_path)
    assert not out["ok"]
    assert _accounts(tmp_path) == []
