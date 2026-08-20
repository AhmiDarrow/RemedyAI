"""Mail over IMAP + SMTP with an app password — no cloud project required.

Why this exists: Google OAuth needs a registered Cloud project, and the
``gmail.modify`` scope is *restricted* — publishing it publicly requires a paid
annual security assessment, with unverified apps capped at 100 manually-added
test users. That is a real wall for a local-first product people just install.

An **app password** (Google/Microsoft/Fastmail all issue them once 2FA is on)
needs none of that: the owner pastes one credential, it lives in Remedy's
existing DPAPI-protected secret store, and it is revoked by deleting it at the
provider. Standard library only — ``imaplib`` / ``smtplib`` / ``email``.

Implements the same MailProvider surface as the Gmail adapter, including the
Phase 3 follow-through verbs (reply in thread, archive, mark read).
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import re
import smtplib
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from remedy.assistant.providers.base import MailMessage

logger = logging.getLogger(__name__)

# Known hosts so the owner only has to supply address + app password.
PRESETS: dict[str, dict[str, Any]] = {
    "gmail.com": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "archive_folder": "[Gmail]/All Mail",
        "drafts_folder": "[Gmail]/Drafts",
        "label": "Gmail",
        "app_password_url": "https://myaccount.google.com/apppasswords",
    },
    "googlemail.com": {"alias_of": "gmail.com"},
    "outlook.com": {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "archive_folder": "Archive",
        "drafts_folder": "Drafts",
        "label": "Outlook",
        "app_password_url": "https://account.microsoft.com/security",
    },
    "hotmail.com": {"alias_of": "outlook.com"},
    "live.com": {"alias_of": "outlook.com"},
    "yahoo.com": {
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "archive_folder": "Archive",
        "drafts_folder": "Draft",
        "label": "Yahoo",
        "app_password_url": "https://login.yahoo.com/account/security",
    },
    "fastmail.com": {
        "imap_host": "imap.fastmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.fastmail.com",
        "smtp_port": 465,
        "archive_folder": "Archive",
        "drafts_folder": "Drafts",
        "label": "Fastmail",
        "app_password_url": "https://app.fastmail.com/settings/security/apppasswords",
    },
    "icloud.com": {
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "archive_folder": "Archive",
        "drafts_folder": "Drafts",
        "label": "iCloud",
        "app_password_url": "https://appleid.apple.com/account/manage",
    },
    "me.com": {"alias_of": "icloud.com"},
}

SECRET_KEY = "mail_app_password"
ADDRESS_KEY = "mail_address"


def preset_for(address: str) -> dict[str, Any]:
    """Server settings for an address' domain ({} when unknown)."""
    dom = (address or "").split("@")[-1].strip().lower()
    row = PRESETS.get(dom) or {}
    alias = row.get("alias_of")
    if alias:
        row = PRESETS.get(alias) or {}
    return dict(row)


@dataclass
class MailAccount:
    address: str
    password: str
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    archive_folder: str = "Archive"
    drafts_folder: str = "Drafts"

    @classmethod
    def from_address(
        cls, address: str, password: str, **overrides: Any
    ) -> MailAccount:
        p = preset_for(address)
        acct = cls(
            address=address.strip(),
            password=password,
            imap_host=str(p.get("imap_host") or ""),
            imap_port=int(p.get("imap_port") or 993),
            smtp_host=str(p.get("smtp_host") or ""),
            smtp_port=int(p.get("smtp_port") or 587),
            archive_folder=str(p.get("archive_folder") or "Archive"),
            drafts_folder=str(p.get("drafts_folder") or "Drafts"),
        )
        for k, v in overrides.items():
            if v not in (None, "") and hasattr(acct, k):
                setattr(acct, k, v)
        return acct

    def is_ready(self) -> bool:
        return bool(self.address and self.password and self.imap_host and self.smtp_host)


