"""What UI Automation reads back from a window — and what it must refuse to invent.

What breaks if this code is wrong: ``desktop_uia`` is how Remedy *sees* a native
window. Every fact it returns — the text in an edit box, the name of the focused
control, the click point of a button — is handed straight to the model, which
then acts on the owner's real desktop. So a wrong answer here is worse than no
answer: a stale or invented coordinate becomes a click in the wrong place, and a
silently swallowed failure becomes a confident lie about what is on screen.

The module is also a *soft* dependency: comtypes may not be installed, the
typelib may not be generated, the COM object may be dead. Every one of those
paths has to end in ``None`` or ``{"ok": False, ...}`` — never an exception that
takes the whole snapshot down, and never a fabricated element.

Everything below runs against the doubles in ``tests.harness.fake_win32``.
Nothing here loads UIAutomationCore.dll or touches a real window.
"""

from __future__ import annotations

import contextlib
import sys

import pytest

from remedy.core.computer import desktop_uia
from tests.harness.fake_win32 import (
    FakeDesktop,
    FakeTogglePattern,
    FakeUIAutomation,
    UIA_BoundingRectanglePropertyId,
    UIA_ControlTypePropertyId,
    UIA_IsEnabledPropertyId,
    UIA_NamePropertyId,
    UIA_TogglePatternId,
    UIA_ValueValuePropertyId,
    install_fake_win32,
    uia_element,
)

# ------------------------------------------------------------------ helpers --


def _boom(*_args, **_kwargs):
    """Stand-in for a COM call on an object that has died under us."""
    raise RuntimeError("the COM object went away")


def _raise_oserror(*_args, **_kwargs):
    raise OSError("already initialised with a different threading model")


@contextlib.contextmanager
def _no_comtypes():
    """A machine where comtypes was never installed: ``import comtypes`` fails.

    ``None`` in sys.modules is what CPython leaves behind for a module that must
    not be importable, and ``import`` raises ImportError on it.
    """
    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "comtypes" or name.startswith("comtypes.")
    }
    for name in saved:
        sys.modules.pop(name, None)
    sys.modules["comtypes"] = None  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.modules.pop("comtypes", None)
        sys.modules.update(saved)


@contextlib.contextmanager
def _typelib_not_generated(*, fail: bool = False):
    """comtypes.gen has no UIAutomationClient until GetModule() builds it.

    That is the state of every fresh machine — the wrapper module is generated
    from the DLL on first use. *fail* models the DLL not being there at all.
    Yields the list of GetModule() arguments so a test can prove it was called.
    """
    gen = sys.modules["comtypes.gen"]
    client = sys.modules["comtypes.client"]
    uia_mod = sys.modules.pop("comtypes.gen.UIAutomationClient")
    del gen.UIAutomationClient
    old_get_module = client.GetModule
    calls: list[str] = []

    def _generate(name, *_a, **_k):
        calls.append(name)
        if fail:
            raise OSError("UIAutomationCore.dll not found")
        gen.UIAutomationClient = uia_mod
        sys.modules["comtypes.gen.UIAutomationClient"] = uia_mod
        return uia_mod

    client.GetModule = _generate
    try:
        yield calls
    finally:
        client.GetModule = old_get_module
        gen.UIAutomationClient = uia_mod
        sys.modules["comtypes.gen.UIAutomationClient"] = uia_mod


def _tree(*children, name: str = "App", hwnd: int = 101, **kw) -> FakeUIAutomation:
    """A desktop root holding one window, the shape ElementFromHandle expects."""
    win = uia_element(name, "window", hwnd=hwnd, children=list(children), **kw)
    return FakeUIAutomation(uia_element("Desktop", "pane", children=[win]))


def _nest(depth: int, leaf):
    """*leaf* wrapped in anonymous groups so it sits at ``depth`` in the walk."""
    node = leaf
    for _ in range(max(0, depth - 1)):
        node = uia_element("", "group", children=[node])
    return node


_ALL_READS = [
    lambda: desktop_uia.read_window_text(101),
    lambda: desktop_uia.focused_element_info(),
    lambda: desktop_uia.uia_control_snapshot(hwnd=101),
]


