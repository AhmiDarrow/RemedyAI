"""Native apps: UIA/DOM first, screenshot last."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.computer.desktop_uia import preferred_click_action, structured_observe_hint


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


def test_click_prefers_invoke_then_center(monkeypatch):
    from remedy.core.computer.executor import ComputerExecutor

    calls: list[str] = []

    def fake_action(hwnd, name, *, role="", action="invoke", text=""):
        calls.append(action)
        return {"ok": True, "message": f"{action} ok"}

    monkeypatch.setattr(
        "remedy.core.computer.desktop_uia.element_action", fake_action
    )
    ex = ComputerExecutor.__new__(ComputerExecutor)
    win = SimpleNamespace(
        desktop_snapshot=lambda **k: [],
        click_element=lambda *a, **k: calls.append("center"),
    )
    el = {"hwnd": 42, "name": "Save", "role": "button", "ref": "c1", "x": 8, "y": 8}
    out = ComputerExecutor._desktop_click_element(ex, win, el, button="left", clicks=1)
    assert out.get("_method") == "uia_invoke"
    assert calls == ["invoke"]
    assert preferred_click_action("checkbox") == "toggle"
