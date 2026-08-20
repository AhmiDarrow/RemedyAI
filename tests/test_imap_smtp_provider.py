"""The mailbox adapter that carries the owner's password and speaks for them.

``assistant/providers/imap_smtp.py`` is the only code in Remedy that can put a
message on the wire under the owner's name, move their mail out of the inbox,
or hand their app password to a host. When it is wrong the damage is not a
stack trace: a listing that quietly marks the whole inbox read, a reply that
leaves the thread and lands in a stranger's mailbox, an archive that deletes
before it copies, a connection left open with a credential on it, or a
rejected password reported as "b'[AUTHENTICATIONFAILED]...'" to somebody who
has no idea what that means.

So these tests drive the real provider against the strict doubles in
``tests/harness/fake_mail.py`` and mostly assert the negative: what must be
refused, who must *not* receive the message, what must not be flagged, what
must always be closed, and which errors must not be swallowed. A handful of
tests named ``test_BUG_...`` pin behaviour that is wrong but current, so that
whoever fixes it finds out immediately.

No socket is opened: ``imaplib``/``smtplib`` are replaced for the duration.
"""

from __future__ import annotations

import email.utils
import imaplib

import pytest

from remedy.assistant.providers.imap_smtp import (
    ADDRESS_KEY,
    PRESETS,
    SECRET_KEY,
    ImapSmtpMailProvider,
    MailAccount,
    _addr_list,
    _decode,
    _friendly_error,
    _imap_criteria,
    _imap_quote,
    clear_mail_credentials,
    get_imap_mail,
    load_mail_account,
    preset_for,
    save_mail_credentials,
)
from tests.harness.fake_mail import (
    FakeIMAPServer,
    FakeMailbox,
    FakeMessage,
    FakeSMTPServer,
    imap_auth_failed,
    imap_disabled,
    imap_needs_app_password,
    install_fake_mail,
    sample_mailbox,
)

PASSWORD = "abcdefghijklmnop"


@pytest.fixture
def world(monkeypatch):
    """imaplib/smtplib pointed at the doubles, loaded with the sample mailbox."""
    return install_fake_mail(monkeypatch)


@pytest.fixture
def strict(monkeypatch):
    """A server that answers NO to a message set that matched nothing."""
    return install_fake_mail(
        monkeypatch,
        imap=FakeIMAPServer(sample_mailbox(), strict_message_set=True),
    )


def provider(address: str = "owner@fastmail.com", password: str = PASSWORD):
    return ImapSmtpMailProvider(MailAccount.from_address(address, password))


def seq(world_, key: str) -> str:
    return world_.mailbox.seq_of(key)


# --- presets and the account they build --------------------------------------


@pytest.mark.parametrize(
    "address", ["someone@example.invalid", "no-at-sign", "", "   ", "@gmail.com.evil"]
)
def test_a_domain_remedy_has_no_servers_for_yields_no_preset(address):
    assert preset_for(address) == {}


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("a@googlemail.com", "imap.gmail.com"),
        ("a@hotmail.com", "outlook.office365.com"),
        ("a@live.com", "outlook.office365.com"),
        ("a@me.com", "imap.mail.me.com"),
    ],
)
def test_an_alias_domain_resolves_to_the_host_it_is_an_alias_of(alias, canonical):
    assert preset_for(alias)["imap_host"] == canonical
    assert "alias_of" not in preset_for(alias)


def test_the_preset_is_a_copy_so_a_caller_cannot_corrupt_the_table():
    """A shared dict handed out by reference would let one connect attempt
    rewrite the servers every later one uses."""
    got = preset_for("a@gmail.com")
    got["imap_host"] = "evil.example.com"
    assert PRESETS["gmail.com"]["imap_host"] == "imap.gmail.com"
    assert preset_for("a@gmail.com")["imap_host"] == "imap.gmail.com"


@pytest.mark.parametrize("address", ["A@GMAIL.COM", " a@Gmail.Com ", "a@gmail.com "])
def test_the_domain_is_matched_however_the_owner_typed_it(address):
    assert preset_for(address)["label"] == "Gmail"


def test_an_explicit_override_replaces_the_preset_but_a_blank_one_does_not():
    acct = MailAccount.from_address(
        " owner@gmail.com ",
        PASSWORD,
        imap_host="mail.company.test",
        smtp_host="",
        drafts_folder=None,
    )
    assert acct.address == "owner@gmail.com"  # stripped, not carried with spaces
    assert acct.imap_host == "mail.company.test"
    assert acct.smtp_host == "smtp.gmail.com"
    assert acct.drafts_folder == "[Gmail]/Drafts"


def test_an_override_for_a_field_that_does_not_exist_is_ignored():
    """Otherwise a typo'd keyword would silently stick a dead attribute on the
    account instead of being noticed."""
    acct = MailAccount.from_address("a@gmail.com", PASSWORD, imapHost="typo.example")
    assert not hasattr(acct, "imapHost")
    assert acct.imap_host == "imap.gmail.com"


@pytest.mark.parametrize(
    ("kwargs", "ready"),
    [
        ({}, True),
        ({"address": ""}, False),
        ({"password": ""}, False),
        ({"imap_host": ""}, False),
        ({"smtp_host": ""}, False),
    ],
)
def test_an_account_is_ready_only_with_both_hosts_and_a_credential(kwargs, ready):
    fields = {
        "address": "a@gmail.com",
        "password": PASSWORD,
        "imap_host": "imap.gmail.com",
        "smtp_host": "smtp.gmail.com",
    }
    fields.update(kwargs)
    assert MailAccount(**fields).is_ready() is ready


