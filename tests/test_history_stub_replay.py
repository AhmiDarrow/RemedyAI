"""History-sanitized file_write args must not be model-copyable stubs.

Execute-path soft-skip/refuse now lives on file_write / RMDY workspace.write
(Go owns tool batching). This module keeps the sanitizer contract tests.
"""

from __future__ import annotations

import copy
import json

from remedy.core.provider_sanitize import (
    FILE_WRITE_CONTENT_HISTORY_MAX,
    _rewrite_write_tool_args,
    sanitize_message,
)

BODY = "export default function HomePage() {\n  return <div>hi</div>;\n}\n" * 80
assert len(BODY) > FILE_WRITE_CONTENT_HISTORY_MAX


def _assistant_turn(args: object) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "file_write", "arguments": args},
            }
        ],
    }


def test_sanitize_message_never_mutates_live_dict_args():
    """Hypothesis (a): sanitizer shares/mutates the live args. It must not."""
    live = {"path": "src/HomePage.tsx", "content": BODY}
    msg = _assistant_turn(live)
    snapshot = copy.deepcopy(msg)
    out = sanitize_message(msg)
    assert msg == snapshot
    assert live["content"] == BODY
    assert "_history_summarized" not in live
    sanitized = json.loads(out["tool_calls"][0]["function"]["arguments"])
    assert sanitized["content"] == ""


def test_history_form_has_no_model_copyable_private_keys():
    """Hypothesis (b): the model copies private keys it sees in history."""
    out = _rewrite_write_tool_args({"path": "src/HomePage.tsx", "content": BODY}, "file_write")
    assert out["content"] == ""
    assert "omitted" in str(out.get("history_note") or "").lower()
    assert not [k for k in out if str(k).startswith("_")], out.keys()
