"""Truncated file_read JSON must salvage path and execute (not write-style error)."""

from __future__ import annotations

import json

from remedy.core.provider_sanitize import coerce_tool_arguments_json


def test_file_read_truncated_salvages_path():
    raw = '{"path": "src/core/app.py", "offset": 0'
    out = coerce_tool_arguments_json(raw, tool_name="file_read")
    data = json.loads(out)
    assert data.get("path") == "src/core/app.py"
    assert not data.get("_invalid_json")


def test_list_dir_truncated_salvages_path():
    raw = '{"path": "src"'
    out = coerce_tool_arguments_json(raw, tool_name="list_dir")
    data = json.loads(out)
    assert data.get("path") == "src"
    assert not data.get("_invalid_json")


def test_file_write_unclosed_content_is_stream_truncated():
    raw = '{"path": "src/a.py", "content": "print(1)\\nprint(2)'
    out = coerce_tool_arguments_json(raw, tool_name="file_write")
    data = json.loads(out)
    assert data.get("path") == "src/a.py"
    assert data.get("_stream_truncated") is True
    assert not data.get("content")
