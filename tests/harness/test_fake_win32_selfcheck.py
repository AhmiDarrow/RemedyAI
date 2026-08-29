"""Proof that the Win32 doubles behave like the real thing — and never act.

What breaks if this code is wrong: every desktop test built on
``tests.harness.fake_win32`` inherits whatever these doubles do. If the fake
``windll`` quietly answered "success" to a call the real one refuses, or if the
fake UI Automation tree walked differently from the real one, the desktop suite
would go green while the shipped code was broken — or, worse, a double that
forwarded to the real API would start clicking on the owner's machine. So the
emphasis here is on the negative side: what the double must refuse, what it
must only RECORD, and what it must not invent.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from remedy.core.computer import desktop_uia
from tests.harness.fake_win32 import (
    CallLog,
    FakeConsoleHost,
    FakeDesktop,
    FakeDLL,
    FakeUIAutomation,
    FakeWinDLL,
    FakeWindow,
    build_fake_comtypes,
    install_fake_win32,
    uia_element,
)

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(
    not WINDOWS,
    reason="the code under test builds ctypes.wintypes structures, which only exist on Windows",
)


# ---------------------------------------------------------------- windll ---


def test_an_unknown_export_is_created_on_demand_and_records_its_arguments():
    dll = FakeDLL("user32")
    assert dll.SomeBrandNewApiW(1, "two") == 1
    assert dll.log.count("user32.SomeBrandNewApiW") == 1
    assert dll.log.last("user32.SomeBrandNewApiW").args == (1, "two")


def test_an_export_marked_missing_is_refused_instead_of_invented():
    dll = FakeDLL("kernel32", missing={"CreatePseudoConsole"})
    assert hasattr(dll, "CreatePipe")
    assert not hasattr(dll, "CreatePseudoConsole")
    with pytest.raises(AttributeError):
        _probe = dll.CreatePseudoConsole


def test_dunder_lookups_are_not_answered_with_a_callable():
    # hasattr(obj, "__deepcopy__") and friends are probed by copy/pickle/pytest;
    # a fake that answers them with a function corrupts unrelated machinery.
    dll = FakeDLL("user32")
    for name in ("__deepcopy__", "__iter__", "__wrapped__", "_private"):
        assert not hasattr(dll, name), name


def test_argtypes_and_restype_may_be_assigned_and_are_never_enforced():
    # Real call sites declare signatures before calling; the double must accept
    # the declaration and then ignore it rather than type-checking arguments.
    dll = FakeDLL("kernel32")
    dll.GlobalLock.restype = ctypes.c_void_p
    dll.GlobalLock.argtypes = [ctypes.c_void_p]
    assert dll.GlobalLock("not a pointer at all") == 1
    assert dll.GlobalLock.restype is ctypes.c_void_p


def test_scripted_returns_are_consumed_in_order_then_fall_back_to_the_default():
    dll = FakeDLL("user32", default_return=0)
    dll.set_return("GetForegroundWindow", 77)
    dll.function("SetForegroundWindow").set_returns([0, 0, 1])
    assert dll.GetForegroundWindow() == 77
    assert [dll.SetForegroundWindow(5) for _ in range(4)] == [0, 0, 1, 0]


def test_a_scripted_error_is_raised_and_still_recorded():
    dll = FakeDLL("user32")
    dll.function("EnumWindows").set_error(OSError("boom"))
    with pytest.raises(OSError, match="boom"):
        dll.EnumWindows(None, 0)
    rec = dll.log.last("EnumWindows")
    assert rec is not None and isinstance(rec.error, OSError)


def test_one_dll_object_is_shared_by_every_lookup_spelling():
    windll = FakeWinDLL()
    assert windll.kernel32 is windll.dll("kernel32.dll") is windll("kernel32")
    windll.kernel32.set_return("GetCurrentThreadId", 4)
    assert windll("kernel32", use_last_error=True).GetCurrentThreadId() == 4


def test_every_dll_shares_one_ordered_call_log():
    windll = FakeWinDLL()
    windll.user32.GetForegroundWindow()
    windll.kernel32.GetCurrentThreadId()
    windll.user32.CloseClipboard()
    log: CallLog = windll.log
    assert log.names() == [
        "user32.GetForegroundWindow",
        "kernel32.GetCurrentThreadId",
        "user32.CloseClipboard",
    ]
    assert "kernel32.GetCurrentThreadId" in log


# --------------------------------------------------------------- desktop ---


def _desktop() -> FakeDesktop:
    return FakeDesktop(
        windows=[
            FakeWindow(hwnd=101, title="Untitled - Notepad", right=900, bottom=700),
            FakeWindow(hwnd=202, title="", right=400, bottom=300),  # no title
            FakeWindow(hwnd=303, title="Hidden", visible=False),
            FakeWindow(hwnd=404, title="Tiny", right=4, bottom=4),  # below 8x8
        ],
        foreground=101,
    )


@windows_only
def test_list_windows_sees_exactly_the_titled_visible_windows():
    from remedy.core.computer import desktop_win

    with install_fake_win32(desktop=_desktop()) as fake:
        got = desktop_win.list_windows()
    assert [w["hwnd"] for w in got] == [101]
    assert got[0]["title"] == "Untitled - Notepad"
    assert got[0]["bounds"] == {"left": 0, "top": 0, "right": 900, "bottom": 700}
    assert fake.log.count("user32.EnumWindows") == 1


@windows_only
@pytest.mark.parametrize(
    ("window", "why"),
    [
        (FakeWindow(hwnd=1, title=""), "untitled windows carry no useful handle"),
        (FakeWindow(hwnd=2, title="Hidden", visible=False), "invisible windows are not targets"),
        (FakeWindow(hwnd=3, title="Sliver", right=4, bottom=4), "8x8 is the floor"),
    ],
)
def test_a_window_that_cannot_be_a_target_is_left_out(window, why):
    from remedy.core.computer import desktop_win

    with install_fake_win32(desktop=FakeDesktop(windows=[window])):
        assert desktop_win.list_windows() == [], why


@windows_only
def test_the_enumeration_limit_stops_the_walk_early():
    from remedy.core.computer import desktop_win

    desk = FakeDesktop(windows=[FakeWindow(hwnd=i, title=f"w{i}") for i in range(1, 11)])
    with install_fake_win32(desktop=desk):
        assert len(desktop_win.list_windows(limit=3)) == 3


@windows_only
def test_focus_window_reports_failure_when_the_foreground_lock_holds():
    # The interesting case: Windows silently ignores SetForegroundWindow. The
    # code must NOT report success — it verifies with GetForegroundWindow.
    from remedy.core.computer import desktop_win

    desk = _desktop()
    desk.foreground_locked = True
    desk.foreground = 202  # some other app owns the foreground
    with install_fake_win32(desktop=desk) as fake:
        assert desktop_win.focus_window(101) is False
    assert desk.foreground == 202
    assert fake.log.count("user32.SetForegroundWindow") >= 1
    assert desk.attached_threads, "it should have tried the AttachThreadInput fallback"


@windows_only
def test_send_input_is_recorded_and_nothing_is_ever_delivered():
    from remedy.core.computer import desktop_win

    desk = _desktop()
    with install_fake_win32(desktop=desk):
        desktop_win.click(120, 240)
    assert desk.sent_input, "the click must be observable"
    assert all(isinstance(call[0], int) for call in desk.sent_input)
    # SetCursorPos is the only positioning call and it too is inert.
    assert desk.window(101).show_commands == []


@windows_only
def test_the_clipboard_round_trips_through_the_fake_and_not_the_real_one():
    from remedy.core.computer import desktop_win

    desk = FakeDesktop(clipboard="whatever was there")
    with install_fake_win32(desktop=desk):
        assert desktop_win.get_clipboard_text() == "whatever was there"
        assert desktop_win.set_clipboard_text("Remedy wrote this") is True
        assert desktop_win.get_clipboard_text() == "Remedy wrote this"
    assert desk.clipboard_writes == ["Remedy wrote this"]
    assert desk.clipboard_open is False, "the clipboard must be released again"


@windows_only
def test_manage_window_moves_only_the_fake_windows_bounds():
    from remedy.core.computer import desktop_win

    desk = _desktop()
    with install_fake_win32(desktop=desk):
        assert desktop_win.manage_window(101, "move", x=10, y=20)["ok"] is True
        assert desktop_win.manage_window(101, "minimize")["ok"] is True
        assert desktop_win.manage_window(101, "close")["ok"] is True
    w = desk.window(101)
    assert (w.left, w.top, w.width, w.height) == (10, 20, 900, 700)
    assert w.show_commands == [6]  # SW_MINIMIZE
    assert w.posted == [(0x0010, 0, 0)]  # WM_CLOSE, posted not executed


# ------------------------------------------------------------------- UIA ---


def _notepad_uia() -> FakeUIAutomation:
    editor = uia_element(
        "Text Editor",
        "edit",
        value="the quick brown fox",
        bounds=(10, 60, 800, 500),
    )
    save = uia_element("Save", "button", bounds=(10, 10, 60, 24), invokable=True)
    save_menu = uia_element("Save", "menuitem", bounds=(0, 0, 100, 20), invokable=True)
    wrap = uia_element("Word wrap", "checkbox", bounds=(80, 10, 90, 24), toggle="off")
    far = uia_element(
        "Below the fold",
        "listitem",
        bounds=(0, 5000, 0, 0),
        offscreen=True,
        scrollable=True,
    )
    dim = uia_element("Print", "button", bounds=(200, 10, 60, 24), enabled=False)
    window = uia_element(
        "Untitled - Notepad",
        "window",
        hwnd=101,
        bounds=(0, 0, 900, 700),
        children=[save, save_menu, wrap, editor, far, dim],
    )
    root = uia_element("Desktop", "pane", hwnd=0, children=[window])
    return FakeUIAutomation(root)


def test_uia_looks_available_once_the_doubles_are_installed():
    with install_fake_win32(uia=_notepad_uia()):
        assert desktop_uia.uia_available() is True


def test_read_window_text_gathers_field_values_and_static_names():
    with install_fake_win32(uia=_notepad_uia()):
        got = desktop_uia.read_window_text(101)
    assert got is not None
    assert got["title"] == "Untitled - Notepad"
    assert "[Text Editor]: the quick brown fox" in got["text"]
    assert "Save" in got["text"].splitlines()
    assert {"name": "Text Editor", "role": "edit", "value": "the quick brown fox"} in got["fields"]


def test_a_repeated_name_collapses_instead_of_being_listed_twice():
    # Menus and toolbars repeat the same label; consecutive repeats collapse.
    with install_fake_win32(uia=_notepad_uia()):
        text = desktop_uia.read_window_text(101)["text"]
    assert text.splitlines().count("Save") == 1


def test_read_window_text_returns_none_for_a_handle_no_element_owns():
    with install_fake_win32(uia=_notepad_uia()):
        assert desktop_uia.read_window_text(4242) is None
        assert desktop_uia.read_window_text(0) is None


def test_a_property_that_raises_is_swallowed_rather_than_killing_the_walk():
    tree = _notepad_uia()
    editor = tree.root.children[0].children[3]
    editor.raise_on = {30005, 30045}  # Name and Value both blow up
    with install_fake_win32(uia=tree):
        got = desktop_uia.read_window_text(101)
    assert got is not None, "one hostile element must not lose the whole window"
    assert "Save" in got["text"]


def test_focused_element_info_reports_the_focused_control():
    tree = _notepad_uia()
    tree.focused = tree.root.children[0].children[3]
    with install_fake_win32(uia=tree):
        assert desktop_uia.focused_element_info() == {
            "name": "Text Editor",
            "role": "edit",
            "value": "the quick brown fox",
        }


def test_focused_element_info_is_none_when_nothing_has_focus():
    with install_fake_win32(uia=FakeUIAutomation()):
        assert desktop_uia.focused_element_info() is None


def test_preferred_click_action_toggle_vs_invoke():
    from remedy.core.computer.desktop_uia import preferred_click_action

    assert preferred_click_action("checkbox") == "toggle"
    assert preferred_click_action("switch") == "toggle"
    assert preferred_click_action("button") == "invoke"
    assert preferred_click_action("menuitem") == "invoke"


def test_element_action_invoke_drives_the_pattern_and_says_so():
    tree = _notepad_uia()
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(101, "Save", role="button", action="invoke")
    assert got["ok"] is True
    save = tree.root.children[0].children[0]
    assert save.actions == [("invoke",)]


def test_element_action_prefers_the_requested_role_over_the_first_match():
    tree = _notepad_uia()
    with install_fake_win32(uia=tree):
        desktop_uia.element_action(101, "Save", role="menuitem", action="invoke")
    save_button, save_menu = tree.root.children[0].children[:2]
    assert save_menu.actions == [("invoke",)]
    assert save_button.actions == []


def test_element_action_sets_a_value_atomically_and_verifies_the_readback():
    tree = _notepad_uia()
    editor = tree.root.children[0].children[3]
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(101, "Text Editor", action="set_value", text="typed")
    assert got == {
        "ok": True,
        "message": "Set edit 'Text Editor' value, verified",
        "verified": True,
    }
    assert editor.value == "typed"


def test_a_readonly_field_reports_verified_false_rather_than_pretending():
    tree = _notepad_uia()
    editor = tree.root.children[0].children[3]
    editor.readonly = True
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(101, "Text Editor", action="set_value", text="typed")
    assert got["verified"] is False
    assert "readback differs" in got["message"]
    assert editor.value == "the quick brown fox"


def test_toggling_a_checkbox_reports_the_state_it_landed_in():
    tree = _notepad_uia()
    with install_fake_win32(uia=tree):
        first = desktop_uia.element_action(101, "Word wrap", action="toggle")
        second = desktop_uia.element_action(101, "Word wrap", action="toggle")
    assert first["message"].endswith("→ on")
    assert second["message"].endswith("→ off")


def test_scroll_into_view_brings_an_offscreen_item_onscreen():
    tree = _notepad_uia()
    far = tree.root.children[0].children[4]
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(101, "Below the fold", action="scroll_into_view")
    assert got["ok"] is True
    assert far.offscreen is False


@pytest.mark.parametrize(
    ("name", "action", "fragment"),
    [
        ("No Such Control", "invoke", "not found"),
        ("Text Editor", "invoke", "not invokable"),
        ("Save", "set_value", "no value pattern"),
        ("Save", "toggle", "not toggleable"),
        ("Save", "scroll_into_view", "no scroll-item pattern"),
        ("Save", "teleport", "Unknown UIA action"),
    ],
)
def test_an_action_the_element_cannot_perform_is_refused_not_faked(name, action, fragment):
    tree = _notepad_uia()
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(101, name, action=action)
    assert got["ok"] is False
    assert fragment in got["message"]


def test_a_pattern_that_throws_is_reported_as_a_failure_not_a_success():
    tree = _notepad_uia()
    save = tree.root.children[0].children[0]
    save.patterns[10000].error = RuntimeError("element is gone")
    with install_fake_win32(uia=tree):
        got = desktop_uia.element_action(101, "Save", role="button", action="invoke")
    assert got["ok"] is False
    assert "element is gone" in got["message"]


def test_the_control_snapshot_walks_the_window_and_numbers_its_refs():
    with install_fake_win32(uia=_notepad_uia()):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert got is not None
    assert [e["ref"] for e in got] == [f"c{i + 1}" for i in range(len(got))]
    by_name = {e["name"]: e for e in got}
    assert by_name["Text Editor"]["bounds"] == {
        "left": 10,
        "top": 60,
        "right": 810,
        "bottom": 560,
    }
    assert (by_name["Text Editor"]["x"], by_name["Text Editor"]["y"]) == (410, 310)
    assert all(e["hwnd"] == 101 for e in got)


def test_a_disabled_control_is_left_out_of_the_snapshot():
    with install_fake_win32(uia=_notepad_uia()):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    assert "Print" not in {e["name"] for e in got}


def test_an_offscreen_control_is_kept_but_flagged():
    with install_fake_win32(uia=_notepad_uia()):
        got = desktop_uia.uia_control_snapshot(hwnd=101)
    fold = next(e for e in got if e["name"] == "Below the fold")
    assert fold["offscreen"] is True


def test_the_snapshot_stops_at_max_elements():
    root = uia_element(
        "App",
        "window",
        hwnd=7,
        children=[uia_element(f"b{i}", "button", invokable=True) for i in range(20)],
    )
    tree = FakeUIAutomation(uia_element("Desktop", "pane", children=[root]))
    with install_fake_win32(uia=tree):
        got = desktop_uia.uia_control_snapshot(hwnd=7, max_elements=5)
    assert len(got) == 5


def test_the_snapshot_is_none_rather_than_empty_when_nothing_qualifies():
    tree = FakeUIAutomation(
        uia_element("Desktop", "pane", children=[uia_element("", "window", hwnd=9)])
    )
    with install_fake_win32(uia=tree):
        assert desktop_uia.uia_control_snapshot(hwnd=9) is None


def test_find_all_honours_the_tree_scope_it_was_given():
    child = uia_element("kid", "button")
    grandchild = uia_element("grandkid", "button")
    child.add_child(grandchild)
    root = uia_element("root", "window", children=[child])
    automation = FakeUIAutomation(root)
    true_cond = automation.CreateTrueCondition()
    assert [e.name for e in _all(root.FindAll(2, true_cond))] == ["kid"]
    assert [e.name for e in _all(root.FindAll(4, true_cond))] == ["kid", "grandkid"]
    assert [e.name for e in _all(root.FindAll(1, true_cond))] == ["root"]


def test_a_property_condition_matches_only_the_named_element():
    tree = _notepad_uia()
    cond = tree.CreatePropertyCondition(30005, "Word wrap")
    found = tree.root.FindAll(4, cond)
    assert found.Length == 1
    assert found.GetElement(0).name == "Word wrap"


def test_querying_a_pattern_as_the_wrong_interface_is_refused():
    modules = build_fake_comtypes(FakeUIAutomation())
    uia_mod = modules["comtypes.gen.UIAutomationClient"]
    button = uia_element("Go", "button", invokable=True)
    pattern = button.GetCurrentPattern(10000)
    assert pattern.QueryInterface(uia_mod.IUIAutomationInvokePattern) is pattern
    with pytest.raises(TypeError):
        pattern.QueryInterface(uia_mod.IUIAutomationValuePattern)


def _all(array):
    return [array.GetElement(i) for i in range(array.Length)]


# --------------------------------------------------------------- console ---


def test_a_pipe_carries_bytes_from_the_write_handle_to_the_read_handle():
    host = FakeConsoleHost()
    windll = FakeWinDLL()
    host.install_into(windll)
    k32 = windll.kernel32
    read_h, write_h = ctypes.c_void_p(), ctypes.c_void_p()
    assert k32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), None, 0) == 1

    written = ctypes.c_ulong(0)
    payload = b"C:\\> dir\r\n"
    k32.WriteFile(write_h.value, ctypes.create_string_buffer(payload), len(payload),
                  ctypes.byref(written), None)
    assert written.value == len(payload)
    assert host.written(write_h.value) == payload

    host.feed(read_h.value, b"Volume in drive C")
    buf = ctypes.create_string_buffer(64)
    got = ctypes.c_ulong(0)
    assert k32.ReadFile(read_h.value, buf, 64, ctypes.byref(got), None) == 1
    assert buf.raw[: got.value] == b"Volume in drive C"
    # Drained: a second read reports zero bytes instead of repeating itself.
    assert k32.ReadFile(read_h.value, buf, 64, ctypes.byref(got), None) == 1
    assert got.value == 0


def test_reading_or_writing_a_closed_handle_fails_instead_of_succeeding_quietly():
    host = FakeConsoleHost()
    windll = FakeWinDLL()
    host.install_into(windll)
    k32 = windll.kernel32
    read_h, write_h = ctypes.c_void_p(), ctypes.c_void_p()
    k32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), None, 0)
    host.feed(read_h.value, b"stale")
    assert k32.CloseHandle(read_h.value) == 1

    buf = ctypes.create_string_buffer(16)
    got = ctypes.c_ulong(7)
    assert k32.ReadFile(read_h.value, buf, 16, ctypes.byref(got), None) == 0
    assert got.value == 0


def test_closing_the_same_handle_twice_is_recorded_rather_than_accepted():
    host = FakeConsoleHost()
    windll = FakeWinDLL()
    host.install_into(windll)
    read_h, write_h = ctypes.c_void_p(), ctypes.c_void_p()
    windll.kernel32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), None, 0)
    assert windll.kernel32.CloseHandle(read_h.value) == 1
    assert windll.kernel32.CloseHandle(read_h.value) == 0
    assert host.double_closed == [read_h.value]


def test_the_attribute_list_size_probe_reports_failure_like_the_real_api():
    # InitializeProcThreadAttributeList(NULL, ...) sets the size AND returns
    # FALSE. A double that returned TRUE would hide a caller bug.
    host = FakeConsoleHost()
    windll = FakeWinDLL()
    host.install_into(windll)
    size = ctypes.c_size_t(0)
    assert windll.kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size)) == 0
    assert size.value > 0
    buf = (ctypes.c_char * size.value)()
    assert windll.kernel32.InitializeProcThreadAttributeList(buf, 1, 0, ctypes.byref(size)) == 1


@windows_only
def test_spawn_conpty_is_reported_unsupported_when_the_export_is_missing():
    from remedy.execution.host import conpty

    windll = FakeWinDLL()
    with install_fake_win32(windll=windll):
        assert conpty.spawn_conpty_supported() is True
        windll.kernel32.set_missing("CreatePseudoConsole")
        assert conpty.spawn_conpty_supported() is False


@windows_only
def test_conpty_spawn_builds_the_console_and_the_command_line_it_was_asked_for():
    """The fake proves the pipes, the pseudoconsole and the CreateProcessW
    arguments are all what the real API would have been handed.

    This used to end in ``pytest.raises(ValueError)``: the last statement of
    ``_spawn_conpty_sync`` called ``int()`` on a ``wintypes.HANDLE``, which
    raises — so the spawn always threw after the child had already been
    created, and ConPTY silently never worked.
    """
    from remedy.execution.host import conpty

    host = FakeConsoleHost()
    with install_fake_win32(console=host):
        proc = conpty._spawn_conpty_sync(
            ["cmd.exe", "/c", "echo hi"], "C:\\work", {"A": "b"}
        )
    assert proc.pid > 0

    assert host.pseudoconsoles == [
        {
            "cols": 120,
            "rows": 40,
            "input": host.pipes[0].read_handle,
            "output": host.pipes[1].write_handle,
            "flags": 0,
        }
    ]
    assert host.spawns[0]["cmdline"] == 'cmd.exe /c "echo hi"'
    assert host.spawns[0]["cwd"] == "C:\\work"
    assert host.spawns[0]["flags"] == 0x00080000 | 0x00000400
    # The pty-side ends are handed to the console and closed by the parent.
    assert host.pipes[0].write_handle not in host.closed


@windows_only
def test_a_failed_createprocess_closes_every_handle_it_opened():
    from remedy.execution.host import conpty

    host = FakeConsoleHost(fail_create_process=True)
    with install_fake_win32(console=host):
        with pytest.raises(OSError, match="CreateProcessW failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert host.open_handles == [], f"leaked {host.open_handles}"
    assert host.closed_pseudoconsoles


@windows_only
def test_a_failed_pseudoconsole_is_raised_and_leaves_no_handle_behind():
    from remedy.execution.host import conpty

    host = FakeConsoleHost(create_pseudoconsole_hr=-2147024809)
    with install_fake_win32(console=host):
        with pytest.raises(OSError, match="CreatePseudoConsole failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert host.open_handles == []
    assert host.spawns == [], "no process may be created once the console failed"


@windows_only
def test_a_failed_pipe_is_raised_before_anything_else_happens():
    from remedy.execution.host import conpty

    host = FakeConsoleHost(fail_create_pipe=True)
    with install_fake_win32(console=host):
        with pytest.raises(OSError, match="CreatePipe input failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert host.pseudoconsoles == []
    assert host.spawns == []


@windows_only
@pytest.mark.asyncio
async def test_the_conpty_handle_stream_reads_and_writes_through_the_fake():
    from remedy.execution.host import conpty

    host = FakeConsoleHost()
    windll = FakeWinDLL()
    host.install_into(windll)
    read_h, write_h = ctypes.c_void_p(), ctypes.c_void_p()
    windll.kernel32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), None, 0)

    with install_fake_win32(windll=windll, console=host):
        stdin = conpty._HandleStream(write_h.value, write=True)
        stdout = conpty._HandleStream(read_h.value, write=False)
        stdin.write(b"echo hi\r\n")
        await stdin.drain()
        host.feed(read_h.value, b"hi\r\n")
        assert await stdout.read(64) == b"hi\r\n"
        # A write-only stream never yields bytes, and a closed one goes quiet.
        assert await stdin.read(16) == b""
        stdout.close()
        assert await stdout.read(16) == b""
    assert host.written(write_h.value) == b"echo hi\r\n"
    assert host.is_closed(read_h.value)


@windows_only
@pytest.mark.asyncio
async def test_spawn_conpty_surfaces_the_failure_rather_than_returning_a_dead_process():
    from remedy.execution.host import conpty

    host = FakeConsoleHost(fail_create_process=True)
    with install_fake_win32(console=host):
        with pytest.raises(OSError, match="CreateProcessW failed"):
            await conpty.spawn_conpty(["cmd.exe"], cwd=None, env=None)


@windows_only
def test_terminating_the_fake_process_closes_its_console_and_handles():
    from remedy.execution.host import conpty

    host = FakeConsoleHost()
    windll = FakeWinDLL()
    host.install_into(windll)
    read_h, write_h = ctypes.c_void_p(), ctypes.c_void_p()
    windll.kernel32.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h), None, 0)
    with install_fake_win32(windll=windll, console=host):
        proc = conpty._ConPTYProcess(
            pid=4321,
            process_handle=0x900,
            stdin_handle=write_h.value,
            stdout_handle=read_h.value,
            pc_handle=0x910,
        )
        proc.terminate()
    assert host.terminated == [(0x900, 1)]
    assert host.closed_pseudoconsoles == [0x910]
    assert proc.returncode == 1


# ---------------------------------------------------------- installation ---


def test_the_installer_restores_every_attribute_it_touched():
    had_windll = hasattr(ctypes, "windll")
    before = getattr(ctypes, "windll", None)
    platform_before = sys.platform
    comtypes_before = sys.modules.get("comtypes")

    with install_fake_win32(uia=FakeUIAutomation()) as fake:
        assert ctypes.windll is fake.windll
        assert sys.platform == "win32"
        assert sys.modules["comtypes"].client.CreateObject() is fake.uia

    assert hasattr(ctypes, "windll") is had_windll
    assert getattr(ctypes, "windll", None) is before
    assert sys.platform == platform_before
    assert sys.modules.get("comtypes") is comtypes_before


def test_the_platform_stamp_can_be_declined():
    with install_fake_win32(platform=None):
        assert sys.platform == ("win32" if WINDOWS else sys.platform)


def test_nested_installs_unwind_in_order():
    outer = FakeWinDLL()
    inner = FakeWinDLL()
    with install_fake_win32(windll=outer):
        with install_fake_win32(windll=inner):
            assert ctypes.windll is inner
        assert ctypes.windll is outer


def test_the_harness_never_names_a_real_win32_loader():
    # The one property that makes this file safe to import on the owner's
    # machine: it cannot reach a real DLL, so it cannot change any OS state.
    source = Path(__file__).with_name("fake_win32.py").read_text(encoding="utf-8")
    for forbidden in (
        "ctypes.windll.",
        "ctypes.WinDLL(",
        "ctypes.OleDLL(",
        "ctypes.cdll",
        "ctypes.CDLL(",
        "from ctypes import wintypes",
        "import ctypes.wintypes",
    ):
        assert forbidden not in source, f"the double must not contain {forbidden!r}"
