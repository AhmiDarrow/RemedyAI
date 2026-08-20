"""Doubles for every Win32 surface Remedy's desktop code touches.

What breaks if this code is wrong: the desktop layer (``desktop_win``,
``desktop_uia``, ``execution/host/conpty``) is the one part of Remedy that can
physically move the owner's mouse, type into whatever window happens to be in
front, replace the clipboard or spawn a real console. Tests for it must never
reach the real API. This module supplies stand-ins — a recording ``windll``, a
walkable UI Automation tree, and an in-memory console/pipe host — so those code
paths can be exercised on a build agent, a Mac, or the owner's own machine
without a single pixel moving.

It is a DOUBLE, not a wrapper: nothing here loads user32/kernel32 or calls a
real Win32 entry point, and it imports cleanly on non-Windows so any test that
imports it can at least be collected everywhere. (Code under test that itself
builds ``ctypes.WINFUNCTYPE`` callbacks or ``ctypes.wintypes`` structures still
needs a Windows interpreter — the double cannot conjure those.)

Typical use::

    from tests.harness.fake_win32 import FakeDesktop, FakeWindow, install_fake_win32

    def test_something():
        desktop = FakeDesktop(windows=[FakeWindow(hwnd=1, title="Notepad")])
        with install_fake_win32(desktop=desktop) as fake:
            ...  # call the code under test
        assert fake.log.count("user32.SendInput") == 0
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import types
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

# The attribute names live in strings so that this module contains no literal
# expression that could load or reach into a real DLL — the grep-able proof
# (see the self-check) that nothing here can call the real thing.
_WINDLL_ATTR = "windll"
_WINDLL_FACTORY_ATTRS = ("WinDLL", "OleDLL")

__all__ = [
    "CONPTY_PRELUDE",
    "CONTROL_TYPE_IDS",
    "CallLog",
    "CallRecord",
    "FakeConsoleHost",
    "FakeDLL",
    "FakeDesktop",
    "FakeFunction",
    "FakePipe",
    "FakeUIAElement",
    "FakeUIAutomation",
    "FakeWin32",
    "FakeWinDLL",
    "FakeWindow",
    "build_fake_comtypes",
    "fake_cmd_shell",
    "install_fake_win32",
    "uia_element",
]


# --------------------------------------------------------------------------
# ctypes argument helpers (pure inspection of caller-owned memory)
# --------------------------------------------------------------------------


def deref(arg: Any) -> Any:
    """The object behind a ``ctypes.byref()`` wrapper (or the arg itself)."""
    return getattr(arg, "_obj", arg)


def handle_value(arg: Any) -> int:
    """Best-effort int for anything used as a Win32 HANDLE."""
    obj = deref(arg)
    if isinstance(obj, int):
        return obj
    value = getattr(obj, "value", None)
    if isinstance(value, int):
        return value
    if value is None:
        return 0
    with contextlib.suppress(TypeError, ValueError):
        return int(value)
    return 0


def set_out(arg: Any, value: Any) -> None:
    """Write an out-parameter (``byref(x)`` or a bare ctypes instance)."""
    deref(arg).value = value


def write_text_buffer(buf: Any, text: str, size: int) -> int:
    """Fill a ``create_unicode_buffer`` the way GetWindowTextW does.

    Returns the number of characters written, NOT counting the terminator —
    which is exactly what the real API returns.
    """
    room = max(0, int(size) - 1)
    cut = text[:room]
    with contextlib.suppress(Exception):
        buf.value = cut
    return len(cut)


def buffer_bytes(buf: Any, n: int) -> bytes:
    """Read *n* bytes out of a create_string_buffer-ish object."""
    raw = getattr(buf, "raw", None)
    if raw is not None:
        return bytes(raw[: max(0, int(n))])
    if isinstance(buf, (bytes, bytearray)):
        return bytes(buf[: max(0, int(n))])
    return b""


# --------------------------------------------------------------------------
# Call recording
# --------------------------------------------------------------------------


@dataclass
class CallRecord:
    dll: str
    func: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    result: Any = None
    error: BaseException | None = None

    @property
    def name(self) -> str:
        return f"{self.dll}.{self.func}"

    def __repr__(self) -> str:  # readable pytest failure output
        return f"<call {self.name} args={len(self.args)} -> {self.result!r}>"


class CallLog:
    """Every call made through the fake DLLs, in order."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def record(self, rec: CallRecord) -> CallRecord:
        self.records.append(rec)
        return rec

    def _matches(self, name: str) -> list[CallRecord]:
        want = name.strip()
        return [r for r in self.records if r.name == want or r.func == want]

    def calls(self, name: str) -> list[CallRecord]:
        return self._matches(name)

    def count(self, name: str) -> int:
        return len(self._matches(name))

    def last(self, name: str) -> CallRecord | None:
        got = self._matches(name)
        return got[-1] if got else None

    def args_for(self, name: str) -> list[tuple[Any, ...]]:
        return [r.args for r in self._matches(name)]

    def names(self) -> list[str]:
        return [r.name for r in self.records]

    def clear(self) -> None:
        self.records.clear()

    def __contains__(self, name: object) -> bool:
        return bool(isinstance(name, str) and self._matches(name))

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[CallRecord]:
        return iter(self.records)

    def __repr__(self) -> str:
        return f"<CallLog {len(self.records)} calls: {', '.join(self.names()[:8])}>"


class FakeFunction:
    """One entry point. Records the call, returns a scripted value.

    Resolution order for the result: an installed *handler*, then the queued
    ``returns`` sequence, then a fixed ``return_value``, then the DLL default.
    ``argtypes``/``restype`` may be assigned freely — real callers do — and are
    kept for inspection but never enforced.
    """

    def __init__(
        self,
        dll: str,
        name: str,
        log: CallLog,
        *,
        default: Any = 1,
        handler: Callable[..., Any] | None = None,
    ) -> None:
        self.dll = dll
        self.name = name
        self.log = log
        self.default = default
        self.handler = handler
        self.return_value: Any = _UNSET
        self.returns: list[Any] = []
        self.error: BaseException | None = None
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes: Any = None
        self.restype: Any = None
        self.errcheck: Any = None

    def set_return(self, value: Any) -> FakeFunction:
        self.return_value = value
        return self

    def set_returns(self, values: Iterable[Any]) -> FakeFunction:
        self.returns = list(values)
        return self

    def set_handler(self, handler: Callable[..., Any] | None) -> FakeFunction:
        self.handler = handler
        return self

    def set_error(self, exc: BaseException) -> FakeFunction:
        self.error = exc
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(args)
        rec = CallRecord(self.dll, self.name, args, dict(kwargs))
        self.log.record(rec)
        if self.error is not None:
            rec.error = self.error
            raise self.error
        try:
            if self.handler is not None:
                result = self.handler(*args, **kwargs)
            elif self.returns:
                result = self.returns.pop(0)
            elif self.return_value is not _UNSET:
                result = self.return_value
            else:
                result = self.default
        except BaseException as exc:  # a scripted handler may model a failure
            rec.error = exc
            raise
        rec.result = result
        return result

    def __repr__(self) -> str:
        return f"<FakeFunction {self.dll}.{self.name} calls={len(self.calls)}>"


