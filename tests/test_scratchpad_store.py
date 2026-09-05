"""Studio scratch pad — server store the agent can read."""

from __future__ import annotations

from pathlib import Path

from remedy.core.scratchpad_store import read_scratch, scratch_id, write_scratch


def test_scratch_roundtrip(tmp_path: Path):
    write_scratch("sess-1", "hello notes", home=tmp_path)
    assert read_scratch("sess-1", home=tmp_path) == "hello notes"
    write_scratch("sess-1", " more", home=tmp_path, append=True)
    assert read_scratch("sess-1", home=tmp_path) == "hello notes more"
    assert scratch_id("../evil") == "_evil"
    assert scratch_id("") == "_global"