# ------------------------------------------------------- availability guard --


def test_uia_is_unavailable_off_windows_even_though_comtypes_imports():
    with install_fake_win32(uia=FakeUIAutomation(), platform="linux"):
        assert desktop_uia.uia_available() is False


def test_uia_is_unavailable_when_comtypes_is_not_installed():
    with install_fake_win32(), _no_comtypes():
        assert desktop_uia.uia_available() is False


def test_probing_availability_does_not_create_a_com_object():
    # Availability is a question, not an action: asking must not spin up
    # CUIAutomation, which on a real machine costs an apartment and a
    # cross-process connection.
    tree = FakeUIAutomation()
    with install_fake_win32(uia=tree):
        assert desktop_uia.uia_available() is True
    assert tree.calls == []


@pytest.mark.parametrize("call", _ALL_READS)
def test_every_read_returns_none_off_windows(call):
    with install_fake_win32(uia=_tree(), platform="linux"):
        assert call() is None


@pytest.mark.parametrize("call", _ALL_READS)
def test_every_read_returns_none_without_comtypes(call):
    with install_fake_win32(), _no_comtypes():
        assert call() is None


def test_element_action_is_refused_rather_than_raised_when_uia_is_missing():
    with install_fake_win32(), _no_comtypes():
        got = desktop_uia.element_action(101, "Save", action="invoke")
    assert got["ok"] is False
    assert "not found" in got["message"]


# ------------------------------------------------------------- typelib load --


def test_the_typelib_is_generated_on_demand_when_the_wrapper_is_missing():
    tree = _tree(uia_element("Hello", "text"))
    with install_fake_win32(uia=tree), _typelib_not_generated() as generated:
        got = desktop_uia.read_window_text(101)
    assert generated == ["UIAutomationCore.dll"]
    assert got["text"] == "Hello"


def test_the_control_snapshot_also_generates_the_typelib_on_demand():
    tree = _tree(uia_element("Save", "button"))
    with install_fake_win32(uia=tree), _typelib_not_generated() as generated:
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert generated == ["UIAutomationCore.dll"]
    assert {e["name"] for e in got} == {"App", "Save"}


@pytest.mark.parametrize("call", _ALL_READS)
def test_a_typelib_that_cannot_be_generated_yields_none_not_an_exception(call):
    tree = _tree(uia_element("Save", "button"))
    tree.focused = tree.root.children[0]
    with install_fake_win32(uia=tree), _typelib_not_generated(fail=True):
        assert call() is None


def test_a_coinitialize_that_refuses_a_second_apartment_is_ignored():
    # CoInitialize raises on a thread already initialised in another model.
    # That is normal, not fatal — the read must go through anyway.
    tree = _tree(uia_element("Hello", "text"))
    with install_fake_win32(uia=tree):
        sys.modules["comtypes"].CoInitialize = _raise_oserror
        got = desktop_uia.read_window_text(101)
    assert got["text"] == "Hello"


# ----------------------------------------------------- read_window_text ------


def test_a_document_contributes_its_text_pattern_when_it_has_no_value():
    doc = uia_element("Report", "document", text="chapter one")
    with install_fake_win32(uia=_tree(doc)):
        got = desktop_uia.read_window_text(101)
    assert "[Report]: chapter one" in got["text"]
    assert got["fields"] == [{"name": "Report", "role": "document", "value": "chapter one"}]


def test_a_value_property_that_raises_falls_back_to_the_value_pattern():
    # The cached property can be unavailable while the live pattern still works.
    box = uia_element("Query", "edit", value="pinned", raise_on={UIA_ValueValuePropertyId})
    with install_fake_win32(uia=_tree(box)):
        got = desktop_uia.read_window_text(101)
    assert got["fields"] == [{"name": "Query", "role": "edit", "value": "pinned"}]


def test_the_value_property_wins_over_the_text_pattern():
    both = uia_element("Both", "combobox", value="from-value", text="from-text")
    with install_fake_win32(uia=_tree(both)):
        got = desktop_uia.read_window_text(101)
    assert got["fields"][0]["value"] == "from-value"


