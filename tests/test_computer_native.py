"""Native desktop power-ups: UIA read-back, window verbs, paste typing,
press-hold, act→verify evidence — the "operate any software as the user" layer.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from remedy.core.computer import desktop_win as W
from remedy.core.computer.executor import ComputerExecutor
from remedy.core.computer.types import (
    COMPUTER_TOOL_NAMES,
    ComputerAction,
    action_from_tool,
)

IS_WIN = sys.platform == "win32"


def test_press_hold_is_first_class_tool() -> None:
    assert "computer_press_hold" in COMPUTER_TOOL_NAMES
    assert action_from_tool("computer_press_hold") is ComputerAction.PRESS_HOLD


# --- executor routing (fake win module — no live desktop needed) -----------


def _patch_win(monkeypatch, **overrides: Any):
    """Patch desktop_win functions the executor calls."""
    calls: dict[str, list] = {}

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
        monkeypatch.setattr(W, name, fn, raising=True)
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
    r = W.type_text_fast(("line\n" * 100))
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