class _Unset:
    def __repr__(self) -> str:
        return "<unset>"

    def __bool__(self) -> bool:
        return False


_UNSET = _Unset()


class FakeDLL:
    """A stand-in for one loaded DLL. Unknown names are created on demand.

    Names listed in *missing* raise AttributeError instead, so a test can model
    an older Windows where e.g. ``CreatePseudoConsole`` does not exist.
    """

    def __init__(
        self,
        name: str,
        log: CallLog | None = None,
        *,
        default_return: Any = 1,
        missing: Iterable[str] = (),
    ) -> None:
        self.name = name
        self.log = log if log is not None else CallLog()
        self.default_return = default_return
        self.missing: set[str] = set(missing)
        self.functions: dict[str, FakeFunction] = {}

    def function(self, name: str) -> FakeFunction:
        fn = self.functions.get(name)
        if fn is None:
            fn = FakeFunction(self.name, name, self.log, default=self.default_return)
            self.functions[name] = fn
        return fn

    def on(self, name: str, handler: Callable[..., Any]) -> FakeFunction:
        return self.function(name).set_handler(handler)

    def set_return(self, name: str, value: Any) -> FakeFunction:
        return self.function(name).set_return(value)

    def set_missing(self, *names: str) -> None:
        self.missing.update(names)

    def __getattr__(self, name: str) -> Any:
        # Never invent dunders: hasattr() probes for copy/pickle protocols would
        # otherwise hand back a callable and confuse the interpreter.
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.missing:
            raise AttributeError(f"{self.name} has no export {name!r}")
        return self.function(name)

    def __getitem__(self, key: Any) -> Any:
        # ctypes supports ordinal/name indexing; a few call sites use it.
        return self.function(str(key))

    def __repr__(self) -> str:
        return f"<FakeDLL {self.name} exports={sorted(self.functions)}>"