def test_an_empty_edit_is_listed_as_a_field_but_adds_no_text():
    # An empty box is a fact worth knowing — "there is a Search field here" —
    # but it must not put a phantom line into the text.
    with install_fake_win32(uia=_tree(uia_element("Search", "edit"))):
        got = desktop_uia.read_window_text(101)
    assert got["fields"] == [{"name": "Search", "role": "edit", "value": ""}]
    assert got["text"] == ""


def test_an_unnamed_edit_with_no_value_contributes_nothing_at_all():
    with install_fake_win32(uia=_tree(uia_element("", "edit"))):
        got = desktop_uia.read_window_text(101)
    assert got["fields"] == []
    assert got["text"] == ""


def test_a_whitespace_only_value_is_kept_as_a_field_but_not_as_text():
    with install_fake_win32(uia=_tree(uia_element("", "edit", value="   "))):
        got = desktop_uia.read_window_text(101)
    assert got["fields"] == [{"name": "edit", "role": "edit", "value": "   "}]
    assert got["text"] == ""


def test_a_control_whose_type_is_unreadable_is_skipped_but_its_children_are_read():
    broken = uia_element(
        "Mystery",
        "group",
        raise_on={UIA_ControlTypePropertyId},
        children=[uia_element("Inner label", "text")],
    )
    with install_fake_win32(uia=_tree(broken)):
        got = desktop_uia.read_window_text(101)
    assert "Mystery" not in got["text"]
    assert "Inner label" in got["text"]


def test_reading_a_window_uia_does_not_know_yields_none():
    with install_fake_win32(uia=_tree(uia_element("Hello", "text"))):
        assert desktop_uia.read_window_text(999) is None


def test_a_control_whose_name_is_unreadable_is_skipped_but_its_children_are_read():
    broken = uia_element(
        "Mystery",
        "text",
        raise_on={UIA_NamePropertyId},
        children=[uia_element("Inner label", "text")],
    )
    with install_fake_win32(uia=_tree(broken)):
        got = desktop_uia.read_window_text(101)
    assert got["text"] == "Inner label"


def test_a_repeat_that_is_not_consecutive_is_kept():
    # Only *adjacent* duplicates are menu noise; "OK … Cancel … OK" is real.
    tree = _tree(
        uia_element("OK", "button"),
        uia_element("Cancel", "button"),
        uia_element("OK", "button"),
    )
    with install_fake_win32(uia=tree):
        got = desktop_uia.read_window_text(101)
    assert got["text"].splitlines() == ["OK", "Cancel", "OK"]


def test_the_window_title_comes_from_the_root_element():
    with install_fake_win32(uia=_tree(name="Untitled - Notepad")):
        got = desktop_uia.read_window_text(101)
    assert got["title"] == "Untitled - Notepad"
    assert got["text"] == ""


def test_the_text_is_cut_to_max_chars():
    tree = _tree(*[uia_element(f"{i}" + "x" * 29, "text") for i in range(3)])
    with install_fake_win32(uia=tree):
        got = desktop_uia.read_window_text(101, max_chars=50)
    assert len(got["text"]) == 50


def test_a_tiny_max_chars_still_walks_the_first_kilobyte_of_fields():
    # The walk budget has a 1000 char floor; only the returned *text* obeys a
    # smaller max_chars. Fields must not vanish because the caller asked for a
    # short excerpt.
    tree = _tree(uia_element("Query", "edit", value="a long stored value"))
    with install_fake_win32(uia=tree):
        got = desktop_uia.read_window_text(101, max_chars=1)
    assert len(got["text"]) == 1
    assert got["fields"] == [{"name": "Query", "role": "edit", "value": "a long stored value"}]


def test_the_walk_stops_once_the_character_budget_is_spent():
    # Twenty 200-character labels against a 1000-character budget: the walk must
    # abandon the rest of the window rather than read 4000 characters.
    labels = [f"{i:03d}" + "x" * 197 for i in range(20)]
    tree = _tree(*[uia_element(label, "text") for label in labels])
    with install_fake_win32(uia=tree):
        got = desktop_uia.read_window_text(101, max_chars=1000)
    lines = got["text"].splitlines()
    assert [ln[:3] for ln in lines] == ["000", "001", "002", "003", "004"]
    assert "005" not in got["text"]


