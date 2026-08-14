"""Taste, inbox, away mode, visual observe, inject budget."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.away_mode import looks_like_away_request
from remedy.core.build_engine import (
    BuildTurnState,
    begin_build_turn,
    can_machine_inject,
    keep_agency_after_green,
)
from remedy.core.companion_inbox import format_inbox_block, poll_new_drops
from remedy.core.companion_observe import (
    append_observe_messages,
    design_observe_path,
    design_observe_paths,
    format_observe_message,
    maybe_visual_observe,
    write_set_looks_visual,
)
from remedy.core.companion_taste import (
    extract_taste,
    format_taste_block,
    load_taste,
    remember_taste,
)

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_away_phrases():
    assert looks_like_away_request("I'm stepping away, finish this")
    assert looks_like_away_request("work alone on the API")
    assert looks_like_away_request("take it from here")
    assert not looks_like_away_request("thanks")


def test_begin_build_stamps_away(tmp_path):
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=SimpleNamespace(home_dir=tmp_path),
        effective_project_path=lambda: tmp_path,
    )
    st = begin_build_turn(rt, "I'm stepping away — finish without me")
    assert st is not None
    assert st.away_mode is True
    assert st.max_serial_explore == 1


def test_taste_roundtrip(tmp_path):
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path))
    remember_taste("Prefer 8px spacing and Inter", rt)
    remember_taste("Prefer 8px spacing and Inter", rt)  # de-dupe
    items = load_taste(rt)
    assert len(items) == 1
    block = format_taste_block(items)
    assert "8px" in block
    assert extract_taste("I prefer dark mode and 8px spacing.")


def test_inbox_new_drop(tmp_path, monkeypatch):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (desk / "mock.png").write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path / ".remedy"))
    (tmp_path / ".remedy").mkdir()
    first = poll_new_drops(rt, extra_roots=[desk], mark_seen=True)
    assert any(d["name"] == "mock.png" for d in first)
    second = poll_new_drops(rt, extra_roots=[desk], mark_seen=True)
    assert second == []
    assert "mock.png" in format_inbox_block(first)


def test_visual_write_set():
    assert write_set_looks_visual(["src/App.tsx"], "")
    assert write_set_looks_visual(["a.py"], "pygame window")
    assert not write_set_looks_visual(["a.py"], "add helper")


def test_maybe_visual_observe_once(tmp_path, monkeypatch):
    png = tmp_path / "ui.png"
    png.write_bytes(_TINY_PNG)
    monkeypatch.setattr(
        "remedy.core.companion_observe.capture_foreground_png",
        lambda rt=None: {"ok": True, "path": str(png), "via": "window"},
    )
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        lambda path, extra_question=None, timeout_s=25.0, **k: {
            "ok": True,
            "text": "Hierarchy: header then hero.",
            "error": "",
        },
    )
    st = BuildTurnState(active=True, write_set=["App.tsx"], goal="fix the landing")
    rt = SimpleNamespace(config=None)
    first = maybe_visual_observe(rt, st)
    assert first is not None
    assert first["ok"] is True
    assert st.visual_observe_ran is True
    assert maybe_visual_observe(rt, st) is None


def test_maybe_visual_observe_design_kind_queues(tmp_path, monkeypatch):
    png = tmp_path / "ui.png"
    png.write_bytes(_TINY_PNG)
    questions: list[str] = []
    monkeypatch.setattr(
        "remedy.core.companion_observe.capture_foreground_png",
        lambda rt=None: {"ok": True, "path": str(png), "via": "window"},
    )

    def _decode(path, extra_question=None, timeout_s=25.0, **k):
        questions.append(extra_question or "")
        return {"ok": True, "text": "Hierarchy: nav then hero.", "error": ""}

    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        _decode,
    )
    st = BuildTurnState(active=True, write_set=["App.tsx"], goal="fix the landing")
    rt = SimpleNamespace(config=None)
    first = maybe_visual_observe(rt, st)
    assert first is not None
    assert first["ok"] is True
    assert first["decode_ok"] is True
    assert questions
    q = questions[0]
    assert "Click targets" not in q
    assert "computer_click" not in q
    assert "hierarchy" in q.lower()
    shots = getattr(rt, "_pending_cua_shots", None) or []
    assert shots
    assert shots[-1]["kind"] == "design"
    blob = format_observe_message(first)["content"]
    assert "file_read" not in blob.lower()
    assert "Hierarchy: nav then hero." in blob
    md_src = str(png).replace("\\", "/")
    assert "![" in blob
    assert md_src in blob


def test_maybe_visual_observe_failed_capture_unseen(monkeypatch):
    monkeypatch.setattr(
        "remedy.core.companion_observe.capture_foreground_png",
        lambda rt=None: {"ok": False, "error": "no capture backend", "via": "none"},
    )
    st = BuildTurnState(active=True, write_set=["App.tsx"], goal="landing")
    rt = SimpleNamespace(config=None)
    first = maybe_visual_observe(rt, st)
    assert first is not None
    assert first["ok"] is False
    assert first.get("queued") is False
    assert "file_read" not in (first.get("message") or "").lower()
    assert "cannot see" in (first.get("message") or "").lower()
    blob = format_observe_message(first)["content"]
    assert "file_read" not in blob.lower()
    assert "cannot see" in blob.lower()
    assert "ascii" in blob.lower()


def test_design_observe_decoder_idle_native_vision(tmp_path, monkeypatch):
    png = tmp_path / "ui.png"
    png.write_bytes(_TINY_PNG)
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        lambda path, extra_question=None, timeout_s=25.0, **k: {
            "ok": False,
            "text": "",
            "error": "local decoder idle (not running).",
        },
    )
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.chat_supports_native_vision",
        lambda _rt=None: True,
    )
    rt = SimpleNamespace()
    res = design_observe_path(rt, str(png), hint="landing")
    assert res["ok"] is True
    assert res["queued"] is True
    assert res["native"] is True
    assert res["decode_ok"] is False
    assert "decoder idle" in res["message"]
    assert "file_read" not in res["message"].lower()
    blob = format_observe_message(res)["content"]
    assert "decoder idle" in blob
    assert "cannot see" not in blob.lower()
    assert "file_read" not in blob.lower()
    assert getattr(rt, "_pending_cua_shots", None)


def test_design_observe_decoder_idle_no_native_cannot_see(tmp_path, monkeypatch):
    png = tmp_path / "ui.png"
    png.write_bytes(_TINY_PNG)
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        lambda path, extra_question=None, timeout_s=25.0, **k: {
            "ok": False,
            "text": "",
            "error": "local decoder idle (not running).",
        },
    )
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.chat_supports_native_vision",
        lambda _rt=None: False,
    )
    rt = SimpleNamespace()
    res = design_observe_path(rt, str(png), hint="landing")
    assert res["ok"] is True
    assert res["queued"] is True
    assert res["native"] is False
    assert res["decode_ok"] is False
    assert "decoder idle" not in res["message"].lower()
    assert "cannot see" in res["message"].lower()
    assert "ascii" in res["message"].lower()
    blob = format_observe_message(res)["content"]
    assert "decoder idle" not in blob.lower()
    assert "cannot see" in blob.lower()
    assert "file_read" not in blob.lower()


def test_observe_then_flush_design_image_url(tmp_path, monkeypatch):
    png = tmp_path / "ui.png"
    png.write_bytes(_TINY_PNG)
    monkeypatch.setattr(
        "remedy.core.companion_observe.capture_foreground_png",
        lambda rt=None: {"ok": True, "path": str(png), "via": "window"},
    )
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        lambda path, extra_question=None, timeout_s=25.0, **k: {
            "ok": False,
            "text": "",
            "error": "local decoder idle (not running).",
        },
    )
    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.chat_supports_native_vision",
        lambda _rt=None: True,
    )
    st = BuildTurnState(active=True, write_set=["App.tsx"], goal="landing")
    rt = SimpleNamespace()
    vis = maybe_visual_observe(rt, st)
    assert vis is not None
    messages: list[dict] = []
    flush_msg = append_observe_messages(rt, vis, messages)
    assert len(messages) == 2
    text_msg, native_msg = messages
    assert "file_read" not in text_msg["content"].lower()
    assert str(png).replace("\\", "/") in text_msg["content"]
    assert flush_msg is native_msg
    kinds = [p.get("type") for p in native_msg["content"]]
    assert "image_url" in kinds
    header = native_msg["content"][0]["text"].lower()
    assert "critique" in header
    assert "do not computer_click" in header
    assert "you can see this" in header


def test_design_observe_paths_on_evidence(tmp_path, monkeypatch):
    png = tmp_path / "hero.png"
    png.write_bytes(_TINY_PNG)
    (tmp_path / "notes.svg").write_text("<svg/>", encoding="utf-8")
    questions: list[str] = []

    def _decode(path, extra_question=None, timeout_s=25.0, **k):
        questions.append(extra_question or "")
        return {"ok": True, "text": "Contrast fail on CTA.", "error": ""}

    monkeypatch.setattr(
        "remedy.core.computer.vision_observe.decode_screenshot_brief",
        _decode,
    )
    rt = SimpleNamespace()
    seen = design_observe_paths(
        rt,
        [str(png), str(tmp_path / "missing.png"), str(tmp_path / "notes.svg")],
        hint="hero",
    )
    assert len(seen) == 1
    assert seen[0]["decode_ok"] is True
    assert "file_read" not in seen[0]["message"].lower()
    assert questions and "Click targets" not in questions[0]


def test_inject_budget_and_keep_agency_ui():
    st = BuildTurnState(active=True, write_set=["App.tsx"], goal="landing")
    assert can_machine_inject(st, cap=2, consume=False) is True
    assert st.machine_injects == 0
    assert can_machine_inject(st, cap=2) is True
    assert can_machine_inject(st, cap=2) is True
    assert can_machine_inject(st, cap=2) is False
    assert can_machine_inject(st, cap=2, consume=False) is False
    st.visual_observe_ran = False
    assert keep_agency_after_green(st) is True
    st.visual_observe_ran = True
    # UI writes already observed — play regex may still be false
    assert keep_agency_after_green(st) is False
    st.away_mode = True
    st.open_todo_count = 2
    assert keep_agency_after_green(st) is True


def test_is_remedy_chrome_skips_own_app() -> None:
    from remedy.core.companion_observe import is_remedy_chrome

    assert is_remedy_chrome(
        {"exe_name": "Remedy Desktop.exe", "title": "chat", "exe": r"C:\App\Remedy Desktop.exe"}
    )
    assert is_remedy_chrome(
        {"exe_name": "app.exe", "title": "x", "exe": r"C:\Users\x\Remedy Desktop\app.exe"}
    )
    assert is_remedy_chrome({"exe_name": "game.exe", "title": "Remedy Desktop — Settings", "exe": ""})
    assert not is_remedy_chrome(
        {"exe_name": "python.exe", "title": "My Game", "exe": r"C:\proj\python.exe"}
    )
