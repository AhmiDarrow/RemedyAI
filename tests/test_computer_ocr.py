"""OCR word boxes for computer-use — click by text when DOM/vision fail."""

from __future__ import annotations

from pathlib import Path

from remedy.core.computer.elements import find_best_element, score_element
from remedy.core.computer.ocr import (
    group_ocr_phrases,
    infer_ocr_role,
    merge_ocr_elements,
    ocr_click_boxes,
    words_to_elements,
)
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
    assert els[0]["role"] == "button"


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


def test_group_phrases_joins_composer_placeholder() -> None:
    """Live miss: OCR returned one box per word, so click-by-text
    'What's happening?' never found a control."""
    words = [
        {"text": "What's", "x": 40, "y": 80, "w": 70, "h": 22, "line": 0},
        {"text": "happening?", "x": 118, "y": 80, "w": 110, "h": 22, "line": 0},
        {"text": "Add", "x": 40, "y": 140, "w": 36, "h": 18, "line": 1},
        {"text": "a", "x": 80, "y": 140, "w": 12, "h": 18, "line": 1},
        {"text": "GIF", "x": 98, "y": 140, "w": 34, "h": 18, "line": 1},
        {"text": "Post", "x": 520, "y": 200, "w": 48, "h": 24, "line": 2},
    ]
    phrases = group_ocr_phrases(words)
    names = [p["text"] for p in phrases]
    assert "What's happening?" in names
    assert "Add a GIF" in names
    assert "Post" in names
    els = words_to_elements(words, scale=1.0, space="page")
    composer = find_best_element(els, "What's happening?")
    assert composer is not None
    assert composer["name"] == "What's happening?"
    assert composer["role"] == "textbox"
    gif = next(e for e in els if e["name"] == "Add a GIF")
    assert score_element(gif, "What's happening?") < score_element(
        composer, "What's happening?"
    )
    post = find_best_element(els, "Post")
    assert post is not None
    assert post["name"] == "Post"
    assert post["role"] == "button"


def test_group_phrases_splits_nav_on_large_gap() -> None:
    words = [
        {"text": "Home", "x": 10, "y": 8, "w": 50, "h": 18, "line": 0},
        {"text": "Explore", "x": 120, "y": 8, "w": 70, "h": 18, "line": 0},
        {"text": "Post", "x": 250, "y": 8, "w": 44, "h": 18, "line": 0},
    ]
    names = [p["text"] for p in group_ocr_phrases(words)]
    assert names == ["Home", "Explore", "Post"]


def test_infer_ocr_role_composer_and_action() -> None:
    assert infer_ocr_role("What's happening?") == "textbox"
    assert infer_ocr_role("Post") == "button"
    assert infer_ocr_role("Continue") == "button"
    assert infer_ocr_role("Home") == "text"


def test_hidpi_phrase_click_coords_divide_by_scale() -> None:
    words = [
        {"text": "What's", "x": 80, "y": 160, "w": 140, "h": 44, "line": 0},
        {"text": "happening?", "x": 236, "y": 160, "w": 220, "h": 44, "line": 0},
    ]
    els = words_to_elements(words, scale=2.0, space="page")
    phrase = next(e for e in els if e["name"] == "What's happening?")
    # union: x=80,y=160,w=376,h=44 → center (268, 182) / 2
    assert phrase["x"] == 134
    assert phrase["y"] == 91


def test_ocr_click_text_hits_reply_not_bar_center() -> None:
    """Action-bar words share a line; click-text Reply must not hit the union."""
    words = [
        {"text": "Reply", "x": 10, "y": 40, "w": 48, "h": 18, "line": 0},
        {"text": "Retweet", "x": 64, "y": 40, "w": 70, "h": 18, "line": 0},
        {"text": "Share", "x": 140, "y": 40, "w": 50, "h": 18, "line": 0},
    ]
    phrases = group_ocr_phrases(words)
    assert [p["text"] for p in phrases] == ["Reply Retweet Share"]
    boxes = ocr_click_boxes(words)
    assert [b["text"] for b in boxes] == [
        "Reply Retweet Share",
        "Reply",
        "Retweet",
        "Share",
    ]
    els = words_to_elements(words, scale=1.0, space="page")
    hit = find_best_element(els, "Reply", min_score=40.0)
    assert hit is not None
    assert hit["name"] == "Reply"
    bar = next(e for e in els if e["name"] == "Reply Retweet Share")
    # Reply center x=34; bar union x=10 w=180 center=100
    assert hit["x"] == 34
    assert bar["x"] == 100
    assert hit["x"] != bar["x"]
    # Phrase match still wins for a multi-word query.
    composer_words = [
        {"text": "What's", "x": 40, "y": 80, "w": 70, "h": 22, "line": 0},
        {"text": "happening?", "x": 118, "y": 80, "w": 110, "h": 22, "line": 0},
    ]
    cels = words_to_elements(composer_words, scale=1.0, space="page")
    composer = find_best_element(cels, "What's happening?", min_score=40.0)
    assert composer is not None
    assert composer["name"] == "What's happening?"