def test_only_forty_fields_are_reported():
    tree = _tree(*[uia_element(f"e{i:02d}", "edit", value="v") for i in range(50)])
    with install_fake_win32(uia=tree):
        got = desktop_uia.read_window_text(101)
    assert len(got["fields"]) == 40
    assert got["fields"][0]["name"] == "e00"


def test_a_long_field_name_and_value_are_clipped():
    tree = _tree(uia_element("n" * 200, "edit", value="v" * 9000))
    with install_fake_win32(uia=tree):
        field = desktop_uia.read_window_text(101)["fields"][0]
    assert len(field["name"]) == 80
    assert len(field["value"]) == 4000


@pytest.mark.parametrize(("depth", "visible"), [(14, True), (15, False)])
def test_the_text_walk_stops_below_fifteen_levels_of_nesting(depth, visible):
    leaf = uia_element("DEEPEST", "text")
    with install_fake_win32(uia=_tree(_nest(depth, leaf))):
        got = desktop_uia.read_window_text(101)
    assert ("DEEPEST" in got["text"]) is visible


def test_only_sixty_children_of_one_element_are_read():
    tree = _tree(*[uia_element(f"L{i:03d}", "text") for i in range(70)])
    with install_fake_win32(uia=tree):
        got = desktop_uia.read_window_text(101)
    assert len(got["text"].splitlines()) == 60


def test_an_automation_object_that_throws_yields_none_rather_than_propagating():
    tree = _tree(uia_element("Hello", "text"))
    tree.ElementFromHandle = _boom
    with install_fake_win32(uia=tree):
        assert desktop_uia.read_window_text(101) is None


def test_reading_a_window_never_sends_input():
    # The read path must be exactly that. If this ever records a SendInput the
    # snapshot is moving the owner's mouse.
    desktop = FakeDesktop()
    tree = _tree(uia_element("Save", "button"), uia_element("Query", "edit", value="x"))
    with install_fake_win32(desktop=desktop, uia=tree):
        desktop_uia.read_window_text(101)
        desktop_uia.focused_element_info()
        desktop_uia.uia_control_snapshot(hwnd=101)
    assert desktop.sent_input == []
    assert desktop.clipboard_writes == []


# --------------------------------------------------- focused_element_info ----


def test_a_focused_element_that_throws_yields_none():
    tree = _tree()
    tree.GetFocusedElement = _boom
    with install_fake_win32(uia=tree):
        assert desktop_uia.focused_element_info() is None


def test_focused_element_name_and_value_are_clipped():
    tree = _tree()
    tree.focused = uia_element("n" * 300, "edit", value="v" * 900)
    with install_fake_win32(uia=tree):
        got = desktop_uia.focused_element_info()
    assert len(got["name"]) == 120
    assert len(got["value"]) == 400


def test_a_focused_control_of_an_unknown_type_is_reported_as_unknown():
    tree = _tree()
    tree.focused = uia_element("Odd", control_type=99999)
    with install_fake_win32(uia=tree):
        assert desktop_uia.focused_element_info()["role"] == "unknown"


def test_a_focused_control_with_no_value_reports_an_empty_string():
    tree = _tree()
    tree.focused = uia_element("Save", "button")
    with install_fake_win32(uia=tree):
        assert desktop_uia.focused_element_info() == {
            "name": "Save",
            "role": "button",
            "value": "",
        }


# ---------------------------------------------------------- element_action ---


def test_element_action_without_a_window_handle_is_refused():
    with install_fake_win32(uia=_tree(uia_element("Save", "button", invokable=True))):
        got = desktop_uia.element_action(0, "Save", action="invoke")
    assert got["ok"] is False
    assert "not found in hwnd=0" in got["message"]


def test_element_action_on_a_window_uia_does_not_know_is_refused():
    tree = _tree(uia_element("Save", "button", invokable=True))
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(999, "Save", action="invoke")
    assert got["ok"] is False
    assert "not found in hwnd=999" in got["message"]