def test_hosts_can_be_supplied_for_a_domain_remedy_does_not_know():
    acct = MailAccount.from_address(
        "owner@company.test",
        PASSWORD,
        imap_host="imap.company.test",
        smtp_host="smtp.company.test",
        smtp_port=465,
    )
    assert acct.is_ready()
    assert (acct.imap_port, acct.smtp_port) == (993, 465)


# --- headers and errors the owner has to read --------------------------------


def test_an_encoded_word_header_is_decoded_to_text_a_person_can_read():
    assert _decode("=?utf-8?q?Caf=C3=A9_r=C3=A9union?=") == "Café réunion"


@pytest.mark.parametrize(
    "value", ["=?no-such-charset?q?x?=", "=?utf-8?q?=FF=FF?=", "", None]
)
def test_a_header_that_cannot_be_decoded_is_returned_not_raised(value):
    """A single malformed subject line must not take down a whole listing."""
    assert isinstance(_decode(value), str)


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (imap_auth_failed(), "16-character"),
        (imap_auth_failed("Invalid credentials for owner"), "16-character"),
        (imap_needs_app_password(), "requires an app password"),
        (imap_disabled(), "IMAP is turned off"),
        (Exception("IMAP is disabled for this account"), "IMAP is turned off"),
        (Exception("connection reset by peer"), "Mail error: connection reset by peer"),
    ],
)
def test_a_server_error_is_turned_into_something_the_owner_can_act_on(error, fragment):
    out = _friendly_error(error, "owner@gmail.com")
    assert isinstance(out, RuntimeError)
    assert fragment in str(out)


@pytest.mark.parametrize(
    ("address", "url"),
    [
        ("a@gmail.com", "https://myaccount.google.com/apppasswords"),
        ("a@googlemail.com", "https://myaccount.google.com/apppasswords"),
        ("a@hotmail.com", "https://account.microsoft.com/security"),
        ("a@yahoo.com", "https://login.yahoo.com/account/security"),
        ("a@fastmail.com", "https://app.fastmail.com/settings/security/apppasswords"),
        ("a@icloud.com", "https://appleid.apple.com/account/manage"),
    ],
)
def test_the_rejection_hint_links_to_that_providers_own_app_password_page(address, url):
    assert url in str(_friendly_error(imap_auth_failed(), address))


def test_an_unknown_domain_still_gets_the_advice_but_no_invented_link():
    out = str(_friendly_error(imap_auth_failed(), "owner@company.test"))
    assert "2-step verification" in out
    assert "http" not in out


def test_the_original_server_error_is_kept_as_the_cause(world):
    """The friendly text is for the owner; the raw line still has to reach the
    log, or nobody can diagnose an unusual failure."""
    world.imap.fail_at_login(imap_auth_failed())
    with pytest.raises(RuntimeError) as excinfo:
        provider().list_messages()
    assert "AUTHENTICATIONFAILED" in str(excinfo.value.__cause__)
    assert "AUTHENTICATIONFAILED" not in str(excinfo.value)


# --- query translation -------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "criteria"),
    [
        ("", ("ALL",)),
        ("   ", ("ALL",)),
        ("inbox", ("ALL",)),
        ("in:inbox", ("ALL",)),
        ("ALL", ("ALL",)),
        ("unread", ("UNSEEN",)),
        ("is:unread", ("UNSEEN",)),
        ("UNSEEN", ("UNSEEN",)),
        ("from:billing@example.com", ("FROM", "billing@example.com")),
        ("From:  Zoë Ünicode ", ("FROM", '"Zoë Ünicode"')),
        ("subject:Invoice 4471", ("SUBJECT", '"Invoice 4471"')),
        ("SINCE 1-Aug-2026", ("SINCE", "1-Aug-2026")),
        ("regnet", ("TEXT", "regnet")),
        ("two words", ("TEXT", '"two words"')),
    ],
)
def test_a_friendly_query_becomes_the_imap_criteria_it_claims(query, criteria):
    assert _imap_criteria(query) == criteria


