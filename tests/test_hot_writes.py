"""Hot-write ledger survives compact without re-embedding file bodies."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.memory.harness.brief import SessionBrief, brief_to_context_block
from remedy.memory.harness.hot_writes import record_hot_write, sync_hot_writes_into_brief


def test_hot_writes_are_injected_and_capped() -> None:
    brief = SessionBrief(session_id="s")
    rt = SimpleNamespace(_session_brief=brief, _hot_writes=[])
    record_hot_write(rt, "src/theme.css", tool="file_write")
    for i in range(8):
        record_hot_write(rt, f"src/lessons/{i}.tsx", tool="file_read")
    record_hot_write(rt, "src/theme.css", tool="file_edit")
    sync_hot_writes_into_brief(rt)
    block = brief_to_context_block(brief)
    assert "Hot writes" in block
    assert "src/theme.css" in block
    assert "never restore" in block.lower()
    assert "content=" not in block
    # Recency: rewrite of theme.css is at the tail.
    assert brief.hot_writes[-1] == "src/theme.css"
