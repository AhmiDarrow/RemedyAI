"""OCR word boxes for computer-use — click by text when DOM/vision fail."""

from __future__ import annotations

from pathlib import Path

from remedy.core.computer.ocr import merge_ocr_elements, words_to_elements
from remedy.core.computer.vision_observe import format_vision_block


def test_words_to_elements_page_space_divides_by_scale() -> None:
    words = [{"text": "Post", "x": 10, "y": 20, "w": 80, "h": 30}]
    els = words_to_elements(words, scale=2.0, space="page")
    assert len(els) == 1
    assert els[0]["ref"] == "o1"
    assert els[0]["name"] == "Post"
    assert els[0]["source"] == "ocr"
    # center (50, 35) / 2
    assert els[0]["x"] == 25
    assert els[0]["y"] == 18  # 35/2 rounded


def test_words_to_elements_screen_space_adds_origin() -> None:
    words = [{"text": "OK", "x": 4, "y": 6, "w": 10, "h": 8}]
    els = words_to_elements(
        words, origin_x=100, origin_y=200, space="screen"
    )
    assert els[0]["x"] == 109  # 4+5 + 100
    assert els[0]["y"] == 210  # 6+4 + 200


def test_merge_ocr_keeps_dom_refs() -> None:
    existing = [{"ref": "e1", "name": "Home"}, {"ref": "o1", "name": "old"}]
    ocr = [{"ref": "o1", "name": "Post"}]
    merged = merge_ocr_elements(existing, ocr)
    refs = [str(e.get("ref")) for e in merged]
    assert refs == ["e1", "o1"]
    assert merged[1]["name"] == "Post"


def test_skipped_vision_on_browser_does_not_suggest_desktop_xy() -> None:
    block = format_vision_block(
        {"ok": False, "error": "RMB holds VRAM"},
        path="C:/shot.png",
        surface="browser",
    )
    low = block.lower()
    assert "do not click desktop" in low
    assert "rmb" in low
    assert "computer_snapshot" in low


def test_skipped_vision_on_desktop_still_allows_xy_for_games() -> None:
    block = format_vision_block(
        {"ok": False, "error": "decoder idle"},
        path="C:/game.png",
        surface="desktop",
    )
    low = block.lower()
    assert "native/game" in low or "computer_click x/y" in low


def test_read_screenshot_ocr_missing_file(tmp_path: Path) -> None:
    from remedy.core.computer.ocr import read_screenshot_ocr

    out = read_screenshot_ocr(tmp_path / "nope.png")
    assert out["ok"] is False
    assert out["words"] == []