@pytest.mark.parametrize(
    ("value", "quoted"),
    [
        ("plain.atom-1@example.com", "plain.atom-1@example.com"),
        ("two words", '"two words"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("", '""'),
    ],
)
def test_a_search_term_with_specials_is_quoted_and_escaped_not_pasted_raw(value, quoted):
    """An unescaped quote would end the IMAP string early and turn the rest of
    the owner's search text into commands."""
    assert _imap_quote(value) == quoted


def test_a_query_that_would_break_out_of_the_search_string_stays_one_atom(world):
    provider().list_messages(query='" UID DELETE 1:*')
    assert world.imap.searches[-1] == ("TEXT", '"\\" UID DELETE 1:*"')


@pytest.mark.parametrize(
    ("value", "addrs"),
    [
        ("", []),
        ("   ", []),
        ("b@x.test", ["b@x.test"]),
        ('Bob <b@x.test>, "Smith, Jane" <j@x.test>', ["b@x.test", "j@x.test"]),
    ],
)
def test_only_real_addresses_become_envelope_recipients(value, addrs):
    assert _addr_list(value) == addrs


# --- listing -----------------------------------------------------------------


def test_listing_asks_for_headers_only_and_never_downloads_a_body(world):
    """A listing that pulled RFC822 would drag every attachment in the inbox
    over the wire on every refresh."""
    out = provider().list_messages(limit=3)
    assert out and all(m.snippet == "" for m in out)
    for _seq, spec in world.imap.fetches:
        assert "BODY.PEEK[HEADER.FIELDS" in spec
        assert "RFC822" not in spec


def test_listing_selects_the_inbox_read_only_so_it_cannot_mark_mail_read(world):
    before = set(world.imap.seen_keys())
    provider().list_messages()
    assert world.imap.selects[-1] == ("INBOX", True)
    assert set(world.imap.seen_keys()) == before


@pytest.mark.parametrize(
    ("limit", "wanted"), [(0, 9), (None, 9), (-5, 1), (3, 3), (1000, 9), (2, 2)]
)
def test_the_limit_is_clamped_into_something_a_server_will_answer(world, limit, wanted):
    """0/None mean "the default", never "everything"; a negative can never ask
    for a reversed range, and 1000 never asks a server for 1000 messages."""
    assert len(provider().list_messages(limit=limit)) == wanted


def test_a_search_the_server_refuses_returns_nothing_rather_than_raising(world):
    world.imap.search_result = ("NO", [b"Invalid search"])
    assert provider().list_messages(query="unread") == []
    assert world.imap.leaked_connections == []


def test_a_message_the_server_no_longer_has_is_skipped_not_reported(strict):
    """Between SEARCH and FETCH another client can expunge. The rows that are
    still there must come back; the vanished one must not become a blank."""
    strict.imap.search_result = ("OK", [b"1 99"])
    assert [m.id for m in provider().list_messages()] == ["1"]


def test_a_fetch_that_answers_ok_with_no_payload_is_skipped(world):
    world.imap.search_result = ("OK", [b"1 99"])
    assert [m.id for m in provider().list_messages()] == ["1"]


def test_a_listing_is_newest_first_and_decodes_the_headers_it_shows(world):
    """Newest last in IMAP order, so an undreversed listing shows the oldest
    mail in the inbox first — and an encoded subject shows as ``=?utf-8?q?…``."""
    out = provider().list_messages(limit=2)
    assert [m.id for m in out] == [seq(world, "headerless"), seq(world, "mislabelled")]

    unicode_row = provider().list_messages(query="from:zoe@example.com")[0]
    assert unicode_row.subject == "Café ☕ — réunion demain"
    assert unicode_row.from_addr == "Zoë Ünicode <zoe@example.com>"


def test_a_message_with_no_headers_is_labelled_rather_than_shown_blank(world):
    out = provider().list_messages(limit=1)[0]
    assert out.subject == "(no subject)"
    assert out.from_addr == ""


# --- reading one message -----------------------------------------------------


@pytest.mark.parametrize("message_id", ["", "   ", None])
def test_an_empty_message_id_is_refused_before_any_connection_is_opened(
    world, message_id
):
    with pytest.raises(ValueError):
        provider().get_message(message_id)
    assert world.imap.connections == []


def test_a_message_id_the_server_rejects_is_reported_as_not_found(strict):
    with pytest.raises(RuntimeError, match="not found"):
        provider().get_message("999")
    assert strict.imap.leaked_connections == []


def test_a_lenient_server_answering_ok_with_nothing_is_still_not_found(world):
    """The realistic case, and the one that was broken. Most servers answer
    ('OK', [None]) for a message set they ignored rather than NO, so the
    not-found branch never fired and the owner got a blank message —
    "(no subject)" with an empty body — for mail that does not exist."""
    with pytest.raises(RuntimeError, match="not found"):
        provider().get_message("999")
    assert world.imap.leaked_connections == []


def test_marking_a_message_that_is_not_there_is_not_reported_as_success(world):
    """STORE against an ignored message set is an OK no-op, so trusting the
    return code alone told the owner a message was marked when none was."""
    with pytest.raises(RuntimeError, match="not found"):
        provider().mark_read("999")
    assert world.imap.leaked_connections == []


def test_marking_an_already_read_message_on_a_silent_server_is_still_success(
    monkeypatch,
):
    """Servers that suppress the FETCH for a flag that did not change answer
    ('OK', [None]) for a real message. Treating that as "not found" told the
    owner a message they had just opened did not exist."""
    world_ = install_fake_mail(
        monkeypatch,
        imap=FakeIMAPServer(sample_mailbox(), silent_noop_store=True),
    )
    uid = world_.mailbox.uid_of("plain")
    world_.mailbox.by_key("plain").flags.add("\\Seen")
    out = provider().mark_read(uid)
    assert out["ok"] is True
    # ...while a message that is not there is still not "marked".
    with pytest.raises(RuntimeError, match="not found"):
        provider().mark_read("999")
    assert world_.imap.leaked_connections == []


def test_a_drafts_folder_the_server_refuses_is_not_reported_as_saved(world):
    """A mailbox whose Drafts folder is named differently answers NO
    [TRYCREATE]; the draft exists nowhere and the owner used to be told it was
    saved."""
    world.imap.fail("append", "NO [TRYCREATE] Mailbox does not exist")
    with pytest.raises(Exception, match="draft|TRYCREATE|Drafts"):
        provider().create_draft(to="b@x.test", subject="Later", body="Half written")
    assert world.imap.leaked_connections == []


def test_a_non_ascii_message_id_fails_loudly_and_still_closes_the_connection(world):
    """Sequence numbers are ASCII digits; anything else is a caller bug and
    must not be smuggled onto the wire."""
    with pytest.raises(UnicodeEncodeError):
        provider().get_message("１２３")
    assert world.imap.leaked_connections == []


def test_reading_a_message_carries_the_headers_a_reply_will_need(world):
    msg = provider().get_message(seq(world, "threaded"))
    assert msg.raw["message_id_header"] == "<threaded@example.com>"
    assert msg.raw["references"] == "<root@example.com> <second@example.com>"
    assert msg.raw["reply_to"] == "Legal Inbox <legal-inbox@example.com>"
    assert "paralegal@example.com" in msg.raw["cc"]


def test_a_huge_body_is_capped_so_one_mail_cannot_flood_the_caller(monkeypatch):
    box = FakeMailbox(
        {"INBOX": [FakeMessage(key="huge", subject="Log", body="x" * 50_000)]}
    )
    install_fake_mail(monkeypatch, mailbox=box)
    assert len(provider().get_message("1").snippet) == 4000


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("plain", "The invoice is waiting on the portal."),
        ("alt", "Plain part wins."),  # never the HTML sibling
        ("htmlonly", "Hello"),  # tags stripped, not shown as markup
        ("attached", "Notes are attached."),  # not the text attachment
        ("latin1", "Grüße aus München"),
        ("headerless", "A message with no headers at all."),
    ],
)
def test_the_readable_body_is_found_whatever_shape_the_message_is(world, key, expected):
    body = provider().get_message(seq(world, key)).snippet
    assert expected in body


