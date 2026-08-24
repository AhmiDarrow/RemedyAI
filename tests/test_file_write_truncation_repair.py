"""file_write must not infinite-loop on truncated tool JSON (HISTORY_STUB)."""

from __future__ import annotations

import json

from remedy.core.local_agent_optimize import local_completion_cap
from remedy.core.provider_sanitize import coerce_tool_arguments_json


def test_local_completion_cap_allows_real_file_write():
    # 32k window + write tools must be well above 1024 (old bug)
    cap = local_completion_cap(32768, tools_present=True, force_tools=True)
    assert cap >= 4096
    # write-aware: ~window//3
    assert cap >= 10000


def test_write_tools_cap_even_without_force():
    from remedy.core.local_agent_optimize import local_completion_cap as cap_fn

    c = cap_fn(32768, tools_present=True, force_tools=False, write_tools=True)
    assert c >= 4096


def test_unclosed_file_write_is_not_executed_as_a_stump():
    """Mid-stream cut (e.g. ``int…``) must not land a half-file on disk."""
    raw = (
        '{"path": "src/main.py", "content": "def main():\\n'
        '    x: int'
    )
    out = coerce_tool_arguments_json(raw, tool_name="file_write")
    data = json.loads(out)
    assert data.get("_stream_truncated") is True
    assert data.get("_invalid_json") is True
    assert data.get("path") == "src/main.py"
    assert not data.get("content")


def test_closed_file_write_json_is_unchanged():
    raw = json.dumps({"path": "src/main.py", "content": "def main():\n    print(1)\n"})
    out = coerce_tool_arguments_json(raw, tool_name="file_write")
    data = json.loads(out)
    assert data == {"path": "src/main.py", "content": "def main():\n    print(1)\n"}


def test_coerce_valid_json_unchanged():
    raw = json.dumps({"path": "a.py", "content": "print(1)\n"})
    out = coerce_tool_arguments_json(raw)
    assert json.loads(out) == {"path": "a.py", "content": "print(1)\n"}


def test_coerce_garbage_still_invalid():
    out = coerce_tool_arguments_json("{not json at all")
    data = json.loads(out)
    assert data.get("_invalid_json") is True
    assert data.get("_stream_truncated") is True
