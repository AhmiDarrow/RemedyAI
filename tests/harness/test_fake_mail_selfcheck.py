"""Proof that the mail doubles are strict enough to be worth trusting.

A test double for a mail server is only useful if it says *no* in the same
places a real server does. If ``tests/harness/fake_mail.py`` accepts a FETCH
before SELECT, ignores read-only mode, hands back the whole message when only
a few headers were asked for, or invents a tidy return value where imaplib
returns ``('OK', [None])``, then every mail test built on it is measuring the
double rather than the provider.

So this file checks the doubles against the shapes real ``imaplib``/``smtplib``
produce, and then drives the actual ``ImapSmtpMailProvider`` through them end
to end. The second half doubles as the fidelity proof: if the provider works
against these doubles the same way it works against Fastmail, the doubles are
close enough.

A handful of tests here document provider behaviour that is wrong but current
(marked BUG). They pass on purpose — they are the regression net for whoever
fixes it.
"""

from __future__ import annotations

import email
import imaplib
import smtplib

import pytest

from remedy.assistant.providers.imap_smtp import (
    ImapSmtpMailProvider,
    MailAccount,
)
from tests.harness.fake_mail import (
    Attachment,
    FakeIMAPServer,
    FakeMailbox,
    FakeMessage,
    FakeSMTPServer,
    imap_auth_failed,
    imap_disabled,
    imap_needs_app_password,
    install_fake_mail,
    sample_mailbox,
    smtp_auth_failed,
    smtp_recipients_refused,
)

PASSWORD = "abcdefghijklmnop"

HEADER_FETCH = "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])"


@pytest.fixture
def world(monkeypatch):
    """imaplib/smtplib pointed at the doubles, loaded with the sample mailbox."""
    return install_fake_mail(monkeypatch)


def provider(address: str = "owner@fastmail.com", password: str = PASSWORD):
    return ImapSmtpMailProvider(MailAccount.from_address(address, password))


def logged_in(server: FakeIMAPServer, folder: str = "INBOX", readonly: bool = True):
    conn = server.connect("imap.example.com", 993, timeout=30)
    conn.login("owner@example.com", PASSWORD)
    conn.select(folder, readonly)
    return conn


# --- the IMAP double refuses what a real server refuses ----------------------


def test_a_command_before_login_is_refused():
    server = FakeIMAPServer()
    conn = server.connect()
    with pytest.raises(imaplib.IMAP4.error):
        conn.select("INBOX")


@pytest.mark.parametrize("command", ["search", "fetch", "store", "copy", "expunge"])
def test_a_command_before_select_is_refused(command):
    server = FakeIMAPServer()
    conn = server.connect()
    conn.login("owner@example.com", PASSWORD)
    args = {
        "search": (None, "ALL"),
        "fetch": ("1", "(RFC822)"),
        "store": ("1", "+FLAGS", "\\Seen"),
        "copy": ("1", "Archive"),
        "expunge": (),
    }[command]
    with pytest.raises(imaplib.IMAP4.error):
        getattr(conn, command)(*args)


def test_anything_after_logout_is_refused():
    server = FakeIMAPServer()
    conn = logged_in(server)
    conn.logout()
    for call in (lambda: conn.search(None, "ALL"), lambda: conn.logout()):
        with pytest.raises(imaplib.IMAP4.error):
            call()


def test_appending_needs_no_select_because_real_servers_allow_it():
    server = FakeIMAPServer()
    conn = server.connect()
    conn.login("owner@example.com", PASSWORD)
    typ, _data = conn.append('"Drafts"', "\\Draft", '"17-Aug-2026 09:14:00 +0000"', b"X: 1\r\n\r\n")
    assert typ == "OK"