def _decode(value: str) -> str:
    """MIME-decode a header ('=?utf-8?...' → real text). Never raises."""
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _friendly_error(exc: Exception, address: str) -> RuntimeError:
    """Turn provider gibberish into something the owner can act on."""
    msg = str(exc)
    low = msg.lower()
    p = preset_for(address)
    url = p.get("app_password_url") or ""
    if "authenticationfailed" in low.replace(" ", "") or "invalid credentials" in low:
        hint = (
            "That address/app-password was rejected. App passwords need "
            "2-step verification switched on, and the password is the 16-character "
            "one the provider generates — not your normal account password."
        )
        if url:
            hint += f" Generate one at {url}"
        return RuntimeError(hint)
    if "application-specific password required" in low:
        return RuntimeError(
            "This account requires an app password (not the normal password)."
            + (f" Generate one at {url}" if url else "")
        )
    if "imap access is disabled" in low or "imap is disabled" in low:
        return RuntimeError(
            "IMAP is turned off for this mailbox — enable IMAP in the mail "
            "provider's settings, then reconnect."
        )
    return RuntimeError(f"Mail error: {msg}")


class ImapSmtpMailProvider:
    """MailProvider over IMAP/SMTP. Connections are per-call (simple + safe)."""

    provider_id = "imap"

    def __init__(self, account: MailAccount) -> None:
        self.account = account

    # -- connections --------------------------------------------------------

    def _imap(self) -> imaplib.IMAP4_SSL:
        a = self.account
        conn = None
        try:
            conn = imaplib.IMAP4_SSL(a.imap_host, a.imap_port, timeout=30)
            conn.login(a.address, a.password)
            return conn
        except Exception as exc:
            # Close what we opened. A login failure used to let the socket
            # escape unclosed — held by the server until GC, once per retry of
            # a wrong app password.
            if conn is not None:
                with _Quiet():
                    conn.logout()
            raise _friendly_error(exc, a.address) from exc

    def _smtp(self) -> smtplib.SMTP | smtplib.SMTP_SSL:
        a = self.account
        srv_open: smtplib.SMTP | smtplib.SMTP_SSL | None = None
        try:
            if int(a.smtp_port) == 465:
                srv: smtplib.SMTP | smtplib.SMTP_SSL = smtplib.SMTP_SSL(
                    a.smtp_host, a.smtp_port, timeout=30
                )
                srv_open = srv
            else:
                srv = smtplib.SMTP(a.smtp_host, a.smtp_port, timeout=30)
                srv_open = srv
                srv.starttls()
            srv_open = srv
            srv.login(a.address, a.password)
            return srv
        except Exception as exc:
            # Same as _imap: do not leave the TLS socket open behind a refused
            # login.
            if srv_open is not None:
                with _Quiet():
                    srv_open.quit()
            raise _friendly_error(exc, a.address) from exc

    def verify(self) -> dict[str, Any]:
        """Prove both directions work — used by the connect flow."""
        conn = self._imap()
        try:
            # The connect flow's only check. It used to ignore this, so a
            # mailbox whose INBOX the server refused still reported
            # "IMAP + SMTP verified".
            typ, _ = conn.select("INBOX", readonly=True)
            if typ != "OK":
                raise RuntimeError(
                    "Signed in, but the server refused to open INBOX — "
                    "check the mailbox name and permissions."
                )
        finally:
            with _Quiet():
                conn.logout()
        srv = self._smtp()
        with _Quiet():
            srv.quit()
        return {
            "ok": True,
            "address": self.account.address,
            "imap_host": self.account.imap_host,
            "smtp_host": self.account.smtp_host,
            "message": f"Connected {self.account.address} (IMAP + SMTP verified)",
        }

    # -- read ---------------------------------------------------------------

    def list_messages(self, *, query: str = "", limit: int = 20) -> list[MailMessage]:
        """Recent messages. ``query`` accepts a raw IMAP search or simple text."""
        lim = max(1, min(int(limit or 20), 50))
        conn = self._imap()
        out: list[MailMessage] = []
        try:
            conn.select("INBOX", readonly=True)
            criteria = _imap_criteria(query)
            typ, data = conn.search(None, *criteria)
            if typ != "OK":
                return []
            ids = (data[0] or b"").split()
            for num in reversed(ids[-lim:]):  # newest first
                typ, msg_data = conn.fetch(
                    num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])"
                )
                if typ != "OK" or not msg_data:
                    continue
                raw = b""
                for part in msg_data:
                    if isinstance(part, tuple) and len(part) > 1:
                        raw = part[1]
                        break
                if not raw:
                    continue
                hdr = email.message_from_bytes(raw)
                out.append(
                    MailMessage(
                        id=num.decode("ascii", "ignore"),
                        subject=_decode(hdr.get("Subject", "")) or "(no subject)",
                        from_addr=_decode(hdr.get("From", "")),
                        snippet="",
                        date=hdr.get("Date", ""),
                        thread_id=hdr.get("Message-ID", ""),
                        raw={"uid": num.decode("ascii", "ignore")},
                    )
                )
        finally:
            with _Quiet():
                conn.logout()
        return out

    def get_message(self, message_id: str) -> MailMessage:
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id required")
        conn = self._imap()
        try:
            conn.select("INBOX", readonly=True)
            typ, msg_data = conn.fetch(mid.encode("ascii"), "(RFC822)")
            # imaplib answers ('OK', [None]) for a message set the server
            # ignored, so `not msg_data` was False and the owner got a blank
            # message — "(no subject)" with an empty body — instead of being
            # told the message is not there.
            if (
                typ != "OK"
                or not msg_data
                or not any(
                    isinstance(part, tuple) and len(part) > 1 for part in msg_data
                )
            ):
                raise RuntimeError(f"Message {mid} not found")
            raw = b""
            for part in msg_data:
                if isinstance(part, tuple) and len(part) > 1:
                    raw = part[1]
                    break
            msg = email.message_from_bytes(raw)
            return MailMessage(
                id=mid,
                subject=_decode(msg.get("Subject", "")) or "(no subject)",
                from_addr=_decode(msg.get("From", "")),
                snippet=_body_text(msg)[:4000],
                date=msg.get("Date", ""),
                thread_id=msg.get("Message-ID", ""),
                raw={
                    "message_id_header": msg.get("Message-ID", ""),
                    "references": msg.get("References", ""),
                    "reply_to": _decode(msg.get("Reply-To", "")),
                    "cc": _decode(msg.get("Cc", "")),
                },
            )
        finally:
            with _Quiet():
                conn.logout()

    # -- write --------------------------------------------------------------

    def _send(self, msg: MIMEText, to_addrs: list[str]) -> None:
        srv = self._smtp()
        try:
            srv.send_message(msg, from_addr=self.account.address, to_addrs=to_addrs)
        finally:
            with _Quiet():
                srv.quit()

    def send_message(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        msg = MIMEText(body or "", "plain", "utf-8")
        msg["From"] = self.account.address
        msg["To"] = to
        msg["Subject"] = subject or ""
        msg["Message-ID"] = email.utils.make_msgid()
        msg["Date"] = email.utils.formatdate(localtime=True)
        self._send(msg, _addr_list(to))
        return {
            "ok": True,
            "message_id": msg["Message-ID"],
            "thread_id": "",
            "to": to,
            "subject": subject,
            "message": f"Sent to {to}: {subject or '(no subject)'}",
        }

    def reply_to_message(
        self, message_id: str, *, body: str, reply_all: bool = False
    ) -> dict[str, Any]:
        """Reply IN THREAD — In-Reply-To/References from the original."""
        original = self.get_message(message_id)
        raw = original.raw or {}
        orig_id = str(raw.get("message_id_header") or "")
        to_addr = str(raw.get("reply_to") or "") or original.from_addr
        if not to_addr:
            raise RuntimeError("Could not determine a reply address for that message.")
        subject = original.subject or ""
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        references = " ".join(
            x for x in (str(raw.get("references") or ""), orig_id) if x
        ).strip()
        msg = MIMEText(body or "", "plain", "utf-8")
        msg["From"] = self.account.address
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg["Message-ID"] = email.utils.make_msgid()
        msg["Date"] = email.utils.formatdate(localtime=True)
        if orig_id:
            msg["In-Reply-To"] = orig_id
            msg["References"] = references or orig_id
        recipients = _addr_list(to_addr)
        cc = str(raw.get("cc") or "")
        if reply_all and cc:
            msg["Cc"] = cc
            recipients += _addr_list(cc)
        self._send(msg, recipients)
        return {
            "ok": True,
            "message_id": msg["Message-ID"],
            "thread_id": orig_id,
            "to": to_addr,
            "subject": subject,
            "in_reply_to": orig_id,
            "message": f"Replied in thread to {to_addr}: {subject or '(no subject)'}",
        }

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        msg = MIMEText(body or "", "plain", "utf-8")
        msg["From"] = self.account.address
        msg["To"] = to
        msg["Subject"] = subject or ""
        msg["Date"] = email.utils.formatdate(localtime=True)
        conn = self._imap()
        try:
            folder = self.account.drafts_folder
            # A mailbox whose Drafts folder is named differently (Gmail's
            # "[Gmail]/Drafts", any localised name) answers NO [TRYCREATE].
            # Ignoring that told the owner the draft was saved when no draft
            # existed anywhere.
            typ, _resp = conn.append(
                f'"{folder}"', "\\Draft", imaplib.Time2Internaldate(_now()), msg.as_bytes()
            )
            if typ != "OK":
                raise RuntimeError(
                    f"Could not save the draft to {folder!r} — "
                    "check the drafts folder name for this mailbox."
                )
        finally:
            with _Quiet():
                conn.logout()
        return {
            "ok": True,
            "draft_id": "",
            "message_id": "",
            "to": to,
            "subject": subject,
            "message": f"Draft saved to {self.account.drafts_folder} for {to}",
        }

    # -- follow-through -----------------------------------------------------

    def mark_read(self, message_id: str, *, read: bool = True) -> dict[str, Any]:
        conn = self._imap()
        try:
            conn.select("INBOX")
            op = "+FLAGS" if read else "-FLAGS"
            typ, resp = conn.store(message_id.encode("ascii"), op, "\\Seen")
            # STORE against a message set the server ignored is an OK no-op
            # with an empty response, so trusting the return code alone told
            # the owner a message was marked when none was.
            if typ != "OK" or not any(resp or []):
                raise RuntimeError(f"Message {message_id} not found")
        finally:
            with _Quiet():
                conn.logout()
        return {
            "ok": True,
            "message_id": message_id,
            "message": "Marked read" if read else "Marked unread",
        }

    def archive_message(self, message_id: str) -> dict[str, Any]:
        """Move out of the inbox into the archive folder."""
        conn = self._imap()
        try:
            conn.select("INBOX")
            mid = message_id.encode("ascii")
            folder = f'"{self.account.archive_folder}"'
            # Copy into the archive, then flag+expunge from INBOX. (Plain COPY
            # works on every IMAP server; UID MOVE is not universal.)
            typ, _resp = conn.copy(mid, folder)
            if typ != "OK":
                raise RuntimeError(
                    f"Could not copy to {self.account.archive_folder!r} — "
                    "check the archive folder name for this mailbox."
                )
            conn.store(mid, "+FLAGS", "\\Deleted")
            with _Quiet():
                conn.expunge()
        finally:
            with _Quiet():
                conn.logout()
        return {
            "ok": True,
            "message_id": message_id,
            "message": f"Archived to {self.account.archive_folder}",
        }


def _now():
    import time as _t

    return _t.time()


class _Quiet:
    """Swallow teardown errors — a failed logout must not mask a good result."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: Any) -> bool:
        return True


def _addr_list(value: str) -> list[str]:
    return [a for _n, a in email.utils.getaddresses([value or ""]) if a]


def _imap_quote(value: str) -> str:
    """IMAP search atoms with spaces/specials must be quoted strings."""
    v = str(value or "")
    if re.fullmatch(r"[A-Za-z0-9_.@-]+", v):
        return v
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _imap_criteria(query: str) -> tuple[str, ...]:
    """Map a friendly query to IMAP search criteria."""
    q = (query or "").strip()
    if not q or q.lower() in ("in:inbox", "inbox", "all"):
        return ("ALL",)
    if q.lower() in ("unread", "is:unread", "unseen"):
        return ("UNSEEN",)
    m = re.match(r"(?i)^from:\s*(.+)$", q)
    if m:
        return ("FROM", _imap_quote(m.group(1).strip()))
    m = re.match(r"(?i)^subject:\s*(.+)$", q)
    if m:
        return ("SUBJECT", _imap_quote(m.group(1).strip()))
    # Raw IMAP criteria pass through (e.g. 'SINCE 1-Aug-2026')
    if q.upper().split()[0] in {
        "ALL", "UNSEEN", "SEEN", "FROM", "TO", "SUBJECT", "BODY", "SINCE", "BEFORE",
    }:
        return tuple(q.split())
    return ("TEXT", _imap_quote(q))


def _body_text(msg: Any) -> str:
    """Plain-text body, preferring text/plain over stripped HTML."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                with _Quiet():
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace"
                    )
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                with _Quiet():
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace"
                    )
                    return re.sub(r"<[^>]+>", " ", html)
        return ""
    with _Quiet():
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", "replace"
        )
    return ""