def test_a_text_attachment_is_never_mistaken_for_the_body(world):
    """The attachment is also ``text/plain``; picking it would show the owner
    a transcript where the covering note should be."""
    body = provider().get_message(seq(world, "attached")).snippet
    assert "ATTACHMENT TEXT" not in body


def test_a_body_that_lies_about_its_charset_is_repaired_not_raised(world):
    body = provider().get_message(seq(world, "mislabelled")).snippet
    assert body.startswith("Joyeux anniversaire")


def test_a_body_in_a_charset_that_does_not_exist_reads_as_empty(monkeypatch):
    """``.decode('totally-made-up')`` raises LookupError; an unreadable body
    must cost the snippet, not the whole call."""
    box = FakeMailbox(
        {
            "INBOX": [
                FakeMessage(
                    key="bogus",
                    raw_override=(
                        b"Subject: Odd\r\n"
                        b"From: odd@example.com\r\n"
                        b"Content-Type: text/plain; charset=totally-made-up\r\n"
                        b"\r\n"
                        b"unreadable\r\n"
                    ),
                )
            ]
        }
    )
    install_fake_mail(monkeypatch, mailbox=box)
    msg = provider().get_message("1")
    assert msg.snippet == ""
    assert msg.subject == "Odd"  # the headers still came through


def test_a_multipart_newsletter_with_no_plain_part_is_read_as_stripped_text(monkeypatch):
    """Marketing mail is routinely multipart with an HTML part and an empty or
    absent plain one — showing the owner raw tags is not an answer."""
    box = FakeMailbox(
        {
            "INBOX": [
                FakeMessage(
                    key="htmlmultipart",
                    raw_override=(
                        b"Subject: Newsletter\r\n"
                        b"From: news@example.com\r\n"
                        b'Content-Type: multipart/alternative; boundary="B"\r\n'
                        b"\r\n"
                        b"--B\r\n"
                        b"Content-Type: image/gif\r\n"
                        b"\r\n"
                        b"GIF89a\r\n"
                        b"--B\r\n"
                        b"Content-Type: text/html; charset=utf-8\r\n"
                        b"\r\n"
                        b"<html><body><p>Sale <b>today</b>.</p></body></html>\r\n"
                        b"--B--\r\n"
                    ),
                )
            ]
        }
    )
    install_fake_mail(monkeypatch, mailbox=box)
    body = provider().get_message("1").snippet
    assert "Sale" in body and "today" in body
    assert "<b>" not in body


def test_a_multipart_message_with_no_text_at_all_reads_as_empty(monkeypatch):
    box = FakeMailbox(
        {
            "INBOX": [
                FakeMessage(
                    key="binary",
                    raw_override=(
                        b"Subject: Scan\r\n"
                        b"From: scanner@example.com\r\n"
                        b'Content-Type: multipart/mixed; boundary="B"\r\n'
                        b"\r\n"
                        b"--B\r\n"
                        b"Content-Type: image/png\r\n"
                        b"Content-Transfer-Encoding: base64\r\n"
                        b"\r\n"
                        b"iVBORw0KGgo=\r\n"
                        b"--B--\r\n"
                    ),
                )
            ]
        }
    )
    install_fake_mail(monkeypatch, mailbox=box)
    assert provider().get_message("1").snippet == ""


def test_reading_a_message_leaves_the_mailbox_exactly_as_it_was(world):
    before = set(world.imap.seen_keys())
    provider().get_message(seq(world, "unicode"))
    assert set(world.imap.seen_keys()) == before
    assert world.imap.stores == []


# --- sending -----------------------------------------------------------------


def test_every_address_on_the_to_line_becomes_an_envelope_recipient(world):
    provider().send_message(
        to='Bob <b@x.test>, "Smith, Jane" <j@x.test>', subject="Hi", body="Hello"
    )
    assert world.smtp.recipients == [("b@x.test", "j@x.test")]


def test_the_envelope_sender_is_the_connected_account(world):
    """The envelope sender decides where bounces go and what SPF is checked
    against — it must be the mailbox we authenticated as, not the To line."""
    provider().send_message(to="b@x.test", subject="Hi", body="Hello")
    sent = world.smtp.last_sent
    assert sent.from_addr == "owner@fastmail.com"
    assert sent.header("From") == "owner@fastmail.com"


def test_a_sent_message_carries_a_message_id_and_a_date(world):
    out = provider().send_message(to="b@x.test", subject="Hi", body="Hello")
    sent = world.smtp.last_sent
    assert sent.header("Message-ID") == out["message_id"]
    assert email.utils.parsedate_tz(sent.header("Date")) is not None


def test_an_empty_subject_and_body_are_sent_as_empty_not_as_none(world):
    out = provider().send_message(to="b@x.test", subject="", body="")
    assert world.smtp.last_sent.body == ""
    assert "(no subject)" in out["message"]


