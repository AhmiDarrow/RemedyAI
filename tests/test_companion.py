"""Personal PC companion: clipboard, foreground, design pass."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.companion import (
    FakeCompanionBackend,
    design_pass,
    format_companion_block,
    gather_companion_snapshot,
    looks_like_companion_request,
    recent_files,
    set_companion_backend,
)
from remedy.core.react_policy import message_wants_tools


def setup_function() -> None:
    set_companion_backend(None)


def teardown_function() -> None:
    set_companion_backend(None)


def test_companion_intent():
    assert looks_like_companion_request("look at this mockup")
    assert looks_like_companion_request("what's on my clipboard")
    assert looks_like_companion_request("I copied a screenshot")
    assert looks_like_companion_request("design this landing page")
    assert looks_like_companion_request("what am I looking at")
    assert not looks_like_companion_request("thanks")
    assert message_wants_tools("look at my screen and fix the contrast")


def test_snapshot_text_clipboard(tmp_path: Path):
    be = FakeCompanionBackend(
        text="def greet():\n    return 1\n",
        fg={"exe_name": "Code.exe", "title": "app.tsx — Old-Remedy"},
    )
    set_companion_backend(be)
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path))
    snap = gather_companion_snapshot(rt, include_recent=False)
    assert snap["clipboard"]["kind"] == "text"
    assert "greet" in snap["clipboard"]["preview"]
    assert snap["foreground"]["exe_name"] == "Code.exe"
    block = format_companion_block(snap)
    assert "Clipboard: text" in block
    assert "Code.exe" in block
    assert "Do not ask" in block


def test_snapshot_image_saved(tmp_path: Path):
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
    be = FakeCompanionBackend(image_png=png)
    set_companion_backend(be)
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path))
    snap = gather_companion_snapshot(rt, include_recent=False)
    assert snap["clipboard"]["kind"] == "image"
    assert Path(snap["clipboard"]["path"]).is_file()


def test_clipboard_read_image_no_file_read(tmp_path: Path, monkeypatch):
    import asyncio

    from remedy.core.agent_companion_tools import register_companion_tools

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    be = FakeCompanionBackend(image_png=png)
    set_companion_backend(be)
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        lambda path, extra_question=None, timeout_s=25.0, **k: {
            "ok": True,
            "text": "A copied mockup.",
            "error": "",
        },
    )
    handlers: dict[str, object] = {}

    class _Reg:
        def register_builtin_handler(self, name, desc, fn, schema):
            handlers[name] = fn

    rt = SimpleNamespace(
        tool_registry=_Reg(),
        config=SimpleNamespace(home_dir=tmp_path),
    )
    register_companion_tools(rt)
    text = asyncio.run(handlers["clipboard_read"]())
    assert "file_read" not in text.lower()
    assert "A copied mockup." in text
    assert "do not ask" in text.lower()


def test_clipboard_write_roundtrip():
    be = FakeCompanionBackend()
    set_companion_backend(be)
    assert be.set_clipboard_text("hello owner")
    assert be.clipboard_text() == "hello owner"


def test_design_pass_seeds_todos(tmp_path: Path):
    be = FakeCompanionBackend(
        files=[str(tmp_path / "hero.png")],
        fg={"exe_name": "Figma.exe", "title": "Landing"},
    )
    set_companion_backend(be)
    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        config=SimpleNamespace(home_dir=tmp_path),
        _build_turn=None,
    )
    res = design_pass(rt, goal="make the hero readable")
    assert res["ok"] is True
    assert any("hero.png" in str(e) for e in res["evidence"])
    todos = getattr(rt, "_build_todos", None) or []
    ids = [t.id for t in todos]
    assert "critique" in ids and "make" in ids and "look" in ids


def test_recent_files_prefers_new(tmp_path: Path, monkeypatch):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (desk / "old.txt").write_text("x", encoding="utf-8")
    fresh = desk / "fresh.png"
    fresh.write_text("y", encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    hits = recent_files(limit=5)
    names = [h["name"] for h in hits]
    assert "fresh.png" in names