def test_a_wrong_password_is_rejected_only_when_accounts_are_scripted():
    lenient = FakeIMAPServer()
    lenient.connect().login("anyone@example.com", "whatever")  # no accounts => no check

    strict = FakeIMAPServer(accounts={"owner@example.com": PASSWORD})
    with pytest.raises(imaplib.IMAP4.error):
        strict.connect().login("owner@example.com", "wrong")


def test_selecting_a_missing_folder_answers_no_without_raising():
    """imaplib returns the NO to the caller. Anything that raises here would
    hide the fact that the provider never looks at the return code."""
    server = FakeIMAPServer()
    conn = server.connect()
    conn.login("owner@example.com", PASSWORD)
    typ, _data = conn.select("No Such Folder")
    assert typ == "NO"
    assert conn.state == "AUTH"


def test_the_connection_arguments_are_recorded():
    server = FakeIMAPServer()
    server.connect("imap.fastmail.com", 993, timeout=30)
    assert server.connections == [("imap.fastmail.com", 993, 30)]


# --- SEARCH ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        (("ALL",), ["plain", "unicode", "alt", "htmlonly", "attached", "latin1",
                    "threaded", "mislabelled", "headerless"]),
        (("UNSEEN",), ["unicode", "htmlonly", "latin1", "threaded", "mislabelled",
                       "headerless"]),
        (("SEEN",), ["plain", "alt", "attached"]),
        (("FROM", "billing@example.com"), ["plain"]),
        (("SUBJECT", '"Weekly digest"'), ["alt"]),
        (("TEXT", '"margin"'), ["threaded"]),
        (("FROM", "nobody@example.com"), []),
    ],
)
def test_search_returns_the_sequence_numbers_that_match(criteria, expected):
    server = FakeIMAPServer()
    conn = logged_in(server)
    _typ, data = conn.search(None, *criteria)
    keys = [server.mailbox.folder("INBOX")[int(n) - 1].key for n in data[0].split()]
    assert keys == expected


def test_search_records_the_criteria_verbatim():
    """What the provider *asked* for matters even where the double does not
    filter on it — a quoting bug is invisible in the result set."""
    server = FakeIMAPServer()
    conn = logged_in(server)
    conn.search(None, "SINCE", "1-Aug-2026")
    assert server.searches == [("SINCE", "1-Aug-2026")]


def test_a_search_result_can_be_forced():
    server = FakeIMAPServer()
    server.search_result = ("OK", [b"7 3 1"])
    conn = logged_in(server)
    assert conn.search(None, "ALL") == ("OK", [b"7 3 1"])


# --- FETCH -------------------------------------------------------------------


def test_fetching_a_number_that_does_not_exist_answers_ok_with_none():
    """This is imaplib's real shape, and the reason a caller must check for a
    tuple in the response rather than trusting a non-empty list."""
    server = FakeIMAPServer()
    conn = logged_in(server)
    assert conn.fetch("999", "(RFC822)") == ("OK", [None])


def test_a_strict_server_answers_no_to_a_bad_message_set_instead():
    server = FakeIMAPServer(strict_message_set=True)
    conn = logged_in(server)
    typ, _data = conn.fetch("999", "(RFC822)")
    assert typ == "NO"


def test_a_header_fetch_returns_only_the_requested_headers():
    server = FakeIMAPServer()
    conn = logged_in(server)
    _typ, data = conn.fetch("1", HEADER_FETCH)
    payload = data[0][1].decode()
    assert "Subject: Invoice 4471" in payload
    assert "To:" not in payload
    assert "invoice is waiting" not in payload


def test_the_fetch_response_has_the_prefix_tuple_shape_imaplib_produces():
    server = FakeIMAPServer()
    conn = logged_in(server)
    _typ, data = conn.fetch("1", "(RFC822)")
    assert isinstance(data[0], tuple) and len(data[0]) == 2
    assert data[0][0].startswith(b"1 (RFC822 {")
    assert data[-1] == b")"


