"""Controllable doubles for ``remedy.core.computer.host_binding`` calls.

``desktop_uia`` and ``execution.host.conpty`` no longer talk to comtypes /
ctypes Win32; they forward to ``host_binding`` (Zig). Tests that used to patch
comtypes or ``ctypes.WinDLL`` must mock this boundary instead.

UIA: walks a ``FakeUIAutomation`` tree with the same limits the Zig path
documents. ConPTY: drives a ``FakeConsoleHost`` through the binding surface
(spawn/read/write/poll/kill/close) without loading kernel32.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import types
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from tests.harness.fake_win32 import (
    CONTROL_TYPE_IDS,
    FakeConsoleHost,
    FakePipe,
    FakeUIAElement,
    FakeUIAutomation,
    TreeScope_Children,
    TreeScope_Descendants,
    UIA_BoundingRectanglePropertyId,
    UIA_ControlTypePropertyId,
    UIA_InvokePatternId,
    UIA_IsEnabledPropertyId,
    UIA_IsOffscreenPropertyId,
    UIA_NamePropertyId,
    UIA_ScrollItemPatternId,
    UIA_TextPatternId,
    UIA_TogglePatternId,
    UIA_ValuePatternId,
    UIA_ValueValuePropertyId,
)

__all__ = [
    "FakeHostConpty",
    "FakeHostUia",
    "install_fake_conpty",
    "install_fake_host_uia",
    "uia_element",
]

# Re-export builder so callers need one import for tree literals.
from tests.harness.fake_win32 import uia_element as uia_element  # noqa: E402

_CONTROL_TYPES = {v: k for k, v in CONTROL_TYPE_IDS.items()}

_PREFERRED = frozenset(
    {
        50000,
        50002,
        50003,
        50004,
        50005,
        50007,
        50011,
        50013,
        50019,
        50024,
        50029,
        50031,
    }
)

_SHALLOW_EXTRA = frozenset({50020, 50025, 50026, 50032, 50033})
_CLICKABLE_UNNAMED = frozenset({"button", "edit", "hyperlink", "checkbox", "menuitem"})


class FakeHostUia:
    """In-memory host_binding UIA surface over a ``FakeUIAutomation`` tree."""

    def __init__(
        self,
        automation: FakeUIAutomation | None = None,
        *,
        available: bool = True,
    ) -> None:
        self.automation = automation if automation is not None else FakeUIAutomation()
        self.available = available
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.input_calls: list[str] = []
        self.clipboard_writes: list[str] = []

    def uia_available(self) -> bool:
        self.calls.append(("uia_available", (), {}))
        return bool(self.available)

    def uia_read_window_text(self, hwnd: int, max_chars: int = 12000) -> dict[str, Any] | None:
        self.calls.append(("uia_read_window_text", (hwnd, max_chars), {}))
        if not hwnd:
            return None
        try:
            root = self.automation.ElementFromHandle(int(hwnd))
            if root is None:
                return None
            title = _el_name(root)
            lines: list[str] = []
            fields: list[dict[str, str]] = []
            budget = max(1000, int(max_chars))
            seen = 0

            def walk(el: FakeUIAElement | None, depth: int) -> None:
                nonlocal seen
                if el is None or depth > 14 or seen >= budget:
                    return
                role = _el_role(el)
                name = _el_name(el)
                if role in ("edit", "document", "combobox", "spinner"):
                    val = _el_value(el)
                    if val:
                        label = name or role
                        fields.append({"name": label[:80], "role": role, "value": val[:4000]})
                        entry = f"[{label}]: {val}" if name else val
                        lines.append(entry[: budget - seen])
                        seen += len(entry)
                    elif name:
                        fields.append({"name": name[:80], "role": role, "value": ""})
                elif name and role in (
                    "text",
                    "button",
                    "checkbox",
                    "radiobutton",
                    "hyperlink",
                    "listitem",
                    "menuitem",
                    "tabitem",
                    "header",
                    "titlebar",
                ):
                    lines.append(name[: budget - seen])
                    seen += len(name)
                with contextlib.suppress(Exception):
                    kids = el.FindAll(TreeScope_Children, self.automation.CreateTrueCondition())
                    n = int(kids.Length) if kids is not None else 0
                    for i in range(min(n, 60)):
                        if seen >= budget:
                            break
                        walk(kids.GetElement(i), depth + 1)

            walk(root, 0)
            out: list[str] = []
            for ln in lines:
                s = ln.strip()
                if s and (not out or out[-1] != s):
                    out.append(s)
            return {
                "title": title,
                "text": "\n".join(out)[: int(max_chars)],
                "fields": fields[:40],
            }
        except Exception:
            return None

    def uia_focused_element(self) -> dict[str, Any] | None:
        self.calls.append(("uia_focused_element", (), {}))
        try:
            el = self.automation.GetFocusedElement()
            if el is None:
                return None
            return {
                "name": _el_name(el)[:120],
                "role": _el_role(el) or "unknown",
                "value": _el_value(el)[:400],
            }
        except Exception:
            return None

    def uia_control_snapshot(
        self,
        hwnd: int = 0,
        max_elements: int = 80,
        preferred_only: bool = True,
    ) -> list[dict[str, Any]] | None:
        self.calls.append(
            ("uia_control_snapshot", (hwnd, max_elements, preferred_only), {})
        )
        try:
            return self._control_snapshot(hwnd, max_elements, preferred_only)
        except Exception:
            return None

    def _control_snapshot(
        self,
        hwnd: int,
        max_elements: int,
        preferred_only: bool,
    ) -> list[dict[str, Any]] | None:
        asked = 80 if max_elements is None else int(max_elements)
        max_n = max(1, min(80 if asked == 0 else asked, 120))
        elements: list[dict[str, Any]] = []

        def add_from(element: FakeUIAElement | None, *, depth: int, prefix_hwnd: int | None) -> None:
            if len(elements) >= max_n or element is None or depth > 12:
                return
            try:
                ctrl = int(element.GetCurrentPropertyValue(UIA_ControlTypePropertyId) or 0)
            except Exception:
                ctrl = 0
            try:
                enabled = bool(element.GetCurrentPropertyValue(UIA_IsEnabledPropertyId))
            except Exception:
                enabled = True
            try:
                offscreen = bool(element.GetCurrentPropertyValue(UIA_IsOffscreenPropertyId))
            except Exception:
                offscreen = False
            try:
                name = str(element.GetCurrentPropertyValue(UIA_NamePropertyId) or "").strip()
            except Exception:
                name = ""
            left, top, width, height = _bounds(element)

            include = True
            if preferred_only and ctrl and ctrl not in _PREFERRED:
                include = bool(name) and depth <= 2 and ctrl in _SHALLOW_EXTRA
            usable_size = (width >= 4 and height >= 4) or offscreen
            if include and enabled and usable_size:
                role = _CONTROL_TYPES.get(ctrl, f"type_{ctrl}")
                if name or role in _CLICKABLE_UNNAMED:
                    cx = left + width // 2
                    cy = top + height // 2
                    entry: dict[str, Any] = {
                        "ref": f"c{len(elements) + 1}",
                        "tag": role,
                        "role": role,
                        "name": (name or role)[:120],
                        "x": cx,
                        "y": cy,
                        "w": width,
                        "h": height,
                        "hwnd": prefix_hwnd,
                        "bounds": {
                            "left": left,
                            "top": top,
                            "right": left + width,
                            "bottom": top + height,
                        },
                        "uia": True,
                    }
                    if offscreen:
                        entry["offscreen"] = True
                    elements.append(entry)

            try:
                kids = element.FindAll(TreeScope_Children, self.automation.CreateTrueCondition())
                n = int(kids.Length) if kids is not None else 0
                for i in range(min(n, 40)):
                    if len(elements) >= max_n:
                        break
                    add_from(kids.GetElement(i), depth=depth + 1, prefix_hwnd=prefix_hwnd)
            except Exception:
                return

        if hwnd:
            root = self.automation.ElementFromHandle(int(hwnd))
            if root is None:
                return None
            add_from(root, depth=0, prefix_hwnd=int(hwnd))
        else:
            root = self.automation.GetRootElement()
            try:
                tops = root.FindAll(TreeScope_Children, self.automation.CreateTrueCondition())
                n = int(tops.Length) if tops is not None else 0
                for i in range(min(n, 15)):
                    if len(elements) >= max_n:
                        break
                    win_el = tops.GetElement(i)
                    try:
                        name = str(win_el.GetCurrentPropertyValue(UIA_NamePropertyId) or "").strip()
                    except Exception:
                        name = ""
                    if not name:
                        continue
                    try:
                        nh = win_el.CurrentNativeWindowHandle
                        wh = int(nh) if nh else None
                    except Exception:
                        wh = None
                    add_from(win_el, depth=0, prefix_hwnd=wh)
            except Exception:
                add_from(root, depth=0, prefix_hwnd=None)

        return elements if elements else None

    def uia_element_action(
        self,
        hwnd: int,
        name: str,
        *,
        role: str = "",
        action: str = "invoke",
        text: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "uia_element_action",
                (hwnd, name),
                {"role": role, "action": action, "text": text},
            )
        )
        el = _find_element(self.automation, hwnd, name, role)
        if el is None:
            return {
                "ok": False,
                "message": f"UIA element {name!r} not found in hwnd={hwnd} (re-snapshot?)",
            }
        label = f"{_el_role(el) or 'control'} {_el_name(el)!r}"
        try:
            if action == "invoke":
                pat = el.GetCurrentPattern(UIA_InvokePatternId)
                if not pat:
                    return {"ok": False, "message": f"{label} is not invokable — use a click"}
                pat.Invoke()
                return {"ok": True, "message": f"Invoked {label}"}
            if action == "set_value":
                pat = el.GetCurrentPattern(UIA_ValuePatternId)
                if not pat:
                    return {
                        "ok": False,
                        "message": f"{label} has no value pattern — click it and type instead",
                    }
                pat.SetValue(str(text))
                got = ""
                with contextlib.suppress(Exception):
                    got = str(pat.CurrentValue or "")
                okv = got == str(text)
                role_l = (_el_role(el) or "").lower()
                name_l = (_el_name(el) or "").lower()
                is_password = "password" in role_l or "password" in name_l
                # Password fields must never claim verified readback (and must
                # not echo the secret or its length) — matches Zig/desktop_uia.
                if is_password:
                    return {
                        "ok": True,
                        "verified": False,
                        "message": (
                            f"Set {label} value (unverified — password fields "
                            "typically do not read back)"
                        ),
                    }
                return {
                    "ok": True,
                    "verified": okv,
                    "message": (
                        f"Set {label} value"
                        + (", verified" if okv else ", readback differs")
                    ),
                }
            if action == "toggle":
                pat = el.GetCurrentPattern(UIA_TogglePatternId)
                if not pat:
                    return {"ok": False, "message": f"{label} is not toggleable"}
                pat.Toggle()
                state = ""
                with contextlib.suppress(Exception):
                    state = {0: "off", 1: "on", 2: "indeterminate"}.get(
                        int(pat.CurrentToggleState), "?"
                    )
                return {"ok": True, "message": f"Toggled {label} → {state}"}
            if action == "scroll_into_view":
                pat = el.GetCurrentPattern(UIA_ScrollItemPatternId)
                if not pat:
                    return {"ok": False, "message": f"{label} has no scroll-item pattern"}
                pat.ScrollIntoView()
                return {"ok": True, "message": f"Scrolled {label} into view"}
            return {"ok": False, "message": f"Unknown UIA action {action!r}"}
        except Exception as exc:
            return {"ok": False, "message": f"UIA {action} failed on {label}: {exc}"}

    # Input / clipboard trackers for "reads must not inject" proofs.
    def mouse_move(self, *_a: Any, **_k: Any) -> None:
        self.input_calls.append("mouse_move")

    def mouse_click(self, *_a: Any, **_k: Any) -> None:
        self.input_calls.append("mouse_click")

    def type_text(self, *_a: Any, **_k: Any) -> None:
        self.input_calls.append("type_text")

    def clipboard_set_text(self, text: str) -> None:
        self.clipboard_writes.append(str(text))


def _el_name(el: FakeUIAElement) -> str:
    with contextlib.suppress(Exception):
        return str(el.GetCurrentPropertyValue(UIA_NamePropertyId) or "").strip()
    return ""


def _el_role(el: FakeUIAElement) -> str:
    with contextlib.suppress(Exception):
        return _CONTROL_TYPES.get(int(el.GetCurrentPropertyValue(UIA_ControlTypePropertyId) or 0), "")
    return ""


def _el_value(el: FakeUIAElement) -> str:
    with contextlib.suppress(Exception):
        v = el.GetCurrentPropertyValue(UIA_ValueValuePropertyId)
        if v:
            return str(v)
    with contextlib.suppress(Exception):
        pat = el.GetCurrentPattern(UIA_ValuePatternId)
        if pat:
            v = pat.CurrentValue
            if v:
                return str(v)
    with contextlib.suppress(Exception):
        pat = el.GetCurrentPattern(UIA_TextPatternId)
        if pat:
            rng = pat.DocumentRange
            if rng is not None:
                v = rng.GetText(20000)
                if v:
                    return str(v)
    return ""


def _bounds(el: FakeUIAElement) -> tuple[int, int, int, int]:
    try:
        rect = el.GetCurrentPropertyValue(UIA_BoundingRectanglePropertyId)
        if rect is None:
            return 0, 0, 0, 0
        if hasattr(rect, "left"):
            left, top = int(rect.left), int(rect.top)
            return left, top, int(rect.right - rect.left), int(rect.bottom - rect.top)
        seq = list(rect)
        if len(seq) >= 4:
            return int(seq[0]), int(seq[1]), int(seq[2]), int(seq[3])
    except Exception:
        return 0, 0, 0, 0
    return 0, 0, 0, 0


def _find_element(
    automation: FakeUIAutomation,
    hwnd: int,
    name: str,
    role: str = "",
) -> FakeUIAElement | None:
    if not hwnd:
        return None
    try:
        root = automation.ElementFromHandle(int(hwnd))
        if root is None:
            return None
        cond = automation.CreatePropertyCondition(UIA_NamePropertyId, str(name))
        found = root.FindAll(TreeScope_Descendants, cond)
        n = int(found.Length) if found is not None else 0
        want_role = (role or "").strip().lower()
        fallback = None
        for i in range(min(n, 20)):
            el = found.GetElement(i)
            if not want_role or _el_role(el) == want_role:
                return el
            if fallback is None:
                fallback = el
        return fallback
    except Exception:
        return None


@contextlib.contextmanager
def install_fake_host_uia(
    automation: FakeUIAutomation | None = None,
    *,
    available: bool = True,
    platform: str | None = "win32",
    patch_input: bool = True,
) -> Iterator[FakeHostUia]:
    """Patch ``host_binding`` UIA entry points to a controllable fake.

    *platform* temporarily sets ``sys.platform`` (``desktop_uia`` short-circuits
    off Windows). Pass ``None`` to leave the real platform alone.
    """
    from remedy.core.computer import host_binding as H

    fake = FakeHostUia(automation, available=available)
    saved: dict[str, Any] = {}
    names = (
        "uia_available",
        "uia_read_window_text",
        "uia_focused_element",
        "uia_control_snapshot",
        "uia_element_action",
    )
    for name in names:
        saved[name] = getattr(H, name)
        setattr(H, name, getattr(fake, name))
    if patch_input:
        for name in ("mouse_move", "mouse_click", "type_text", "clipboard_set_text"):
            if hasattr(H, name):
                saved[name] = getattr(H, name)
                setattr(H, name, getattr(fake, name))

    platform_saved = None
    if platform is not None:
        platform_saved = sys.platform
        sys.platform = platform
    try:
        yield fake
    finally:
        if platform_saved is not None:
            sys.platform = platform_saved
        for name, value in saved.items():
            setattr(H, name, value)


# ---------------------------------------------------------------------------
# ConPTY (ABI 5)
# ---------------------------------------------------------------------------


class _ConptySession:
    __slots__ = (
        "pid",
        "process_handle",
        "pc_handle",
        "stdin_handle",
        "stdout_handle",
        "exit_code",
        "closed",
    )

    def __init__(
        self,
        *,
        pid: int,
        process_handle: int,
        pc_handle: int,
        stdin_handle: int,
        stdout_handle: int,
    ) -> None:
        self.pid = pid
        self.process_handle = process_handle
        self.pc_handle = pc_handle
        self.stdin_handle = stdin_handle
        self.stdout_handle = stdout_handle
        self.exit_code: int | None = None
        self.closed = False


class FakeHostConpty:
    """In-memory host_binding ConPTY surface over a ``FakeConsoleHost``."""

    def __init__(
        self,
        console: FakeConsoleHost | None = None,
        *,
        available: bool = True,
        fail_after_pipes: int | None = None,
        fail_attribute_update: bool = False,
    ) -> None:
        self.console = console if console is not None else FakeConsoleHost()
        self.available = available
        # Fail when creating the Nth pipe (1-based); None = use console flag only.
        self.fail_after_pipes = fail_after_pipes
        self.fail_attribute_update = fail_attribute_update
        self._pipes_created = 0
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.sessions: dict[int, _ConptySession] = {}
        self._next_handle = 0xC000
        self.spawns: list[dict[str, Any]] = []

    def _alloc_handle(self) -> int:
        self._next_handle += 8
        return self._next_handle

    def _session(self, handle: int) -> _ConptySession:
        from remedy.core.computer.host_binding import STATUS_INVALID_ARGUMENT, HostError

        session = self.sessions.get(int(handle))
        if session is None or session.closed:
            raise HostError("conpty", STATUS_INVALID_ARGUMENT)
        return session

    def _pipe(self) -> FakePipe:
        from remedy.core.computer.host_binding import STATUS_OPERATION_FAILED, HostError

        host = self.console
        self._pipes_created += 1
        if host.fail_create_pipe or (
            self.fail_after_pipes is not None and self._pipes_created >= self.fail_after_pipes
        ):
            raise HostError("conpty_spawn", STATUS_OPERATION_FAILED, os_error=1)
        pipe = FakePipe(
            read_handle=host._handle("pipe-read"),
            write_handle=host._handle("pipe-write"),
        )
        pipe.inheritable = False
        host.pipes.append(pipe)
        host._by_handle[pipe.read_handle] = pipe
        host._by_handle[pipe.write_handle] = pipe
        return pipe

    def conpty_available(self) -> bool:
        self.calls.append(("conpty_available", (), {}))
        return bool(self.available)

    def conpty_spawn(
        self,
        argv: Sequence[str],
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        *,
        cols: int = 120,
        rows: int = 40,
    ) -> tuple[int, int]:
        from remedy.core.computer.host_binding import STATUS_OPERATION_FAILED, HostError

        self.calls.append(("conpty_spawn", (list(argv), cwd, env, cols, rows), {}))
        host = self.console
        record = {
            "argv": [str(a) for a in argv],
            "cmdline": subprocess.list2cmdline([str(a) for a in argv]),
            "cwd": cwd,
            "env": None if env is None else dict(env),
            "cols": int(cols) or 120,
            "rows": int(rows) or 40,
            "flags": 0x00080000 | 0x00000400,
            "inherit": False,
        }
        self.spawns.append(record)

        pipe_in: FakePipe | None = None
        pipe_out: FakePipe | None = None
        try:
            pipe_in = self._pipe()
            pipe_out = self._pipe()
        except HostError:
            if pipe_in is not None:
                host.CloseHandle(pipe_in.read_handle)
                host.CloseHandle(pipe_in.write_handle)
            raise

        size = types.SimpleNamespace(X=record["cols"], Y=record["rows"])
        phpc = types.SimpleNamespace(value=0)
        try:
            hr = host.CreatePseudoConsole(
                size, pipe_in.read_handle, pipe_out.write_handle, 0, phpc
            )
        except OSError as exc:
            host.CloseHandle(pipe_in.read_handle)
            host.CloseHandle(pipe_in.write_handle)
            host.CloseHandle(pipe_out.read_handle)
            host.CloseHandle(pipe_out.write_handle)
            winerr = getattr(exc, "winerror", None) or host.create_pseudoconsole_hr or -1
            raise HostError("conpty_spawn", STATUS_OPERATION_FAILED, os_error=int(winerr)) from exc
        if hr != 0:
            host.CloseHandle(pipe_in.read_handle)
            host.CloseHandle(pipe_in.write_handle)
            host.CloseHandle(pipe_out.read_handle)
            host.CloseHandle(pipe_out.write_handle)
            raise HostError("conpty_spawn", STATUS_OPERATION_FAILED, os_error=int(hr))

        pc = int(phpc.value or 0) or host._handle("pseudoconsole")

        # Parent closes the PTY-side ends (handed to the console).
        host.CloseHandle(pipe_in.read_handle)
        host.CloseHandle(pipe_out.write_handle)

        # Attribute list probe + update (recorded for tests that care).
        host.InitializeProcThreadAttributeList(None, 1, 0, types.SimpleNamespace(value=0))
        attr = object()
        if (
            host.fail_attribute_list
            or host.InitializeProcThreadAttributeList(attr, 1, 0, types.SimpleNamespace(value=48))
            == 0
        ):
            host.ClosePseudoConsole(pc)
            host.CloseHandle(pipe_in.write_handle)
            host.CloseHandle(pipe_out.read_handle)
            raise HostError("conpty_spawn", STATUS_OPERATION_FAILED, os_error=87)
        if (
            self.fail_attribute_update
            or host.UpdateProcThreadAttribute(attr, 0, 0x00020016, pc, 8, None, None) == 0
        ):
            host.ClosePseudoConsole(pc)
            host.CloseHandle(pipe_in.write_handle)
            host.CloseHandle(pipe_out.read_handle)
            raise HostError("conpty_spawn", STATUS_OPERATION_FAILED, os_error=87)

        # Match ctypes create_unicode_buffer().value: content up to the first NUL.
        env_recorded = None
        if env is not None:
            env_recorded = "\0".join(f"{k}={v}" for k, v in env.items())

        created = host.CreateProcessW(
            None,
            record["cmdline"],
            None,
            None,
            False,
            record["flags"],
            types.SimpleNamespace(value=env_recorded) if env_recorded is not None else None,
            cwd,
            types.SimpleNamespace(
                StartupInfo=types.SimpleNamespace(
                    dwFlags=0x00000100,
                    hStdInput=None,
                    hStdOutput=None,
                    hStdError=None,
                )
            ),
            None,
        )
        if not created or host.fail_create_process:
            host.ClosePseudoConsole(pc)
            host.CloseHandle(pipe_in.write_handle)
            host.CloseHandle(pipe_out.read_handle)
            raise HostError("conpty_spawn", STATUS_OPERATION_FAILED, os_error=2)

        # Close the thread handle the fake allocated.
        for h, kind in list(host.kinds.items()):
            if kind == "thread" and h not in host.closed:
                host.CloseHandle(h)

        handle = self._alloc_handle()
        self.sessions[handle] = _ConptySession(
            pid=host.pid,
            process_handle=host.process_handle,
            pc_handle=pc,
            stdin_handle=pipe_in.write_handle,
            stdout_handle=pipe_out.read_handle,
        )
        return host.pid, handle

    def conpty_write(self, handle: int, data: bytes | bytearray | memoryview) -> int:
        self.calls.append(("conpty_write", (handle, bytes(data)), {}))
        session = self._session(handle)
        if not session.stdin_handle or session.stdin_handle in self.console.closed:
            return 0
        raw = bytes(data)
        ok = self.console.WriteFile(session.stdin_handle, raw, len(raw), types.SimpleNamespace(value=0))
        return len(raw) if ok else 0

    def conpty_read(self, handle: int, max_len: int = 4096) -> bytes:
        self.calls.append(("conpty_read", (handle, max_len), {}))
        session = self._session(handle)
        if not session.stdout_handle or session.stdout_handle in self.console.closed:
            return b""
        pipe = self.console.pipe_for(session.stdout_handle)
        if pipe is None:
            return b""
        return pipe.take(max(0, int(max_len)))

    def conpty_poll(self, handle: int) -> int | None:
        self.calls.append(("conpty_poll", (handle,), {}))
        session = self._session(handle)
        if session.exit_code is not None:
            return session.exit_code
        code_holder = types.SimpleNamespace(value=0)
        if self.console.GetExitCodeProcess(session.process_handle, code_holder) == 0:
            return None
        if int(code_holder.value) == 259:
            return None
        session.exit_code = int(code_holder.value)
        return session.exit_code

    def conpty_kill(self, handle: int) -> None:
        self.calls.append(("conpty_kill", (handle,), {}))
        session = self._session(handle)
        self.console.TerminateProcess(session.process_handle, 1)
        session.exit_code = 1

    def conpty_close_pipe(self, handle: int, which: int) -> None:
        self.calls.append(("conpty_close_pipe", (handle, which), {}))
        session = self._session(handle)
        if int(which) == 0:
            if session.stdin_handle:
                self.console.CloseHandle(session.stdin_handle)
                session.stdin_handle = 0
        else:
            if session.stdout_handle:
                self.console.CloseHandle(session.stdout_handle)
                session.stdout_handle = 0

    def conpty_close(self, handle: int) -> None:
        self.calls.append(("conpty_close", (handle,), {}))
        session = self.sessions.get(int(handle))
        if session is None or session.closed:
            return
        if session.stdin_handle:
            self.console.CloseHandle(session.stdin_handle)
            session.stdin_handle = 0
        if session.stdout_handle:
            self.console.CloseHandle(session.stdout_handle)
            session.stdout_handle = 0
        if session.pc_handle:
            self.console.ClosePseudoConsole(session.pc_handle)
            session.pc_handle = 0
        if session.process_handle:
            self.console.CloseHandle(session.process_handle)
            session.process_handle = 0
        session.closed = True


@contextlib.contextmanager
def install_fake_conpty(
    console: FakeConsoleHost | None = None,
    *,
    available: bool = True,
    platform: str | None = "win32",
    fail_after_pipes: int | None = None,
    fail_attribute_update: bool = False,
) -> Iterator[FakeHostConpty]:
    """Patch ``host_binding`` ConPTY entry points to a controllable fake."""
    from remedy.core.computer import host_binding as H

    fake = FakeHostConpty(
        console,
        available=available,
        fail_after_pipes=fail_after_pipes,
        fail_attribute_update=fail_attribute_update,
    )
    saved: dict[str, Any] = {}
    names = (
        "conpty_available",
        "conpty_spawn",
        "conpty_write",
        "conpty_read",
        "conpty_poll",
        "conpty_kill",
        "conpty_close_pipe",
        "conpty_close",
    )
    for name in names:
        saved[name] = getattr(H, name)
        setattr(H, name, getattr(fake, name))

    platform_saved = None
    if platform is not None:
        platform_saved = sys.platform
        sys.platform = platform
    try:
        yield fake
    finally:
        if platform_saved is not None:
            sys.platform = platform_saved
        for name, value in saved.items():
            setattr(H, name, value)
