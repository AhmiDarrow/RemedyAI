"""Controllable doubles for ``remedy.core.computer.host_binding`` UIA calls.

``desktop_uia`` no longer talks to comtypes; it forwards to ``host_binding``
(Zig COM). Tests that used to patch comtypes must mock this boundary instead.

This harness walks a ``FakeUIAutomation`` tree with the same limits the Zig
path documents (max_elements clamp, name/value clipping, preferred_only,
depth/children caps, offscreen keep-and-flag). It never loads
UIAutomationCore.dll, never SendInput, and never touches the real clipboard.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from typing import Any

from tests.harness.fake_win32 import (
    CONTROL_TYPE_IDS,
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
    "FakeHostUia",
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
