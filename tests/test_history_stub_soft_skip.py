"""HISTORY_STUB re-writes must soft-skip when real files exist (no fail loop)."""

from __future__ import annotations

from pathlib import Path

from remedy.core.provider_sanitize import FILE_WRITE_CONTENT_HISTORY_MAX, _rewrite_write_tool_args
from remedy.core.workspace_tools.guards import (
    looks_like_history_stub_text,
    resolve_stub_write_skip,
)


def test_history_rewrite_does_not_put_stub_in_content():
    big = "print('hello')\n" * 200
    assert len(big) > FILE_WRITE_CONTENT_HISTORY_MAX
    out = _rewrite_write_tool_args({"path": "src/a.py", "content": big}, "file_write")
    assert out["content"] == ""
    assert out.get("_history_summarized") is True
    assert "NOT_SOURCE_CODE" not in out["content"]
    assert "history_stub" not in out["content"]


def test_looks_like_history_stub():
    assert looks_like_history_stub_text(
        "<<NOT_SOURCE_CODE history_stub kind=file_write content chars=9 "
        "DO_NOT_file_write_this_string history_stub_only>>"
    )
    assert not looks_like_history_stub_text("def main():\n    return 0\n")


def test_soft_skip_when_real_file_exists(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("def main():\n    pass\n", encoding="utf-8")

    class RT:
        def resolve_tool_path(self, path, for_write=False):  # noqa: ANN001
            return target

    msg = resolve_stub_write_skip(RT(), "app.py")
    assert msg is not None
    assert msg.startswith("OK:")
    assert "already on disk" in msg


def test_no_skip_when_missing(tmp_path: Path):
    class RT:
        def resolve_tool_path(self, path, for_write=False):  # noqa: ANN001
            return tmp_path / "missing.py"

    assert resolve_stub_write_skip(RT(), "missing.py") is None
