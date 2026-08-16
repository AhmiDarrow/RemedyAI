"""Write-invalidates-read: a verify-read after an edit must not replay stale content."""

from __future__ import annotations

from remedy.core.agent_tool_batch import _evict_reads_for_path
from remedy.core.react_policy import tool_call_fingerprint


def _read_fp(path: str) -> str:
    return tool_call_fingerprint(
        {"function": {"name": "file_read", "arguments": f'{{"path": "{path}"}}'}}
    )


def test_write_evicts_cached_read_of_same_path():
    fp = _read_fp("src/app.py")
    seen = {fp}
    cache = {fp: "OLD CONTENT"}
    _evict_reads_for_path("src/app.py", seen, cache)
    assert fp not in seen
    assert fp not in cache


def test_write_evicts_by_basename_across_slash_styles():
    fp = _read_fp("src/app.py")
    seen = {fp}
    cache = {fp: "OLD"}
    # Windows-style path arg to the write still evicts the posix-style read.
    _evict_reads_for_path("src\\app.py", seen, cache)
    assert fp not in seen and fp not in cache


def test_write_does_not_evict_unrelated_reads():
    keep = _read_fp("src/other.py")
    seen = {keep}
    cache = {keep: "OTHER"}
    _evict_reads_for_path("src/app.py", seen, cache)
    assert keep in seen and keep in cache


def test_empty_path_is_a_noop():
    fp = _read_fp("src/app.py")
    seen = {fp}
    cache = {fp: "X"}
    _evict_reads_for_path("", seen, cache)
    assert fp in seen and fp in cache
