"""Linux detect_ui_candidates: AT-SPI / OCR / pixel boxes; backends faked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from remedy.core.computer import desktop_linux as lin
from remedy.core.computer import host_binding as H


def _synthetic_frame(width: int, height: int, boxes: list[tuple]) -> tuple:
    stride = (width * 3 + 3) & ~3
    buf = bytearray(b"\x10" * (stride * height))
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1):
            o = y * stride
            for x in range(x0, x1):
                px = o + x * 3
                buf[px] = buf[px + 1] = buf[px + 2] = 240
    return bytes(buf), stride


def _no_backends(monkeypatch) -> None:
    monkeypatch.setattr(lin, "_atspi_clickable_candidates", lambda **k: [])
    monkeypatch.setattr(lin, "_ocr_word_candidates", lambda *a, **k: [])


def test_linux_detect_tiny_frame_safe(monkeypatch) -> None:
    _no_backends(monkeypatch)
    assert lin.detect_ui_candidates(b"", 0, 4, 4) == []


def test_linux_detect_blank_frame_empty_without_a11y(monkeypatch) -> None:
    _no_backends(monkeypatch)
    w, h = 300, 200
    raw, stride = _synthetic_frame(w, h, [])
    assert lin.detect_ui_candidates(raw, stride, w, h) == []


def test_linux_detect_atspi_buttons_without_live_tree(monkeypatch) -> None:
    monkeypatch.setattr(
        lin,
        "_atspi_clickable_candidates",
        lambda **k: [{
            "x": 50, "y": 60, "w": 80, "h": 24, "area": 1920,
            "name": "OK", "role": "push button", "source": "atspi",
        }],
    )
    monkeypatch.setattr(lin, "_ocr_word_candidates", lambda *a, **k: [])
    monkeypatch.setattr(lin, "_pixel_ui_candidates", lambda *a, **k: [])
    cands = lin.detect_ui_candidates(b"\x00" * 100, 10, 400, 300)
    assert len(cands) == 1
    assert cands[0]["name"] == "OK"
    assert cands[0]["x"] == 50
    assert cands[0]["source"] == "atspi"


def test_linux_detect_ocr_when_atspi_empty(monkeypatch) -> None:
    monkeypatch.setattr(lin, "_atspi_clickable_candidates", lambda **k: [])
    monkeypatch.setattr(
        lin,
        "_ocr_word_candidates",
        lambda *a, **k: [{
            "x": 100, "y": 40, "w": 40, "h": 16, "area": 640,
            "name": "File", "role": "text", "source": "ocr",
        }],
    )
    monkeypatch.setattr(lin, "_pixel_ui_candidates", lambda *a, **k: [])
    cands = lin.detect_ui_candidates(b"\x00" * 100, 10, 400, 300)
    assert cands[0]["name"] == "File"
    assert cands[0]["source"] == "ocr"
    assert (cands[0]["x"], cands[0]["y"]) == (100, 40)


def test_linux_detect_merges_atspi_then_ocr(monkeypatch) -> None:
    monkeypatch.setattr(
        lin,
        "_atspi_clickable_candidates",
        lambda **k: [{
            "x": 20, "y": 20, "w": 40, "h": 16, "area": 640,
            "name": "Save", "source": "atspi",
        }],
    )
    monkeypatch.setattr(
        lin,
        "_ocr_word_candidates",
        lambda *a, **k: [{
            "x": 200, "y": 80, "w": 30, "h": 12, "area": 360,
            "name": "Help", "source": "ocr",
        }],
    )
    monkeypatch.setattr(lin, "_pixel_ui_candidates", lambda *a, **k: [])
    cands = lin.detect_ui_candidates(b"\x00" * 100, 10, 400, 300, max_marks=10)
    assert [c["name"] for c in cands] == ["Save", "Help"]


def test_linux_detect_pixels_like_windows(monkeypatch) -> None:
    _no_backends(monkeypatch)
    w, h = 400, 300
    raw, stride = _synthetic_frame(w, h, [(40, 40, 160, 100), (220, 180, 360, 260)])
    cands = lin.detect_ui_candidates(raw, stride, w, h)
    assert len(cands) >= 2

    def inside(c: dict[str, Any], box: tuple) -> bool:
        x0, y0, x1, y1 = box
        return x0 - 12 <= c["x"] <= x1 + 12 and y0 - 12 <= c["y"] <= y1 + 12

    boxes = [(40, 40, 160, 100), (220, 180, 360, 260)]
    assert any(inside(cands[0], b) for b in boxes)
    assert any(inside(cands[1], b) for b in boxes)
    assert all("w" in c and "h" in c and "area" in c for c in cands[:2])


def test_linux_detect_respects_max_marks(monkeypatch) -> None:
    _no_backends(monkeypatch)
    w, h = 500, 400
    boxes = [(x, y, x + 30, y + 20) for x in range(20, 440, 60) for y in range(20, 340, 60)]
    raw, stride = _synthetic_frame(w, h, boxes)
    assert len(lin.detect_ui_candidates(raw, stride, w, h, max_marks=5)) <= 5


def test_linux_host_fail_closed(monkeypatch) -> None:
    """Without a Linux host/display, input fails closed (no silent no-op)."""
    monkeypatch.setattr(lin, "_require_linux", lambda: None)

    def boom(*_a, **_k):
        raise H.HostError("mouse_drag", H.STATUS_UNSUPPORTED)

    monkeypatch.setattr(H, "mouse_drag", boom)
    monkeypatch.setattr(H, "mouse_scroll", boom)
    monkeypatch.setattr(H, "mouse_move", boom)
    monkeypatch.setattr(H, "mouse_button", boom)
    monkeypatch.setattr(H, "focus_window", boom)
    monkeypatch.setattr(H, "manage_window", boom)
    with pytest.raises(RuntimeError, match="remedy_core"):
        lin.press_hold(1, 1, hold_ms=50)
    with pytest.raises(RuntimeError, match="remedy_core"):
        lin.scroll(1, 1)
    with pytest.raises(RuntimeError, match="remedy_core"):
        lin.drag(0, 0, 1, 1)
    assert hasattr(lin, "focus_window")
    assert callable(lin.focus_window)
    assert lin.focus_window(1) is False
    res = lin.manage_window(1, "restore")
    assert res.get("ok") is False


def test_linux_capture_host_error_fails_closed_not_blank(monkeypatch) -> None:
    """Capture HostError must raise — never a soft 10x10 blank frame."""
    monkeypatch.setattr(lin, "_require_linux", lambda: None)

    def boom(*_a, **_k):
        raise H.HostError("capture_virtual_screen", H.STATUS_OPERATION_FAILED)

    monkeypatch.setattr(H, "capture_virtual_screen", boom)
    with pytest.raises(RuntimeError, match="capture") as exc:
        lin._capture_virtual_screen()
    assert isinstance(exc.value.__cause__, H.HostError)
    assert exc.value.__cause__.function == "capture_virtual_screen"
    assert exc.value.__cause__.status == H.STATUS_OPERATION_FAILED


def test_linux_module_has_no_pointer_tool_shellout() -> None:
    """Phase 2 cutover: no external pointer/capture tool argv remains."""
    src = Path(lin.__file__).read_text(encoding="utf-8")
    # Strip the module docstring so the banlist is about call sites.
    if src.startswith('"""'):
        end = src.find('"""', 3)
        body = src[end + 3 :] if end != -1 else src
    else:
        body = src
    for banned in ("xdotool", "ydotool", "wmctrl", "grim", "scrot", "gnome-screenshot", "xsel"):
        assert banned not in body, f"banned tool name still in desktop_linux.py: {banned}"