def test_peeking_does_not_mark_a_message_read_but_a_plain_fetch_does():
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=False)
    conn.fetch("2", HEADER_FETCH)
    assert "\\Seen" not in server.mailbox.by_key("unicode").flags
    conn.fetch("2", "(RFC822)")
    assert "\\Seen" in server.mailbox.by_key("unicode").flags


def test_a_read_only_mailbox_never_gains_the_seen_flag():
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=True)
    conn.fetch("2", "(RFC822)")
    assert "\\Seen" not in server.mailbox.by_key("unicode").flags


# --- STORE / COPY / EXPUNGE / APPEND ----------------------------------------


def test_storing_a_flag_on_a_read_only_mailbox_is_an_error():
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=True)
    with pytest.raises(imaplib.IMAP4.error):
        conn.store("1", "+FLAGS", "\\Seen")


@pytest.mark.parametrize(
    ("command", "expected"),
    [("+FLAGS", True), ("-FLAGS", False)],
)
def test_flags_are_added_and_removed(command, expected):
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=False)
    conn.store(server.mailbox.seq_of("plain").encode(), command, "\\Seen")
    assert ("\\Seen" in server.mailbox.by_key("plain").flags) is expected


def test_copying_to_a_folder_that_does_not_exist_answers_no():
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=False)
    typ, _data = conn.copy(b"1", '"[Gmail]/All Mail"')
    assert typ == "NO"
    assert server.copies == [("1", "[Gmail]/All Mail")]


def test_expunge_drops_deleted_messages_and_renumbers_the_rest():
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=False)
    conn.store(b"1", "+FLAGS", "\\Deleted")
    conn.expunge()
    assert server.expunged == ["plain"]
    assert server.mailbox.keys()[0] == "unicode"
    assert server.mailbox.at("1").key == "unicode"


def test_append_records_the_folder_flags_and_bytes():
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=False)
    conn.append('"Drafts"', "\\Draft", '"17-Aug-2026 09:14:00 +0000"',
                b"Subject: Later\r\n\r\nbody\r\n")
    appended = server.appends[0]
    assert appended.folder == "Drafts"
    assert appended.flags == "\\Draft"
    assert appended.message["Subject"] == "Later"
    assert server.mailbox.folder("Drafts")[0].flags == {"\\Draft"}


def test_appending_to_a_missing_folder_answers_no_and_stores_nothing():
    server = FakeIMAPServer()
    conn = logged_in(server, readonly=False)
    typ, _data = conn.append('"[Gmail]/Drafts"', "\\Draft", '"x"', b"Subject: a\r\n\r\n")
    assert typ == "NO"
    assert server.appends[0].folder == "[Gmail]/Drafts"  # the attempt is still recorded
    assert "[Gmail]/Drafts" not in server.mailbox.folders


# --- scripted failures -------------------------------------------------------


def test_a_scripted_login_failure_raises_the_error_it_was_given():
    server = FakeIMAPServer()
    server.fail_at_login(imap_disabled())
    with pytest.raises(imaplib.IMAP4.error, match="IMAP access is disabled"):
        server.connect().login("owner@example.com", PASSWORD)


def test_a_fetch_can_be_made_to_die_part_way_through():
    server = FakeIMAPServer()
    server.fail_mid_fetch(after=2)
    conn = logged_in(server)
    conn.fetch("1", "(RFC822)")
    conn.fetch("2", "(RFC822)")
    with pytest.raises(imaplib.IMAP4.error):
        conn.fetch("3", "(RFC822)")


def test_a_fault_with_times_stops_firing_and_the_server_recovers():
    server = FakeIMAPServer()
    server.fail("search", times=1)
    conn = logged_in(server)
    with pytest.raises(imaplib.IMAP4.error):
        conn.search(None, "ALL")
    assert conn.search(None, "ALL")[0] == "OK"


