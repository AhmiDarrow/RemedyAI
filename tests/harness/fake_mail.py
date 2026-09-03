"""In-process stand-ins for an IMAP server and an SMTP server.

``assistant/providers/imap_smtp.py`` is the one place in Remedy that carries
the owner's mailbox password and can put a message on the wire under their
name. It cannot be exercised against a real mailbox in a test suite, and
monkeypatching the provider's own methods away proves nothing: the parts that
go wrong are the IMAP grammar (does listing PEEK, or does it silently mark the
whole inbox read?), the return codes nobody checks, the charset of a body that
is not UTF-8, and exactly which recipients ``send_message`` hands to the
server.

So this module supplies doubles for ``imaplib.IMAP4_SSL``, ``smtplib.SMTP``
and ``smtplib.SMTP_SSL`` that behave like a small, strict server: they refuse
commands issued in the wrong order, they honour read-only SELECT, they return
the same odd shapes real ``imaplib`` returns (``('OK', [None])`` for a message
number that does not exist), and they record every byte that would have left
the machine.

What breaks if this file is wrong: a test believes the provider survives a
rejected password, a disabled mailbox, a fetch that dies halfway or a refused
recipient, when in truth the double was simply easier to please than a real
server — and a mail bug ships. Worse, a double that is too permissive hides
the return codes the provider never checks, which is where the real defects in
this area live.

No socket is opened and no address is resolved. The doubles are objects
monkeypatched over ``imaplib``/``smtplib``; installing them also makes an
accidental real connection impossible for the duration of the test.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import re
import smtplib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from typing import Any

__all__ = [
    "Appended",
    "Attachment",
    "FakeIMAP4SSL",
    "FakeIMAPServer",
    "FakeMailWorld",
    "FakeMailbox",
    "FakeMessage",
    "FakeSMTP",
    "FakeSMTPSSL",
    "FakeSMTPServer",
    "SentMail",
    "imap_auth_failed",
    "imap_disabled",
    "imap_needs_app_password",
    "install_fake_mail",
    "sample_mailbox",
    "smtp_auth_failed",
    "smtp_recipients_refused",
]

# Real servers serialise with CRLF; a body split on bare LF decodes
# differently, so the doubles must not quietly hand out \n-only bytes.
_SMTP_POLICY = email.policy.SMTP

_ErrorSpec = BaseException | type[BaseException] | str | Callable[[], BaseException] | None


# --- errors a real server actually produces ---------------------------------


def imap_auth_failed(text: str = "") -> imaplib.IMAP4.error:
    """What Gmail says to a wrong app password (imaplib re-raises the raw line)."""
    return imaplib.IMAP4.error(
        text or "b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'"
    )


def imap_disabled(text: str = "") -> imaplib.IMAP4.error:
    return imaplib.IMAP4.error(
        text or "b'[ALERT] IMAP access is disabled for your domain.'"
    )


def imap_needs_app_password(text: str = "") -> imaplib.IMAP4.error:
    return imaplib.IMAP4.error(
        text or "b'Application-specific password required. Learn more'"
    )


def smtp_auth_failed(code: int = 535) -> smtplib.SMTPAuthenticationError:
    return smtplib.SMTPAuthenticationError(
        code, b"5.7.8 Username and Password not accepted"
    )


def smtp_recipients_refused(
    addrs: Iterable[str] = ("nobody@example.com",),
) -> smtplib.SMTPRecipientsRefused:
    return smtplib.SMTPRecipientsRefused(
        dict.fromkeys(addrs, (550, b"5.1.1 No such user here"))
    )


def _as_error(spec: _ErrorSpec, default: Callable[[], BaseException]) -> BaseException:
    """Accept an instance, a class, a message or a factory — raise a real error."""
    if spec is None:
        return default()
    if isinstance(spec, BaseException):
        return spec
    if isinstance(spec, str):
        return type(default())(spec)
    if isinstance(spec, type) and issubclass(spec, BaseException):
        return spec("scripted failure")
    return spec()


@dataclass
class _Fault:
    """One scripted failure. ``after`` lets a fetch die part-way through a run."""

    error: _ErrorSpec = None
    after: int = 0
    times: int | None = None
    seen: int = 0
    fired: int = 0


class _Faultable:
    """Mixin: ``fail(op)`` scripts a failure, ``_check(op)`` raises it in place."""

    def __init__(self) -> None:
        self._faults: dict[str, list[_Fault]] = {}

    @staticmethod
    def _default_error(op: str) -> BaseException:
        raise NotImplementedError

    def fail(
        self,
        op: str,
        error: _ErrorSpec = None,
        *,
        after: int = 0,
        times: int | None = None,
    ) -> None:
        """Make ``op`` raise. ``after=N`` lets N calls through first."""
        self._faults.setdefault(op, []).append(
            _Fault(error=error, after=after, times=times)
        )

    def _check(self, op: str) -> None:
        for fault in self._faults.get(op, ()):
            fault.seen += 1
            if fault.seen <= fault.after:
                continue
            if fault.times is not None and fault.fired >= fault.times:
                continue
            fault.fired += 1
            raise _as_error(fault.error, lambda: self._default_error(op))


# --- messages ---------------------------------------------------------------


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes = b""
    maintype: str = "application"
    subtype: str = "octet-stream"


@dataclass
class FakeMessage:
    """One message in the fake mailbox, rendered to RFC822 bytes on demand."""

    key: str = ""
    subject: str = ""
    from_addr: str = ""
    to: str = ""
    cc: str = ""
    reply_to: str = ""
    date: str = "Mon, 17 Aug 2026 09:14:00 +0000"
    message_id: str = ""
    references: str = ""
    body: str = ""
    charset: str = "utf-8"
    html: str = ""
    html_only: bool = False
    attachments: tuple[Attachment, ...] = ()
    flags: set[str] = field(default_factory=set)
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: Assigned by the mailbox. Stable for the life of the message and never
    #: reused — unlike a sequence number, which is a position and renumbers on
    #: every expunge.
    uid: int = 0
    # Bytes exactly as the server would hand them over, for the cases a
    # well-formed builder cannot express: a body that lies about its charset,
    # a message with no headers at all.
    raw_override: bytes | None = None

    def build(self) -> EmailMessage:
        msg = EmailMessage()
        for name, value in (
            ("Subject", self.subject),
            ("From", self.from_addr),
            ("To", self.to),
            ("Cc", self.cc),
            ("Reply-To", self.reply_to),
            ("Date", self.date),
            ("Message-ID", self.message_id),
            ("References", self.references),
        ):
            if value:
                msg[name] = value
        for name, value in self.extra_headers.items():
            msg[name] = value
        if self.html_only:
            msg.set_content(self.html, subtype="html", charset=self.charset)
        else:
            msg.set_content(self.body, charset=self.charset)
            if self.html:
                msg.add_alternative(self.html, subtype="html", charset=self.charset)
        for att in self.attachments:
            msg.add_attachment(
                att.content,
                maintype=att.maintype,
                subtype=att.subtype,
                filename=att.filename,
            )
        return msg

    def as_bytes(self) -> bytes:
        if self.raw_override is not None:
            return self.raw_override
        return self.build().as_bytes(policy=_SMTP_POLICY)

    def header_bytes(self, fields: Sequence[str]) -> bytes:
        """Only the named headers — how BODY[HEADER.FIELDS (…)] answers.

        Handing back the whole message here would let a provider that never
        asked for the body look as if it worked.
        """
        parsed = email.message_from_bytes(self.as_bytes())
        wanted = {f.upper() for f in fields}
        lines = [
            f"{name}: {value}".encode(errors="replace")
            for name, value in parsed.items()
            if name.upper() in wanted
        ]
        return b"\r\n".join(lines) + b"\r\n\r\n"

    def all_header_bytes(self) -> bytes:
        raw = self.as_bytes()
        for sep in (b"\r\n\r\n", b"\n\n"):
            head, found, _rest = raw.partition(sep)
            if found:
                return head + sep
        return raw

    def searchable_text(self) -> str:
        return self.as_bytes().decode("utf-8", "replace")


def _msg(key: str, **kw: Any) -> FakeMessage:
    kw.setdefault("message_id", f"<{key}@example.com>")
    return FakeMessage(key=key, **kw)


def sample_mailbox() -> FakeMailbox:
    """A spread wide enough to break a naive reader.

    Every entry is here because something in the provider has to survive it:
    encoded-word headers, an alternative part, HTML with no plain sibling, a
    *text* attachment that must not be mistaken for the body, a latin-1 body,
    a body that lies about being UTF-8, and a message with no Subject or From
    at all.
    """
    return FakeMailbox(
        {
            "INBOX": [
                _msg(
                    "plain",
                    subject="Invoice 4471",
                    from_addr="Billing <billing@example.com>",
                    to="owner@example.com",
                    body="The invoice is waiting on the portal.\nThanks.\n",
                    flags={"\\Seen"},
                ),
                _msg(
                    "unicode",
                    subject="Café ☕ — réunion demain",
                    from_addr="Zoë Ünicode <zoe@example.com>",
                    to="owner@example.com",
                    body="On se voit à 9h ?\n",
                ),
                _msg(
                    "alt",
                    subject="Weekly digest",
                    from_addr="digest@example.com",
                    to="owner@example.com",
                    body="Plain part wins.\n",
                    html="<p>HTML part loses.</p>",
                    flags={"\\Seen"},
                ),
                _msg(
                    "htmlonly",
                    subject="Newsletter",
                    from_addr="news@example.com",
                    to="owner@example.com",
                    html="<html><body><p>Hello <b>there</b>.</p></body></html>",
                    html_only=True,
                ),
                _msg(
                    "attached",
                    subject="Notes from the call",
                    from_addr="pm@example.com",
                    to="owner@example.com",
                    body="Notes are attached.\n",
                    attachments=(
                        Attachment(
                            "transcript.txt",
                            b"ATTACHMENT TEXT, NOT THE BODY",
                            "text",
                            "plain",
                        ),
                        Attachment(
                            "chart.png", b"\x89PNG\r\n\x1a\n\x00binary", "image", "png"
                        ),
                    ),
                    flags={"\\Seen"},
                ),
                _msg(
                    "latin1",
                    subject="Wetterbericht",
                    from_addr="wetter@example.de",
                    to="owner@example.com",
                    body="Grüße aus München, es regnet.\n",
                    charset="iso-8859-1",
                ),
                _msg(
                    "threaded",
                    subject="Re: Contract draft",
                    from_addr="Legal <legal@example.com>",
                    to="owner@example.com",
                    cc="paralegal@example.com, archive@example.com",
                    reply_to="Legal Inbox <legal-inbox@example.com>",
                    references="<root@example.com> <second@example.com>",
                    body="Comments are in the margin.\n",
                ),
                FakeMessage(
                    key="mislabelled",
                    # Declares UTF-8, carries latin-1. Real mail does this daily.
                    raw_override=(
                        b"Subject: Anniversaire\r\n"
                        b"From: fete@example.fr\r\n"
                        b"Date: Mon, 17 Aug 2026 09:14:00 +0000\r\n"
                        b"Message-ID: <mislabelled@example.com>\r\n"
                        b"Content-Type: text/plain; charset=utf-8\r\n"
                        b"Content-Transfer-Encoding: 8bit\r\n"
                        b"\r\n"
                        b"Joyeux anniversaire \xe0 tous\r\n"
                    ),
                ),
                FakeMessage(
                    key="headerless",
                    raw_override=b"\r\nA message with no headers at all.\r\n",
                ),
            ],
            "Archive": [],
            "Drafts": [],
        }
    )


# --- mailbox ----------------------------------------------------------------


def _unquote(value: Any) -> str:
    text = _as_text(value).strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return text.replace('\\"', '"').replace("\\\\", "\\")


def _as_text(value: Any) -> str:
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


class FakeMailbox:
    """Folders of messages. Sequence numbers are 1-based positions, as in IMAP."""

    def __init__(self, folders: dict[str, list[FakeMessage]] | None = None) -> None:
        self.folders: dict[str, list[FakeMessage]] = {"INBOX": []}
        self._next_uid = 1
        for name, msgs in (folders or {}).items():
            self.folders[name] = list(msgs)
        for msgs in self.folders.values():
            for msg in msgs:
                if not msg.uid:
                    msg.uid = self._next_uid
                    self._next_uid += 1

    def assign_uid(self, msg: FakeMessage) -> int:
        """Give a newly added message its permanent id."""
        if not msg.uid:
            msg.uid = self._next_uid
            self._next_uid += 1
        return msg.uid

    def by_uid(self, uid: str | int, name: str = "INBOX") -> FakeMessage | None:
        try:
            want = int(_as_text(uid))
        except (TypeError, ValueError):
            return None
        return next((m for m in self.folder(name) if m.uid == want), None)

    def uid_of(self, key: str, name: str = "INBOX") -> str:
        """The UID of the message with this key, as the wire spells it."""
        return str(self.by_key(key, name).uid)

    def folder(self, name: str = "INBOX") -> list[FakeMessage]:
        return self.folders[_unquote(name)]

    def has_folder(self, name: str) -> bool:
        return _unquote(name) in self.folders

    def add_folder(self, name: str) -> None:
        self.folders.setdefault(_unquote(name), [])

    def keys(self, name: str = "INBOX") -> list[str]:
        return [m.key for m in self.folder(name)]

    def by_key(self, key: str, name: str = "INBOX") -> FakeMessage:
        for msg in self.folder(name):
            if msg.key == key:
                return msg
        raise KeyError(key)

    def seq_of(self, key: str, name: str = "INBOX") -> str:
        return str(self.folder(name).index(self.by_key(key, name)) + 1)

    def at(self, seq: str | int, name: str = "INBOX") -> FakeMessage | None:
        try:
            index = int(_as_text(seq))
        except (TypeError, ValueError):
            return None
        msgs = self.folder(name)
        if 1 <= index <= len(msgs):
            return msgs[index - 1]
        return None

    def expunge(self, name: str = "INBOX") -> list[str]:
        """Drop \\Deleted messages and renumber — the trap a stale id falls into."""
        msgs = self.folder(name)
        gone = [m.key for m in msgs if "\\Deleted" in m.flags]
        self.folders[_unquote(name)] = [m for m in msgs if "\\Deleted" not in m.flags]
        return gone


# --- IMAP -------------------------------------------------------------------


_HEADER_FIELDS_RE = re.compile(r"HEADER\.FIELDS\s*\(([^)]*)\)", re.IGNORECASE)

# Criteria that swallow the token after them, so the parser stays in step.
_SEARCH_WITH_ARG = frozenset(
    {
        "FROM", "TO", "CC", "BCC", "SUBJECT", "BODY", "TEXT", "SINCE", "BEFORE",
        "ON", "HEADER", "KEYWORD", "LARGER", "SMALLER",
    }
)


def _flag_set(flags: Any) -> set[str]:
    return {f for f in _as_text(flags).replace("(", " ").replace(")", " ").split() if f}


def _matches(msg: FakeMessage, criteria: Sequence[Any]) -> bool:
    """Enough IMAP SEARCH to stay honest about what the provider asked for."""
    parsed = email.message_from_bytes(msg.as_bytes())
    toks = [_as_text(c) for c in criteria]
    i = 0
    while i < len(toks):
        tok = toks[i].upper()
        arg = _unquote(toks[i + 1]) if i + 1 < len(toks) else ""
        if tok in ("UNSEEN", "NEW"):
            if "\\Seen" in msg.flags:
                return False
            i += 1
        elif tok == "SEEN":
            if "\\Seen" not in msg.flags:
                return False
            i += 1
        elif tok in ("FROM", "TO", "CC", "SUBJECT"):
            header = {"CC": "Cc", "FROM": "From", "TO": "To", "SUBJECT": "Subject"}[tok]
            if arg.lower() not in str(parsed.get(header, "")).lower():
                return False
            i += 2
        elif tok in ("BODY", "TEXT"):
            if arg.lower() not in msg.searchable_text().lower():
                return False
            i += 2
        elif tok in _SEARCH_WITH_ARG:
            # Accepted and recorded, but not filtered on: a test that cares
            # asserts against ``server.searches`` rather than pretending this
            # double implements IMAP date arithmetic.
            i += 2
        else:  # ALL, and anything else this double does not model
            i += 1
    return True


@dataclass
class Appended:
    """One APPEND, kept whole so a test can read the draft that was stored."""

    folder: str
    flags: str
    date_time: str
    raw: bytes

    @property
    def message(self) -> Message:
        return email.message_from_bytes(self.raw)


class FakeIMAP4SSL:
    """One IMAP connection. Refuses anything issued out of order."""

    def __init__(
        self,
        server: FakeIMAPServer,
        host: str = "",
        port: int = 993,
        *,
        timeout: float | None = None,
        **_kw: Any,
    ) -> None:
        self.server = server
        self.host = host
        self.port = port
        self.timeout = timeout
        self.state = "NONAUTH"
        self.selected = ""
        self.is_readonly = False
        self.closed = False

    # -- plumbing --

    def _require(self, op: str, *states: str) -> None:
        if self.closed:
            raise imaplib.IMAP4.error(f"{op} on a logged-out connection")
        if self.state not in states:
            raise imaplib.IMAP4.error(
                f"{op} illegal in state {self.state}, allowed in {list(states)}"
            )
        self.server._check(op)
        self.server.ops.append(op)

    def _mailbox(self) -> list[FakeMessage]:
        return self.server.mailbox.folder(self.selected)

    # -- the commands the provider actually calls --

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self._require("login", "NONAUTH")
        self.server.logins.append((user, password))
        expected = self.server.accounts
        if expected is not None and expected.get(user) != password:
            raise imap_auth_failed()
        self.state = "AUTH"
        return "OK", [b"LOGIN completed"]

    def select(
        self, mailbox: str = "INBOX", readonly: bool = False
    ) -> tuple[str, list[bytes]]:
        self._require("select", "AUTH", "SELECTED")
        name = _unquote(mailbox)
        self.server.selects.append((name, bool(readonly)))
        if not self.server.mailbox.has_folder(name):
            # imaplib returns NO here rather than raising — the caller has to
            # look at the return code, and mostly nobody does.
            self.state = "AUTH"
            self.selected = ""
            return "NO", [b"Mailbox doesn't exist: " + name.encode()]
        self.state = "SELECTED"
        self.selected = name
        self.is_readonly = bool(readonly)
        return "OK", [str(len(self.server.mailbox.folder(name))).encode()]

    # -- UID commands ------------------------------------------------------
    # A UID is stable; a sequence number is a position. The provider speaks
    # UID so that archiving one message does not silently shift every id the
    # caller is still holding onto.

    def _uid_to_seq(self, message_set: Any) -> str:
        """Translate a UID set to the sequence numbers it points at *now*.

        A UID with no message left resolves to nothing, which the delegated
        command then treats exactly as a message set the server ignored.
        """
        msgs = self._mailbox()
        out: list[str] = []
        for part in _as_text(message_set).split(","):
            try:
                want = int(part.strip())
            except ValueError:
                continue
            for i, msg in enumerate(msgs, 1):
                if msg.uid == want:
                    out.append(str(i))
                    break
        return ",".join(out)

    def uid(self, command: Any, *args: Any) -> tuple[str, list[Any]]:
        cmd = _as_text(command).upper()
        self.server.uid_calls.append((cmd, tuple(_as_text(a) for a in args)))
        if cmd == "SEARCH":
            self._require("search", "SELECTED")
            self.server.searches.append(tuple(_as_text(c) for c in args))
            if self.server.search_result is not None:
                return self.server.search_result
            hits = [
                str(msg.uid).encode()
                for msg in self._mailbox()
                if _matches(msg, args)
            ]
            return "OK", [b" ".join(hits)]
        if not args:
            raise imaplib.IMAP4.error(f"UID {cmd} needs a message set")
        seq = self._uid_to_seq(args[0])
        if cmd == "FETCH":
            return self.fetch(seq, args[1])
        if cmd == "STORE":
            return self.store(seq, args[1], args[2])
        if cmd == "COPY":
            return self.copy(seq, args[1])
        raise imaplib.IMAP4.error(f"unsupported UID command {cmd}")

    def search(self, charset: Any, *criteria: Any) -> tuple[str, list[bytes]]:
        self._require("search", "SELECTED")
        self.server.searches.append(tuple(_as_text(c) for c in criteria))
        self.server.search_charsets.append(charset)
        if self.server.search_result is not None:
            return self.server.search_result
        hits = [
            str(i + 1).encode()
            for i, msg in enumerate(self._mailbox())
            if _matches(msg, criteria)
        ]
        return "OK", [b" ".join(hits)]

    def fetch(self, message_set: Any, message_parts: Any) -> tuple[str, list[Any]]:
        self._require("fetch", "SELECTED")
        seq = _as_text(message_set)
        spec = _as_text(message_parts)
        self.server.fetches.append((seq, spec))
        msg = self.server.mailbox.at(seq, self.selected)
        if msg is None:
            # Exactly what imaplib hands back for a number the server ignored.
            if self.server.strict_message_set:
                return "NO", [b"Invalid messageset"]
            return "OK", [None]
        payload, label = self._section(msg, spec)
        if "PEEK" not in spec.upper() and not self.is_readonly:
            msg.flags.add("\\Seen")
        prefix = f"{seq} ({label} {{{len(payload)}}}".encode()
        return "OK", [(prefix, payload), b")"]

    def _section(self, msg: FakeMessage, spec: str) -> tuple[bytes, str]:
        upper = spec.upper()
        fields = _HEADER_FIELDS_RE.search(spec)
        if fields:
            return msg.header_bytes(fields.group(1).split()), spec.strip("()").replace(
                ".PEEK", ""
            )
        if "HEADER" in upper:
            return msg.all_header_bytes(), "BODY[HEADER]"
        if "RFC822" in upper:
            return msg.as_bytes(), "RFC822"
        return msg.as_bytes(), "BODY[]"

    def store(self, message_set: Any, command: Any, flags: Any) -> tuple[str, list[Any]]:
        self._require("store", "SELECTED")
        seq = _as_text(message_set)
        cmd = _as_text(command)
        self.server.stores.append((seq, cmd, _as_text(flags)))
        if self.is_readonly:
            raise imaplib.IMAP4.error("STORE attempted on a read-only mailbox")
        msg = self.server.mailbox.at(seq, self.selected)
        if msg is None:
            return "OK", [None]
        wanted = _flag_set(flags)
        before = set(msg.flags)
        if cmd.startswith("+"):
            msg.flags |= wanted
        elif cmd.startswith("-"):
            msg.flags -= wanted
        else:
            msg.flags = wanted
        if self.server.silent_noop_store and msg.flags == before:
            return "OK", [None]
        return "OK", [f"{seq} (FLAGS ({' '.join(sorted(msg.flags))}))".encode()]

    def copy(self, message_set: Any, new_mailbox: Any) -> tuple[str, list[Any]]:
        self._require("copy", "SELECTED")
        seq = _as_text(message_set)
        target = _unquote(new_mailbox)
        self.server.copies.append((seq, target))
        if not self.server.mailbox.has_folder(target):
            return "NO", [b"[TRYCREATE] No such mailbox: " + target.encode()]
        msg = self.server.mailbox.at(seq, self.selected)
        if msg is None:
            # Dovecot and Gmail both answer OK to a message set that matched
            # nothing, which is how a stale id becomes a silent no-op.
            if self.server.strict_message_set:
                return "NO", [b"Invalid messageset"]
            return "OK", [None]
        self.server.mailbox.folder(target).append(msg)
        return "OK", [b"[COPYUID 1 1 1] Copy completed"]

    def expunge(self) -> tuple[str, list[bytes]]:
        self._require("expunge", "SELECTED")
        if self.is_readonly:
            raise imaplib.IMAP4.error("EXPUNGE attempted on a read-only mailbox")
        gone = self.server.mailbox.expunge(self.selected)
        self.server.expunged.extend(gone)
        return "OK", [str(len(gone)).encode()]

    def append(
        self, mailbox: Any, flags: Any, date_time: Any, message: bytes
    ) -> tuple[str, list[bytes]]:
        self._require("append", "AUTH", "SELECTED")
        folder = _unquote(mailbox)
        raw = bytes(message)
        self.server.appends.append(
            Appended(folder, _as_text(flags), _as_text(date_time), raw)
        )
        if not self.server.mailbox.has_folder(folder):
            return "NO", [b"[TRYCREATE] No such mailbox: " + folder.encode()]
        parsed = email.message_from_bytes(raw)
        self.server.mailbox.folder(folder).append(
            FakeMessage(
                key=f"appended-{len(self.server.appends)}",
                raw_override=raw,
                message_id=str(parsed.get("Message-ID", "")),
                flags=_flag_set(flags),
            )
        )
        return "OK", [b"[APPENDUID 1 1] APPEND completed"]

    def logout(self) -> tuple[str, list[bytes]]:
        if self.closed:
            raise imaplib.IMAP4.error("logout on a logged-out connection")
        self.server._check("logout")
        self.server.ops.append("logout")
        self.closed = True
        self.state = "LOGOUT"
        self.server.logouts += 1
        return "BYE", [b"LOGOUT Requested"]

    def close(self) -> tuple[str, list[bytes]]:
        self._require("close", "SELECTED")
        self.state = "AUTH"
        self.selected = ""
        return "OK", [b"CLOSE completed"]


class FakeIMAPServer(_Faultable):
    """The scriptable server behind every :class:`FakeIMAP4SSL` connection."""

    def __init__(
        self,
        mailbox: FakeMailbox | None = None,
        *,
        accounts: dict[str, str] | None = None,
        strict_message_set: bool = False,
        silent_noop_store: bool = False,
    ) -> None:
        super().__init__()
        self.mailbox = mailbox if mailbox is not None else sample_mailbox()
        # None accepts any credential; a dict checks address → password.
        self.accounts = accounts
        self.strict_message_set = strict_message_set
        #: Some servers send no untagged FETCH when a STORE changed nothing
        #: (the flag was already set), so the reply is ``('OK', [None])`` for
        #: a message that very much exists.
        self.silent_noop_store = silent_noop_store
        self.search_result: tuple[str, list[bytes]] | None = None

        self.connections: list[tuple[str, int, float | None]] = []
        self.logins: list[tuple[str, str]] = []
        self.selects: list[tuple[str, bool]] = []
        self.searches: list[tuple[str, ...]] = []
        self.search_charsets: list[Any] = []
        self.fetches: list[tuple[str, str]] = []
        self.stores: list[tuple[str, str, str]] = []
        self.copies: list[tuple[str, str]] = []
        #: Every UID command as the caller spelled it — the provider's view,
        #: before translation to whatever position the message occupies now.
        self.uid_calls: list[tuple[str, tuple[str, ...]]] = []
        self.appends: list[Appended] = []
        self.expunged: list[str] = []
        self.logouts = 0
        self.ops: list[str] = []
        self.open_connections: list[FakeIMAP4SSL] = []

    @staticmethod
    def _default_error(op: str) -> BaseException:
        if op == "login":
            return imap_auth_failed()
        return imaplib.IMAP4.error(f"{op.upper()} failed")

    def connect(
        self,
        host: str = "",
        port: int = 993,
        *_args: Any,
        timeout: float | None = None,
        **kw: Any,
    ) -> FakeIMAP4SSL:
        """Stands in for ``imaplib.IMAP4_SSL(host, port, timeout=…)``."""
        self.connections.append((host, port, timeout))
        self._check("connect")
        self.ops.append("connect")
        conn = FakeIMAP4SSL(self, host, port, timeout=timeout, **kw)
        self.open_connections.append(conn)
        return conn

    __call__ = connect

    # -- convenience scripting --

    def fail_at_login(self, error: _ErrorSpec = None) -> None:
        self.fail("login", error)

    def fail_on_select(self, error: _ErrorSpec = None) -> None:
        self.fail("select", error)

    def fail_mid_fetch(self, after: int = 1, error: _ErrorSpec = None) -> None:
        """Die on the (after+1)-th FETCH — a connection dropped part-way."""
        self.fail("fetch", error, after=after)

    # -- what a test wants to assert --

    @property
    def leaked_connections(self) -> list[FakeIMAP4SSL]:
        """Connections never logged out. The provider promises there are none."""
        return [c for c in self.open_connections if not c.closed]

    def seen_keys(self, folder: str = "INBOX") -> list[str]:
        return [m.key for m in self.mailbox.folder(folder) if "\\Seen" in m.flags]


# --- SMTP -------------------------------------------------------------------


@dataclass
class SentMail:
    """One message the provider handed to the server, exactly as given."""

    from_addr: str
    to_addrs: tuple[str, ...]
    raw: bytes
    mail_options: tuple[str, ...] = ()
    rcpt_options: tuple[str, ...] = ()

    @property
    def message(self) -> Message:
        return email.message_from_bytes(self.raw)

    def header(self, name: str) -> str:
        """Return one unfolded header value, still MIME-encoded if applicable."""
        return re.sub(r"\r?\n[ \t]+", " ", str(self.message.get(name, ""))).strip()

    @property
    def subject(self) -> str:
        """The subject a human would read, so a test can assert plain text."""
        raw = self.header("Subject")
        try:
            return str(make_header(decode_header(raw)))
        except Exception:
            return raw

    @property
    def body(self) -> str:
        msg = self.message
        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(msg.get_content_charset() or "utf-8", "replace")


class FakeSMTP(_Faultable):
    """One SMTP connection. ``smtplib`` has no server object, so faults and
    recordings live on :class:`FakeSMTPServer` and are shared by reference."""

    is_ssl = False

    def __init__(
        self,
        recorder: FakeSMTPServer,
        host: str = "",
        port: int = 0,
        *,
        timeout: float | None = None,
        **_kw: Any,
    ) -> None:
        super().__init__()
        self.recorder = recorder
        self._faults = recorder._faults  # scripted on the server, felt here
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = False
        self.closed = False

    @staticmethod
    def _default_error(op: str) -> BaseException:
        if op == "login":
            return smtp_auth_failed()
        if op == "send":
            return smtplib.SMTPSenderRefused(
                451, b"4.3.0 Temporary failure, try again later", "sender@example.com"
            )
        return smtplib.SMTPException(f"{op.upper()} failed")

    def _guard(self, op: str) -> None:
        if self.closed:
            raise smtplib.SMTPServerDisconnected(f"{op} after quit")
        self._check(op)
        self.recorder.ops.append(op)

    def starttls(self, *_args: Any, **_kw: Any) -> tuple[int, bytes]:
        self._guard("starttls")
        if self.is_ssl:
            # smtplib refuses this too. Catching it here stops a provider from
            # "upgrading" a connection that is already TLS with nobody noticing.
            raise smtplib.SMTPNotSupportedError(
                "STARTTLS on a connection that is already SSL"
            )
        if self.started_tls:
            raise smtplib.SMTPNotSupportedError("STARTTLS issued twice")
        self.started_tls = True
        self.recorder.starttls_calls += 1
        return 220, b"2.0.0 Ready to start TLS"

    def login(self, user: str, password: str, **_kw: Any) -> tuple[int, bytes]:
        self._guard("login")
        self.recorder.logins.append((user, password))
        if self.recorder.require_tls and not self.is_ssl and not self.started_tls:
            raise smtplib.SMTPNotSupportedError(
                "AUTH refused on an unencrypted connection"
            )
        expected = self.recorder.accounts
        if expected is not None and expected.get(user) != password:
            raise smtp_auth_failed()
        self.logged_in = True
        return 235, b"2.7.0 Accepted"

    def send_message(
        self,
        msg: Message,
        from_addr: str | None = None,
        to_addrs: Sequence[str] | str | None = None,
        mail_options: Sequence[str] = (),
        rcpt_options: Sequence[str] = (),
    ) -> dict[str, tuple[int, bytes]]:
        self._guard("send")
        if self.recorder.require_login and not self.logged_in:
            raise smtplib.SMTPSenderRefused(
                530, b"5.7.0 Authentication Required", from_addr or ""
            )
        sender = from_addr if from_addr is not None else str(msg.get("From", ""))
        if to_addrs is None:
            recipients = tuple(
                a for _n, a in email.utils.getaddresses(msg.get_all("To") or [])
            )
        elif isinstance(to_addrs, str):
            recipients = (to_addrs,)
        else:
            recipients = tuple(to_addrs)
        record = SentMail(
            from_addr=sender,
            to_addrs=recipients,
            raw=msg.as_bytes(policy=_SMTP_POLICY),
            mail_options=tuple(mail_options),
            rcpt_options=tuple(rcpt_options),
        )
        refused = {
            a: (550, b"5.1.1 No such user here")
            for a in recipients
            if a in self.recorder.refuse_recipients
        }
        if refused and len(refused) == len(recipients):
            # smtplib only raises when *every* recipient was refused; a partial
            # refusal is returned, and the message still went to the rest.
            self.recorder.refused.append(record)
            raise smtplib.SMTPRecipientsRefused(refused)
        self.recorder.sent.append(record)
        return refused

    def sendmail(
        self,
        from_addr: str,
        to_addrs: Sequence[str] | str,
        msg: bytes | str,
        mail_options: Sequence[str] = (),
        rcpt_options: Sequence[str] = (),
    ) -> dict[str, tuple[int, bytes]]:
        raw = msg.encode("utf-8", "replace") if isinstance(msg, str) else bytes(msg)
        return self.send_message(
            email.message_from_bytes(raw),
            from_addr=from_addr,
            to_addrs=to_addrs,
            mail_options=mail_options,
            rcpt_options=rcpt_options,
        )

    def quit(self) -> tuple[int, bytes]:
        if self.closed:
            raise smtplib.SMTPServerDisconnected("quit issued twice")
        self._check("quit")
        self.recorder.ops.append("quit")
        self.closed = True
        self.recorder.quits += 1
        return 221, b"2.0.0 Bye"

    def close(self) -> None:
        self.closed = True

    def ehlo_or_helo_if_needed(self) -> None:
        return None


class FakeSMTPSSL(FakeSMTP):
    is_ssl = True


class FakeSMTPServer(_Faultable):
    """Records every connection and every message handed to SMTP."""

    def __init__(
        self,
        *,
        accounts: dict[str, str] | None = None,
        refuse_recipients: Iterable[str] = (),
        require_login: bool = True,
        require_tls: bool = True,
    ) -> None:
        super().__init__()
        self.accounts = accounts
        self.refuse_recipients = set(refuse_recipients)
        self.require_login = require_login
        self.require_tls = require_tls

        self.connections: list[tuple[str, int, float | None, bool]] = []
        self.logins: list[tuple[str, str]] = []
        self.sent: list[SentMail] = []
        self.refused: list[SentMail] = []
        self.starttls_calls = 0
        self.quits = 0
        self.ops: list[str] = []
        self.open_connections: list[FakeSMTP] = []

    @staticmethod
    def _default_error(op: str) -> BaseException:
        return FakeSMTP._default_error(op)

    def _open(
        self, cls: type[FakeSMTP], host: str, port: int, timeout: Any, kw: dict[str, Any]
    ) -> FakeSMTP:
        self.connections.append((host, port, timeout, cls.is_ssl))
        self._check("connect")
        self.ops.append("connect")
        conn = cls(self, host, port, timeout=timeout, **kw)
        self.open_connections.append(conn)
        return conn

    def connect_plain(
        self,
        host: str = "",
        port: int = 0,
        *_args: Any,
        timeout: float | None = None,
        **kw: Any,
    ) -> FakeSMTP:
        """Stands in for ``smtplib.SMTP(host, port, timeout=…)``."""
        return self._open(FakeSMTP, host, port, timeout, kw)

    def connect_ssl(
        self,
        host: str = "",
        port: int = 0,
        *_args: Any,
        timeout: float | None = None,
        **kw: Any,
    ) -> FakeSMTP:
        """Stands in for ``smtplib.SMTP_SSL(host, port, timeout=…)``."""
        return self._open(FakeSMTPSSL, host, port, timeout, kw)

    # -- convenience scripting --

    def fail_at_login(self, error: _ErrorSpec = None) -> None:
        self.fail("login", error)

    def fail_on_send(self, error: _ErrorSpec = None, *, after: int = 0) -> None:
        self.fail("send", error, after=after)

    def fail_on_starttls(self, error: _ErrorSpec = None) -> None:
        self.fail("starttls", error)

    # -- what a test wants to assert --

    @property
    def last_sent(self) -> SentMail | None:
        return self.sent[-1] if self.sent else None

    @property
    def leaked_connections(self) -> list[FakeSMTP]:
        return [c for c in self.open_connections if not c.closed]

    @property
    def recipients(self) -> list[tuple[str, ...]]:
        """Every envelope recipient list — who the message really went to."""
        return [s.to_addrs for s in self.sent]


# --- installation -----------------------------------------------------------


@dataclass
class FakeMailWorld:
    imap: FakeIMAPServer
    smtp: FakeSMTPServer

    @property
    def mailbox(self) -> FakeMailbox:
        return self.imap.mailbox


def install_fake_mail(
    monkeypatch: Any,
    *,
    mailbox: FakeMailbox | None = None,
    imap: FakeIMAPServer | None = None,
    smtp: FakeSMTPServer | None = None,
    accounts: dict[str, str] | None = None,
) -> FakeMailWorld:
    """Point ``imaplib``/``smtplib`` at the doubles for the rest of the test.

    Patching the library names rather than the provider's own helpers means a
    stray real connection is impossible while this is installed: there is no
    remaining code path to a socket.
    """
    imap_server = imap if imap is not None else FakeIMAPServer(mailbox, accounts=accounts)
    if imap is not None and mailbox is not None:
        imap_server.mailbox = mailbox
    smtp_server = smtp if smtp is not None else FakeSMTPServer(accounts=accounts)

    monkeypatch.setattr(imaplib, "IMAP4_SSL", imap_server.connect)
    monkeypatch.setattr(smtplib, "SMTP", smtp_server.connect_plain)
    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_server.connect_ssl)
    return FakeMailWorld(imap=imap_server, smtp=smtp_server)