def test_BUG_a_to_line_with_no_address_in_it_still_reports_success(world):
    """Nothing was delivered: the envelope recipient list is empty. The caller
    is told the mail was sent, and the owner believes it went out."""
    out = provider().send_message(to="", subject="Hi", body="Hello")
    assert out["ok"] is True
    assert world.smtp.recipients == [()]


def test_the_smtp_connection_is_closed_when_every_recipient_is_refused(monkeypatch):
    import smtplib

    world_ = install_fake_mail(
        monkeypatch, smtp=FakeSMTPServer(refuse_recipients=["nobody@x.test"])
    )
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        provider().send_message(to="nobody@x.test", subject="Hi", body="Hello")
    assert world_.smtp.leaked_connections == []
    assert world_.smtp.sent == []


@pytest.mark.parametrize(
    ("address", "smtp_port", "ssl"),
    [("owner@fastmail.com", 465, True), ("owner@gmail.com", 587, False)],
)
def test_both_connections_carry_a_timeout_so_a_dead_host_cannot_hang_remedy(
    world, address, smtp_port, ssl
):
    provider(address).send_message(to="b@x.test", subject="Hi", body="Hello")
    provider(address).list_messages(limit=1)
    assert world.smtp.connections[-1][1:] == (smtp_port, 30, ssl)
    assert world.imap.connections[-1][1:] == (993, 30)


def test_the_password_is_only_ever_offered_to_the_hosts_in_the_preset(world):
    provider("owner@gmail.com").send_message(to="b@x.test", subject="Hi", body="Hello")
    provider("owner@gmail.com").list_messages(limit=1)
    assert {h for h, *_ in world.smtp.connections} == {"smtp.gmail.com"}
    assert {h for h, *_ in world.imap.connections} == {"imap.gmail.com"}
    assert world.smtp.logins == [("owner@gmail.com", PASSWORD)]


# --- replying ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Contract draft", "Re: Contract draft"),
        ("Re: Contract draft", "Re: Contract draft"),
        ("RE: Contract draft", "RE: Contract draft"),
        ("re: Contract draft", "re: Contract draft"),
    ],
)
def test_a_subject_that_is_already_a_reply_is_not_prefixed_again(
    monkeypatch, subject, expected
):
    box = FakeMailbox(
        {"INBOX": [FakeMessage(key="one", subject=subject, from_addr="a@x.test")]}
    )
    world_ = install_fake_mail(monkeypatch, mailbox=box)
    provider().reply_to_message("1", body="ok")
    assert world_.smtp.last_sent.subject == expected


def test_BUG_replying_to_a_subjectless_message_sends_the_placeholder_label(monkeypatch):
    """``get_message`` substitutes "(no subject)" for display, and the reply
    path treats that as the real subject — so the recipient gets a mail titled
    "Re: (no subject)" instead of an empty subject line."""
    box = FakeMailbox({"INBOX": [FakeMessage(key="one", from_addr="a@x.test")]})
    world_ = install_fake_mail(monkeypatch, mailbox=box)
    provider().reply_to_message("1", body="ok")
    assert world_.smtp.last_sent.subject == "Re: (no subject)"


def test_the_references_chain_keeps_the_originals_and_adds_the_message_answered(world):
    """Drop this and every mail client shows the reply as a new conversation."""
    provider().reply_to_message(seq(world, "threaded"), body="ok")
    sent = world.smtp.last_sent
    assert sent.header("In-Reply-To") == "<threaded@example.com>"
    assert sent.header("References") == (
        "<root@example.com> <second@example.com> <threaded@example.com>"
    )


def test_a_reply_goes_to_the_reply_to_address_rather_than_the_from(world):
    out = provider().reply_to_message(seq(world, "threaded"), body="ok")
    assert out["to"] == "Legal Inbox <legal-inbox@example.com>"
    assert world.smtp.recipients == [("legal-inbox@example.com",)]


def test_a_message_with_no_message_id_gets_no_in_reply_to_header(monkeypatch):
    box = FakeMailbox(
        {"INBOX": [FakeMessage(key="one", subject="Hi", from_addr="a@x.test")]}
    )
    world_ = install_fake_mail(monkeypatch, mailbox=box)
    out = provider().reply_to_message("1", body="ok")
    sent = world_.smtp.last_sent
    assert sent.header("In-Reply-To") == ""
    assert sent.header("References") == ""
    assert out["thread_id"] == ""


def test_a_message_with_nobody_to_reply_to_is_refused_before_smtp_is_touched(
    monkeypatch,
):
    box = FakeMailbox({"INBOX": [FakeMessage(key="one", subject="Anonymous")]})
    world_ = install_fake_mail(monkeypatch, mailbox=box)
    with pytest.raises(RuntimeError, match="reply address"):
        provider().reply_to_message("1", body="ok")
    assert world_.smtp.connections == []
    assert world_.imap.leaked_connections == []


def test_reply_all_widens_the_envelope_only_when_it_is_asked_to(world):
    p = provider()
    p.reply_to_message(seq(world, "threaded"), body="ok")
    p.reply_to_message(seq(world, "threaded"), body="ok", reply_all=True)
    narrow, wide = world.smtp.recipients
    assert narrow == ("legal-inbox@example.com",)
    assert set(wide) == {
        "legal-inbox@example.com",
        "paralegal@example.com",
        "archive@example.com",
    }
    assert world.smtp.sent[0].header("Cc") == ""


