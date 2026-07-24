"""Last-phase partner loop: approvals, knowledge pack, goals helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.approvals import APPROVALS, ApprovalQueue
from remedy.memory.knowledge_pack import import_knowledge_pack


def test_approval_needs_ask_destructive():
    q = ApprovalQueue()
    assert q.needs_ask("rm -rf /tmp/foo") is not None
    assert q.needs_ask("git reset --hard HEAD") is not None
    assert q.needs_ask("ls -la") is None


def test_approval_session_scope():
    q = ApprovalQueue()
    item = q.create(
        tool_name="bash_exec",
        command="rm -rf ./build",
        reason="test",
        session_id="s1",
    )
    assert not q.is_approved("bash_exec", "rm -rf ./build", session_id="s1")
    q.resolve(item.id, approve=True, scope="session")
    assert q.is_approved("bash_exec", "rm -rf ./build", session_id="s1")
    assert not q.is_approved("bash_exec", "rm -rf ./build", session_id="other")


@pytest.mark.asyncio
async def test_import_knowledge_pack(tmp_path: Path):
    (tmp_path / "a.md").write_text("# Hello\nWorld", encoding="utf-8")
    (tmp_path / "b.txt").write_text("note", encoding="utf-8")
    (tmp_path / "skip.bin").write_bytes(b"\x00\x01")

    class FakeStore:
        def __init__(self) -> None:
            self.entries = []

        async def upsert(self, entry):
            self.entries.append(entry)
            return entry

    store = FakeStore()
    result = await import_knowledge_pack(store, tmp_path)
    assert result["ok"] is True
    assert result["imported"] == 2
    assert len(store.entries) == 2


def test_singleton_approvals_create_list():
    # Use global carefully — create unique command
    cmd = f"rm -rf ./unique-test-{Path.cwd().name}"
    item = APPROVALS.create(
        tool_name="bash_exec",
        command=cmd,
        reason="unit",
        session_id="unit",
    )
    pending = APPROVALS.list_pending(session_id="unit")
    assert any(p.id == item.id for p in pending)
    APPROVALS.resolve(item.id, approve=False)
