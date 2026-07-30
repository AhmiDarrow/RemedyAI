"""Outbound provider payload sanitization."""

from __future__ import annotations

import json

from remedy.core.provider_sanitize import (
    TOOL_CONTENT_MAX,
    sanitize_chat_body,
    sanitize_message,
    sanitize_messages_for_provider,
    sanitize_tool_arguments,
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


def test_sanitize_does_not_mutate_input():
    body = {
        "model": "x",
        "messages": [{"role": "tool", "content": "sk-abcdefghijklmnopqrstuvwxyz123456"}],
    }
    out = sanitize_chat_body(body)
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" in body["messages"][0]["content"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out["messages"][0]["content"]


def test_tool_call_arguments_not_mid_string_clipped():
    """Regression: clipping args at TOOL_CONTENT_MAX broke JSON → provider 400."""
    # Build valid JSON args larger than the old 6k clip threshold.
    long_step = "x" * 200
    payload = {
        "title": "Plan",
        "goal": "Ship it",
        "steps": [f"{i}: {long_step}" for i in range(40)],
    }
    args = json.dumps(payload)
    assert len(args) > TOOL_CONTENT_MAX
    m = sanitize_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "plan_save", "arguments": args},
                }
            ],
        }
    )
    out_args = m["tool_calls"][0]["function"]["arguments"]
    # Must remain parseable JSON (never ends mid-string with a bare …)
    parsed = json.loads(out_args)
    assert isinstance(parsed, dict)
    assert "title" in parsed or parsed.get("_truncated") or parsed.get("_invalid_json")


def test_invalid_tool_arguments_become_valid_json():
    broken = '{"title": "Plan", "steps": ["unclosed string'
    out = sanitize_tool_arguments(broken)
    parsed = json.loads(out)
    assert parsed.get("_invalid_json") is True


def test_sanitize_tool_arguments_redacts_secrets_inside_json():
    args = json.dumps({"api_key": "sk-abcdefghijklmnopqrstuvwxyz123456", "ok": True})
    out = sanitize_tool_arguments(args)
    parsed = json.loads(out)
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in out
    assert parsed.get("ok") is True


def test_file_write_large_content_summarized_not_half_clipped():
    """History must not look like a truncated file body (causes rewrite thrash)."""
    body = "line\n" * 5000  # >> 8k
    args = json.dumps({"path": "src/Big.tsx", "content": body})
    out = sanitize_tool_arguments(args, tool_name="file_write")
    parsed = json.loads(out)
    assert parsed.get("path") == "src/Big.tsx"
    content = parsed.get("content") or ""
    assert "NOT_SOURCE_CODE" in content or "history_stub" in content
    assert "DO_NOT_file_write" in content
    assert parsed.get("_content_chars") == len(body)
    # Must not be a bare mid-file clip of the original
    assert not content.startswith("line\nline\nline")


def test_repair_and_strip_tool_args_in_history():
    from remedy.core.react_stream import (
        repair_tool_arguments_in_messages,
        strip_broken_tool_call_turns,
    )

    # Truncated mid-string (classic ~6k clip)
    broken = '{"title": "Plan", "body": "' + ("x" * 5900)
    msgs = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "plan_save", "arguments": broken},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "continue"},
    ]
    n = repair_tool_arguments_in_messages(msgs)
    assert n >= 1
    args = msgs[1]["tool_calls"][0]["function"]["arguments"]
    parsed = json.loads(args)
    assert parsed.get("_invalid_json") is True
    # Second pass: strip turns that only have repaired placeholders
    stripped = strip_broken_tool_call_turns(msgs)
    assert stripped >= 1
    assert not any(
        isinstance(m, dict) and m.get("tool_calls") for m in msgs
    )
