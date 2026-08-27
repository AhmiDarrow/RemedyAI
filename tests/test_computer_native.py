"""Native desktop power-ups: UIA read-back, window verbs, paste typing,
press-hold, act→verify evidence — the "operate any software as the user" layer.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from remedy.core.computer import desktop_win as W
from remedy.core.computer.desktop_os import native
from remedy.core.computer.executor import ComputerExecutor
from remedy.core.computer.types import (
    COMPUTER_TOOL_NAMES,
    ComputerAction,
    action_from_tool,
)

IS_WIN = sys.platform == "win32"


def _OS():
    """Desktop module the executor actually calls (win or linux)."""
    return native()


def test_press_hold_is_first_class_tool() -> None:
    assert "computer_press_hold" in COMPUTER_TOOL_NAMES
    assert action_from_tool("computer_press_hold") is ComputerAction.PRESS_HOLD


# --- executor routing (fake OS module — no live desktop needed) -----------


def _patch_win(monkeypatch, **overrides: Any):
    """Patch native() desktop functions the executor calls (win or linux)."""
    calls: dict[str, list] = {}
    mod = _OS()

    def rec(name, ret=None):
        def _f(*a, **k):
            calls.setdefault(name, []).append((a, k))
            return ret

        return _f

    defaults = {
        "foreground_window_info": rec(
            "foreground_window_info", {"hwnd": 42, "title": "Fake App"}
        ),
        "list_windows": rec(
            "list_windows",
            [{"hwnd": 42, "title": "Fake App", "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100}}],
        ),
        "manage_window": rec("manage_window", {"ok": True, "message": "minimize hwnd=42"}),
        "press_hold": rec("press_hold", {"held_ms": 2600, "x": 10, "y": 20}),
        "press_key": rec("press_key"),
        "scroll": rec("scroll"),
    }
    defaults.update(overrides)
    for name, fn in defaults.items():
        monkeypatch.setattr(mod, name, fn, raising=True)
    return calls


def test_windows_manage_modes_route(tmp_path, monkeypatch) -> None:
    calls = _patch_win(monkeypatch)
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.WINDOWS, mode="minimize", hwnd=42)
    assert out["ok"] is True
    assert "minimize" in out["message"]
    assert calls["manage_window"], "manage_window was not called"


def test_windows_manage_by_title(tmp_path, monkeypatch) -> None:
    calls = _patch_win(monkeypatch)
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.WINDOWS, mode="maximize", title="fake app")
    assert out["ok"] is True
    (a, _k) = calls["manage_window"][0]
    assert a[0] == 42  # resolved hwnd from title


def test_windows_manage_requires_window(tmp_path, monkeypatch) -> None:
    _patch_win(monkeypatch, list_windows=lambda **k: [])
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.WINDOWS, mode="close", title="nope")
    assert out["ok"] is False


def test_press_hold_desktop_branch(tmp_path, monkeypatch) -> None:
    calls = _patch_win(monkeypatch)
    monkeypatch.setattr(ComputerExecutor, "_abort_check", lambda self: False)
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.PRESS_HOLD, x=10, y=20, hold_ms=500)
    assert out["ok"] is True
    assert "Held" in out["message"]
    assert calls["press_hold"], "press_hold was not called"


def test_press_hold_needs_coordinates(tmp_path, monkeypatch) -> None:
    _patch_win(monkeypatch)
    monkeypatch.setattr(ComputerExecutor, "_abort_check", lambda self: False)
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.PRESS_HOLD)
    assert out["ok"] is False


def test_page_text_desktop_reads_via_uia(tmp_path, monkeypatch) -> None:
    _patch_win(monkeypatch)
    import remedy.core.computer.desktop_uia as U

    monkeypatch.setattr(
        U,
        "read_window_text",
        lambda hwnd, **k: {
            "title": "Fake App",
            "text": "[Name]: Ada Lovelace\nSave",
            "fields": [{"name": "Name", "role": "edit", "value": "Ada Lovelace"}],
        },
    )
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.PAGE_TEXT)
    assert out["ok"] is True
    assert "Ada Lovelace" in str(out.get("extra", {}).get("text") or out)


def test_page_text_desktop_honest_when_unreadable(tmp_path, monkeypatch) -> None:
    _patch_win(monkeypatch)
    import remedy.core.computer.desktop_uia as U

    monkeypatch.setattr(U, "read_window_text", lambda hwnd, **k: None)
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.PAGE_TEXT)
    assert out["ok"] is False
    assert "screenshot" in out["message"].lower()


def test_scroll_defaults_to_foreground_center(tmp_path, monkeypatch) -> None:
    calls = _patch_win(monkeypatch)
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.SCROLL)
    assert out["ok"] is True
    (a, k) = calls["scroll"][0]
    # center of the fake 100x100 window, not (0,0)
    assert a[0] == 50 and a[1] == 50


def test_key_result_carries_evidence(tmp_path, monkeypatch) -> None:
    _patch_win(monkeypatch)
    import remedy.core.computer.desktop_uia as U

    monkeypatch.setattr(
        U,
        "focused_element_info",
        lambda: {"name": "Editor", "role": "document", "value": "hi"},
    )
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.KEY, key="ctrl+s")
    assert out["ok"] is True
    # public_result flattens extra into the top-level dict
    assert out.get("foreground") == "Fake App"
    assert (out.get("focused") or {}).get("role") == "document"


# --- type_text_fast threshold logic ----------------------------------------


def test_type_fast_short_uses_keystrokes(monkeypatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        W, "type_text", lambda t, **k: seen.setdefault("typed", t) or len(t)
    )
    monkeypatch.setattr(
        W, "set_clipboard_text", lambda t: seen.setdefault("clip", t) or True
    )
    r = W.type_text_fast("short")
    assert r["method"] == "keystrokes"
    assert seen.get("typed") == "short"
    assert "clip" not in seen


def test_type_fast_long_pastes_and_restores_clipboard(monkeypatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(W, "type_text", lambda t, **k: len(t))
    monkeypatch.setattr(W, "get_clipboard_text", lambda: "USER_CLIP")
    clips: list[str] = []
    monkeypatch.setattr(W, "set_clipboard_text", lambda t: clips.append(t) or True)
    monkeypatch.setattr(W, "press_key", lambda k: seen.setdefault("key", k))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    long = "x" * 500
    r = W.type_text_fast(long)
    assert r["method"] == "paste"
    assert seen.get("key") == "ctrl+v"
    # pasted the payload, then restored the user's clipboard
    assert clips == [long, "USER_CLIP"]


def test_type_fast_newlines_stay_keystrokes(monkeypatch) -> None:
    monkeypatch.setattr(W, "type_text", lambda t, **k: len(t))
    r = W.type_text_fast("line\n" * 100)
    assert r["method"] == "keystrokes"


# --- live (Windows only, real desktop) --------------------------------------


@pytest.mark.skipif(not IS_WIN, reason="Windows only")
def test_clipboard_roundtrip_preserves_user_data() -> None:
    old = W.get_clipboard_text()
    try:
        assert W.set_clipboard_text("remedy-native-test") is True
        assert W.get_clipboard_text() == "remedy-native-test"
    finally:
        W.set_clipboard_text(old)
    assert W.get_clipboard_text() == old


@pytest.mark.skipif(not IS_WIN, reason="Windows only")
def test_foreground_window_info_shape() -> None:
    info = W.foreground_window_info()
    assert set(info) == {"hwnd", "title"}


# --- fast PNG writer correctness (BGR→RGB slice swap) -----------------------


def test_png_writer_swaps_bgr_to_rgb(tmp_path) -> None:
    import struct
    import zlib

    w, h = 2, 1
    stride = (w * 3 + 3) & ~3
    # BGR pixels: red(0,0,255), green(0,255,0)
    raw = bytes([0, 0, 255, 0, 255, 0]) + b"\x00" * (stride - 6)
    out = tmp_path / "px.png"
    W._write_png_bgr(out, w, h, raw, stride)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i = data.index(b"IDAT")
    ln = struct.unpack(">I", data[i - 4 : i])[0]
    dec = zlib.decompress(data[i + 4 : i + 4 + ln])
    # scanline: filter byte + RGB pixels
    assert list(dec[1:7]) == [255, 0, 0, 0, 255, 0]  # red, green in RGB


# --- Set-of-Mark drawing ----------------------------------------------------


def test_set_of_mark_draws_box_and_digit() -> None:
    w, h = 60, 40
    stride = (w * 3 + 3) & ~3
    buf = bytearray(b"\x00" * stride * h)
    W._draw_marks_on_bgr(buf, stride, w, h, [{"n": 3, "x": 5, "y": 5}])
    magenta = sum(
        1
        for y in range(h)
        for x in range(w)
        if tuple(buf[y * stride + x * 3 : y * stride + x * 3 + 3]) == (255, 0, 255)
    )
    white = sum(
        1
        for y in range(h)
        for x in range(w)
        if tuple(buf[y * stride + x * 3 : y * stride + x * 3 + 3]) == (255, 255, 255)
    )
    assert magenta > 0 and white > 0  # label box + digit pixels


def test_screenshot_mark_flag_builds_legend(tmp_path, monkeypatch) -> None:
    ex = ComputerExecutor(home_dir=tmp_path)
    ex.bridge.set_last_elements(
        [
            {"ref": "c1", "name": "OK", "x": 100, "y": 200},
            {"ref": "c2", "name": "Cancel", "x": 300, "y": 200},
        ],
        target="desktop",
    )
    captured: dict[str, Any] = {}
    mod = _OS()

    def fake_shot(path=None, *, marks=None):
        captured["marks"] = marks
        return {"path": "x.png", "width": 800, "height": 600, "origin": {"x": 0, "y": 0}}

    monkeypatch.setattr(mod, "screenshot_png", fake_shot)
    monkeypatch.setattr(ComputerExecutor, "_see_if_needed", lambda self, r, **k: r)
    out = ex._run_desktop(ComputerAction.SCREENSHOT, mark=True)
    assert out["ok"] is True
    assert captured["marks"] and captured["marks"][0]["n"] == 1
    legend = out.get("marks") or []
    assert {m["ref"] for m in legend} == {"c1", "c2"}


# --- UAC / system-prompt guard ----------------------------------------------


def test_uac_blocks_input_actions(tmp_path, monkeypatch) -> None:
    mod = _OS()
    monkeypatch.setattr(
        mod,
        "detect_system_prompt",
        lambda: {"blocked": True, "kind": "uac", "message": "UAC is up — approve it"},
    )
    ex = ComputerExecutor(home_dir=tmp_path)
    for act in (ComputerAction.CLICK, ComputerAction.TYPE, ComputerAction.KEY):
        out = ex._run_desktop(act, x=10, y=10, text="secret", key="enter")
        assert out["ok"] is False
        assert out.get("blocked") == "uac"


def test_no_system_prompt_allows_actions(tmp_path, monkeypatch) -> None:
    mod = _OS()
    monkeypatch.setattr(
        mod, "detect_system_prompt", lambda: {"blocked": False, "kind": "", "message": ""}
    )
    monkeypatch.setattr(mod, "press_key", lambda k: None)
    monkeypatch.setattr(mod, "foreground_window_info", lambda: {"hwnd": 1, "title": "App"})
    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_desktop(ComputerAction.KEY, key="enter")
    assert out["ok"] is True


@pytest.mark.skipif(not IS_WIN, reason="Windows only")
def test_detect_system_prompt_clean_when_no_uac() -> None:
    r = W.detect_system_prompt()
    assert r["blocked"] in (True, False)  # shape; usually False in CI/dev
    assert set(r) == {"blocked", "kind", "message"}


# --- pixel candidate detection (Set-of-Mark without an a11y tree) -----------


def _synthetic_frame(width: int, height: int, boxes: list[tuple]) -> tuple:
    """Dark frame with bright filled boxes → strong edges for the detector."""
    stride = (width * 3 + 3) & ~3
    buf = bytearray(b"\x10" * (stride * height))
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1):
            o = y * stride
            for x in range(x0, x1):
                p = o + x * 3
                buf[p] = buf[p + 1] = buf[p + 2] = 240
    return bytes(buf), stride


def test_detect_ui_candidates_finds_drawn_boxes() -> None:
    w, h = 400, 300
    raw, stride = _synthetic_frame(w, h, [(40, 40, 160, 100), (220, 180, 360, 260)])
    cands = W.detect_ui_candidates(raw, stride, w, h)
    assert len(cands) >= 2
    # each candidate center should fall inside one of the drawn boxes
    def inside(c, box):
        x0, y0, x1, y1 = box
        return x0 - 12 <= c["x"] <= x1 + 12 and y0 - 12 <= c["y"] <= y1 + 12

    boxes = [(40, 40, 160, 100), (220, 180, 360, 260)]
    assert any(inside(cands[0], b) for b in boxes)
    assert any(inside(cands[1], b) for b in boxes)


def test_detect_ui_candidates_ignores_blank_frame() -> None:
    w, h = 300, 200
    raw, stride = _synthetic_frame(w, h, [])
    assert W.detect_ui_candidates(raw, stride, w, h) == []


def test_detect_ui_candidates_respects_max() -> None:
    w, h = 500, 400
    boxes = [(x, y, x + 30, y + 20) for x in range(20, 440, 60) for y in range(20, 340, 60)]
    raw, stride = _synthetic_frame(w, h, boxes)
    assert len(W.detect_ui_candidates(raw, stride, w, h, max_marks=5)) <= 5


def test_detect_ui_candidates_tiny_frame_safe() -> None:
    assert W.detect_ui_candidates(b"", 0, 4, 4) == []


def test_screenshot_marks_fall_back_to_pixels(tmp_path, monkeypatch) -> None:
    """No a11y elements → marks come from pixel detection, with x/y in legend."""
    ex = ComputerExecutor(home_dir=tmp_path)
    ex.bridge.set_last_elements([], target="desktop")
    mod = _OS()
    monkeypatch.setattr(mod, "desktop_snapshot", lambda **k: [])
    monkeypatch.setattr(
        mod, "_capture_virtual_screen", lambda: (b"\x00" * 300, 30, 10, 10, 5, 7)
    )
    monkeypatch.setattr(
        mod,
        "detect_ui_candidates",
        lambda raw, stride, w, h, **k: [{"x": 100, "y": 50, "w": 40, "h": 20, "area": 800}],
    )
    captured: dict[str, Any] = {}

    def fake_shot(path=None, *, marks=None):
        captured["marks"] = marks
        return {"path": "x.png", "width": 800, "height": 600, "origin": {"x": 5, "y": 7}}

    monkeypatch.setattr(mod, "screenshot_png", fake_shot)
    monkeypatch.setattr(ComputerExecutor, "_see_if_needed", lambda self, r, **k: r)
    out = ex._run_desktop(ComputerAction.SCREENSHOT, mark=True)
    assert out["ok"] is True
    # screen coords = candidate + capture origin
    assert captured["marks"] == [{"n": 1, "x": 105, "y": 57}]
    legend = out.get("marks") or []
    assert legend[0]["source"] == "pixels"
    assert (legend[0]["x"], legend[0]["y"]) == (105, 57)
    assert "pixel-detected" in out["message"]


# --- desktop app playbook is present in guidance ---------------------------


def test_guidance_has_desktop_playbook() -> None:
    from remedy.core.computer.guidance import COMPUTER_USE_SYSTEM_ADDENDUM

    assert "Desktop app playbook" in COMPUTER_USE_SYSTEM_ADDENDUM
    for route in ("ctrl+l", "alt+n", "Name Box", "ctrl+z"):
        assert route in COMPUTER_USE_SYSTEM_ADDENDUM


def test_a_wedged_computer_host_can_still_be_abandoned():
    """``stop`` keeps the handle when the worker outlives its join, so a second
    worker cannot land on the same job queue. ``force`` is the escape hatch for
    a worker that will never return, and must stay available."""
    import threading

    from remedy.core.computer.cli_host import LocalComputerHost

    host = LocalComputerHost()
    release = threading.Event()
    host._thread = threading.Thread(target=release.wait, daemon=True)
    host._thread.start()
    try:
        assert host.stop(timeout=0.05) is False
        assert host.running, "running must not lie while the worker is alive"

        assert host.stop(timeout=0.05, force=True) is True
        assert not host.running
    finally:
        release.set()


def test_stopping_a_host_that_never_started_succeeds():
    from remedy.core.computer.cli_host import LocalComputerHost

    assert LocalComputerHost().stop() is True
