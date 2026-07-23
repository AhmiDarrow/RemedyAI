"""Tests for structured tool/API error helpers."""

from __future__ import annotations

from remedy.core.errors import (
    ExecutionError,
    RemedyError,
    as_remedy_error,
    format_tool_error,
    tool_error_payload,
)


def test_tool_error_payload_shape() -> None:
    p = tool_error_payload("missing file", code="NOT_FOUND", tool_name="file_read", path="x")
    assert p["ok"] is False
    assert p["error"] == "missing file"
    assert p["code"] == "NOT_FOUND"
    assert p["tool_name"] == "file_read"
    assert p["details"]["path"] == "x"


def test_format_tool_error_readable() -> None:
    s = format_tool_error("boom", code="EXEC", tool_name="bash_exec")
    assert "Error" in s
    assert "bash_exec" in s
    assert "boom" in s


def test_as_remedy_error_passthrough_and_wrap() -> None:
    e = ExecutionError("x", tool_name="t")
    assert as_remedy_error(e) is e
    wrapped = as_remedy_error(ValueError("nope"))
    assert isinstance(wrapped, RemedyError)
    assert "nope" in str(wrapped)
