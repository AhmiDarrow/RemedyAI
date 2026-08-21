"""Model replays of history-sanitized file_write args must not spin HISTORY_STUB.

Live session (grok-4.5 via xAI, 2026-08-20): the model copied the
history-sanitized form of its own earlier ``file_write`` (``content: ""``,
``_history_summarized``, ``_content_chars``…) back as a fresh tool call.
The execute guard refused it seven times in a row even though the real
file was already on disk.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

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
    """Hypothesis (b): the model copies private keys it sees in history.

    Ship only path + empty content + a human note upstream.
    """
    out = _rewrite_write_tool_args({"path": "src/HomePage.tsx", "content": BODY}, "file_write")
    assert out["content"] == ""
    assert "omitted" in str(out.get("history_note") or "").lower()
    assert not [k for k in out if str(k).startswith("_")], out.keys()


class _RT:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list = []

    def resolve_tool_path(self, path, for_write=False):  # noqa: ANN001
        return self.root / Path(path).name

    async def call_tool(self, tc):  # noqa: ANN001
        self.calls.append(tc)
        from remedy.models import ToolResult

        target = self.resolve_tool_path(tc.arguments["path"])
        target.write_text(tc.arguments["content"], encoding="utf-8")
        return ToolResult(success=True, data=f"wrote {target}")


async def _run(rt, args: dict) -> str:
    from remedy.core.agent_tool_batch import execute_tool_calls

    tcs = [
        {
            "id": "call_replay",
            "type": "function",
            "function": {"name": "file_write", "arguments": json.dumps(args)},
        }
    ]
    outs = []
    async for _ev, msg in execute_tool_calls(rt, tcs, seen_fps=set(), result_cache={}):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            outs.append(msg)
    assert len(outs) == 1
    return outs[0]["content"]


@pytest.mark.asyncio
async def test_replayed_history_form_soft_succeeds_when_file_on_disk(tmp_path: Path):
    rt = _RT(tmp_path)
    (tmp_path / "HomePage.tsx").write_text(BODY, encoding="utf-8")

    # Exactly what the live DB stored (old sanitized form, private keys included).
    replayed = {
        "path": "src/HomePage.tsx",
        "content": "",
        "_content_chars": len(BODY),
        "_history_summarized": True,
        "_body_omitted": True,
        "history_note": (
            f"file body omitted from chat history ({len(BODY)} chars already on disk). "
            "Do NOT re-file_write a history stub."
        ),
    }
    body = await _run(rt, replayed)
    assert "HISTORY_STUB" not in body, body
    assert body.startswith("OK:")
    assert rt.calls == []  # nothing rewritten
    assert (tmp_path / "HomePage.tsx").read_text(encoding="utf-8") == BODY


@pytest.mark.asyncio
async def test_replayed_new_history_form_soft_succeeds(tmp_path: Path):
    rt = _RT(tmp_path)
    (tmp_path / "HomePage.tsx").write_text(BODY, encoding="utf-8")
    replayed = _rewrite_write_tool_args({"path": "src/HomePage.tsx", "content": BODY}, "file_write")
    body = await _run(rt, replayed)
    assert "HISTORY_STUB" not in body, body
    assert body.startswith("OK:")
    assert rt.calls == []


@pytest.mark.asyncio
async def test_replayed_history_form_still_refused_when_file_missing(tmp_path: Path):
    rt = _RT(tmp_path)
    replayed = {
        "path": "src/HomePage.tsx",
        "content": "",
        "_history_summarized": True,
        "_body_omitted": True,
        "history_note": "file body omitted from chat history (99 chars already on disk).",
    }
    body = await _run(rt, replayed)
    assert "HISTORY_STUB" in body
    assert rt.calls == []
    assert not (tmp_path / "HomePage.tsx").exists()


@pytest.mark.asyncio
async def test_real_content_with_stray_private_flags_is_written(tmp_path: Path):
    """Model sometimes copies the flags but supplies real source — write it."""
    rt = _RT(tmp_path)
    args = {
        "path": "src/HomePage.tsx",
        "content": BODY,
        "_history_summarized": True,
        "_content_chars": len(BODY),
    }
    body = await _run(rt, args)
    assert "HISTORY_STUB" not in body, body
    assert len(rt.calls) == 1
    assert "_history_summarized" not in rt.calls[0].arguments
    assert (tmp_path / "HomePage.tsx").read_text(encoding="utf-8") == BODY