def test_a_search_that_throws_is_reported_as_not_found_not_as_a_crash():
    tree = _tree(uia_element("Save", "button", invokable=True))
    tree.root.children[0].FindAll = _boom
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(101, "Save", action="invoke")
    assert got["ok"] is False
    assert "not found" in got["message"]


def test_the_requested_role_is_a_preference_not_a_filter():
    # Snapshot roles drift (a "button" that UIA calls a splitbutton). Insisting
    # on the role would strand the caller; the first same-named element is used.
    button = uia_element("Save", "button", invokable=True)
    with install_fake_win32(uia=_tree(button)):
        got = desktop_uia.element_action(101, "Save", role="checkbox", action="invoke")
    assert got["ok"] is True
    assert button.actions == [("invoke",)]


def test_only_the_first_twenty_same_named_matches_are_considered():
    kids = [uia_element("Dup", "button", invokable=True) for _ in range(25)]
    wanted = uia_element("Dup", "edit", value="")
    kids[22] = wanted
    with install_fake_win32(uia=_tree(*kids)):
        got = desktop_uia.element_action(101, "Dup", role="edit", action="invoke")
    assert got["ok"] is True, "the fallback match is used, not nothing"
    assert kids[0].actions == [("invoke",)]
    assert wanted.actions == [], "match 23 is past the cap and is never reached"


def test_set_value_stringifies_a_non_string_argument():
    box = uia_element("Amount", "edit", value="")
    with install_fake_win32(uia=_tree(box)):
        got = desktop_uia.element_action(101, "Amount", action="set_value", text=1234)
    assert got["ok"] is True
    assert got["verified"] is True
    assert box.value == "1234"


def test_clearing_a_field_with_an_empty_value_is_allowed_and_verified():
    box = uia_element("Amount", "edit", value="99")
    with install_fake_win32(uia=_tree(box)):
        got = desktop_uia.element_action(101, "Amount", action="set_value", text="")
    assert got["verified"] is True
    assert "verified" in got["message"]
    assert "chars" not in got["message"]
    assert box.value == ""


def test_a_control_of_an_unknown_type_is_labelled_control_not_left_blank():
    odd = uia_element("Widget", control_type=99999)
    with install_fake_win32(uia=_tree(odd)):
        got = desktop_uia.element_action(101, "Widget", action="invoke")
    assert got == {"ok": False, "message": "control 'Widget' is not invokable — use a click"}


class _StuckToggle(FakeTogglePattern):
    """A toggle reporting a state the UIA spec does not define."""

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
    # The toggle happened; only the read-back failed. Reporting ok=False here
    # would make the caller toggle a second time and undo itself.
    box = uia_element("Word wrap", "checkbox", toggle="off")
    box.patterns[UIA_TogglePatternId] = pattern_cls(UIA_TogglePatternId, box)
    with install_fake_win32(uia=_tree(box)):
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
    # The message is the model's only clue about what to try instead, so the
    # refusal has to say which pattern was missing rather than just "failed".
    button = uia_element("Save", "button", invokable=True)
    with install_fake_win32(uia=_tree(button)):
        got = desktop_uia.element_action(101, "Save", action=action, text="x")
    assert got["ok"] is False
    assert fragment in got["message"]
    assert button.actions == []


def test_a_pattern_that_dies_mid_action_is_reported_with_its_error():
    item = uia_element("Below", "listitem", offscreen=True, scrollable=True)
    item.patterns[10017].error = RuntimeError("element is gone")
    with install_fake_win32(uia=_tree(item)):
        got = desktop_uia.element_action(101, "Below", action="scroll_into_view")
    assert got["ok"] is False
    assert "element is gone" in got["message"]
    assert item.offscreen is True


def test_scroll_into_view_reports_the_control_it_moved():
    item = uia_element("Below", "listitem", offscreen=True, scrollable=True)
    with install_fake_win32(uia=_tree(item)):
        got = desktop_uia.element_action(101, "Below", action="scroll_into_view")
    assert got == {"ok": True, "message": "Scrolled listitem 'Below' into view"}
    assert item.offscreen is False


