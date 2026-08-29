"""Native apps: UIA/DOM first, screenshot last."""

from __future__ import annotations

from remedy.core.computer.desktop_uia import structured_observe_hint


def test_controls_say_use_refs_not_pixels():
    h = structured_observe_hint(n_windows=3, n_controls=12)
    assert "cN" in h or "control" in h.lower()
    assert "guess" in h.lower() or "pixels" in h.lower()


def test_windows_only_escalates_to_screenshot_not_xy():
    h = structured_observe_hint(n_windows=4, n_controls=0)
    assert "screenshot" in h.lower() or "OCR" in h or "ocr" in h.lower()
    assert "x/y" in h.lower() or "guess" in h.lower()


def test_empty_tree_forbids_guessed_coordinates():
    h = structured_observe_hint(n_windows=0, n_controls=0)
    assert "screenshot" in h.lower()
    assert "guess" in h.lower() or "never" in h.lower()
