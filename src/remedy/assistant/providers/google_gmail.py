"""Gmail API adapter (list / get / create draft / send)."""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from remedy.assistant.providers.base import MailMessage, MailProvider

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


class GoogleGmailProvider:
    """MailProvider backed by Gmail API v1."""

    provider_id = "google"

    def __init__(self, home: Path | str | None = None) -> None:
        self.home = home

    def _bearer(self) -> str:
        from remedy.assistant.google_oauth import get_valid_access_token

        return get_valid_access_token(self.home)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{GMAIL_API}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {
            "Authorization": f"Bearer {self._bearer()}",
            "Accept": "application/json",
            "User-Agent": "RemedyDesktop-Gmail/1.0",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(err)
                msg = parsed.get("error", {}).get("message") or err
            except json.JSONDecodeError:
                msg = err or str(e)
            low = str(msg).lower()
            if e.code == 403 and (
                "has not been used" in low or "is disabled" in low
            ):
                raise RuntimeError(
                    "Gmail API is disabled for the Google Cloud project behind this "
                    "OAuth client. Enable **Gmail API** in Google Cloud Console "
                    "(APIs & Services → Library → Gmail API → Enable), wait ~1 minute, "
                    f"then retry. Detail: {msg}"
                ) from e
            if e.code in (401, 403) and (
                "insufficient" in low
                or "scope" in low
                or "permission" in low
                or "request had insufficient authentication" in low
            ):
                raise RuntimeError(
                    "Google hasn't granted this permission yet. Reconnect in "
                    "Settings → Personal assistant → Google (Gmail) → Connect, and "
                    "accept the mail-management permission (archive / mark read). "
                    f"Detail: {msg}"
                ) from e
            raise RuntimeError(f"Gmail API {e.code}: {msg}") from e

    def list_messages(self, *, query: str = "", limit: int = 20) -> list[MailMessage]:
        lim = max(1, min(int(limit or 20), 50))
        q: dict[str, str] = {"maxResults": str(lim)}
        if (query or "").strip():
            q["q"] = query.strip()
        listing = self._request("GET", "/users/me/messages", query=q)
        out: list[MailMessage] = []
        for row in listing.get("messages") or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            mid = str(row["id"])
            try:
                meta = self._request(
                    "GET",
                    f"/users/me/messages/{mid}",
                    query={
                        "format": "metadata",
                        "metadataHeaders": "From,Subject,Date",
                    },
                )
            except Exception as exc:
                logger.debug("gmail meta %s: %s", mid, exc)
                out.append(MailMessage(id=mid, subject="(unavailable)", snippet=""))
                continue
            headers = {
                str(h.get("name") or "").lower(): str(h.get("value") or "")
                for h in (meta.get("payload") or {}).get("headers") or []
                if isinstance(h, dict)
            }
            out.append(
                MailMessage(
                    id=mid,
                    subject=headers.get("subject") or "(no subject)",
                    from_addr=headers.get("from") or "",
                    snippet=str(meta.get("snippet") or ""),
                    date=headers.get("date") or "",
                    thread_id=str(meta.get("threadId") or row.get("threadId") or ""),
                    raw=meta,
                )
            )
        return out

    def get_message(self, message_id: str) -> MailMessage:
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id required")
        data = self._request(
            "GET",
            f"/users/me/messages/{mid}",
            query={"format": "full"},
        )
        headers = {
            str(h.get("name") or "").lower(): str(h.get("value") or "")
            for h in (data.get("payload") or {}).get("headers") or []
            if isinstance(h, dict)
        }
        body = _extract_body(data.get("payload") or {})
        return MailMessage(
            id=str(data.get("id") or mid),
            subject=headers.get("subject") or "(no subject)",
            from_addr=headers.get("from") or "",
            snippet=(body or str(data.get("snippet") or ""))[:4000],
            date=headers.get("date") or "",
            thread_id=str(data.get("threadId") or ""),
            raw=data,
        )

    def _raw_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str = "",
        references: str = "",
        cc: str = "",
    ) -> str:
        msg = MIMEText(body or "", "plain", "utf-8")
        msg["To"] = to
        msg["Subject"] = subject or ""
        if cc:
            msg["Cc"] = cc
        # Threading headers — without these a "reply" starts a NEW conversation
        # in the recipient's client even if Gmail groups it server-side.
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    def _headers_of(self, data: dict[str, Any]) -> dict[str, str]:
        return {
            str(h.get("name") or "").lower(): str(h.get("value") or "")
            for h in (data.get("payload") or {}).get("headers") or []
            if isinstance(h, dict)
        }

    def reply_to_message(
        self,
        message_id: str,
        *,
        body: str,
        reply_all: bool = False,
    ) -> dict[str, Any]:
        """Reply IN THREAD to an existing message.

        Pulls the original's Message-ID / From / Subject so the reply threads
        properly in every mail client, and posts it with the same threadId.
        """
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id required")
        original = self._request(
            "GET",
            f"/users/me/messages/{mid}",
            query={"format": "metadata"},
        )
        headers = self._headers_of(original)
        thread_id = str(original.get("threadId") or "")
        orig_msg_id = headers.get("message-id") or ""
        # Reply goes to Reply-To when present, else the sender.
        to_addr = headers.get("reply-to") or headers.get("from") or ""
        if not to_addr:
            raise RuntimeError("Could not determine a reply address for that message.")
        cc = headers.get("cc", "") if reply_all else ""
        subject = headers.get("subject") or ""
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        references = headers.get("references") or ""
        if orig_msg_id:
            references = f"{references} {orig_msg_id}".strip()
        raw = self._raw_message(
            to=to_addr,
            subject=subject,
            body=body,
            in_reply_to=orig_msg_id,
            references=references,
            cc=cc,
        )
        data = self._request(
            "POST",
            "/users/me/messages/send",
            body={"raw": raw, "threadId": thread_id} if thread_id else {"raw": raw},
        )
        return {
            "ok": True,
            "message_id": str(data.get("id") or ""),
            "thread_id": str(data.get("threadId") or thread_id),
            "to": to_addr,
            "subject": subject,
            "in_reply_to": orig_msg_id,
            "message": f"Replied in thread to {to_addr}: {subject or '(no subject)'}",
        }

    def modify_labels(
        self,
        message_id: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add/remove Gmail labels (INBOX, UNREAD, STARRED, or label ids)."""
        mid = (message_id or "").strip()
        if not mid:
            raise ValueError("message_id required")
        body: dict[str, Any] = {}
        if add:
            body["addLabelIds"] = [str(x) for x in add]
        if remove:
            body["removeLabelIds"] = [str(x) for x in remove]
        if not body:
            raise RuntimeError("Nothing to change — pass add= or remove=.")
        data = self._request("POST", f"/users/me/messages/{mid}/modify", body=body)
        return {
            "ok": True,
            "message_id": str(data.get("id") or mid),
            "labels": [str(x) for x in (data.get("labelIds") or [])],
            "message": "Labels updated",
        }

    def archive_message(self, message_id: str) -> dict[str, Any]:
        """Archive = drop it out of the inbox (Gmail keeps it searchable)."""
        out = self.modify_labels(message_id, remove=["INBOX"])
        out["message"] = "Archived (out of inbox, still searchable)"
        return out

    def mark_read(self, message_id: str, *, read: bool = True) -> dict[str, Any]:
        out = (
            self.modify_labels(message_id, remove=["UNREAD"])
            if read
            else self.modify_labels(message_id, add=["UNREAD"])
        )
        out["message"] = "Marked read" if read else "Marked unread"
        return out

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        raw = self._raw_message(to=to, subject=subject, body=body)
        data = self._request(
            "POST",
            "/users/me/drafts",
            body={"message": {"raw": raw}},
        )
        mid = str((data.get("message") or {}).get("id") or data.get("id") or "")
        return {
            "ok": True,
            "draft_id": str(data.get("id") or ""),
            "message_id": mid,
            "to": to,
            "subject": subject,
            "message": f"Draft created to {to}: {subject or '(no subject)'}",
        }

    def send_message(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send mail immediately (gmail.compose / gmail.send scope)."""
        raw = self._raw_message(to=to, subject=subject, body=body)
        data = self._request(
            "POST",
            "/users/me/messages/send",
            body={"raw": raw},
        )
        mid = str(data.get("id") or "")
        return {
            "ok": True,
            "message_id": mid,
            "thread_id": str(data.get("threadId") or ""),
            "to": to,
            "subject": subject,
            "message": f"Sent to {to}: {subject or '(no subject)'}",
        }


def _extract_body(payload: dict[str, Any]) -> str:
    """Best-effort plain text from Gmail message payload."""
    if not isinstance(payload, dict):
        return ""
    mime = str(payload.get("mimeType") or "")
    body = payload.get("body") or {}
    data = body.get("data") if isinstance(body, dict) else None
    if data and "text/plain" in mime:
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            pass
    for part in payload.get("parts") or []:
        if not isinstance(part, dict):
            continue
        text = _extract_body(part)
        if text:
            return text
    if data:
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def get_google_gmail(home: Path | str | None = None) -> MailProvider | None:
    from remedy.assistant.google_oauth import load_tokens

    if not load_tokens(home).connected:
        return None
    return GoogleGmailProvider(home=home)
