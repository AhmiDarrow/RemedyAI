"""Gmail provider unit tests (mocked HTTP)."""

from __future__ import annotations

import json
from unittest.mock import patch

from remedy.assistant import google_oauth as go
from remedy.assistant.providers.google_gmail import GoogleGmailProvider, _extract_body


def test_extract_body_plain():
    import base64

    raw = base64.urlsafe_b64encode(b"Hello world").decode("ascii").rstrip("=")
    text = _extract_body({"mimeType": "text/plain", "body": {"data": raw}})
    assert "Hello world" in text


def test_list_messages(tmp_path):
    go.save_tokens(
        go.GoogleTokens(
            access_token="tok",
            refresh_token="rt",
            expires_at=9e12,
            email="u@g.com",
        ),
        home=tmp_path,
    )
    mail = GoogleGmailProvider(home=tmp_path)
    calls: list[str] = []

    class FakeResp:
        def __init__(self, payload: dict):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self._payload).encode()

    def fake_urlopen(req, timeout=30):
        url = req.full_url if hasattr(req, "full_url") else str(req.get_full_url())
        calls.append(url)
        assert "Bearer tok" in req.headers.get("Authorization", "")
        if "/messages?" in url or url.rstrip("/").endswith("/messages"):
            return FakeResp({"messages": [{"id": "m1", "threadId": "t1"}]})
        return FakeResp(
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "Hi there",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Hello"},
                        {"name": "From", "value": "a@b.com"},
                        {"name": "Date", "value": "Mon, 1 Jan 2026"},
                    ]
                },
            }
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        msgs = mail.list_messages(query="in:inbox", limit=5)
    assert len(msgs) == 1
    assert msgs[0].subject == "Hello"
    assert msgs[0].from_addr == "a@b.com"