def test_reply_all_on_a_message_with_no_cc_adds_no_empty_cc_header(monkeypatch):
    box = FakeMailbox(
        {"INBOX": [FakeMessage(key="one", subject="Hi", from_addr="a@x.test")]}
    )
    world_ = install_fake_mail(monkeypatch, mailbox=box)
    provider().reply_to_message("1", body="ok", reply_all=True)
    assert world_.smtp.last_sent.header("Cc") == ""
    assert world_.smtp.recipients == [("a@x.test",)]


# --- drafts ------------------------------------------------------------------


def test_a_draft_is_stored_with_the_draft_flag_and_never_handed_to_smtp(world):
    """The whole point of a draft is that it did not go out."""
    out = provider().create_draft(to="b@x.test", subject="Later", body="Half written")
    stored = world.imap.appends[-1]
    assert stored.folder == "Drafts"
    assert "\\Draft" in stored.flags
    assert stored.message.get("Subject") == "Later"
    assert world.smtp.connections == []
    assert world.smtp.sent == []
    assert out["ok"] is True


def test_the_draft_folder_comes_from_the_preset_and_is_quoted_for_imap(monkeypatch):
    """``[Gmail]/Drafts`` has a bracket and a slash — unquoted it is not a
    valid mailbox name and the APPEND is rejected."""
    box = sample_mailbox()
    box.add_folder("[Gmail]/Drafts")
    world_ = install_fake_mail(monkeypatch, mailbox=box)
    out = provider("owner@gmail.com").create_draft(to="b@x.test", subject="S", body="B")
    assert world_.imap.appends[-1].folder == "[Gmail]/Drafts"
    assert box.keys("[Gmail]/Drafts")
    assert "[Gmail]/Drafts" in out["message"]


def test_a_draft_is_stamped_with_an_internal_date_the_server_will_accept(world):
    provider().create_draft(to="b@x.test", subject="Later", body="Half written")
    stamped = world.imap.appends[-1].date_time
    assert stamped.startswith('"') and stamped.endswith('"')
    assert email.utils.parsedate_tz(stamped.strip('"')) is not None


# --- follow-through ----------------------------------------------------------


@pytest.mark.parametrize(("read", "op"), [(True, "+FLAGS"), (False, "-FLAGS")])
def test_marking_read_selects_the_inbox_writable_and_moves_only_that_flag(
    world, read, op
):
    target = seq(world, "unicode")
    provider().mark_read(target, read=read)
    assert world.imap.selects[-1] == ("INBOX", False)
    assert world.imap.stores == [(target, op, "\\Seen")]
    assert ("\\Seen" in world.mailbox.by_key("unicode").flags) is read


def test_marking_one_message_read_leaves_every_other_message_alone(world):
    before = set(world.imap.seen_keys())
    provider().mark_read(seq(world, "unicode"))
    assert set(world.imap.seen_keys()) == before | {"unicode"}


def test_archiving_copies_before_it_deletes_and_then_expunges(world):
    """Reversed, this loses the message: flag+expunge with a failed copy means
    the mail is gone from the inbox and nowhere else."""
    provider().archive_message(seq(world, "plain"))
    assert world.imap.ops.index("copy") < world.imap.ops.index("store")
    assert world.imap.ops.index("store") < world.imap.ops.index("expunge")
    assert "plain" in world.mailbox.keys("Archive")
    assert "plain" not in world.mailbox.keys("INBOX")


def test_archiving_a_message_that_is_not_there_is_not_reported_as_archived(world):
    """UID COPY of an ignored message set is an OK no-op with no COPYUID, so
    a stale id came back "Archived to Archive" and the owner believed it."""
    out = provider().archive_message("999")
    assert out["ok"] is False
    assert "not found" in out["message"]
    assert world.imap.expunged == []
    assert world.imap.leaked_connections == []


def test_archiving_a_message_that_is_not_there_on_a_strict_server(strict):
    out = provider().archive_message("999")
    assert out["ok"] is False
    assert "not found" in out["message"]


def test_an_archive_folder_this_mailbox_does_not_have_is_reported_and_nothing_is_lost(
    world,
):
    with pytest.raises(RuntimeError, match=r"\[Gmail\]/All Mail"):
        provider("owner@gmail.com").archive_message(seq(world, "plain"))
    assert world.imap.stores == []
    assert world.imap.expunged == []
    assert "plain" in world.mailbox.keys("INBOX")
    assert world.imap.leaked_connections == []


# --- connections, verification and cleanup -----------------------------------


def test_verify_proves_both_directions_and_leaves_nothing_open(world):
    out = provider().verify()
    assert out["ok"] and out["address"] == "owner@fastmail.com"
    assert (out["imap_host"], out["smtp_host"]) == (
        "imap.fastmail.com",
        "smtp.fastmail.com",
    )
    assert world.imap.leaked_connections == []
    assert world.smtp.leaked_connections == []
    assert world.imap.logouts == 1 and world.smtp.quits == 1


def test_verify_never_reaches_smtp_when_the_imap_password_is_rejected(world):
    world.imap.fail_at_login()
    with pytest.raises(RuntimeError, match="16-character"):
        provider().verify()
    assert world.smtp.connections == []


def test_verify_selects_the_inbox_read_only(world):
    provider().verify()
    assert world.imap.selects == [("INBOX", True)]


@pytest.mark.parametrize(
    "call",
    [
        lambda p, w: p.list_messages(),
        lambda p, w: p.get_message(seq(w, "plain")),
        lambda p, w: p.create_draft(to="b@x.test", subject="s", body="b"),
        lambda p, w: p.mark_read(seq(w, "plain")),
        lambda p, w: p.archive_message(seq(w, "plain")),
        lambda p, w: p.verify(),
    ],
)
def test_every_verb_logs_out_of_the_connection_it_opened(world, call):
    """Connections are per-call by design; a leaked one holds an authenticated
    session open with the owner's credential on it."""
    call(provider(), world)
    assert world.imap.leaked_connections == []


