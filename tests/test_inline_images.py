"""Base64 images never reach chat persistence or provider history."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("REMEDY_HOME", tempfile.mkdtemp(prefix="remedy-inline-img-"))

from remedy.memory.inline_images import (  # noqa: E402
    extract_inline_images,
    strip_inline_images,
)

_PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def _data_uri(size: int) -> str:
    raw = _PNG_HEAD + os.urandom(max(size - len(_PNG_HEAD), 0))
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def test_strip_replaces_large_payload_and_keeps_small() -> None:
    big = f"before ![Dark Forest hero]({_data_uri(1_000_000)}) after"
    out = strip_inline_images(big)
    assert len(out) < 200
    assert "data:image" not in out
    assert "[inline image omitted] (Dark Forest hero)" in out
    assert out.startswith("before ") and out.endswith(" after")

    tiny = f"![icon]({_data_uri(64)})"
    assert strip_inline_images(tiny) == tiny


def test_extract_writes_file_under_home(tmp_path: Path) -> None:
    text = f"![hero art]({_data_uri(1_000_000)})\n\nSaved: done"
    out, saved = extract_inline_images(text, home_dir=tmp_path)
    assert len(saved) == 1
    path = saved[0]
    assert path.is_file()
    assert path.parent == tmp_path / "comfy_out" / "inline"
    assert path.read_bytes()[:8] == _PNG_HEAD
    assert "data:image" not in out
    assert len(out) < 2_048
    assert f"[image saved: {path}]" in out
    assert "![hero art](<" in out


def test_extract_raw_uri_without_markdown(tmp_path: Path) -> None:
    out, saved = extract_inline_images("payload: " + _data_uri(10_000), home_dir=tmp_path)
    assert len(saved) == 1
    assert out == f"payload: [image saved: {saved[0]}]"


@pytest.mark.asyncio
async def test_store_persists_path_not_payload(tmp_path: Path) -> None:
    from uuid import uuid4

    from remedy.memory.store import MemoryStore
    from remedy.models import ChatMessage, ChatMessageRole, ChatSession

    store = MemoryStore(tmp_path / "memory.db")
    await store.initialize()
    session = await store.create_chat_session(ChatSession(id=str(uuid4()), title="t"))
    uri = _data_uri(1_000_000)
    msg = ChatMessage(
        session_id=session.id,
        role=ChatMessageRole.ASSISTANT,
        content=f"![Dark Forest UI hero art]({uri})\n\n**hero**",
        tool_results=[{"name": "comfyui", "content": f"ok ![x]({uri})"}],
    )
    await store.add_chat_message(msg)
    rows = await store.get_chat_messages(session.id)
    row = rows[-1]
    assert len(row.content) < 2_048
    assert "data:image" not in row.content
    assert "[image saved: " in row.content
    saved = list((tmp_path / "comfy_out" / "inline").glob("*.png"))
    assert len(saved) == 1 and saved[0].stat().st_size >= 1_000_000
    assert "data:image" not in row.tool_results[0]["content"]

    # Provider history replay is also clean.
    from remedy.core.agent_history import load_session_history

    hist = await load_session_history(store, session.id, "next question")
    assert hist and all("data:image" not in m["content"] for m in hist)


@pytest.mark.asyncio
async def test_legacy_row_heals_on_read(tmp_path: Path) -> None:
    """Old 1MB data-URI rows extract on GET, persist the path, keep the picture."""
    import json
    from datetime import UTC, datetime
    from uuid import uuid4

    from remedy.memory.store import MemoryStore
    from remedy.models import ChatSession

    store = MemoryStore(tmp_path / "memory.db")
    await store.initialize()
    session = await store.create_chat_session(ChatSession(id=str(uuid4()), title="t"))
    uri = _data_uri(50_000)
    content = f"![Dark Forest UI hero art]({uri})\n\n**hero**"
    mid = str(uuid4())
    with store._locked():
        db = store._ensure_db()
        db.execute(
            """INSERT INTO chat_messages (id, session_id, role, content, thinking,
               tool_calls, tool_results, model, agent, tokens, created_at, reverted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                session.id,
                "assistant",
                content,
                None,
                "[]",
                json.dumps([{"name": "comfyui", "content": f"ok ![x]({uri})"}]),
                None,
                None,
                None,
                datetime.now(UTC).isoformat(),
                0,
            ),
        )
        db.commit()
    rows = await store.get_chat_messages(session.id)
    row = next(m for m in rows if str(m.id) == mid)
    assert "data:image" not in row.content
    assert "![Dark Forest UI hero art](<" in row.content
    saved = list((tmp_path / "comfy_out" / "inline").glob("*.png"))
    assert len(saved) == 1
    # Second read does not keep a payload in SQLite.
    raw = store._ensure_db().execute(
        "SELECT content FROM chat_messages WHERE id = ?", (mid,)
    ).fetchone()[0]
    assert "data:image" not in raw
    assert "data:image" not in row.tool_results[0]["content"]


def test_comfy_markdown_never_embeds_data_uri(tmp_path: Path) -> None:
    from remedy.tools.comfyui import markdown_for_image

    img = tmp_path / "attachments" / "sid" / "remedy_comfy_00001_.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(_PNG_HEAD + b"\0" * 4096)
    meta = {"name": img.name, "path": str(img), "mime": "image/png", "home_dir": str(tmp_path)}
    md = markdown_for_image(meta, caption="hero", embed_data_uri=True)
    assert "base64" not in md
    assert "![hero](attachments/sid/remedy_comfy_00001_.png)" in md
    assert f"Saved: `{img}`" in md
