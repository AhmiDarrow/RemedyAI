"""UI Automation public API after the Zig COM cutover.

What breaks if this code is wrong: ``desktop_uia`` is how Remedy *sees* a native
window. Every fact it returns is handed to the model, which then acts on the
owner's real desktop. Soft misses must stay ``None`` / ``{"ok": False}`` — never
an exception, and never a fabricated element.

The production boundary is ``remedy.core.computer.host_binding`` (Zig COM). Tests
below mock that boundary via ``tests.harness.fake_host_binding`` — a reference
walker that keeps the same clamps, clipping, preferred_only, offscreen, and
depth/children limits the product documents. Live read-only smoke (skipped when
UIA is unavailable) proves the real DLL still returns the same shapes. Nothing
here injects input or writes the clipboard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from remedy.core.computer import desktop_uia
from remedy.core.computer import host_binding as H
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError
from tests.harness.fake_host_binding import install_fake_host_uia, uia_element
from tests.harness.fake_win32 import (
    FakeTogglePattern,
    FakeUIAutomation,
    UIA_BoundingRectanglePropertyId,
    UIA_ControlTypePropertyId,
    UIA_IsEnabledPropertyId,
    UIA_NamePropertyId,
    UIA_TogglePatternId,
    UIA_ValueValuePropertyId,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "uia"


# ------------------------------------------------------------------ helpers --


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _boom(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("the COM object went away")


def _tree(*children: Any, name: str = "App", hwnd: int = 101, **kw: Any) -> FakeUIAutomation:
    win = uia_element(name, "window", hwnd=hwnd, children=list(children), **kw)
    return FakeUIAutomation(uia_element("Desktop", "pane", children=[win]))


def _nest(depth: int, leaf: Any) -> Any:
    node = leaf
    for _ in range(max(0, depth - 1)):
        node = uia_element("", "group", children=[node])
    return node


_ALL_READS = [
    lambda: desktop_uia.read_window_text(101),
    lambda: desktop_uia.focused_element_info(),
    lambda: desktop_uia.uia_control_snapshot(hwnd=101),
]


# ------------------------------------------------------- pure helpers --------


def test_structured_observe_hint_prefers_controls_then_windows():
    assert "cN" in desktop_uia.structured_observe_hint(n_windows=2, n_controls=3)
    assert "Window list only" in desktop_uia.structured_observe_hint(n_windows=2, n_controls=0)
    assert "No structured controls" in desktop_uia.structured_observe_hint(
        n_windows=0, n_controls=0
    )


def test_preferred_click_action_toggle_vs_invoke():
    assert desktop_uia.preferred_click_action("checkbox") == "toggle"
    assert desktop_uia.preferred_click_action("switch") == "toggle"
    assert desktop_uia.preferred_click_action("radiobutton") == "toggle"
    assert desktop_uia.preferred_click_action("button") == "invoke"
    assert desktop_uia.preferred_click_action("menuitem") == "invoke"
    assert desktop_uia.preferred_click_action("") == "invoke"


# ----------------------------------------------------- soft failure paths ----


def test_uia_is_unavailable_off_windows():
    with install_fake_host_uia(FakeUIAutomation(), platform="linux"):
        assert desktop_uia.uia_available() is False
        assert desktop_uia.read_window_text(101) is None
        assert desktop_uia.focused_element_info() is None
        assert desktop_uia.uia_control_snapshot(hwnd=101) is None
        got = desktop_uia.element_action(101, "Save", action="invoke")
        assert got["ok"] is False
        assert "not found" in got["message"]


@pytest.mark.parametrize("call", _ALL_READS)
def test_every_read_returns_none_off_windows(call):
    with install_fake_host_uia(_tree(), platform="linux"):
        assert call() is None


def test_reads_return_none_when_native_core_is_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        H,
        "uia_available",
        lambda: (_ for _ in ()).throw(NativeRuntimeUnavailableError("missing")),
    )
    monkeypatch.setattr(
        H,
        "uia_read_window_text",
        lambda *_a, **_k: (_ for _ in ()).throw(NativeRuntimeUnavailableError("missing")),
    )
    monkeypatch.setattr(
        H,
        "uia_focused_element",
        lambda: (_ for _ in ()).throw(NativeRuntimeUnavailableError("missing")),
    )
    monkeypatch.setattr(
        H,
        "uia_control_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(NativeRuntimeUnavailableError("missing")),
    )
    monkeypatch.setattr(
        H,
        "uia_element_action",
        lambda *_a, **_k: (_ for _ in ()).throw(NativeRuntimeUnavailableError("missing")),
    )
    assert desktop_uia.uia_available() is False
    assert desktop_uia.read_window_text(101) is None
    assert desktop_uia.focused_element_info() is None
    assert desktop_uia.uia_control_snapshot(hwnd=101) is None
    got = desktop_uia.element_action(101, "Save")
    assert got["ok"] is False


def test_host_errors_on_reads_collapse_to_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        H,
        "uia_read_window_text",
        lambda *_a, **_k: (_ for _ in ()).throw(
            H.HostError("uia_read_window_text", H.STATUS_OPERATION_FAILED)
        ),
    )
    monkeypatch.setattr(
        H,
        "uia_focused_element",
        lambda: (_ for _ in ()).throw(
            H.HostError("uia_focused_element", H.STATUS_OPERATION_FAILED)
        ),
    )
    monkeypatch.setattr(
        H,
        "uia_control_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(
            H.HostError("uia_control_snapshot", H.STATUS_OPERATION_FAILED)
        ),
    )
    assert desktop_uia.read_window_text(101) is None
    assert desktop_uia.focused_element_info() is None
    assert desktop_uia.uia_control_snapshot(hwnd=101) is None


def test_read_window_text_rejects_hwnd_zero_without_calling_native(
    monkeypatch: pytest.MonkeyPatch,
):
    called = {"n": 0}

    def _track(*_a: Any, **_k: Any) -> None:
        called["n"] += 1
        raise AssertionError("native must not be called for hwnd=0")

    monkeypatch.setattr(H, "uia_read_window_text", _track)
    assert desktop_uia.read_window_text(0) is None
    assert called["n"] == 0


def test_element_action_is_refused_rather_than_raised_when_host_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    # Binding tests force win32: desktop_uia short-circuits UIA off Windows.
    monkeypatch.setattr(desktop_uia.sys, "platform", "win32")
    monkeypatch.setattr(
        H,
        "uia_element_action",
        lambda *_a, **_k: (_ for _ in ()).throw(
            H.HostError("uia_element_action", H.STATUS_OPERATION_FAILED)
        ),
    )
    got = desktop_uia.element_action(101, "Save", action="invoke")
    assert got["ok"] is False
    assert "failed" in got["message"]


# ----------------------------------------------------- binding passthrough ---


def test_binding_forwards_snapshot_args(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(desktop_uia.sys, "platform", "win32")
    seen: dict[str, Any] = {}

    def _fake(hwnd: int, max_elements: int, preferred_only: bool):
        seen.update(hwnd=hwnd, max_elements=max_elements, preferred_only=preferred_only)
        return [
            {
                "ref": "c1",
                "tag": "button",
                "role": "button",
                "name": "Save",
                "x": 1,
                "y": 2,
                "w": 3,
                "h": 4,
                "hwnd": hwnd,
                "bounds": {"left": 0, "top": 0, "right": 3, "bottom": 4},
                "uia": True,
            }
        ]

    monkeypatch.setattr(H, "uia_control_snapshot", _fake)
    got = desktop_uia.uia_control_snapshot(hwnd=101, max_elements=5, preferred_only=False)
    assert seen == {"hwnd": 101, "max_elements": 5, "preferred_only": False}
    assert got is not None and got[0]["name"] == "Save"


def test_binding_maps_desktop_root_hwnd_none_to_zero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(desktop_uia.sys, "platform", "win32")
    seen: dict[str, Any] = {}

    def _fake(hwnd: int, max_elements: int, preferred_only: bool):
        seen["hwnd"] = hwnd
        return None

    monkeypatch.setattr(H, "uia_control_snapshot", _fake)
    assert desktop_uia.uia_control_snapshot() is None
    assert seen["hwnd"] == 0


def test_element_action_forwards_and_preserves_dict(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(desktop_uia.sys, "platform", "win32")
    monkeypatch.setattr(
        H,
        "uia_element_action",
        lambda hwnd, name, *, role="", action="invoke", text="": {
            "ok": True,
            "message": f"Invoked button '{name}'",
        },
    )
    got = desktop_uia.element_action(101, "Save", role="button", action="invoke")
    assert got == {"ok": True, "message": "Invoked button 'Save'"}


def test_desktop_uia_never_invents_elements_when_host_returns_none(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(H, "uia_control_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(H, "uia_read_window_text", lambda *_a, **_k: None)
    monkeypatch.setattr(H, "uia_focused_element", lambda: None)
    assert desktop_uia.uia_control_snapshot(hwnd=101) is None
    assert desktop_uia.read_window_text(101) is None
    assert desktop_uia.focused_element_info() is None


# ----------------------------------------------------- read_window_text ------


def test_a_document_contributes_its_text_pattern_when_it_has_no_value():
    doc = uia_element("Report", "document", text="chapter one")
    with install_fake_host_uia(_tree(doc)):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert "[Report]: chapter one" in got["text"]
    assert got["fields"] == [{"name": "Report", "role": "document", "value": "chapter one"}]


def test_a_value_property_that_raises_falls_back_to_the_value_pattern():
    box = uia_element("Query", "edit", value="pinned", raise_on={UIA_ValueValuePropertyId})
    with install_fake_host_uia(_tree(box)):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["fields"] == [{"name": "Query", "role": "edit", "value": "pinned"}]


def test_the_value_property_wins_over_the_text_pattern():
    both = uia_element("Both", "combobox", value="from-value", text="from-text")
    with install_fake_host_uia(_tree(both)):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["fields"][0]["value"] == "from-value"


def test_an_empty_edit_is_listed_as_a_field_but_adds_no_text():
    with install_fake_host_uia(_tree(uia_element("Search", "edit"))):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["fields"] == [{"name": "Search", "role": "edit", "value": ""}]
    assert got["text"] == ""


def test_an_unnamed_edit_with_no_value_contributes_nothing_at_all():
    with install_fake_host_uia(_tree(uia_element("", "edit"))):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["fields"] == []
    assert got["text"] == ""


def test_a_whitespace_only_value_is_kept_as_a_field_but_not_as_text():
    with install_fake_host_uia(_tree(uia_element("", "edit", value="   "))):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["fields"] == [{"name": "edit", "role": "edit", "value": "   "}]
    assert got["text"] == ""


def test_a_control_whose_type_is_unreadable_is_skipped_but_its_children_are_read():
    broken = uia_element(
        "Mystery",
        "group",
        raise_on={UIA_ControlTypePropertyId},
        children=[uia_element("Inner label", "text")],
    )
    with install_fake_host_uia(_tree(broken)):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert "Mystery" not in got["text"]
    assert "Inner label" in got["text"]


def test_reading_a_window_uia_does_not_know_yields_none():
    with install_fake_host_uia(_tree(uia_element("Hello", "text"))):
        assert desktop_uia.read_window_text(999) is None


def test_a_control_whose_name_is_unreadable_is_skipped_but_its_children_are_read():
    broken = uia_element(
        "Mystery",
        "text",
        raise_on={UIA_NamePropertyId},
        children=[uia_element("Inner label", "text")],
    )
    with install_fake_host_uia(_tree(broken)):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["text"] == "Inner label"


def test_a_repeat_that_is_not_consecutive_is_kept():
    tree = _tree(
        uia_element("OK", "button"),
        uia_element("Cancel", "button"),
        uia_element("OK", "button"),
    )
    with install_fake_host_uia(tree):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["text"].splitlines() == ["OK", "Cancel", "OK"]


def test_the_window_title_comes_from_the_root_element():
    with install_fake_host_uia(_tree(name="Untitled - Notepad")):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["title"] == "Untitled - Notepad"
    assert got["text"] == ""


def test_the_text_is_cut_to_max_chars():
    tree = _tree(*[uia_element(f"{i}" + "x" * 29, "text") for i in range(3)])
    with install_fake_host_uia(tree):
        got = desktop_uia.read_window_text(101, max_chars=50)
    assert got is not None
    assert len(got["text"]) == 50


def test_a_tiny_max_chars_still_walks_the_first_kilobyte_of_fields():
    tree = _tree(uia_element("Query", "edit", value="a long stored value"))
    with install_fake_host_uia(tree):
        got = desktop_uia.read_window_text(101, max_chars=1)
    assert got is not None
    assert len(got["text"]) == 1
    assert got["fields"] == [{"name": "Query", "role": "edit", "value": "a long stored value"}]


def test_the_walk_stops_once_the_character_budget_is_spent():
    labels = [f"{i:03d}" + "x" * 197 for i in range(20)]
    tree = _tree(*[uia_element(label, "text") for label in labels])
    with install_fake_host_uia(tree):
        got = desktop_uia.read_window_text(101, max_chars=1000)
    assert got is not None
    lines = got["text"].splitlines()
    assert [ln[:3] for ln in lines] == ["000", "001", "002", "003", "004"]
    assert "005" not in got["text"]


def test_only_forty_fields_are_reported():
    tree = _tree(*[uia_element(f"e{i:02d}", "edit", value="v") for i in range(50)])
    with install_fake_host_uia(tree):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert len(got["fields"]) == 40
    assert got["fields"][0]["name"] == "e00"


def test_a_long_field_name_and_value_are_clipped():
    tree = _tree(uia_element("n" * 200, "edit", value="v" * 9000))
    with install_fake_host_uia(tree):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    field = got["fields"][0]
    assert len(field["name"]) == 80
    assert len(field["value"]) == 4000


@pytest.mark.parametrize(("depth", "visible"), [(14, True), (15, False)])
def test_the_text_walk_stops_below_fifteen_levels_of_nesting(depth, visible):
    leaf = uia_element("DEEPEST", "text")
    with install_fake_host_uia(_tree(_nest(depth, leaf))):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert ("DEEPEST" in got["text"]) is visible


def test_only_sixty_children_of_one_element_are_read():
    tree = _tree(*[uia_element(f"L{i:03d}", "text") for i in range(70)])
    with install_fake_host_uia(tree):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert len(got["text"].splitlines()) == 60


def test_an_automation_object_that_throws_yields_none_rather_than_propagating():
    tree = _tree(uia_element("Hello", "text"))
    tree.ElementFromHandle = _boom  # type: ignore[method-assign]
    with install_fake_host_uia(tree):
        assert desktop_uia.read_window_text(101) is None


def test_reading_a_window_never_sends_input():
    tree = _tree(uia_element("Save", "button"), uia_element("Query", "edit", value="x"))
    with install_fake_host_uia(tree) as fake:
        desktop_uia.read_window_text(101)
        desktop_uia.focused_element_info()
        desktop_uia.uia_control_snapshot(hwnd=101)
    assert fake.input_calls == []
    assert fake.clipboard_writes == []


# --------------------------------------------------- focused_element_info ----


def test_a_focused_element_that_throws_yields_none():
    tree = _tree()
    tree.GetFocusedElement = _boom  # type: ignore[method-assign]
    with install_fake_host_uia(tree):
        assert desktop_uia.focused_element_info() is None


def test_focused_element_name_and_value_are_clipped():
    tree = _tree()
    tree.focused = uia_element("n" * 300, "edit", value="v" * 900)
    with install_fake_host_uia(tree):
        got = desktop_uia.focused_element_info()
    assert got is not None
    assert len(got["name"]) == 120
    assert len(got["value"]) == 400


def test_a_focused_control_of_an_unknown_type_is_reported_as_unknown():
    tree = _tree()
    tree.focused = uia_element("Odd", control_type=99999)
    with install_fake_host_uia(tree):
        got = desktop_uia.focused_element_info()
    assert got is not None
    assert got["role"] == "unknown"


def test_a_focused_control_with_no_value_reports_an_empty_string():
    tree = _tree()
    tree.focused = uia_element("Save", "button")
    with install_fake_host_uia(tree):
        assert desktop_uia.focused_element_info() == {
            "name": "Save",
            "role": "button",
            "value": "",
        }


# ---------------------------------------------------------- element_action ---


def test_element_action_without_a_window_handle_is_refused():
    with install_fake_host_uia(_tree(uia_element("Save", "button", invokable=True))):
        got = desktop_uia.element_action(0, "Save", action="invoke")
    assert got["ok"] is False
    assert "not found in hwnd=0" in got["message"]


def test_element_action_on_a_window_uia_does_not_know_is_refused():
    tree = _tree(uia_element("Save", "button", invokable=True))
    with install_fake_host_uia(tree):
        got = desktop_uia.element_action(999, "Save", action="invoke")
    assert got["ok"] is False
    assert "not found in hwnd=999" in got["message"]


def test_a_search_that_throws_is_reported_as_not_found_not_as_a_crash():
    tree = _tree(uia_element("Save", "button", invokable=True))
    tree.root.children[0].FindAll = _boom  # type: ignore[method-assign]
    with install_fake_host_uia(tree):
        got = desktop_uia.element_action(101, "Save", action="invoke")
    assert got["ok"] is False
    assert "not found" in got["message"]


def test_the_requested_role_is_a_preference_not_a_filter():
    button = uia_element("Save", "button", invokable=True)
    with install_fake_host_uia(_tree(button)):
        got = desktop_uia.element_action(101, "Save", role="checkbox", action="invoke")
    assert got["ok"] is True
    assert button.actions == [("invoke",)]


def test_only_the_first_twenty_same_named_matches_are_considered():
    kids = [uia_element("Dup", "button", invokable=True) for _ in range(25)]
    wanted = uia_element("Dup", "edit", value="")
    kids[22] = wanted
    with install_fake_host_uia(_tree(*kids)):
        got = desktop_uia.element_action(101, "Dup", role="edit", action="invoke")
    assert got["ok"] is True, "the fallback match is used, not nothing"
    assert kids[0].actions == [("invoke",)]
    assert wanted.actions == [], "match 23 is past the cap and is never reached"


def test_set_value_stringifies_a_non_string_argument():
    box = uia_element("Amount", "edit", value="")
    with install_fake_host_uia(_tree(box)):
        got = desktop_uia.element_action(101, "Amount", action="set_value", text=1234)  # type: ignore[arg-type]
    assert got["ok"] is True
    assert got["verified"] is True
    assert box.value == "1234"


def test_clearing_a_field_with_an_empty_value_is_allowed_and_verified():
    box = uia_element("Amount", "edit", value="99")
    with install_fake_host_uia(_tree(box)):
        got = desktop_uia.element_action(101, "Amount", action="set_value", text="")
    assert got["verified"] is True
    assert "verified" in got["message"]
    assert "chars" not in got["message"]
    assert box.value == ""


def test_a_control_of_an_unknown_type_is_labelled_control_not_left_blank():
    odd = uia_element("Widget", control_type=99999)
    with install_fake_host_uia(_tree(odd)):
        got = desktop_uia.element_action(101, "Widget", action="invoke")
    assert got == {"ok": False, "message": "control 'Widget' is not invokable — use a click"}


class _StuckToggle(FakeTogglePattern):
    @property
    def CurrentToggleState(self) -> int:  # noqa: N802 - COM spelling
        return 7


class _UnreadableToggle(FakeTogglePattern):
    @property
    def CurrentToggleState(self) -> int:  # noqa: N802 - COM spelling
        raise RuntimeError("state unavailable")


@pytest.mark.parametrize(
    ("pattern_cls", "tail"),
    [(_StuckToggle, "→ ?"), (_UnreadableToggle, "→ ")],
    ids=["undefined-state", "unreadable-state"],
)
def test_a_toggle_whose_state_cannot_be_read_still_reports_the_toggle(pattern_cls, tail):
    box = uia_element("Word wrap", "checkbox", toggle="off")
    box.patterns[UIA_TogglePatternId] = pattern_cls(UIA_TogglePatternId, box)
    with install_fake_host_uia(_tree(box)):
        got = desktop_uia.element_action(101, "Word wrap", action="toggle")
    assert got["ok"] is True
    assert got["message"].endswith(tail)
    assert box.actions == [("toggle",)]


@pytest.mark.parametrize(
    ("action", "fragment"),
    [
        ("set_value", "no value pattern"),
        ("toggle", "not toggleable"),
        ("scroll_into_view", "no scroll-item pattern"),
        ("teleport", "Unknown UIA action"),
    ],
)
def test_an_action_the_control_cannot_perform_is_named_in_the_refusal(action, fragment):
    button = uia_element("Save", "button", invokable=True)
    with install_fake_host_uia(_tree(button)):
        got = desktop_uia.element_action(101, "Save", action=action, text="x")
    assert got["ok"] is False
    assert fragment in got["message"]
    assert button.actions == []


def test_a_pattern_that_dies_mid_action_is_reported_with_its_error():
    item = uia_element("Below", "listitem", offscreen=True, scrollable=True)
    item.patterns[10017].error = RuntimeError("element is gone")
    with install_fake_host_uia(_tree(item)):
        got = desktop_uia.element_action(101, "Below", action="scroll_into_view")
    assert got["ok"] is False
    assert "element is gone" in got["message"]
    assert item.offscreen is True


def test_scroll_into_view_reports_the_control_it_moved():
    item = uia_element("Below", "listitem", offscreen=True, scrollable=True)
    with install_fake_host_uia(_tree(item)):
        got = desktop_uia.element_action(101, "Below", action="scroll_into_view")
    assert got == {"ok": True, "message": "Scrolled listitem 'Below' into view"}
    assert item.offscreen is False


def test_a_refused_action_leaves_the_control_untouched():
    box = uia_element("Query", "edit", value="original")
    with install_fake_host_uia(_tree(box)):
        got = desktop_uia.element_action(101, "Query", action="invoke")
    assert got["ok"] is False
    assert box.value == "original"
    assert box.actions == []


# ----------------------------------------------------- uia_control_snapshot --


def test_the_snapshot_is_none_for_a_window_handle_uia_does_not_know():
    with install_fake_host_uia(_tree(uia_element("Save", "button"))):
        assert desktop_uia.uia_control_snapshot(hwnd=999) is None


def test_a_host_failure_yields_none_rather_than_an_exception(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        H,
        "uia_control_snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(
            H.HostError("uia_control_snapshot", H.STATUS_OPERATION_FAILED)
        ),
    )
    assert desktop_uia.uia_control_snapshot(hwnd=101) is None


def test_the_desktop_walk_covers_named_top_level_windows():
    win = uia_element("Notepad", "window", hwnd=101, children=[uia_element("Save", "button")])
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[win]))
    with install_fake_host_uia(tree):
        got = desktop_uia.uia_control_snapshot()
    assert got is not None
    assert [e["name"] for e in got] == ["Notepad", "Save"]
    assert {e["hwnd"] for e in got} == {101}


def test_an_unnamed_top_level_window_is_skipped_along_with_its_children():
    named = uia_element("Notepad", "window", hwnd=101, children=[uia_element("Save", "button")])
    ghost = uia_element("", "window", hwnd=102, children=[uia_element("Hidden", "button")])
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[named, ghost]))
    with install_fake_host_uia(tree):
        names = {e["name"] for e in (desktop_uia.uia_control_snapshot() or [])}
    assert "Hidden" not in names


def test_a_top_level_window_whose_name_cannot_be_read_is_skipped():
    named = uia_element("Notepad", "window", hwnd=101, children=[uia_element("Save", "button")])
    broken = uia_element(
        "Secret",
        "window",
        hwnd=102,
        raise_on={UIA_NamePropertyId},
        children=[uia_element("Hidden", "button")],
    )
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[named, broken]))
    with install_fake_host_uia(tree):
        names = {e["name"] for e in (desktop_uia.uia_control_snapshot() or [])}
    assert names == {"Notepad", "Save"}


def test_the_desktop_walk_stops_at_max_elements_between_windows():
    windows = [
        uia_element(f"W{i}", "window", hwnd=100 + i, children=[uia_element("Save", "button")])
        for i in range(3)
    ]
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=windows))
    with install_fake_host_uia(tree):
        got = desktop_uia.uia_control_snapshot(max_elements=1)
    assert got is not None
    assert [e["name"] for e in got] == ["W0"]


def test_a_window_whose_native_handle_is_unreadable_still_yields_its_controls():
    win = uia_element("Notepad", "window", hwnd=101, children=[uia_element("Save", "button")])
    win.hwnd = "not-a-handle"  # type: ignore[assignment]
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[win]))
    with install_fake_host_uia(tree):
        got = desktop_uia.uia_control_snapshot()
    assert got is not None
    assert [e["name"] for e in got] == ["Notepad", "Save"]
    assert all(e["hwnd"] is None for e in got), "an unknown handle is None, never a guess"


def test_when_the_desktop_enumeration_fails_the_root_itself_is_returned():
    root = uia_element("Desktop", "pane", children=[uia_element("Notepad", "window", hwnd=101)])
    root.FindAll = _boom  # type: ignore[method-assign]
    with install_fake_host_uia(FakeUIAutomation(root)):
        got = desktop_uia.uia_control_snapshot()
    assert got is not None
    assert [e["name"] for e in got] == ["Desktop"]


def test_an_element_whose_children_cannot_be_listed_is_still_reported():
    button = uia_element("Save", "button")
    button.FindAll = _boom  # type: ignore[method-assign]
    with install_fake_host_uia(_tree(button)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert "Save" in {e["name"] for e in got}


@pytest.mark.parametrize(
    ("asked", "expected"),
    [(0, 80), (None, 80), (5, 5), (500, 120), (-3, 1)],
)
def test_max_elements_is_clamped_to_a_sane_range(asked, expected):
    groups = [
        uia_element("", "group", children=[uia_element(f"b{g}_{i}", "button") for i in range(40)])
        for g in range(4)
    ]
    with install_fake_host_uia(_tree(*groups)):
        got = desktop_uia.uia_control_snapshot(hwnd=101, max_elements=asked)  # type: ignore[arg-type]
    assert got is not None
    assert len(got) == expected


def test_only_forty_children_of_one_element_are_walked():
    tree = _tree(*[uia_element(f"b{i:03d}", "button") for i in range(50)])
    with install_fake_host_uia(tree):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    buttons = [e["name"] for e in got if e["name"].startswith("b")]
    assert len(buttons) == 40


@pytest.mark.parametrize(("depth", "visible"), [(12, True), (13, False)])
def test_the_control_walk_stops_below_thirteen_levels_of_nesting(depth, visible):
    leaf = uia_element("DEEPEST", "button")
    with install_fake_host_uia(_tree(_nest(depth, leaf))):
        got = desktop_uia.uia_control_snapshot(hwnd=101) or []
    assert ("DEEPEST" in {e["name"] for e in got}) is visible


def test_deep_static_text_is_only_reached_when_preferred_only_is_off():
    deep_text = _nest(3, uia_element("Deep label", "text"))
    with install_fake_host_uia(_tree(deep_text)):
        strict = desktop_uia.uia_control_snapshot(hwnd=101) or []
        loose = desktop_uia.uia_control_snapshot(hwnd=101, preferred_only=False) or []
    assert "Deep label" not in {e["name"] for e in strict}
    assert "Deep label" in {e["name"] for e in loose}


def test_an_unnamed_clickable_control_is_named_after_its_role():
    with install_fake_host_uia(_tree(uia_element("", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert "button" in {e["name"] for e in got}


def test_an_unnamed_non_clickable_control_is_left_out_entirely():
    with install_fake_host_uia(_tree(uia_element("", "slider"), uia_element("Keep", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert {e["name"] for e in got} == {"App", "Keep"}


def test_a_control_whose_type_is_unreadable_is_reported_as_type_zero():
    odd = uia_element("Mystery", "button", raise_on={UIA_ControlTypePropertyId})
    with install_fake_host_uia(_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    mystery = next(e for e in got if e["name"] == "Mystery")
    assert mystery["role"] == "type_0", "an unknown type is labelled, not guessed at"


def test_a_control_whose_name_is_unreadable_falls_back_to_its_role():
    odd = uia_element("Save", "button", raise_on={UIA_NamePropertyId})
    with install_fake_host_uia(_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert "button" in {e["name"] for e in got}


class _Rect:
    left, top, right, bottom = 10, 20, 110, 70


def test_a_tagrect_shaped_bounding_rectangle_is_converted_not_misread():
    el = uia_element("Save", "button", properties={UIA_BoundingRectanglePropertyId: _Rect()})
    with install_fake_host_uia(_tree(el)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    save = next(e for e in got if e["name"] == "Save")
    assert save["bounds"] == {"left": 10, "top": 20, "right": 110, "bottom": 70}
    assert (save["x"], save["y"], save["w"], save["h"]) == (60, 45, 100, 50)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"properties": {UIA_BoundingRectanglePropertyId: None}},
        {"properties": {UIA_BoundingRectanglePropertyId: (5, 6)}},
        {"raise_on": {UIA_BoundingRectanglePropertyId}},
        {"bounds": (0, 0, 3, 3)},
    ],
    ids=["no-rect", "short-rect", "raising-rect", "too-small"],
)
def test_a_control_without_usable_bounds_is_dropped_rather_than_clicked_at_zero(kwargs):
    bad = uia_element("Ghost", "button", **kwargs)
    with install_fake_host_uia(_tree(bad, uia_element("Save", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert "Ghost" not in {e["name"] for e in got}
    assert "Save" in {e["name"] for e in got}


def test_an_offscreen_control_keeps_its_place_even_with_an_empty_rectangle():
    fold = uia_element("Below", "listitem", bounds=(0, 5000, 0, 0), offscreen=True)
    with install_fake_host_uia(_tree(fold)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    entry = next(e for e in got if e["name"] == "Below")
    assert entry["offscreen"] is True


def test_an_onscreen_control_is_not_flagged_offscreen():
    with install_fake_host_uia(_tree(uia_element("Save", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    save = next(e for e in got if e["name"] == "Save")
    assert "offscreen" not in save


def test_a_control_whose_offscreen_state_is_unreadable_is_treated_as_visible():
    odd = uia_element("Save", "button", raise_on={30022})
    with install_fake_host_uia(_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    save = next(e for e in got if e["name"] == "Save")
    assert "offscreen" not in save


def test_a_control_whose_enabled_state_is_unreadable_is_kept():
    odd = uia_element("Save", "button", raise_on={UIA_IsEnabledPropertyId})
    with install_fake_host_uia(_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert "Save" in {e["name"] for e in got}


def test_long_control_names_are_clipped_in_the_snapshot():
    with install_fake_host_uia(_tree(uia_element("n" * 300, "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert max(len(e["name"]) for e in got) == 120


def test_uia_available_is_true_when_the_fake_host_reports_ready():
    with install_fake_host_uia(FakeUIAutomation(), available=True) as fake:
        assert desktop_uia.uia_available() is True
    assert ("uia_available", (), {}) in fake.calls


# ----------------------------------------------------- contract fixtures -----


def test_contract_snapshot_fixture_has_required_fields():
    payload = _load_fixture("contract_uia_control_snapshot.json")
    result = payload.get("result")
    schema = payload.get("element_schema") or {}
    assert schema, "fixture must document the element schema"
    if result is None:
        # Schema-only capture is allowed; live_* / harness regen may fill result.
        assert set(schema) >= {
            "ref",
            "tag",
            "role",
            "name",
            "x",
            "y",
            "w",
            "h",
            "hwnd",
            "bounds",
            "uia",
        }
        return
    assert isinstance(result, list) and result
    required = {"ref", "tag", "role", "name", "x", "y", "w", "h", "hwnd", "bounds", "uia"}
    for entry in result:
        assert required <= set(entry)
        assert entry["ref"].startswith("c")
        assert entry["uia"] is True
        assert set(entry["bounds"]) == {"left", "top", "right", "bottom"}
        if "offscreen" in entry:
            assert entry["offscreen"] is True
        assert len(entry["name"]) <= 120


def test_contract_read_window_text_fixture_shape():
    payload = _load_fixture("contract_read_window_text.json")
    result = payload.get("result")
    schema = payload.get("result_schema") or {}
    assert set(schema) >= {"title", "text", "fields"}
    if result is None:
        return
    assert set(result) >= {"title", "text", "fields"}
    assert isinstance(result["fields"], list)
    for field in result["fields"]:
        assert set(field) == {"name", "role", "value"}
        assert len(field["name"]) <= 80
        assert len(field["value"]) <= 4000


def test_contract_focused_element_fixture_shape():
    payload = _load_fixture("contract_focused_element_info.json")
    result = payload["result"]
    assert set(result) == {"name", "role", "value"}
    assert len(result["name"]) <= 120
    assert len(result["value"]) <= 400


def test_contract_element_action_fixture_shapes():
    payload = _load_fixture("contract_element_action.json")
    cases = payload.get("cases") or payload.get("result")
    assert isinstance(cases, list) and cases
    for case in cases:
        body = case["result"]
        assert "ok" in body and "message" in body
        assert isinstance(body["ok"], bool)
        if "verified" in body:
            assert isinstance(body["verified"], bool)


def test_contract_misc_preferred_and_hints():
    payload = _load_fixture("contract_misc.json")
    for role, action in payload["preferred_click_action"].items():
        assert desktop_uia.preferred_click_action(role) == action
    hints = payload["structured_observe_hint"]
    assert desktop_uia.structured_observe_hint(n_windows=1, n_controls=1) == hints["with_controls"]
    assert desktop_uia.structured_observe_hint(n_windows=1, n_controls=0) == hints["windows_only"]
    assert desktop_uia.structured_observe_hint(n_windows=0, n_controls=0) == hints["empty"]


def test_harness_contract_snapshot_matches_fixture_schema():
    """Reference walker produces the same keys the frozen contract documents."""
    from tests.harness.uia_fixture_capture import harness_notepad_tree

    with install_fake_host_uia(harness_notepad_tree()):
        snap = desktop_uia.uia_control_snapshot(hwnd=101, max_elements=80, preferred_only=True)
        text = desktop_uia.read_window_text(101)
        focus = desktop_uia.focused_element_info()
    assert snap is not None and snap
    required = {"ref", "tag", "role", "name", "x", "y", "w", "h", "hwnd", "bounds", "uia"}
    for entry in snap:
        assert required <= set(entry)
        assert entry["uia"] is True
    assert text is not None and set(text) >= {"title", "text", "fields"}
    assert focus is not None and set(focus) == {"name", "role", "value"}
    assert any(e.get("offscreen") for e in snap), "Below listitem must stay flagged"


# ----------------------------------------------------- live smoke (Windows) --


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA only")
def test_live_uia_available_matches_native_probe():
    if not H.available():
        pytest.skip("remedy_core host surface not built")
    assert desktop_uia.uia_available() is H.uia_available()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA only")
def test_live_focused_element_shape_when_present():
    if not desktop_uia.uia_available():
        pytest.skip("UIA unavailable")
    got = desktop_uia.focused_element_info()
    if got is None:
        pytest.skip("no focused element")
    assert set(got) == {"name", "role", "value"}
    assert isinstance(got["name"], str)
    assert isinstance(got["role"], str)
    assert isinstance(got["value"], str)
    assert len(got["name"]) <= 120
    assert len(got["value"]) <= 400


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA only")
def test_live_desktop_snapshot_shape_or_none():
    if not desktop_uia.uia_available():
        pytest.skip("UIA unavailable")
    got = desktop_uia.uia_control_snapshot(max_elements=20)
    if got is None:
        return
    assert isinstance(got, list) and 1 <= len(got) <= 20
    required = {"ref", "tag", "role", "name", "x", "y", "w", "h", "hwnd", "bounds", "uia"}
    for entry in got:
        assert required <= set(entry)
        assert entry["uia"] is True
        assert len(entry["name"]) <= 120


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA only")
def test_live_reads_never_send_input_or_write_clipboard(monkeypatch: pytest.MonkeyPatch):
    if not desktop_uia.uia_available():
        pytest.skip("UIA unavailable")
    sent: list[Any] = []
    clips: list[Any] = []

    monkeypatch.setattr(H, "mouse_move", lambda *_a, **_k: sent.append("mouse"))
    monkeypatch.setattr(H, "mouse_click", lambda *_a, **_k: sent.append("mouse"))
    monkeypatch.setattr(H, "type_text", lambda *_a, **_k: sent.append("type"))
    monkeypatch.setattr(H, "clipboard_set_text", lambda text: clips.append(text))

    desktop_uia.focused_element_info()
    desktop_uia.uia_control_snapshot(max_elements=10)
    hwnd, _title = H.foreground_window()
    if hwnd:
        desktop_uia.read_window_text(hwnd, max_chars=500)
    assert sent == []
    assert clips == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA only")
def test_live_element_action_missing_name_is_soft_failure():
    if not desktop_uia.uia_available():
        pytest.skip("UIA unavailable")
    hwnd, _title = H.foreground_window()
    if not hwnd:
        pytest.skip("no foreground window")
    got = desktop_uia.element_action(
        hwnd, "remedy-uia-no-such-control-zz", action="invoke"
    )
    assert got["ok"] is False
    assert "not found" in got["message"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UIA only")
def test_live_fixture_shapes_still_match_recorded_keys():
    """Live capture keys must stay aligned with the frozen contract fixtures."""
    for name in (
        "live_uia_control_snapshot.json",
        "live_read_window_text.json",
        "live_focused_element_info.json",
    ):
        path = FIXTURES / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = payload.get("result")
        if result is None:
            continue
        if name.endswith("snapshot.json"):
            assert isinstance(result, list)
            if result:
                assert {"ref", "tag", "role", "name", "bounds", "uia"} <= set(result[0])
        elif name.endswith("read_window_text.json"):
            assert {"title", "text", "fields"} <= set(result)
        else:
            assert {"name", "role", "value"} <= set(result)
