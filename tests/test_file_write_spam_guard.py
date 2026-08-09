"""Refuse empty / repetitive spam file_write that poisons the project tree."""

from __future__ import annotations

from remedy.core.workspace_tools.guards import (
    looks_like_empty_source_write,
    looks_like_repetitive_spam_text,
    refuse_bad_file_write,
)


def test_empty_source_write_refused():
    assert looks_like_empty_source_write("src/main.py", "") is True
    assert looks_like_empty_source_write("src/main.py", "   \n  ") is True
    assert looks_like_empty_source_write("src/main.py", "print(1)\n") is False
    assert looks_like_empty_source_write("notes.bin", "") is False


def test_repetitive_import_spam_refused():
    line = "    QSplitter, QToolBar, QStatusBar, QMenu, QAction,"
    body = "from PyQt6.QtWidgets import (\n" + "\n".join([line] * 80) + "\n)\n"
    assert looks_like_repetitive_spam_text(body) is True
    assert refuse_bad_file_write("src/main.py", body) is not None
    assert "spam" in (refuse_bad_file_write("src/main.py", body) or "").lower()


def test_normal_source_allowed():
    body = "#!/usr/bin/env python3\n" + "\n".join(
        f"def f{i}():\n    return {i}\n" for i in range(30)
    )
    assert looks_like_repetitive_spam_text(body) is False
    assert refuse_bad_file_write("src/main.py", body) is None


def test_refuse_empty_message():
    msg = refuse_bad_file_write("app.py", "")
    assert msg is not None
    assert "empty" in msg.lower()


def test_empty_write_soft_skip_when_file_exists(tmp_path):
    from types import SimpleNamespace

    from remedy.core.workspace_tools.guards import resolve_empty_write_skip

    f = tmp_path / "app.py"
    f.write_text("print('real')\n" * 5, encoding="utf-8")
    rt = SimpleNamespace(resolve_tool_path=lambda p, **k: f)
    skip = resolve_empty_write_skip(rt, str(f))
    assert skip is not None
    assert "skipped empty" in skip.lower()
    assert "real file already" in skip.lower()