@pytest.mark.parametrize("op", ["select", "search", "fetch"])
def test_a_listing_that_fails_at_any_stage_still_closes_the_connection(world, op):
    world.imap.fail(op)
    with pytest.raises(imaplib.IMAP4.error):
        provider().list_messages()
    assert world.imap.leaked_connections == []


@pytest.mark.parametrize("op", ["store", "copy"])
def test_an_archive_that_fails_at_any_stage_still_closes_the_connection(world, op):
    world.imap.fail(op)
    with pytest.raises((imaplib.IMAP4.error, RuntimeError)):
        provider().archive_message(seq(world, "plain"))
    assert world.imap.leaked_connections == []


def test_a_server_that_refuses_expunge_still_leaves_the_message_copied_and_flagged(
    world,
):
    """EXPUNGE is deliberately tolerated: the copy already succeeded and the
    original is flagged ``\\Deleted``, which every client hides. Raising here
    would report a failure for work that is done."""
    world.imap.fail("expunge")
    out = provider().archive_message(seq(world, "plain"))
    assert out["ok"] is True
    assert "plain" in world.mailbox.keys("Archive")
    assert "\\Deleted" in world.mailbox.by_key("plain").flags
    assert world.imap.leaked_connections == []


def test_a_failed_logout_does_not_turn_a_good_result_into_an_error(world):
    world.imap.fail("logout")
    assert provider().list_messages(limit=1)


def test_a_refused_smtp_login_closes_the_connection_it_opened(world):
    """``_smtp`` used to build the connection then raise out of ``login``
    without quitting it, so the TLS socket was held by the server until GC —
    once per retry of a wrong app password."""
    world.smtp.fail_at_login()
    with pytest.raises(RuntimeError):
        provider().send_message(to="b@x.test", subject="Hi", body="Hello")
    assert world.smtp.leaked_connections == []


def test_a_refused_imap_login_closes_the_connection_it_opened(world):
    world.imap.fail_at_login()
    with pytest.raises(RuntimeError):
        provider().list_messages(limit=1)
    assert world.imap.leaked_connections == []


# --- credential storage ------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "password", "fragment"),
    [
        ("", PASSWORD, "email address"),
        ("not-an-address", PASSWORD, "email address"),
        ("a@gmail.com", "", "App password"),
        ("a@gmail.com", "    ", "App password"),
        ("a@company.test", PASSWORD, "don't know the mail servers"),
    ],
)
def test_a_credential_that_cannot_work_is_refused_before_anything_is_stored(
    tmp_path, address, password, fragment
):
    from remedy.interfaces.secret_store import get_provider_secret

    out = save_mail_credentials(address, password, home=tmp_path)
    assert out["ok"] is False
    assert fragment in out["message"]
    assert get_provider_secret(SECRET_KEY, home=tmp_path) is None
    assert get_provider_secret(ADDRESS_KEY, home=tmp_path) is None


def test_the_spaces_the_provider_shows_in_an_app_password_are_stripped(tmp_path):
    """Google shows the password as four groups of four; pasted verbatim with
    the spaces it is rejected by the server every time."""
    from remedy.interfaces.secret_store import get_provider_secret

    out = save_mail_credentials(
        "owner@gmail.com", "abcd efgh ijkl mnop", home=tmp_path
    )
    assert out["ok"] and out["provider"] == "Gmail"
    assert get_provider_secret(SECRET_KEY, home=tmp_path) == PASSWORD


def test_a_store_that_cannot_be_written_does_not_lose_the_saved_credential(
    tmp_path, monkeypatch
):
    from remedy.interfaces.secret_store import get_provider_secret

    def boom(_home=None):
        raise OSError("assistant store is locked")

    monkeypatch.setattr("remedy.assistant.store.get_assistant_store", boom)
    out = save_mail_credentials("owner@yahoo.com", PASSWORD, home=tmp_path)
    assert out["ok"] and out["capabilities"] == ["mail"]
    assert get_provider_secret(SECRET_KEY, home=tmp_path) == PASSWORD


def test_disconnecting_a_mailbox_that_was_never_connected_says_so(tmp_path):
    out = clear_mail_credentials(home=tmp_path)
    assert out["ok"] and out["address"] == ""
    assert "No mailbox" in out["message"]


def test_the_password_is_forgotten_even_when_the_account_list_cannot_be_updated(
    tmp_path, monkeypatch
):
    from remedy.interfaces.secret_store import get_provider_secret

    save_mail_credentials("owner@yahoo.com", PASSWORD, home=tmp_path)

    def boom(_home=None):
        raise OSError("assistant store is locked")

    monkeypatch.setattr("remedy.assistant.store.get_assistant_store", boom)
    out = clear_mail_credentials(home=tmp_path)
    assert out["ok"] and out["address"] == "owner@yahoo.com"
    assert get_provider_secret(SECRET_KEY, home=tmp_path) is None


@pytest.mark.parametrize("stored", [(ADDRESS_KEY,), (SECRET_KEY,), ()])
def test_half_a_credential_loads_no_account_at_all(tmp_path, stored):
    from remedy.interfaces.secret_store import set_provider_secret

    values = {ADDRESS_KEY: "owner@gmail.com", SECRET_KEY: PASSWORD}
    for key in stored:
        set_provider_secret(key, values[key], home=tmp_path)
    assert load_mail_account(tmp_path) is None
    assert get_imap_mail(tmp_path) is None