class FakeWinDLL:
    """Stands in for ``ctypes.windll`` AND for the ``ctypes.WinDLL`` factory."""

    def __init__(self, log: CallLog | None = None, *, default_return: Any = 1) -> None:
        self.log = log if log is not None else CallLog()
        self.default_return = default_return
        self.dlls: dict[str, FakeDLL] = {}

    def dll(self, name: str) -> FakeDLL:
        key = str(name).lower().removesuffix(".dll")
        got = self.dlls.get(key)
        if got is None:
            got = FakeDLL(key, self.log, default_return=self.default_return)
            self.dlls[key] = got
        return got

    # Called the way the loader is: WinDLL("kernel32", use_last_error=True).
    # The same fake DLL comes back every time, so a test can script it up front.
    def __call__(self, name: str, *args: Any, **kwargs: Any) -> FakeDLL:
        return self.dll(name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.dll(name)

    def __getitem__(self, name: str) -> FakeDLL:
        return self.dll(name)

    def LoadLibrary(self, name: str) -> FakeDLL:  # noqa: N802 - ctypes spelling
        return self.dll(name)

    def __repr__(self) -> str:
        return f"<FakeWinDLL loaded={sorted(self.dlls)}>"


# --------------------------------------------------------------------------
# Desktop model — windows, foreground, clipboard, input
# --------------------------------------------------------------------------


@dataclass
class FakeWindow:
    hwnd: int
    title: str = ""
    left: int = 0
    top: int = 0
    right: int = 800
    bottom: int = 600
    visible: bool = True
    class_name: str = "FakeWindowClass"
    thread_id: int = 4242
    process_id: int = 1234
    root_owner: int | None = None
    # Observed effects — the double records instead of acting.
    show_commands: list[int] = field(default_factory=list)
    posted: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def set_bounds(self, left: int, top: int, width: int, height: int) -> None:
        self.left, self.top = int(left), int(top)
        self.right, self.bottom = int(left) + int(width), int(top) + int(height)


class FakeDesktop:
    """An imaginary desktop: windows, a foreground, a clipboard, an input sink.

    ``SendInput`` is *recorded only*. Nothing here can move a pointer or press
    a key; ``sent_input`` exists so a test can assert an action was attempted
    (or, more usefully, that it was NOT).
    """

    def __init__(
        self,
        windows: Sequence[FakeWindow] | None = None,
        *,
        foreground: int = 0,
        clipboard: str = "",
        screen: tuple[int, int] = (1920, 1080),
        foreground_locked: bool = False,
        dib_fill: int = 0x20,
    ) -> None:
        self.windows: list[FakeWindow] = list(windows or [])
        self.foreground = foreground or (self.windows[0].hwnd if self.windows else 0)
        self.clipboard = clipboard
        self.screen = screen
        # When True, SetForegroundWindow is ignored — Windows' foreground lock.
        self.foreground_locked = foreground_locked
        self.dib_fill = dib_fill
        self.sent_input: list[Any] = []
        self.attached_threads: list[tuple[int, int, bool]] = []
        self.clipboard_open = False
        self.clipboard_writes: list[str] = []
        self.current_thread_id = 999
        self._heap: dict[int, Any] = {}
        self._freed: set[int] = set()

    # -- window lookup ----------------------------------------------------
    def window(self, hwnd: Any) -> FakeWindow | None:
        want = handle_value(hwnd)
        for w in self.windows:
            if w.hwnd == want:
                return w
        return None

    def add_window(self, window: FakeWindow) -> FakeWindow:
        self.windows.append(window)
        return window

    # -- fake global heap (process-local ctypes memory, never a Win32 heap) -
    def _alloc(self, text: str) -> int:
        buf = ctypes.create_unicode_buffer(text)
        addr = ctypes.addressof(buf)
        self._heap[addr] = buf  # keep alive: a dropped buffer is a dangling read
        return addr

    # -- wiring -----------------------------------------------------------
    def install_into(self, windll: FakeWinDLL) -> None:
        user32 = windll.dll("user32")
        kernel32 = windll.dll("kernel32")
        gdi32 = windll.dll("gdi32")
        shcore = windll.dll("shcore")
        self._wire_windows(user32)
        self._wire_input(user32)
        self._wire_clipboard(user32, kernel32)
        self._wire_gdi(user32, gdi32)
        shcore.on("SetProcessDpiAwareness", lambda *_a: 0)
        kernel32.on("GetCurrentThreadId", lambda *_a: self.current_thread_id)

    def _wire_windows(self, user32: FakeDLL) -> None:
        def enum_windows(callback: Any, lparam: Any = 0) -> int:
            for w in list(self.windows):
                if not callback(w.hwnd, lparam):
                    break
            return 1

        def get_window_rect(hwnd: Any, prect: Any) -> int:
            w = self.window(hwnd)
            if w is None:
                return 0
            rect = deref(prect)
            for attr, value in (
                ("left", w.left),
                ("top", w.top),
                ("right", w.right),
                ("bottom", w.bottom),
            ):
                with contextlib.suppress(Exception):
                    setattr(rect, attr, value)
            return 1

        def set_window_pos(hwnd: Any, _after: Any, x: Any, y: Any, cx: Any, cy: Any, _f: Any) -> int:
            w = self.window(hwnd)
            if w is None:
                return 0
            w.set_bounds(int(x), int(y), int(cx), int(cy))
            return 1

        def set_foreground(hwnd: Any) -> int:
            if self.foreground_locked:
                return 0
            w = self.window(hwnd)
            if w is None:
                return 0
            self.foreground = w.hwnd
            return 1

        def get_ancestor(hwnd: Any, _flag: Any) -> int:
            w = self.window(hwnd)
            if w is None:
                return handle_value(hwnd)
            return int(w.root_owner if w.root_owner is not None else w.hwnd)

        def get_thread_process_id(hwnd: Any, ppid: Any = None) -> int:
            w = self.window(hwnd)
            if w is None:
                return 0
            if ppid:
                with contextlib.suppress(Exception):
                    set_out(ppid, w.process_id)
            return w.thread_id

        def show_window(hwnd: Any, cmd: Any) -> int:
            w = self.window(hwnd)
            if w is None:
                return 0
            w.show_commands.append(int(cmd))
            return 1

        def post_message(hwnd: Any, msg: Any, wp: Any = 0, lp: Any = 0) -> int:
            w = self.window(hwnd)
            if w is None:
                return 0
            w.posted.append((int(msg), int(wp), int(lp)))
            return 1

        def attach_thread_input(a: Any, b: Any, flag: Any) -> int:
            self.attached_threads.append((int(a), int(b), bool(flag)))
            return 1

        user32.on("EnumWindows", enum_windows)
        user32.on("EnumChildWindows", lambda _h, cb, lp=0: enum_windows(cb, lp))
        user32.on("IsWindowVisible", lambda h: 1 if (w := self.window(h)) and w.visible else 0)
        user32.on("IsWindow", lambda h: 1 if self.window(h) else 0)
        user32.on(
            "GetWindowTextLengthW",
            lambda h: len(w.title) if (w := self.window(h)) else 0,
        )
        user32.on(
            "GetWindowTextW",
            lambda h, buf, n: write_text_buffer(buf, w.title if (w := self.window(h)) else "", n),
        )
        user32.on(
            "GetClassNameW",
            lambda h, buf, n: write_text_buffer(
                buf, w.class_name if (w := self.window(h)) else "", n
            ),
        )
        user32.on("GetWindowRect", get_window_rect)
        user32.on("GetClientRect", get_window_rect)
        user32.on("SetWindowPos", set_window_pos)
        user32.on("GetForegroundWindow", lambda: int(self.foreground))
        user32.on("SetForegroundWindow", set_foreground)
        user32.on("BringWindowToTop", lambda _h: 1)
        user32.on("GetAncestor", get_ancestor)
        user32.on("GetWindowThreadProcessId", get_thread_process_id)
        user32.on("ShowWindow", show_window)
        user32.on("PostMessageW", post_message)
        user32.on("SendMessageW", lambda *a: post_message(*a[:4]))
        user32.on("AttachThreadInput", attach_thread_input)
        user32.on("GetSystemMetrics", lambda i: self.screen[0] if int(i) == 0 else self.screen[1])

    def _wire_input(self, user32: FakeDLL) -> None:
        def send_input(n: Any, arr: Any, size: Any) -> int:
            # RECORDED, never delivered. This is the line that separates a
            # double from a machine that types on the owner's keyboard.
            self.sent_input.append((int(n), arr, int(size)))
            return int(n)

        def set_cursor_pos(x: Any, y: Any) -> int:
            self.sent_input.append(("SetCursorPos", int(x), int(y)))
            return 1

        def get_cursor_pos(ppt: Any) -> int:
            pt = deref(ppt)
            with contextlib.suppress(Exception):
                pt.x, pt.y = 0, 0
            return 1

        user32.on("SendInput", send_input)
        user32.on("SetCursorPos", set_cursor_pos)
        user32.on("GetCursorPos", get_cursor_pos)
        user32.on("VkKeyScanW", lambda ch: ord(ch) if isinstance(ch, str) and ch else 0)
        user32.on("MapVirtualKeyW", lambda code, _t=0: int(code))
        user32.on("SetProcessDpiAwarenessContext", lambda _ctx: 1)
        user32.on("SetProcessDPIAware", lambda: 1)
        user32.on("keybd_event", lambda *a: self.sent_input.append(("keybd_event", a)))
        user32.on("mouse_event", lambda *a: self.sent_input.append(("mouse_event", a)))

    def _wire_clipboard(self, user32: FakeDLL, kernel32: FakeDLL) -> None:
        def open_clipboard(_hwnd: Any = 0) -> int:
            self.clipboard_open = True
            return 1

        def close_clipboard() -> int:
            self.clipboard_open = False
            return 1

        def get_clipboard_data(_fmt: Any) -> int:
            return self._alloc(self.clipboard)

        def set_clipboard_data(_fmt: Any, handle: Any) -> int:
            addr = handle_value(handle)
            text = ""
            if addr in self._heap:
                with contextlib.suppress(Exception):
                    text = ctypes.wstring_at(addr)
            self.clipboard = text
            self.clipboard_writes.append(text)
            return addr or 1

        def global_alloc(_flags: Any, nbytes: Any) -> int:
            buf = ctypes.create_string_buffer(max(2, int(nbytes)))
            addr = ctypes.addressof(buf)
            self._heap[addr] = buf
            return addr

        user32.on("OpenClipboard", open_clipboard)
        user32.on("CloseClipboard", close_clipboard)
        user32.on("EmptyClipboard", lambda: 1)
        user32.on("IsClipboardFormatAvailable", lambda _f: 1)
        user32.on("GetClipboardData", get_clipboard_data)
        user32.on("SetClipboardData", set_clipboard_data)
        kernel32.on("GlobalAlloc", global_alloc)
        kernel32.on("GlobalLock", lambda h: handle_value(h))
        kernel32.on("GlobalUnlock", lambda _h: 1)
        # Freeing is bookkeeping only: the buffer stays alive so that a
        # use-after-free in the code under test reads sane memory, not a crash.
        kernel32.on("GlobalFree", lambda h: self._freed.add(handle_value(h)) or 0)
        kernel32.on("GlobalSize", lambda h: len(self._heap.get(handle_value(h), b"") or b""))

    def _wire_gdi(self, user32: FakeDLL, gdi32: FakeDLL) -> None:
        def get_dibits(_dc: Any, _bmp: Any, _start: Any, lines: Any, buf: Any, *_rest: Any) -> int:
            with contextlib.suppress(Exception):
                size = ctypes.sizeof(buf)
                buf.raw = bytes([self.dib_fill & 0xFF]) * size
            return int(lines)

        for name in ("GetWindowDC", "GetDC"):
            user32.on(name, lambda *_a: 0x1000)
        user32.on("ReleaseDC", lambda *_a: 1)
        user32.on("PrintWindow", lambda *_a: 1)
        gdi32.on("CreateCompatibleDC", lambda *_a: 0x2000)
        gdi32.on("CreateCompatibleBitmap", lambda *_a: 0x3000)
        gdi32.on("SelectObject", lambda *_a: 0x3001)
        gdi32.on("BitBlt", lambda *_a: 1)
        gdi32.on("GetDIBits", get_dibits)
        gdi32.on("DeleteObject", lambda *_a: 1)
        gdi32.on("DeleteDC", lambda *_a: 1)


# --------------------------------------------------------------------------
# UI Automation
# --------------------------------------------------------------------------

CONTROL_TYPE_IDS: dict[str, int] = {
    "button": 50000,
    "calendar": 50001,
    "checkbox": 50002,
    "combobox": 50003,
    "edit": 50004,
    "hyperlink": 50005,
    "image": 50006,
    "listitem": 50007,
    "list": 50008,
    "menu": 50009,
    "menubar": 50010,
    "menuitem": 50011,
    "progressbar": 50012,
    "radiobutton": 50013,
    "scrollbar": 50014,
    "slider": 50015,
    "spinner": 50016,
    "tab": 50018,
    "tabitem": 50019,
    "text": 50020,
    "toolbar": 50021,
    "tooltip": 50022,
    "tree": 50023,
    "treeitem": 50024,
    "custom": 50025,
    "group": 50026,
    "thumb": 50027,
    "datagrid": 50028,
    "dataitem": 50029,
    "document": 50030,
    "splitbutton": 50031,
    "window": 50032,
    "pane": 50033,
    "header": 50034,
    "headeritem": 50035,
    "table": 50036,
    "titlebar": 50037,
}

# Property ids (UIAutomationClient)
UIA_BoundingRectanglePropertyId = 30001
UIA_ControlTypePropertyId = 30003
UIA_NamePropertyId = 30005
UIA_IsEnabledPropertyId = 30010
UIA_IsOffscreenPropertyId = 30022
UIA_ValueValuePropertyId = 30045

# Tree scopes
TreeScope_Element = 1
TreeScope_Children = 2
TreeScope_Descendants = 4
TreeScope_Parent = 8
TreeScope_Subtree = 7
TreeScope_Ancestors = 16

# Pattern ids
UIA_InvokePatternId = 10000
UIA_ValuePatternId = 10002
UIA_TextPatternId = 10014
UIA_TogglePatternId = 10015
UIA_ScrollItemPatternId = 10017

_PATTERN_INTERFACE = {
    UIA_InvokePatternId: "IUIAutomationInvokePattern",
    UIA_ValuePatternId: "IUIAutomationValuePattern",
    UIA_TextPatternId: "IUIAutomationTextPattern",
    UIA_TogglePatternId: "IUIAutomationTogglePattern",
    UIA_ScrollItemPatternId: "IUIAutomationScrollItemPattern",
}

_TOGGLE_STATES = {"off": 0, "on": 1, "indeterminate": 2}


class FakeInterface:
    """Sentinel standing in for a COM interface type from the typelib."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"<iface {self.name}>"


class FakeCondition:
    def __init__(self, kind: str, prop: int | None = None, value: Any = None) -> None:
        self.kind = kind
        self.prop = prop
        self.value = value

    def matches(self, element: FakeUIAElement) -> bool:
        if self.kind == "true":
            return True
        if self.kind == "property":
            return element.property_value(self.prop) == self.value
        return False

    def __repr__(self) -> str:
        return f"<Condition {self.kind} {self.prop}={self.value!r}>"


class FakeElementArray:
    """IUIAutomationElementArray: ``.Length`` + ``.GetElement(i)``."""

    def __init__(self, elements: Sequence[FakeUIAElement]) -> None:
        self._elements = list(elements)

    @property
    def Length(self) -> int:  # noqa: N802 - COM spelling
        return len(self._elements)

    def GetElement(self, index: int) -> FakeUIAElement:  # noqa: N802 - COM spelling
        return self._elements[int(index)]

    def __len__(self) -> int:
        return len(self._elements)


class FakePattern:
    """One UIA control pattern. QueryInterface refuses the wrong interface."""

    def __init__(self, pattern_id: int, element: FakeUIAElement) -> None:
        self.pattern_id = pattern_id
        self.element = element
        self.iface = _PATTERN_INTERFACE.get(pattern_id, "IUnknown")
        self.error: BaseException | None = None

    def QueryInterface(self, iface: Any) -> FakePattern:  # noqa: N802 - COM spelling
        wanted = getattr(iface, "name", None)
        if wanted is not None and wanted != self.iface:
            raise TypeError(f"{self.iface} cannot be queried as {wanted}")
        return self

    def _act(self, action: str, *detail: Any) -> None:
        if self.error is not None:
            raise self.error
        self.element.actions.append((action, *detail))


class FakeInvokePattern(FakePattern):
    def Invoke(self) -> None:  # noqa: N802 - COM spelling
        self._act("invoke")


class FakeValuePattern(FakePattern):
    def SetValue(self, value: Any) -> None:  # noqa: N802 - COM spelling
        self._act("set_value", str(value))
        if not self.element.readonly:
            self.element.value = str(value)

    @property
    def CurrentValue(self) -> str:  # noqa: N802 - COM spelling
        return self.element.value or ""


class FakeTogglePattern(FakePattern):
    def Toggle(self) -> None:  # noqa: N802 - COM spelling
        self._act("toggle")
        self.element.toggle_state = 0 if self.element.toggle_state == 1 else 1

    @property
    def CurrentToggleState(self) -> int:  # noqa: N802 - COM spelling
        return int(self.element.toggle_state)


class FakeScrollItemPattern(FakePattern):
    def ScrollIntoView(self) -> None:  # noqa: N802 - COM spelling
        self._act("scroll_into_view")
        self.element.offscreen = False


class FakeTextRange:
    def __init__(self, text: str) -> None:
        self.text = text

    def GetText(self, max_length: int = -1) -> str:  # noqa: N802 - COM spelling
        n = int(max_length)
        return self.text if n < 0 else self.text[:n]


class FakeTextPattern(FakePattern):
    @property
    def DocumentRange(self) -> FakeTextRange:  # noqa: N802 - COM spelling
        return FakeTextRange(self.element.text or "")


_PATTERN_CLASSES: dict[int, type[FakePattern]] = {
    UIA_InvokePatternId: FakeInvokePattern,
    UIA_ValuePatternId: FakeValuePattern,
    UIA_TogglePatternId: FakeTogglePattern,
    UIA_ScrollItemPatternId: FakeScrollItemPattern,
    UIA_TextPatternId: FakeTextPattern,
}


class FakeUIAElement:
    """One node of the fake automation tree.

    *bounds* is (left, top, width, height) — the shape UIA actually hands back
    for BoundingRectangle, which is not the same as a Win32 RECT.
    """

    def __init__(
        self,
        name: str = "",
        control_type: int | str = "custom",
        *,
        bounds: tuple[int, int, int, int] = (0, 0, 100, 30),
        value: str | None = None,
        text: str | None = None,
        enabled: bool = True,
        offscreen: bool = False,
        readonly: bool = False,
        hwnd: int = 0,
        toggle: str | int | None = None,
        invokable: bool = False,
        scrollable: bool = False,
        children: Sequence[FakeUIAElement] = (),
        properties: dict[int, Any] | None = None,
        raise_on: Iterable[int] = (),
    ) -> None:
        self.name = name
        self.control_type = (
            CONTROL_TYPE_IDS.get(control_type, 50025)
            if isinstance(control_type, str)
            else int(control_type)
        )
        self.bounds = tuple(int(v) for v in bounds)  # type: ignore[assignment]
        self.value = value
        self.text = text
        self.enabled = enabled
        self.offscreen = offscreen
        self.readonly = readonly
        self.hwnd = int(hwnd)
        self.parent: FakeUIAElement | None = None
        self.children: list[FakeUIAElement] = []
        self.properties: dict[int, Any] = dict(properties or {})
        self.raise_on: set[int] = set(raise_on)
        self.actions: list[tuple[Any, ...]] = []
        self.find_all_calls: list[tuple[int, FakeCondition]] = []
        self.toggle_state = (
            _TOGGLE_STATES.get(toggle, 0) if isinstance(toggle, str) else int(toggle or 0)
        )

        self.patterns: dict[int, FakePattern] = {}
        if invokable:
            self.add_pattern(UIA_InvokePatternId)
        if value is not None:
            self.add_pattern(UIA_ValuePatternId)
        if text is not None:
            self.add_pattern(UIA_TextPatternId)
        if toggle is not None:
            self.add_pattern(UIA_TogglePatternId)
        if scrollable:
            self.add_pattern(UIA_ScrollItemPatternId)
        for child in children:
            self.add_child(child)

    # -- tree -------------------------------------------------------------
    def add_child(self, child: FakeUIAElement) -> FakeUIAElement:
        child.parent = self
        self.children.append(child)
        return child

    def descendants(self) -> list[FakeUIAElement]:
        out: list[FakeUIAElement] = []
        for child in self.children:
            out.append(child)
            out.extend(child.descendants())
        return out

    def add_pattern(self, pattern_id: int) -> FakePattern:
        cls = _PATTERN_CLASSES.get(pattern_id, FakePattern)
        pat = cls(pattern_id, self)
        self.patterns[pattern_id] = pat
        return pat

    # -- the COM-ish surface ---------------------------------------------
    def property_value(self, prop_id: int | None) -> Any:
        if prop_id in self.properties:
            return self.properties[prop_id]
        if prop_id == UIA_NamePropertyId:
            return self.name
        if prop_id == UIA_ControlTypePropertyId:
            return self.control_type
        if prop_id == UIA_ValueValuePropertyId:
            return self.value
        if prop_id == UIA_BoundingRectanglePropertyId:
            return tuple(self.bounds)
        if prop_id == UIA_IsEnabledPropertyId:
            return self.enabled
        if prop_id == UIA_IsOffscreenPropertyId:
            return self.offscreen
        return None

    def GetCurrentPropertyValue(self, prop_id: int) -> Any:  # noqa: N802 - COM spelling
        if prop_id in self.raise_on:
            raise RuntimeError(f"UIA property {prop_id} unavailable on {self.name!r}")
        return self.property_value(prop_id)

    def GetCurrentPattern(self, pattern_id: int) -> FakePattern | None:  # noqa: N802
        return self.patterns.get(int(pattern_id))

    def FindAll(self, scope: int, condition: FakeCondition) -> FakeElementArray:  # noqa: N802
        self.find_all_calls.append((int(scope), condition))
        pool: list[FakeUIAElement] = []
        scope = int(scope)
        if scope & TreeScope_Element:
            pool.append(self)
        if scope & TreeScope_Descendants:
            pool.extend(self.descendants())
        elif scope & TreeScope_Children:
            pool.extend(self.children)
        return FakeElementArray([el for el in pool if condition.matches(el)])

    def FindFirst(self, scope: int, condition: FakeCondition) -> FakeUIAElement | None:  # noqa: N802
        found = self.FindAll(scope, condition)
        return found.GetElement(0) if found.Length else None

    @property
    def CurrentName(self) -> str:  # noqa: N802 - COM spelling
        return self.name

    @property
    def CurrentNativeWindowHandle(self) -> int:  # noqa: N802 - COM spelling
        return self.hwnd

    @property
    def CurrentBoundingRectangle(self) -> tuple[int, ...]:  # noqa: N802 - COM spelling
        return tuple(self.bounds)

    def __repr__(self) -> str:
        role = next((k for k, v in CONTROL_TYPE_IDS.items() if v == self.control_type), "?")
        return f"<uia {role} {self.name!r} kids={len(self.children)}>"


def uia_element(name: str = "", control_type: int | str = "custom", **kw: Any) -> FakeUIAElement:
    """Shorthand builder so trees read as a literal in the test."""
    return FakeUIAElement(name, control_type, **kw)


class FakeUIAutomation:
    """Stands in for the CUIAutomation COM object."""

    def __init__(
        self,
        root: FakeUIAElement | None = None,
        *,
        focused: FakeUIAElement | None = None,
    ) -> None:
        self.root = root if root is not None else FakeUIAElement("Desktop", "pane")
        self.focused = focused
        self.calls: list[tuple[str, Any]] = []

    # -- construction helpers ---------------------------------------------
    def add_window(self, element: FakeUIAElement) -> FakeUIAElement:
        return self.root.add_child(element)

    def element_for_hwnd(self, hwnd: int) -> FakeUIAElement | None:
        want = int(hwnd)
        for el in [self.root, *self.root.descendants()]:
            if el.hwnd and el.hwnd == want:
                return el
        return None

    # -- IUIAutomation ----------------------------------------------------
    def GetRootElement(self) -> FakeUIAElement:  # noqa: N802 - COM spelling
        self.calls.append(("GetRootElement", None))
        return self.root

    def ElementFromHandle(self, hwnd: int) -> FakeUIAElement | None:  # noqa: N802
        self.calls.append(("ElementFromHandle", int(hwnd)))
        return self.element_for_hwnd(int(hwnd))

    def GetFocusedElement(self) -> FakeUIAElement | None:  # noqa: N802 - COM spelling
        self.calls.append(("GetFocusedElement", None))
        return self.focused

    def CreateTrueCondition(self) -> FakeCondition:  # noqa: N802 - COM spelling
        return FakeCondition("true")

    def CreatePropertyCondition(self, prop_id: int, value: Any) -> FakeCondition:  # noqa: N802
        return FakeCondition("property", int(prop_id), value)

    def __repr__(self) -> str:
        return f"<FakeUIAutomation root={self.root!r}>"


def build_fake_comtypes(automation: FakeUIAutomation) -> dict[str, types.ModuleType]:
    """The module objects ``desktop_uia`` expects to import, all fakes.

    Returns a mapping suitable for ``sys.modules.update``: ``comtypes``,
    ``comtypes.client``, ``comtypes.gen`` and ``comtypes.gen.UIAutomationClient``.
    """
    comtypes = types.ModuleType("comtypes")
    client = types.ModuleType("comtypes.client")
    gen = types.ModuleType("comtypes.gen")
    uia_mod = types.ModuleType("comtypes.gen.UIAutomationClient")

    class COMError(Exception):  # noqa: N801 - mirrors comtypes' own spelling
        pass

    comtypes.COMError = COMError  # type: ignore[attr-defined]
    comtypes.CoInitialize = lambda *_a, **_k: None  # type: ignore[attr-defined]
    comtypes.CoUninitialize = lambda *_a, **_k: None  # type: ignore[attr-defined]
    comtypes.GUID = str  # type: ignore[attr-defined]
    comtypes.client = client  # type: ignore[attr-defined]
    comtypes.gen = gen  # type: ignore[attr-defined]

    client.CreateObject = lambda *_a, **_k: automation  # type: ignore[attr-defined]
    client.GetModule = lambda *_a, **_k: uia_mod  # type: ignore[attr-defined]

    gen.UIAutomationClient = uia_mod  # type: ignore[attr-defined]

    uia_mod.CUIAutomation = FakeInterface("CUIAutomation")  # type: ignore[attr-defined]
    for iface in (
        "IUIAutomation",
        "IUIAutomationElement",
        "IUIAutomationInvokePattern",
        "IUIAutomationValuePattern",
        "IUIAutomationTogglePattern",
        "IUIAutomationScrollItemPattern",
        "IUIAutomationTextPattern",
    ):
        setattr(uia_mod, iface, FakeInterface(iface))
    for const, value in (
        ("TreeScope_Element", TreeScope_Element),
        ("TreeScope_Children", TreeScope_Children),
        ("TreeScope_Descendants", TreeScope_Descendants),
        ("TreeScope_Parent", TreeScope_Parent),
        ("TreeScope_Subtree", TreeScope_Subtree),
        ("TreeScope_Ancestors", TreeScope_Ancestors),
        ("UIA_BoundingRectanglePropertyId", UIA_BoundingRectanglePropertyId),
        ("UIA_ControlTypePropertyId", UIA_ControlTypePropertyId),
        ("UIA_NamePropertyId", UIA_NamePropertyId),
        ("UIA_IsEnabledPropertyId", UIA_IsEnabledPropertyId),
        ("UIA_IsOffscreenPropertyId", UIA_IsOffscreenPropertyId),
        ("UIA_ValueValuePropertyId", UIA_ValueValuePropertyId),
        ("UIA_InvokePatternId", UIA_InvokePatternId),
        ("UIA_ValuePatternId", UIA_ValuePatternId),
        ("UIA_TextPatternId", UIA_TextPatternId),
        ("UIA_TogglePatternId", UIA_TogglePatternId),
        ("UIA_ScrollItemPatternId", UIA_ScrollItemPatternId),
    ):
        setattr(uia_mod, const, value)

    return {
        "comtypes": comtypes,
        "comtypes.client": client,
        "comtypes.gen": gen,
        "comtypes.gen.UIAutomationClient": uia_mod,
    }


# --------------------------------------------------------------------------
# ConPTY / console
# --------------------------------------------------------------------------


@dataclass
class FakePipe:
    """One anonymous pipe: a byte queue with two handle ends."""

    read_handle: int = 0
    write_handle: int = 0
    data: bytearray = field(default_factory=bytearray)
    writes: list[bytes] = field(default_factory=list)
    # bInheritHandle off the SECURITY_ATTRIBUTES the pipe was created with;
    # None when the caller passed NULL (which also means "not inheritable").
    inheritable: bool | None = None

    def push(self, chunk: bytes) -> None:
        self.data.extend(chunk)

    def take(self, n: int) -> bytes:
        chunk = bytes(self.data[: max(0, n)])
        del self.data[: len(chunk)]
        return chunk

    @property
    def written(self) -> bytes:
        return b"".join(self.writes)


class FakeConsoleHost:
    """kernel32's console/pipe/process surface, entirely in memory.

    Handles are small integers this object hands out; nothing is a real OS
    handle, so a leak here costs nothing and a double-close is recorded rather
    than corrupting the process.
    """

    def __init__(
        self,
        *,
        first_handle: int = 0x100,
        pid: int = 31337,
        create_pseudoconsole_hr: int = 0,
        fail_create_pipe: bool = False,
        fail_create_process: bool = False,
        fail_attribute_list: bool = False,
        last_error: int = 0,
        echo: bool = False,
        shell: Callable[[str], str] | None = None,
        vt_prelude: bytes = b"",
        newline_submits: bool = False,
    ) -> None:
        self._next = first_handle
        self.pid = pid
        self.create_pseudoconsole_hr = create_pseudoconsole_hr
        self.fail_create_pipe = fail_create_pipe
        self.fail_create_process = fail_create_process
        self.fail_attribute_list = fail_attribute_list
        self.last_error = last_error
        # A real pseudoconsole ECHOES what is typed into it, renders line ends
        # as cursor moves as often as CRLF, and opens with a burst of VT mode
        # switches. ``echo`` turns that on for the input pipe of every
        # pseudoconsole created here; ``shell`` is the child — a callable that
        # gets each typed line and returns what it prints.
        self.echo = echo
        self.shell = shell
        self.vt_prelude = vt_prelude
        # A console submits on CR only; a shell reading a PIPE takes LF. Set
        # this to model the pipe-fed shell with the same in-memory machinery.
        self.newline_submits = newline_submits
        self._console_lines: dict[int, bytearray] = {}
        self._prelude_sent: set[int] = set()
        self._rows: dict[int, int] = {}

        self.kinds: dict[int, str] = {}
        self.closed: list[int] = []
        self.double_closed: list[int] = []
        self.pipes: list[FakePipe] = []
        self._by_handle: dict[int, FakePipe] = {}
        self.pseudoconsoles: list[dict[str, Any]] = []
        self.closed_pseudoconsoles: list[int] = []
        self.spawns: list[dict[str, Any]] = []
        self.terminated: list[tuple[int, int]] = []
        self.process_handle = 0
        self.attribute_lists: list[Any] = []
        self.attribute_updates: list[tuple[Any, ...]] = []

    # -- bookkeeping -------------------------------------------------------
    def _handle(self, kind: str) -> int:
        h = self._next
        self._next += 4
        self.kinds[h] = kind
        return h

    @property
    def open_handles(self) -> list[int]:
        return [h for h in self.kinds if h not in self.closed]

    def pipe_for(self, handle: Any) -> FakePipe | None:
        return self._by_handle.get(handle_value(handle))

    def is_closed(self, handle: Any) -> bool:
        return handle_value(handle) in self.closed

    def feed(self, handle: Any, data: bytes) -> None:
        """Make *data* readable from the pipe that *handle* belongs to."""
        pipe = self.pipe_for(handle)
        if pipe is None:
            raise KeyError(f"no fake pipe for handle {handle_value(handle)}")
        pipe.push(data)

    def written(self, handle: Any) -> bytes:
        pipe = self.pipe_for(handle)
        return pipe.written if pipe else b""

    # -- wiring ------------------------------------------------------------
    def install_into(self, windll: FakeWinDLL) -> None:
        k32 = windll.dll("kernel32")
        k32.on("CreatePipe", self.CreatePipe)
        k32.on("CreatePseudoConsole", self.CreatePseudoConsole)
        k32.on("ClosePseudoConsole", self.ClosePseudoConsole)
        k32.on("ResizePseudoConsole", lambda *_a: 0)
        k32.on("CloseHandle", self.CloseHandle)
        k32.on("ReadFile", self.ReadFile)
        k32.on("WriteFile", self.WriteFile)
        k32.on("PeekNamedPipe", self.PeekNamedPipe)
        k32.on("InitializeProcThreadAttributeList", self.InitializeProcThreadAttributeList)
        k32.on("UpdateProcThreadAttribute", self.UpdateProcThreadAttribute)
        k32.on("DeleteProcThreadAttributeList", lambda *_a: 1)
        k32.on("CreateProcessW", self.CreateProcessW)
        k32.on("TerminateProcess", self.TerminateProcess)
        k32.on("GetLastError", lambda: self.last_error)

    # -- kernel32 entry points --------------------------------------------
    def CreatePipe(self, pread: Any, pwrite: Any, sa: Any = None, _size: Any = 0) -> int:  # noqa: N802
        if self.fail_create_pipe:
            return 0
        inheritable: bool | None = None
        attrs = deref(sa) if sa is not None else None
        if attrs is not None:
            with contextlib.suppress(Exception):
                inheritable = bool(attrs.bInheritHandle)
        pipe = FakePipe(read_handle=self._handle("pipe-read"), write_handle=self._handle("pipe-write"))
        pipe.inheritable = inheritable
        self.pipes.append(pipe)
        self._by_handle[pipe.read_handle] = pipe
        self._by_handle[pipe.write_handle] = pipe
        set_out(pread, pipe.read_handle)
        set_out(pwrite, pipe.write_handle)
        return 1

    def CreatePseudoConsole(  # noqa: N802 - Win32 spelling
        self,
        size: Any,
        hin: Any,
        hout: Any,
        flags: Any = 0,
        phpc: Any = None,
    ) -> int:
        cols = int(getattr(size, "X", 0) or 0)
        rows = int(getattr(size, "Y", 0) or 0)
        self.pseudoconsoles.append(
            {
                "cols": cols,
                "rows": rows,
                "input": handle_value(hin),
                "output": handle_value(hout),
                "flags": int(flags or 0),
            }
        )
        if self.create_pseudoconsole_hr != 0:
            # restype=HRESULT: ctypes does not hand a failing HRESULT back, it
            # raises OSError — the double must do the same or a dead "if hr"
            # branch in the code under test looks alive.
            raise OSError(None, "CreatePseudoConsole failed", None, self.create_pseudoconsole_hr)
        if phpc is not None:
            set_out(phpc, self._handle("pseudoconsole"))
        return 0

    # -- the console the child is "attached" to ----------------------------
    def _console_for_input(self, pipe: FakePipe) -> tuple[int, FakePipe] | None:
        for idx, pc in enumerate(self.pseudoconsoles):
            if pc["input"] in (pipe.read_handle, pipe.write_handle):
                out = self.pipe_for(pc["output"])
                if out is not None:
                    return idx, out
        return None

    def _console_type(self, pipe: FakePipe, chunk: bytes) -> None:
        """What conhost does with typed bytes: echo them, feed the child."""
        found = self._console_for_input(pipe)
        if found is None:
            return
        idx, out = found
        if idx not in self._prelude_sent:
            self._prelude_sent.add(idx)
            if self.vt_prelude:
                out.push(self.vt_prelude)
        buf = self._console_lines.setdefault(idx, bytearray())
        # Cooked console input is submitted by Enter, which is CR. A bare LF
        # is not a line end to conhost — it is typed into the current line
        # and, as observed live, not even echoed — so LF is dropped here.
        if self.newline_submits:
            buf.extend(chunk.replace(b"\r\n", b"\r").replace(b"\n", b"\r"))
        else:
            buf.extend(chunk.replace(b"\n", b""))
        while b"\r" in buf:
            line, _, rest = bytes(buf).partition(b"\r")
            buf[:] = rest
            text = line.decode("utf-8", errors="replace")
            if self.echo:
                # Echo the typed line, then move the cursor to the next row
                # instead of writing CRLF — exactly the rendering that glued
                # ``echo hello`` to the next line in the real stream.
                row = self._rows.get(idx, 1) + 1
                self._rows[idx] = row
                out.push(b"\x1b[?25l" + text.encode("utf-8") + f"\x1b[{row};1H".encode() + b"\x1b[?25h")
            if self.shell is not None:
                produced = self.shell(text)
                if produced:
                    out.push(produced.encode("utf-8"))
                    self._rows[idx] = self._rows.get(idx, 1) + produced.count("\n")

    def ClosePseudoConsole(self, hpc: Any) -> int:  # noqa: N802 - Win32 spelling
        h = handle_value(hpc)
        self.closed_pseudoconsoles.append(h)
        if h and h not in self.closed:
            self.closed.append(h)
        return 0

    def CloseHandle(self, handle: Any) -> int:  # noqa: N802 - Win32 spelling
        h = handle_value(handle)
        if not h:
            return 0
        if h in self.closed:
            self.double_closed.append(h)
            return 0
        self.closed.append(h)
        return 1

    def WriteFile(  # noqa: N802 - Win32 spelling
        self,
        handle: Any,
        buf: Any,
        n: Any,
        pwritten: Any = None,
        _overlapped: Any = None,
    ) -> int:
        pipe = self.pipe_for(handle)
        if pipe is None or self.is_closed(handle):
            if pwritten is not None:
                set_out(pwritten, 0)
            return 0
        chunk = buffer_bytes(buf, int(n))
        pipe.writes.append(chunk)
        if pwritten is not None:
            set_out(pwritten, len(chunk))
        if self.echo or self.shell is not None:
            self._console_type(pipe, chunk)
        return 1

    def ReadFile(  # noqa: N802 - Win32 spelling
        self,
        handle: Any,
        buf: Any,
        n: Any,
        pgot: Any = None,
        _overlapped: Any = None,
    ) -> int:
        pipe = self.pipe_for(handle)
        if pipe is None or self.is_closed(handle):
            if pgot is not None:
                set_out(pgot, 0)
            return 0
        chunk = pipe.take(int(n))
        if chunk:
            with contextlib.suppress(Exception):
                buf.raw = chunk
        if pgot is not None:
            set_out(pgot, len(chunk))
        return 1

    def PeekNamedPipe(  # noqa: N802 - Win32 spelling
        self,
        handle: Any,
        _buf: Any = None,
        _size: Any = 0,
        _pread: Any = None,
        pavail: Any = None,
        _pleft: Any = None,
    ) -> int:
        pipe = self.pipe_for(handle)
        if pavail is not None:
            set_out(pavail, len(pipe.data) if pipe else 0)
        return 1 if pipe else 0

    def InitializeProcThreadAttributeList(  # noqa: N802 - Win32 spelling
        self,
        lst: Any,
        _count: Any = 1,
        _flags: Any = 0,
        psize: Any = None,
    ) -> int:
        if not lst:
            # First probe call: report the buffer size and FAIL, exactly as the
            # real API does. Code that treats this as success is broken.
            if psize is not None:
                set_out(psize, 48)
            return 0
        if self.fail_attribute_list:
            return 0
        self.attribute_lists.append(lst)
        return 1

    def UpdateProcThreadAttribute(self, *args: Any) -> int:  # noqa: N802 - Win32 spelling
        self.attribute_updates.append(args)
        return 0 if self.fail_attribute_list else 1

    def CreateProcessW(  # noqa: N802 - Win32 spelling
        self,
        app: Any = None,
        cmdline: Any = None,
        _pattr: Any = None,
        _tattr: Any = None,
        inherit: Any = False,
        flags: Any = 0,
        env: Any = None,
        cwd: Any = None,
        psiex: Any = None,
        ppi: Any = None,
    ) -> int:
        startup_flags = 0
        std_handles: tuple[Any, Any, Any] | None = None
        si = deref(psiex) if psiex is not None else None
        si = getattr(si, "StartupInfo", si)
        if si is not None:
            with contextlib.suppress(Exception):
                startup_flags = int(si.dwFlags or 0)
            with contextlib.suppress(Exception):
                std_handles = (si.hStdInput, si.hStdOutput, si.hStdError)
        self.spawns.append(
            {
                "app": getattr(app, "value", app),
                "cmdline": getattr(cmdline, "value", cmdline),
                "cwd": getattr(cwd, "value", cwd),
                "flags": int(flags or 0),
                "inherit": bool(inherit),
                "env": getattr(env, "value", env),
                "startup_flags": startup_flags,
                "std_handles": std_handles,
            }
        )
        if self.fail_create_process:
            return 0
        self.process_handle = self._handle("process")
        thread_handle = self._handle("thread")
        if ppi is not None:
            pi = deref(ppi)
            with contextlib.suppress(Exception):
                pi.hProcess = self.process_handle
                pi.hThread = thread_handle
                pi.dwProcessId = self.pid
                pi.dwThreadId = self.pid + 1
        return 1

    def TerminateProcess(self, handle: Any, code: Any = 1) -> int:  # noqa: N802
        self.terminated.append((handle_value(handle), int(code)))
        return 1


#: The VT burst a real pseudoconsole emits before the child's first byte:
#: win32-input-mode on, focus events on, cursor hidden, clear, reset, home.
CONPTY_PRELUDE = b"\x1b[?9001h\x1b[?1004h\x1b[?25l\x1b[2J\x1b[m\x1b[H"


def fake_cmd_shell(cwd: str = "C:\\work") -> Callable[[str], str]:
    """Just enough of ``cmd.exe`` for the host session: ``echo``, ``cd``,
    ``%ERRORLEVEL%``, ``&``-joined commands and ``exit N``. Everything else
    (``chcp`` and friends) prints nothing. Lines end CRLF, as cmd's do.
    """
    state = {"errorlevel": 0}

    def run(line: str) -> str:
        out: list[str] = []
        for raw in line.split("&"):
            part = raw.strip().lstrip("@")
            lower = part.lower()
            if lower.startswith("echo "):
                text = part[5:].strip()
                if text.lower() in ("on", "off"):
                    continue
                out.append(text.replace("%ERRORLEVEL%", str(state["errorlevel"])))
            elif lower == "cd":
                out.append(cwd)
            elif lower.startswith("exit "):
                with contextlib.suppress(ValueError):
                    state["errorlevel"] = int(part.split()[1])
            elif part and not lower.startswith("chcp"):
                out.append(f"'{part.split()[0]}' is not recognized as an internal or external command,")
                state["errorlevel"] = 9009
        return "".join(o + "\r\n" for o in out)

    return run


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------


@dataclass
class FakeWin32:
    """Everything one installed test needs, in one object."""

    windll: FakeWinDLL
    log: CallLog
    desktop: FakeDesktop | None = None
    uia: FakeUIAutomation | None = None
    console: FakeConsoleHost | None = None

    @property
    def user32(self) -> FakeDLL:
        return self.windll.dll("user32")

    @property
    def kernel32(self) -> FakeDLL:
        return self.windll.dll("kernel32")

    @property
    def gdi32(self) -> FakeDLL:
        return self.windll.dll("gdi32")

    @property
    def ole32(self) -> FakeDLL:
        return self.windll.dll("ole32")


_MISSING = object()


class _AttrPatch:
    """Set/restore one attribute, including ones that did not exist."""

    def __init__(self, target: Any, name: str, value: Any) -> None:
        self.target = target
        self.name = name
        self.value = value
        self.old: Any = _MISSING

    def apply(self) -> None:
        self.old = getattr(self.target, self.name, _MISSING)
        setattr(self.target, self.name, self.value)

    def undo(self) -> None:
        if self.old is _MISSING:
            with contextlib.suppress(AttributeError):
                delattr(self.target, self.name)
        else:
            setattr(self.target, self.name, self.old)


@contextlib.contextmanager
def install_fake_win32(
    *,
    desktop: FakeDesktop | None = None,
    uia: FakeUIAutomation | None = None,
    console: FakeConsoleHost | None = None,
    windll: FakeWinDLL | None = None,
    platform: str | None = "win32",
    patch_comtypes: bool = True,
) -> Iterator[FakeWin32]:
    """Put the doubles in place of the real Win32 surfaces for one test.

    Patches ``ctypes.windll`` / ``ctypes.WinDLL`` / ``ctypes.OleDLL``, and (when
    a *uia* tree is given) the ``comtypes`` import surface. ``platform`` is
    stamped onto ``sys.platform`` so the ``sys.platform != "win32"`` guards all
    over the desktop code take the Windows branch; pass ``platform=None`` to
    leave it alone. Everything is restored on exit, including attributes that
    did not exist before (on Linux, ``ctypes`` has no ``windll``).
    """
    fake = windll or FakeWinDLL()
    if desktop is not None:
        desktop.install_into(fake)
    if console is not None:
        console.install_into(fake)

    patches: list[_AttrPatch] = [_AttrPatch(ctypes, _WINDLL_ATTR, fake)]
    patches += [_AttrPatch(ctypes, attr, fake) for attr in _WINDLL_FACTORY_ATTRS]
    if platform is not None:
        patches.append(_AttrPatch(sys, "platform", platform))

    saved_modules: dict[str, Any] = {}
    fake_modules: dict[str, types.ModuleType] = {}
    if uia is not None and patch_comtypes:
        fake_modules = build_fake_comtypes(uia)

    for p in patches:
        p.apply()
    for name, module in fake_modules.items():
        saved_modules[name] = sys.modules.get(name, _MISSING)
        sys.modules[name] = module
    try:
        yield FakeWin32(
            windll=fake,
            log=fake.log,
            desktop=desktop,
            uia=uia,
            console=console,
        )
    finally:
        for name, old in saved_modules.items():
            if old is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        for p in reversed(patches):
            p.undo()