# --- credential storage -----------------------------------------------------


def save_mail_credentials(
    address: str, app_password: str, home: Path | str | None = None
) -> dict[str, Any]:
    """Store address + app password in the DPAPI-protected secret store."""
    from remedy.interfaces.secret_store import set_provider_secret

    addr = (address or "").strip()
    pwd = (app_password or "").replace(" ", "")  # providers show it in groups of 4
    if not addr or "@" not in addr:
        return {"ok": False, "message": "A full email address is required."}
    if not pwd:
        return {"ok": False, "message": "App password is required."}
    p = preset_for(addr)
    if not p:
        return {
            "ok": False,
            "message": (
                f"I don't know the mail servers for {addr.split('@')[-1]}. "
                "Supported without setup: Gmail, Outlook/Hotmail, Yahoo, "
                "Fastmail, iCloud."
            ),
        }
    set_provider_secret(ADDRESS_KEY, addr, home=home)
    set_provider_secret(SECRET_KEY, pwd, home=home)
    caps = _record_linked_account(addr, p, home=home)
    return {
        "ok": True,
        "address": addr,
        "provider": p.get("label") or "mail",
        "capabilities": caps,
        "message": f"Saved credentials for {addr}",
    }


def _provider_id(address: str) -> str:
    """Which linked-account provider a mailbox belongs to."""
    dom = (address or "").split("@")[-1].strip().lower()
    row = PRESETS.get(dom) or {}
    canonical = str(row.get("alias_of") or dom)
    return {
        "gmail.com": "google",
        "outlook.com": "microsoft",
        "yahoo.com": "yahoo",
        "fastmail.com": "fastmail",
        "icloud.com": "icloud",
    }.get(canonical, canonical or "mail")