def test_linux_detect_atspi_still_runs_on_tiny_capture(monkeypatch) -> None:
    monkeypatch.setattr(
        lin,
        "_atspi_clickable_candidates",
        lambda **k: [{
            "x": 80, "y": 90, "w": 64, "h": 20, "area": 1280,
            "name": "Open", "source": "atspi",
        }],
    )
    monkeypatch.setattr(lin, "_ocr_word_candidates", lambda *a, **k: [])
    cands = lin.detect_ui_candidates(b"\x00" * 40, 12, 10, 10)
    assert cands[0]["name"] == "Open"


def test_ocr_word_candidates_from_fake_tesseract(monkeypatch) -> None:
    monkeypatch.setattr(
        lin,
        "_ocr_words_from_bgr",
        lambda *a, **k: [{"text": "Submit", "x": 8, "y": 4, "w": 40, "h": 12}],
    )
    cands = lin._ocr_word_candidates(b"\x00" * 200, 30, 80, 40, max_marks=5)
    assert len(cands) == 1
    assert cands[0]["name"] == "Submit"
    assert cands[0]["x"] == 28
    assert cands[0]["y"] == 10
    assert cands[0]["source"] == "ocr"


def test_desktop_snapshot_atspi_controls(monkeypatch) -> None:
    monkeypatch.setattr(
        lin,
        "_atspi_clickable_candidates",
        lambda **k: [{
            "x": 15, "y": 25, "w": 40, "h": 16, "area": 640,
            "name": "Edit", "role": "entry",
        }],
    )
    monkeypatch.setattr(lin, "list_windows", lambda **k: [])
    els = lin.desktop_snapshot(limit=10, mode="auto")
    assert els[0]["ref"] == "c1"
    assert els[0]["name"] == "Edit"
    assert els[0]["source"] == "atspi"


def test_desktop_snapshot_auto_empty_when_no_atspi(monkeypatch) -> None:
    monkeypatch.setattr(lin, "_atspi_clickable_candidates", lambda **k: [])
    monkeypatch.setattr(
        lin,
        "list_windows",
        lambda **k: [{
            "hwnd": 1,
            "title": "Game",
            "bounds": {"left": 0, "top": 0, "right": 1920, "bottom": 1080},
        }],
    )
    assert lin.desktop_snapshot(mode="auto") == []
    wins = lin.desktop_snapshot(mode="windows")
    assert wins[0]["ref"] == "w1"
    assert wins[0]["name"] == "Game"


def test_atspi_binding_returns_list_on_non_linux() -> None:
    """host_binding.a11y_snapshot is empty / unsupported off Linux, never raises."""
    got = H.a11y_snapshot(10)
    assert isinstance(got, list)


def test_png_roundtrip_keeps_bright_box(tmp_path: Path) -> None:
    w, h = 64, 48
    raw, stride = _synthetic_frame(w, h, [(8, 8, 40, 28)])
    path = tmp_path / "box.png"
    lin._write_png_bgr(path, w, h, raw, stride)
    decoded = lin._read_png_bgr(path)
    assert decoded is not None
    got, gs, gw, gh = decoded
    assert (gw, gh, gs) == (w, h, stride)
    o = 18 * gs + 20 * 3
    assert got[o] > 200 and got[o + 1] > 200 and got[o + 2] > 200