def test_a_refused_action_leaves_the_control_untouched():
    box = uia_element("Query", "edit", value="original")
    with install_fake_win32(uia=_tree(box)):
        got = desktop_uia.element_action(101, "Query", action="invoke")
    assert got["ok"] is False
    assert box.value == "original"
    assert box.actions == []


# ----------------------------------------------------- uia_control_snapshot --


def test_the_snapshot_is_none_for_a_window_handle_uia_does_not_know():
    with install_fake_win32(uia=_tree(uia_element("Save", "button"))):
        assert desktop_uia.uia_control_snapshot(hwnd=999) is None


def test_a_creation_failure_yields_none_rather_than_an_exception():
    tree = _tree(uia_element("Save", "button"))
    with install_fake_win32(uia=tree):
        sys.modules["comtypes.client"].CreateObject = _boom
        assert desktop_uia.uia_control_snapshot(hwnd=101) is None


def test_the_desktop_walk_covers_named_top_level_windows():
    win = uia_element("Notepad", "window", hwnd=101, children=[uia_element("Save", "button")])
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[win]))
    with install_fake_win32(uia=tree):
        got = desktop_uia.uia_control_snapshot()
    assert [e["name"] for e in got] == ["Notepad", "Save"]
    assert {e["hwnd"] for e in got} == {101}


def test_an_unnamed_top_level_window_is_skipped_along_with_its_children():
    named = uia_element("Notepad", "window", hwnd=101, children=[uia_element("Save", "button")])
    ghost = uia_element("", "window", hwnd=102, children=[uia_element("Hidden", "button")])
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[named, ghost]))
    with install_fake_win32(uia=tree):
        names = {e["name"] for e in desktop_uia.uia_control_snapshot()}
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
    with install_fake_win32(uia=tree):
        names = {e["name"] for e in desktop_uia.uia_control_snapshot()}
    assert names == {"Notepad", "Save"}


def test_the_desktop_walk_stops_at_max_elements_between_windows():
    windows = [
        uia_element(f"W{i}", "window", hwnd=100 + i, children=[uia_element("Save", "button")])
        for i in range(3)
    ]
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=windows))
    with install_fake_win32(uia=tree):
        got = desktop_uia.uia_control_snapshot(max_elements=1)
    assert [e["name"] for e in got] == ["W0"]


def test_a_window_whose_native_handle_is_unreadable_still_yields_its_controls():
    win = uia_element("Notepad", "window", hwnd=101, children=[uia_element("Save", "button")])
    win.hwnd = "not-a-handle"  # a COM property that no longer converts to int
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[win]))
    with install_fake_win32(uia=tree):
        got = desktop_uia.uia_control_snapshot()
    assert [e["name"] for e in got] == ["Notepad", "Save"]
    assert all(e["hwnd"] is None for e in got), "an unknown handle is None, never a guess"


def test_when_the_desktop_enumeration_fails_the_root_itself_is_returned():
    root = uia_element("Desktop", "pane", children=[uia_element("Notepad", "window", hwnd=101)])
    root.FindAll = _boom
    with install_fake_win32(uia=FakeUIAutomation(root)):
        got = desktop_uia.uia_control_snapshot()
    assert [e["name"] for e in got] == ["Desktop"]