@pytest.mark.parametrize(
    "spec", ["boom", imaplib.IMAP4.abort, imaplib.IMAP4.error("scripted"), imap_auth_failed]
)
def test_a_fault_accepts_a_message_a_class_an_instance_or_a_factory(spec):
    server = FakeIMAPServer()
    server.fail("select", spec)
    conn = server.connect()
    conn.login("owner@example.com", PASSWORD)
    with pytest.raises(imaplib.IMAP4.error):
        conn.select("INBOX")


# --- the SMTP double ---------------------------------------------------------


def test_starttls_on_an_already_encrypted_connection_is_refused():
    server = FakeSMTPServer()
    srv = server.connect_ssl("smtp.example.com", 465, timeout=30)
    with pytest.raises(smtplib.SMTPNotSupportedError):
        srv.starttls()


def test_login_without_tls_is_refused_the_way_a_real_submission_port_does():
    server = FakeSMTPServer()
    srv = server.connect_plain("smtp.example.com", 587, timeout=30)
    with pytest.raises(smtplib.SMTPNotSupportedError):
        srv.login("owner@example.com", PASSWORD)


def test_sending_before_login_is_refused():
    server = FakeSMTPServer()
    srv = server.connect_plain("smtp.example.com", 587, timeout=30)
    srv.starttls()
    with pytest.raises(smtplib.SMTPSenderRefused):
        srv.sendmail("owner@example.com", ["x@example.com"], b"Subject: hi\r\n\r\nhi\r\n")
    assert server.sent == []


def test_sending_after_quit_is_refused():
    server = FakeSMTPServer(require_login=False, require_tls=False)
    srv = server.connect_plain()
    srv.quit()
    with pytest.raises(smtplib.SMTPServerDisconnected):
        srv.sendmail("a@example.com", ["b@example.com"], b"\r\n")


def test_every_recipient_refused_raises_but_a_partial_refusal_is_returned():
    server = FakeSMTPServer(
        require_login=False, require_tls=False, refuse_recipients={"gone@example.com"}
    )
    srv = server.connect_plain()
    partial = srv.sendmail(
        "owner@example.com", ["ok@example.com", "gone@example.com"], b"\r\nbody\r\n"
    )
    assert list(partial) == ["gone@example.com"]
    assert len(server.sent) == 1

    with pytest.raises(smtplib.SMTPRecipientsRefused):
        srv.sendmail("owner@example.com", ["gone@example.com"], b"\r\nbody\r\n")
    assert len(server.sent) == 1  # the refused one is not counted as sent
    assert len(server.refused) == 1


def test_a_scripted_send_failure_raises_after_the_calls_it_was_told_to_allow():
    server = FakeSMTPServer(require_login=False, require_tls=False)
    server.fail_on_send(smtp_auth_failed(), after=1)
    srv = server.connect_plain()
    srv.sendmail("a@example.com", ["b@example.com"], b"\r\n")
    with pytest.raises(smtplib.SMTPAuthenticationError):
        srv.sendmail("a@example.com", ["b@example.com"], b"\r\n")


# --- the sample mailbox ------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["plain", "unicode", "alt", "htmlonly", "attached", "latin1", "threaded",
     "mislabelled", "headerless"],
)
def test_every_sample_message_renders_to_bytes(key):
    raw = sample_mailbox().by_key(key).as_bytes()
    assert isinstance(raw, bytes) and raw


def test_the_sample_messages_use_crlf_like_a_real_server():
    raw = sample_mailbox().by_key("plain").as_bytes()
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"") .count(b"\n") == 0


def test_a_unicode_subject_is_encoded_on_the_wire_not_left_raw():
    """If the double emitted raw UTF-8 headers the provider's MIME decoding
    would never be exercised."""
    raw = sample_mailbox().by_key("unicode").as_bytes()
    assert b"=?utf-8?" in raw.lower()
    assert "Café".encode() not in raw


def test_the_latin1_message_really_carries_latin1_bytes():
    msg = sample_mailbox().by_key("latin1")
    parsed = email.message_from_bytes(msg.as_bytes())
    assert parsed.get_content_charset() == "iso-8859-1"
    assert parsed.get_payload(decode=True).decode("iso-8859-1").startswith("Grüße")


