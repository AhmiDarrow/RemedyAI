"""Linux detect_ui_candidates: AT-SPI / OCR / pixel boxes; backends faked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from remedy.core.computer import desktop_linux as lin


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


class _Rect:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x, self.y, self.width, self.height = x, y, width, height


class _Comp:
    def __init__(self, rect: _Rect) -> None:
        self._rect = rect

    def get_extents(self, _coord: int = 0) -> _Rect:
        return self._rect


class _Acc:
    def __init__(
        self,
        name: str,
        role: str,
        *,
        extents: tuple[int, int, int, int] | None = None,
        children: list[_Acc] | None = None,
    ) -> None:
        self._name = name
        self._role = role
        self._extents = extents
        self._children = children or []

    def get_name(self) -> str:
        return self._name

    def get_role_name(self) -> str:
        return self._role

    def get_child_count(self) -> int:
        return len(self._children)

    def get_child_at_index(self, i: int) -> _Acc:
        return self._children[i]

    def get_component_iface(self) -> _Comp | None:
        if self._extents is None:
            return None
        return _Comp(_Rect(*self._extents))


def test_walk_atspi_tree_collects_clickable_fakes() -> None:
    ok = _Acc("OK", "push button", extents=(10, 20, 80, 24))
    cancel = _Acc("Cancel", "push button", extents=(100, 20, 80, 24))
    filler = _Acc("", "filler", extents=(0, 0, 800, 600))
    frame = _Acc("App", "frame", extents=(0, 0, 800, 600), children=[ok, cancel, filler])
    desktop = _Acc("desktop", "desktop frame", children=[frame])
    cands = lin._walk_atspi_tree(desktop, max_marks=10)
    names = {c["name"] for c in cands}
    assert "OK" in names
    assert "Cancel" in names
    assert "App" not in names
    assert all(c["source"] == "atspi" for c in cands)


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

def test_png_roundtrip_keeps_bright_box(tmp_path: Path) -> None:
    w, h = 64, 48
    raw, stride = _synthetic_frame(w, h, [(8, 8, 40, 28)])
    path = tmp_path / "box.png"
    lin._write_png_bgr(path, w, h, raw, stride)
    decoded = lin._read_png_bgr(path)
    assert decoded is not None
    got, gs, gw, gh = decoded
    assert (gw, gh, gs) == (w, h, stride)
    # center of the bright box stays bright after RGB roundtrip
    o = 18 * gs + 20 * 3
    assert got[o] > 200 and got[o + 1] > 200 and got[o + 2] > 200