def test_an_element_whose_children_cannot_be_listed_is_still_reported():
    button = uia_element("Save", "button")
    button.FindAll = _boom
    with install_fake_win32(uia=_tree(button)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
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
    with install_fake_win32(uia=_tree(*groups)):
        got = desktop_uia.uia_control_snapshot(hwnd=101, max_elements=asked)
    assert len(got) == expected


def test_only_forty_children_of_one_element_are_walked():
    tree = _tree(*[uia_element(f"b{i:03d}", "button") for i in range(50)])
    with install_fake_win32(uia=tree):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    buttons = [e["name"] for e in got if e["name"].startswith("b")]
    assert len(buttons) == 40


@pytest.mark.parametrize(("depth", "visible"), [(12, True), (13, False)])
def test_the_control_walk_stops_below_thirteen_levels_of_nesting(depth, visible):
    leaf = uia_element("DEEPEST", "button")
    with install_fake_win32(uia=_tree(_nest(depth, leaf))):
        got = desktop_uia.uia_control_snapshot(hwnd=101) or []
    assert ("DEEPEST" in {e["name"] for e in got}) is visible


def test_deep_static_text_is_only_reached_when_preferred_only_is_off():
    # preferred_only keeps the snapshot to things worth clicking; turning it off
    # is how a caller asks for the labels too.
    deep_text = _nest(3, uia_element("Deep label", "text"))
    with install_fake_win32(uia=_tree(deep_text)):
        strict = desktop_uia.uia_control_snapshot(hwnd=101) or []
        loose = desktop_uia.uia_control_snapshot(hwnd=101, preferred_only=False) or []
    assert "Deep label" not in {e["name"] for e in strict}
    assert "Deep label" in {e["name"] for e in loose}


def test_an_unnamed_clickable_control_is_named_after_its_role():
    with install_fake_win32(uia=_tree(uia_element("", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert "button" in {e["name"] for e in got}


def test_an_unnamed_non_clickable_control_is_left_out_entirely():
    with install_fake_win32(uia=_tree(uia_element("", "slider"), uia_element("Keep", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert {e["name"] for e in got} == {"App", "Keep"}


def test_a_control_whose_type_is_unreadable_is_reported_as_type_zero():
    odd = uia_element("Mystery", "button", raise_on={UIA_ControlTypePropertyId})
    with install_fake_win32(uia=_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    mystery = next(e for e in got if e["name"] == "Mystery")
    assert mystery["role"] == "type_0", "an unknown type is labelled, not guessed at"


def test_a_control_whose_name_is_unreadable_falls_back_to_its_role():
    odd = uia_element("Save", "button", raise_on={UIA_NamePropertyId})
    with install_fake_win32(uia=_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert "button" in {e["name"] for e in got}


class _Rect:
    """A tagRECT-shaped bounding rectangle (left/top/right/bottom), not l/t/w/h."""

    left, top, right, bottom = 10, 20, 110, 70


def test_a_tagrect_shaped_bounding_rectangle_is_converted_not_misread():
    el = uia_element("Save", "button", properties={UIA_BoundingRectanglePropertyId: _Rect()})
    with install_fake_win32(uia=_tree(el)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
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
    # A click point of (0, 0) is the corner of the screen. Dropping the element
    # is the only safe answer when its rectangle is unreadable.
    bad = uia_element("Ghost", "button", **kwargs)
    with install_fake_win32(uia=_tree(bad, uia_element("Save", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert "Ghost" not in {e["name"] for e in got}
    assert "Save" in {e["name"] for e in got}


def test_an_offscreen_control_keeps_its_place_even_with_an_empty_rectangle():
    fold = uia_element("Below", "listitem", bounds=(0, 5000, 0, 0), offscreen=True)
    with install_fake_win32(uia=_tree(fold)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    entry = next(e for e in got if e["name"] == "Below")
    assert entry["offscreen"] is True


def test_an_onscreen_control_is_not_flagged_offscreen():
    with install_fake_win32(uia=_tree(uia_element("Save", "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    save = next(e for e in got if e["name"] == "Save")
    assert "offscreen" not in save


def test_a_control_whose_offscreen_state_is_unreadable_is_treated_as_visible():
    odd = uia_element("Save", "button", raise_on={30022})
    with install_fake_win32(uia=_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    save = next(e for e in got if e["name"] == "Save")
    assert "offscreen" not in save


def test_a_control_whose_enabled_state_is_unreadable_is_kept():
    # Unknown must not mean "disabled" — that would silently hide real buttons.
    odd = uia_element("Save", "button", raise_on={UIA_IsEnabledPropertyId})
    with install_fake_win32(uia=_tree(odd)):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert "Save" in {e["name"] for e in got}


def test_long_control_names_are_clipped_in_the_snapshot():
    with install_fake_win32(uia=_tree(uia_element("n" * 300, "button"))):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert max(len(e["name"]) for e in got) == 120