def test_the_mislabelled_message_is_undecodable_as_the_charset_it_claims():
    raw = sample_mailbox().by_key("mislabelled").as_bytes()
    assert b"charset=utf-8" in raw
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")


def test_a_custom_mailbox_can_be_built_from_scratch():
    box = FakeMailbox(
        {
            "INBOX": [
                FakeMessage(
                    key="only",
                    subject="Hi",
                    from_addr="a@example.com",
                    body="text",
                    attachments=(Attachment("f.bin", b"\x00\x01"),),
                )
            ]
        }
    )
    assert box.keys() == ["only"]
    assert b"f.bin" in box.by_key("only").as_bytes()


# --- the provider, driven end to end through the doubles ---------------------


def test_listing_the_inbox_returns_newest_first_and_decodes_the_headers(world):
    out = provider().list_messages()
    assert [m.id for m in out] == [str(i) for i in range(9, 0, -1)]
    assert out[-1].subject == "Invoice 4471"
    assert out[-1].from_addr == "Billing <billing@example.com>"
    subjects = [m.subject for m in out]
    assert "Café ☕ — réunion demain" in subjects


def test_listing_the_inbox_leaves_every_message_unread(world):
    """The provider peeks. If it ever stops, the owner's whole inbox goes grey
    the first time Remedy looks at it — and this test is how we find out."""
    before = set(world.imap.seen_keys())
    provider().list_messages()
    assert set(world.imap.seen_keys()) == before
    assert all("PEEK" in spec for _seq, spec in world.imap.fetches)
    assert world.imap.selects == [("INBOX", True)]


def test_a_message_with_no_subject_is_labelled_not_left_blank(world):
    out = provider().list_messages()
    assert out[0].subject == "(no subject)"


@pytest.mark.parametrize(
    ("query", "criteria"),
    [
        ("", ("ALL",)),
        ("unread", ("UNSEEN",)),
        ("from: billing@example.com", ("FROM", "billing@example.com")),
        ("subject: Weekly digest", ("SUBJECT", '"Weekly digest"')),
        ("margin", ("TEXT", "margin")),
        ("SINCE 1-Aug-2026", ("SINCE", "1-Aug-2026")),
    ],
)
def test_a_query_is_translated_into_the_imap_criteria_it_claims(world, query, criteria):
    provider().list_messages(query=query)
    assert world.imap.searches == [criteria]


def test_the_limit_is_clamped_and_never_asks_for_more_than_it_wants(world):
    provider().list_messages(limit=2)
    assert len(world.imap.fetches) == 2


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("plain", "The invoice is waiting on the portal."),
        ("unicode", "On se voit à 9h ?"),
        ("alt", "Plain part wins."),
        ("latin1", "Grüße aus München"),
    ],
)
def test_the_body_of_a_message_is_decoded_whatever_its_charset(world, key, expected):
    seq = world.mailbox.seq_of(key)
    assert expected in provider().get_message(seq).snippet


def test_a_text_attachment_is_not_mistaken_for_the_body(world):
    """The attachment is text/plain too. Picking the first text/plain part
    without checking for a filename would quote the transcript back instead."""
    got = provider().get_message(world.mailbox.seq_of("attached")).snippet
    assert got.strip() == "Notes are attached."
    assert "ATTACHMENT TEXT" not in got


def test_a_body_that_lies_about_its_charset_is_replaced_not_raised(world):
    got = provider().get_message(world.mailbox.seq_of("mislabelled")).snippet
    assert "Joyeux anniversaire" in got
    assert "�" in got


def test_reading_a_message_leaves_it_unread(world):
    provider().get_message(world.mailbox.seq_of("unicode"))
    assert "\\Seen" not in world.mailbox.by_key("unicode").flags