def test_a_stored_address_remedy_has_no_servers_for_loads_nothing(tmp_path):
    """The credential is there but unusable — handing back an account with no
    hosts would fail later with a connection error instead of "not set up"."""
    from remedy.interfaces.secret_store import set_provider_secret

    set_provider_secret(ADDRESS_KEY, "owner@company.test", home=tmp_path)
    set_provider_secret(SECRET_KEY, PASSWORD, home=tmp_path)
    assert load_mail_account(tmp_path) is None
    assert get_imap_mail(tmp_path) is None


def test_a_saved_mailbox_comes_back_as_a_ready_provider(tmp_path):
    save_mail_credentials("owner@fastmail.com", "abcd efgh ijkl mnop", home=tmp_path)
    mail = get_imap_mail(tmp_path)
    assert isinstance(mail, ImapSmtpMailProvider)
    assert mail.provider_id == "imap"
    assert mail.account.address == "owner@fastmail.com"
    assert mail.account.smtp_port == 465
    assert mail.account.is_ready()


# --- servers that answer NO without raising ---------------------------------
# The doubles' fail() makes an op *raise*; a real server more often answers a
# bad status on a good socket. These drive that path directly, so the
# return-code checks are proven reachable rather than assumed.


def _answering(op: str, status: str = "NO", data=None):
    """Make one IMAP verb answer *status* on an otherwise healthy connection.

    Patched on the *connection* class (FakeIMAP4SSL), which is what the
    provider holds — FakeIMAPServer is the mailbox behind it.
    """
    from tests.harness.fake_mail import FakeIMAP4SSL

    real = getattr(FakeIMAP4SSL, op)

    def patched(self, *a, **kw):
        real(self, *a, **kw)
        return status, (data if data is not None else [b""])

    return FakeIMAP4SSL, patched


def test_a_drafts_folder_the_server_answers_no_to_is_not_reported_as_saved(
    world, monkeypatch
):
    cls, patched = _answering("append")
    monkeypatch.setattr(cls, "append", patched)
    with pytest.raises(RuntimeError, match="draft"):
        provider().create_draft(to="b@x.test", subject="Later", body="Half written")


def test_an_inbox_the_server_answers_no_to_is_not_reported_as_verified(
    world, monkeypatch
):
    """verify() is the connect flow's only check."""
    cls, patched = _answering("select")
    monkeypatch.setattr(cls, "select", patched)
    with pytest.raises(RuntimeError, match="INBOX"):
        provider().verify()


def test_a_store_the_server_answers_no_to_is_not_reported_as_marked(world, monkeypatch):
    cls, patched = _answering("store")
    monkeypatch.setattr(cls, "store", patched)
    with pytest.raises(RuntimeError, match="not found"):
        provider().mark_read(seq(world, "plain"))


# --- stable ids: the reason this provider speaks UID -------------------------
# A plain IMAP SEARCH answers with sequence numbers, which are positions and
# renumber on every expunge. Everything below fails if the provider goes back
# to them: the ids a caller is holding would silently start pointing at
# different messages.


def uid_of(world_, key: str) -> str:
    return world_.mailbox.uid_of(key)


def test_a_listed_id_is_the_stable_uid(world):
    """Documentation, not a discriminator: in an untouched mailbox a UID and a
    position are numerically equal, so this cannot fail on its own. The tests
    below are the ones that catch a regression."""
    listed = {m.subject: m.id for m in provider().list_messages(limit=50)}
    for key in ("plain", "threaded"):
        msg = world.mailbox.by_key(key)
        assert listed.get(msg.subject) == str(msg.uid)


def test_archiving_one_message_does_not_move_the_others(world):
    """The bug in one test. Archive shifts every later position by one; an id
    captured before must still name the same message afterwards."""
    before = provider().list_messages(limit=50)
    assert len(before) >= 3
    target = before[-1]           # oldest, so archiving it renumbers the rest
    keep = before[0]              # an id the caller is still holding
    keep_subject = keep.subject

    provider().archive_message(target.id)

    after = provider().get_message(keep.id)
    assert after.subject == keep_subject, (
        "the held id now points at a different message"
    )


def test_marking_read_after_an_archive_marks_the_message_that_was_named(world):
    before = provider().list_messages(limit=50)
    target, keep = before[-1], before[0]
    kept_key = world.mailbox.by_uid(int(keep.id)).key
    # Some of the sample mailbox is already read; only the *change* matters.
    seen_before = {
        m.key for m in world.mailbox.folder("INBOX") if "\\Seen" in m.flags
    }

    provider().archive_message(target.id)
    provider().mark_read(keep.id)

    seen_after = {
        m.key for m in world.mailbox.folder("INBOX") if "\\Seen" in m.flags
    }
    newly_read = seen_after - seen_before
    assert newly_read == {kept_key}, (
        f"marked {newly_read or 'nothing'} instead of {kept_key!r}"
    )


def test_an_id_for_a_message_that_was_archived_is_reported_not_reused(world):
    """A position frees up and gets reused; a UID never does."""
    before = provider().list_messages(limit=50)
    target = before[-1]
    provider().archive_message(target.id)

    with pytest.raises(RuntimeError, match="not found"):
        provider().get_message(target.id)


def test_every_read_and_write_goes_over_uid_commands(world):
    """A single plain SEARCH/FETCH/STORE/COPY anywhere reintroduces the bug."""
    p = provider()
    listed = p.list_messages(limit=3)
    p.get_message(listed[0].id)
    p.mark_read(listed[0].id)
    p.archive_message(listed[-1].id)

    verbs = {cmd for cmd, _ in world.imap.uid_calls}
    assert {"SEARCH", "FETCH", "STORE", "COPY"} <= verbs
    assert world.imap.search_charsets == [], "a non-UID SEARCH was issued"
