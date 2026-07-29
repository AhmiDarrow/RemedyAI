"""Outbound provider payload sanitization."""

from __future__ import annotations

from remedy.core.provider_sanitize import (
    sanitize_chat_body,
    sanitize_message,
    sanitize_messages_for_provider,
)


def test_redacts_secret_like_strings():
    m = sanitize_message(
        {
            "role": "tool",
            "content": 'token sk-abcdefghijklmnopqrstuvwxyz123456 and ok text',
        }
    )
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in m["content"]
    assert "[redacted]" in m["content"]
    assert "ok text" in m["content"]


def test_redacts_secret_keys_in_jsonish_tool():
    m = sanitize_message(
        {
            "role": "tool",
            "content": '{"access_token": "ya29.secretvalue", "subject": "Hi"}',
        }
    )
    # value-pattern redaction
    assert "ya29.secretvalue" not in m["content"] or "access_token" in m["content"]


def test_tool_content_capped():
    huge = "x" * 50_000
    m = sanitize_message({"role": "tool", "content": huge})
    assert len(m["content"]) < 10_000
    assert m["content"].endswith("…")


def test_sanitize_body_messages():
    body = {
        "model": "grok",
        "messages": [
            {"role": "user", "content": "hello"},
            {
                "role": "tool",
                "content": "Bearer sk-abcdefghijklmnopqrstuvwxyz99999 leaked",
            },
        ],
    }
    out = sanitize_chat_body(body)
    assert body["messages"][1]["content"].startswith("Bearer sk-")  # input untouched
    assert "sk-abcdefghijklmnopqrstuvwxyz99999" not in out["messages"][1]["content"]
    assert out["model"] == "grok"


def test_messages_list():
    msgs = sanitize_messages_for_provider(
        [{"role": "assistant", "content": "fine"}, {"role": "tool", "content": "ok"}]
    )
    assert len(msgs) == 2