def test_an_empty_message_id_is_refused_before_a_connection_is_opened(world):
    with pytest.raises(ValueError):
        provider().get_message("  ")
    assert world.imap.connections == []


# --- sending -----------------------------------------------------------------


def test_sending_records_exactly_what_would_have_left_the_machine(world):
    out = provider().send_message(
        to="friend@example.com", subject="Lunch", body="Tuesday works.\n"
    )
    sent = world.smtp.last_sent
    assert out["ok"] is True
    assert sent.from_addr == "owner@fastmail.com"
    assert sent.to_addrs == ("friend@example.com",)
    assert sent.subject == "Lunch"
    assert sent.body == "Tuesday works.\n"
    assert sent.header("Message-ID") == out["message_id"]


def test_a_unicode_body_survives_the_round_trip(world):
    provider().send_message(to="ami@example.fr", subject="Café", body="À bientôt ☕")
    assert world.smtp.last_sent.body == "À bientôt ☕"
    assert world.smtp.last_sent.subject == "Café"


@pytest.mark.parametrize(
    ("address", "port", "ssl", "starttls"),
    [
        ("owner@fastmail.com", 465, True, 0),
        ("owner@gmail.com", 587, False, 1),
    ],
)
def test_the_transport_matches_the_preset_port(world, address, port, ssl, starttls):
    """Port 465 is implicit TLS; 587 must be upgraded. Getting this backwards
    puts the app password on the wire in clear."""
    provider(address).send_message(to="a@example.com", subject="s", body="b")
    assert world.smtp.connections == [(f"smtp.{address.split('@')[1]}", port, 30, ssl)]
    assert world.smtp.starttls_calls == starttls


def test_the_password_goes_only_to_the_hosts_from_the_preset(world):
    provider().verify()
    assert world.imap.connections == [("imap.fastmail.com", 993, 30)]
    assert world.smtp.connections == [("smtp.fastmail.com", 465, 30, True)]
    assert world.imap.logins == [("owner@fastmail.com", PASSWORD)]
    assert world.smtp.logins == [("owner@fastmail.com", PASSWORD)]


def test_replying_stays_in_the_thread_and_answers_the_reply_to_header(world):
    out = provider().reply_to_message(world.mailbox.seq_of("threaded"), body="Noted.")
    sent = world.smtp.last_sent
    assert sent.to_addrs == ("legal-inbox@example.com",)
    assert sent.header("In-Reply-To") == "<threaded@example.com>"
    assert sent.header("References") == (
        "<root@example.com> <second@example.com> <threaded@example.com>"
    )
    assert sent.subject == "Re: Contract draft"  # not "Re: Re: …"
    assert out["thread_id"] == "<threaded@example.com>"


def test_reply_all_widens_the_envelope_and_a_plain_reply_does_not(world):
    seq = world.mailbox.seq_of("threaded")
    provider().reply_to_message(seq, body="Just you.")
    provider().reply_to_message(seq, body="Everyone.", reply_all=True)
    assert world.smtp.recipients == [
        ("legal-inbox@example.com",),
        ("legal-inbox@example.com", "paralegal@example.com", "archive@example.com"),
    ]


def test_a_draft_is_appended_to_the_drafts_folder_and_never_sent(world):
    out = provider().create_draft(to="a@example.com", subject="Later", body="Half done")
    appended = world.imap.appends[0]
    assert out["ok"] is True
    assert appended.folder == "Drafts"
    assert appended.flags == "\\Draft"
    assert appended.message["To"] == "a@example.com"
    assert world.smtp.sent == []  # a draft is not a send


def test_marking_read_and_unread_moves_the_flag_both_ways(world):
    seq = world.mailbox.seq_of("unicode")
    provider().mark_read(seq)
    assert "\\Seen" in world.mailbox.by_key("unicode").flags
    provider().mark_read(seq, read=False)
    assert "\\Seen" not in world.mailbox.by_key("unicode").flags
    assert world.imap.selects == [("INBOX", False), ("INBOX", False)]


