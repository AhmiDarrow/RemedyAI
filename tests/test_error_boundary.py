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


def test_format_tool_error_with_suggestion() -> None:
    s = format_tool_error(
        "file not found: x",
        code="NOT_FOUND",
        tool_name="file_read",
        suggestion="Call list_dir on parent.",
    )
    assert "NOT_FOUND" in s
    assert "Suggestion: Call list_dir on parent." in s


def test_tool_error_payload_with_suggestion() -> None:
    p = tool_error_payload(
        "missing",
        code="NOT_FOUND",
        tool_name="file_read",
        suggestion="list_dir",
        path="x",
    )
    assert p["suggestion"] == "list_dir"
    assert p["details"]["path"] == "x"


def test_as_remedy_error_passthrough_and_wrap() -> None:
    e = ExecutionError("x", tool_name="t")
    assert as_remedy_error(e) is e
    wrapped = as_remedy_error(ValueError("nope"))
    assert isinstance(wrapped, RemedyError)
    assert "nope" in str(wrapped)
