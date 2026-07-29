"""Gmail API adapter (list / get / create draft)."""

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

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        msg = MIMEText(body or "", "plain", "utf-8")
        msg["To"] = to
        msg["Subject"] = subject or ""
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
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