def test_archiving_copies_then_expunges_rather_than_deleting(world):
    out = provider().archive_message(world.mailbox.seq_of("plain"))
    assert out["ok"] is True
    assert world.mailbox.keys("Archive") == ["plain"]
    assert "plain" not in world.mailbox.keys("INBOX")
    assert world.imap.expunged == ["plain"]


def test_archiving_into_a_folder_this_mailbox_does_not_have_is_reported(world):
    with pytest.raises(RuntimeError, match="check the archive folder name"):
        provider("owner@gmail.com").archive_message("1")
    assert world.mailbox.keys("INBOX")[0] == "plain"  # nothing was deleted


# --- failure paths -----------------------------------------------------------


def test_a_rejected_app_password_becomes_an_instruction_the_owner_can_follow(world):
    world.imap.fail_at_login()
    with pytest.raises(RuntimeError, match="16-character") as err:
        provider("owner@gmail.com").list_messages()
    assert "myaccount.google.com/apppasswords" in str(err.value)


def test_a_disabled_mailbox_says_so_instead_of_quoting_the_server(world):
    world.imap.fail_at_login(imap_disabled())
    with pytest.raises(RuntimeError, match="IMAP is turned off"):
        provider().list_messages()


def test_a_refused_smtp_login_never_reaches_the_send(world):
    world.smtp.fail_at_login()
    with pytest.raises(RuntimeError, match="Mail error"):
        provider().send_message(to="a@example.com", subject="s", body="b")
    assert world.smtp.sent == []


def test_a_send_that_fails_is_not_reported_as_sent(world):
    world.smtp.fail_on_send()
    with pytest.raises(smtplib.SMTPSenderRefused):
        provider().send_message(to="a@example.com", subject="s", body="b")
    assert world.smtp.sent == []


def test_a_fetch_that_dies_part_way_is_not_silently_truncated(world):
    """A half-read inbox presented as the whole inbox is worse than an error:
    the owner would be told a message they can see in their client is gone."""
    world.imap.fail_mid_fetch(after=3)
    with pytest.raises(imaplib.IMAP4.error):
        provider().list_messages()


@pytest.mark.parametrize(
    "op", ["connect", "select", "search", "fetch"]
)
def test_the_imap_connection_is_closed_however_the_call_fails(world, op):
    world.imap.fail(op)
    with pytest.raises((imaplib.IMAP4.error, RuntimeError)):
        provider().list_messages()
    assert world.imap.leaked_connections == []


def test_the_smtp_connection_is_closed_when_the_send_fails(world):
    world.smtp.fail_on_send()
    with pytest.raises(smtplib.SMTPException):
        provider().send_message(to="a@example.com", subject="s", body="b")
    assert world.smtp.leaked_connections == []
    assert world.smtp.quits == 1


def test_a_failed_logout_does_not_lose_a_good_result(world):
    world.imap.fail("logout")
    assert provider().list_messages()


def test_a_mailbox_that_demands_an_app_password_says_which_kind(world):
    world.imap.fail_at_login(imap_needs_app_password())
    with pytest.raises(RuntimeError, match="requires an app password"):
        provider("owner@gmail.com").list_messages()


def test_a_refused_recipient_is_raised_not_reported_as_sent(world):
    """SMTPRecipientsRefused escapes ``send_message`` untranslated. The point
    of the test is the second assertion: nothing may be recorded as sent."""
    world.smtp.fail_on_send(smtp_recipients_refused(["gone@example.com"]))
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        provider().send_message(to="gone@example.com", subject="s", body="b")
    assert world.smtp.sent == []


