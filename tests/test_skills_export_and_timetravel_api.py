"""HTTP coverage: skill pack export + session timeline / time-travel."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore
from remedy.models import (
    ChatMessage,
    ChatMessageRole,
    ChatSession,
    Skill,
    SkillManifest,
    SkillStatus,
)
from remedy.skills.registry import SkillRegistry


class _Runtime:
    def __init__(self, tmp_path: Path, memory: MemoryStore | None = None):
        self.skills = SkillRegistry()
        self.config = type("C", (), {"home_dir": str(tmp_path)})()
        self.memory = memory
        self._session_brief = object()
        for name, desc in (("pack-a", "A"), ("pack-b", "B")):
            self.skills.register(
                Skill(
                    manifest=SkillManifest(
                        name=name,
                        description=desc,
                        status=SkillStatus.ACTIVE,
                    ),
                    instructions=f"# {name}\nDo {desc}.\n",
                )
            )


def test_export_skills_pack_zip(tmp_path: Path):
    rt = _Runtime(tmp_path)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.post("/api/skills/export", json={"names": ["pack-a"]})
    assert r.status_code == 200
    assert "zip" in (r.headers.get("content-type") or "").lower() or r.content[:2] == b"PK"
    assert r.content[:2] == b"PK"  # zip magic


def test_export_all_skills_pack(tmp_path: Path):
    rt = _Runtime(tmp_path)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.post("/api/skills/export", json={})
    assert r.status_code == 200
    assert r.content[:2] == b"PK"


def test_timeline_and_time_travel(tmp_path: Path, monkeypatch):
    import asyncio

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("REMEDY_HOME", str(home))

    async def _prep():
        mem = MemoryStore(str(tmp_path / "mem.db"))
        await mem.initialize()
        sess = await mem.create_chat_session(ChatSession(title="tt"))
        u1 = await mem.add_chat_message(
            ChatMessage(
                session_id=sess.id,
                role=ChatMessageRole.USER,
                content="step one",
            )
        )
        await mem.add_chat_message(
            ChatMessage(
                session_id=sess.id,
                role=ChatMessageRole.ASSISTANT,
                content="done one",
                tool_calls=[{"name": "file_write", "args": {"path": "x.txt"}}],
            )
        )
        u2 = await mem.add_chat_message(
            ChatMessage(
                session_id=sess.id,
                role=ChatMessageRole.USER,
                content="step two bad",
            )
        )
        await mem.add_chat_message(
            ChatMessage(
                session_id=sess.id,
                role=ChatMessageRole.ASSISTANT,
                content="oops",
            )
        )
        return mem, sess.id, u1.id, u2.id

    mem, sid, uid1, uid2 = asyncio.run(_prep())
    rt = _Runtime(home, memory=mem)
    app = create_app(runtime=rt, memory=mem, api_key="")
    client = TestClient(app)

    tl = client.get(f"/api/sessions/{sid}/timeline")
    assert tl.status_code == 200
    body = tl.json()
    assert body["count"] >= 2
    user_steps = [s for s in body["steps"] if s["kind"] == "user"]
    assert len(user_steps) >= 2

    # Create a file undo entry after u2 so restore can act
    work = tmp_path / "f.txt"
    work.write_text("before", encoding="utf-8")
    from remedy.core.time_travel import SessionUndoLog

    log = SessionUndoLog(home)
    log.record_file_write(
        session_id=sid,
        path=work,
        previous_content="before",
        existed=True,
        new_size=5,
        message_id=str(uid2),
    )
    work.write_text("after-bad", encoding="utf-8")

    # Time travel back to first user step
    r = client.post(
        f"/api/sessions/{sid}/time-travel",
        json={"message_id": str(uid1)},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "restored"
    assert data["reverted_count"] >= 1

    msgs = client.get(f"/api/sessions/{sid}/messages")
    assert msgs.status_code == 200
    remaining = msgs.json().get("messages") or []
    # Soft-deleted: non-reverted list should not include step two
    contents = [m.get("content") for m in remaining]
    assert "step two bad" not in contents