def _record_linked_account(
    address: str, preset: dict[str, Any], *, home: Path | str | None = None
) -> list[str]:
    """Put the mailbox on the linked-accounts list.

    The Google OAuth flow has always done this; the app-password flow stored the
    credential and stopped, so a mailbox could be fully working while Settings
    and ``assistant_accounts`` both showed nothing linked at all.

    Returns the capabilities recorded, so the caller can say what it got.
    """
    import time

    from remedy.assistant.models import LinkedAccount
    from remedy.assistant.providers.caldav import caldav_url_for
    from remedy.assistant.store import get_assistant_store

    caps = ["mail"]
    if caldav_url_for(address):
        caps.append("calendar")
    try:
        get_assistant_store(home).upsert_account(
            LinkedAccount(
                id=f"imap_{_provider_id(address)}",
                provider=_provider_id(address),
                email=address,
                capabilities=caps,
                status="connected",
                last_sync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        )
    except Exception as exc:  # noqa: BLE001 — the credential is already saved
        logger.warning("could not record the linked mail account: %s", exc)
    return caps


def clear_mail_credentials(home: Path | str | None = None) -> dict[str, Any]:
    """Disconnect the mailbox: forget the credential and the linked account.

    There was no way to undo ``save_mail_credentials`` at all — connecting was a
    one-way door, and the only way back out was deleting keys by hand.
    """
    from remedy.interfaces.secret_store import clear_provider_secret, get_provider_secret

    addr = (get_provider_secret(ADDRESS_KEY, home=home) or "").strip()
    if not addr:
        return {"ok": True, "message": "No mailbox was connected.", "address": ""}
    clear_provider_secret(SECRET_KEY, home=home)
    clear_provider_secret(ADDRESS_KEY, home=home)
    try:
        from remedy.assistant.store import get_assistant_store

        get_assistant_store(home).remove_account(f"imap_{_provider_id(addr)}")
    except Exception as exc:  # noqa: BLE001 — the credential is already gone
        logger.warning("could not clear the linked mail account: %s", exc)
    return {
        "ok": True,
        "address": addr,
        "message": f"Disconnected {addr}. The app password has been forgotten.",
    }


def load_mail_account(home: Path | str | None = None) -> MailAccount | None:
    from remedy.interfaces.secret_store import get_provider_secret

    addr = (get_provider_secret(ADDRESS_KEY, home=home) or "").strip()
    pwd = (get_provider_secret(SECRET_KEY, home=home) or "").strip()
    if not addr or not pwd:
        return None
    acct = MailAccount.from_address(addr, pwd)
    return acct if acct.is_ready() else None


def get_imap_mail(home: Path | str | None = None) -> ImapSmtpMailProvider | None:
    acct = load_mail_account(home)
    return ImapSmtpMailProvider(acct) if acct is not None else None