def test_a_hand_built_mailbox_can_be_installed_instead_of_the_sample(monkeypatch):
    box = FakeMailbox(
        {"INBOX": [FakeMessage(key="one", subject="Only one", from_addr="a@example.com",
                               body="hi")]}
    )
    world = install_fake_mail(
        monkeypatch, imap=FakeIMAPServer(box), smtp=FakeSMTPServer(require_tls=False)
    )
    out = provider().list_messages()
    assert [m.subject for m in out] == ["Only one"]
    assert world.mailbox is box


# --- provider behaviour that is wrong but current ----------------------------


def test_a_rejected_login_closes_the_connection_it_opened(world):
    """``_imap`` and ``_smtp`` used to build the connection then let the login
    error escape without closing it. Against a real server that is a TLS socket
    held until garbage collection — once per retry of a wrong app password."""
    world.imap.fail_at_login()
    with pytest.raises(RuntimeError):
        provider().list_messages()
    assert world.imap.leaked_connections == []

    world.smtp.fail_at_login()
    with pytest.raises(RuntimeError):
        provider().send_message(to="a@example.com", subject="s", body="b")
    assert world.smtp.leaked_connections == []


def test_a_message_id_that_does_not_exist_is_reported_as_not_found(world):
    """imaplib answers ('OK', [None]) for a message set the server ignored, so
    ``get_message`` treated that as a hit and parsed zero bytes — a deleted or
    mistyped id yielded a blank "(no subject)" message instead of an error."""
    with pytest.raises(RuntimeError, match="not found"):
        provider().get_message("999")
    assert world.imap.leaked_connections == []

def test_a_draft_appended_to_a_missing_folder_is_not_reported_as_saved(world):
    """APPEND answers NO [TRYCREATE] for a Drafts folder named differently —
    Gmail's "[Gmail]/Drafts", any localised name. The return code used to be
    ignored, so the owner was told the draft was saved when it existed
    nowhere."""
    with pytest.raises(RuntimeError, match="draft"):
        provider("owner@gmail.com").create_draft(
            to="a@example.com", subject="s", body="b"
        )
    assert world.imap.appends[0].folder == "[Gmail]/Drafts"
    assert "[Gmail]/Drafts" not in world.mailbox.folders


def test_verify_does_not_claim_success_when_the_inbox_cannot_be_selected(world):
    """verify() is the connect flow's only check. SELECT's return code was
    never read, so "IMAP + SMTP verified" was claimed for a mailbox whose
    INBOX the server refused."""
    world.mailbox.folders.pop("INBOX")
    with pytest.raises(RuntimeError, match="INBOX"):
        provider().verify()


def test_marking_a_missing_message_read_is_not_reported_as_success(world):
    """STORE against a message set the server ignored is an OK no-op, so the
    return code alone said a message was marked when none was."""
    with pytest.raises(RuntimeError, match="not found"):
        provider().mark_read("999")
    # The UID never resolved, so STORE saw an empty set — assert on what
    # the provider actually asked for.
    assert world.imap.uid_calls[-1][0] == "STORE"
    assert world.imap.uid_calls[-1][1][0] == "999"


def test_BUG_archiving_a_message_number_that_matched_nothing_reports_success(world):
    """COPY of an empty message set is an OK no-op on Dovecot and Gmail, and the
    provider only checks the return code, so nothing is archived and the owner
    is told it was."""
    out = provider().archive_message("999")
    assert out["ok"] is True
    assert world.mailbox.keys("Archive") == []


def test_message_ids_are_uids_and_do_not_shift_under_the_caller(world):
    """``list_messages`` labelled them ``uid`` in ``raw`` but they were SELECT
    sequence numbers — positions, which renumber on expunge. Archiving one
    message moved every id the caller was still holding onto the message next
    door, so a later archive or mark-read acted on the wrong mail."""
    listed = {m.subject: m.id for m in provider().list_messages()}
    held = listed["Café ☕ — réunion demain"]

    provider().archive_message(listed["Invoice 4471"])  # renumbers the rest

    assert provider().get_message(held).subject == "Café ☕ — réunion demain"
