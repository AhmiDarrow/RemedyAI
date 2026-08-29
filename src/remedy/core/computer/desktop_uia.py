"""Optional deep desktop control snapshot via UI Automation.

Uses *comtypes* when available (soft dependency — not required to install).
Falls back to None so callers keep window-level snapshot.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any

# Interesting control types (UIA ControlType IDs)
_CONTROL_TYPES = {
    50000: "button",
    50001: "calendar",
    50002: "checkbox",
    50003: "combobox",
    50004: "edit",
    50005: "hyperlink",
    50006: "image",
    50007: "listitem",
    50008: "list",
    50009: "menu",
    50010: "menubar",
    50011: "menuitem",
    50012: "progressbar",
    50013: "radiobutton",
    50014: "scrollbar",
    50015: "slider",
    50016: "spinner",
    50018: "tab",
    50019: "tabitem",
    50020: "text",
    50021: "toolbar",
    50022: "tooltip",
    50023: "tree",
    50024: "treeitem",
    50025: "custom",
    50026: "group",
    50027: "thumb",
    50028: "datagrid",
    50029: "dataitem",
    50030: "document",
    50031: "splitbutton",
    50032: "window",
    50033: "pane",
    50034: "header",
    50035: "headeritem",
    50036: "table",
    50037: "titlebar",
}

def structured_observe_hint(*, n_windows: int, n_controls: int) -> str:
    """What to do next: UIA/DOM first, screenshot/OCR last."""
    if int(n_controls or 0) > 0:
        return (
            "Use control refs (cN) or names. Do not guess pixels. "
            "Screenshot/OCR only if a custom-drawn control is missing."
        )
    if int(n_windows or 0) > 0:
        return (
            "Window list only — UI Automation found no controls. "
            "Focus the app and snapshot again, or computer_screenshot for OCR "
            "(ref=oN). Do not click guessed x/y."
        )
    return (
        "No structured controls. computer_screenshot then click OCR ref=oN "
        "or marked boxes — never guessed coordinates."
    )


# Prefer clickable / typeable types
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


# UIA pattern ids (UIAutomationClient constants)
_PAT_INVOKE = 10000
_PAT_VALUE = 10002
_PAT_TEXT = 10014
_PAT_TOGGLE = 10015
_PAT_SCROLL_ITEM = 10017
_PROP_NAME = 30005
_PROP_CONTROL_TYPE = 30003
_PROP_VALUE_VALUE = 30045
_PROP_BOUNDING_RECT = 30001


def uia_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import comtypes  # noqa: F401
        import comtypes.client  # noqa: F401

        return True
    except ImportError:
        return False


def _uia_mod():
    """Load the UIAutomationClient typelib module (or None). Never raises."""
    if sys.platform != "win32":
        return None
    try:
        import comtypes
        import comtypes.client
    except ImportError:
        return None
    with contextlib.suppress(OSError):
        comtypes.CoInitialize()
    try:
        try:
            from comtypes.gen import UIAutomationClient as UIA  # noqa: N811
        except (ImportError, OSError, ValueError):
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA  # noqa: N811
        return UIA
    except Exception:
        return None


def _uia_obj(UIA):  # noqa: N803
    import comtypes.client

    return comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)


def _el_name(el) -> str:
    with contextlib.suppress(Exception):
        return str(el.GetCurrentPropertyValue(_PROP_NAME) or "").strip()
    return ""


def _el_role(el) -> str:
    with contextlib.suppress(Exception):
        return _CONTROL_TYPES.get(
            int(el.GetCurrentPropertyValue(_PROP_CONTROL_TYPE) or 0), ""
        )
    return ""


def _el_value(el, UIA) -> str:  # noqa: N803
    """Best-effort text content of one element: ValuePattern, then TextPattern."""
    with contextlib.suppress(Exception):
        v = el.GetCurrentPropertyValue(_PROP_VALUE_VALUE)
        if v:
            return str(v)
    with contextlib.suppress(Exception):
        pat = el.GetCurrentPattern(_PAT_VALUE)
        if pat:
            vp = pat.QueryInterface(UIA.IUIAutomationValuePattern)
            v = vp.CurrentValue
            if v:
                return str(v)
    with contextlib.suppress(Exception):
        pat = el.GetCurrentPattern(_PAT_TEXT)
        if pat:
            tp = pat.QueryInterface(UIA.IUIAutomationTextPattern)
            rng = tp.DocumentRange
            if rng is not None:
                v = rng.GetText(20000)
                if v:
                    return str(v)
    return ""


def read_window_text(hwnd: int, *, max_chars: int = 12000) -> dict[str, Any] | None:
    """Read the visible TEXT CONTENT of a native window via UIA.

    The desktop analogue of the browser's page_text: edit fields and documents
    contribute their live values (ValuePattern / TextPattern), labels and
    static text contribute their names. Returns {"title", "text", "fields"}
    or None when UIA is unavailable.
    """
    UIA = _uia_mod()  # noqa: N806
    if UIA is None or not hwnd:
        return None
    try:
        uia = _uia_obj(UIA)
        root = uia.ElementFromHandle(int(hwnd))
        if root is None:
            return None
        title = _el_name(root)
        lines: list[str] = []
        fields: list[dict[str, str]] = []
        budget = max(1000, int(max_chars))
        seen = 0

        def _walk(el, depth: int) -> None:
            nonlocal seen
            if el is None or depth > 14 or seen >= budget:
                return
            role = _el_role(el)
            name = _el_name(el)
            if role in ("edit", "document", "combobox", "spinner"):
                val = _el_value(el, UIA)
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
                cond = uia.CreateTrueCondition()
                kids = el.FindAll(2, cond)  # TreeScope_Children
                n = int(kids.Length) if kids is not None else 0
                for i in range(min(n, 60)):
                    if seen >= budget:
                        break
                    _walk(kids.GetElement(i), depth + 1)

        _walk(root, 0)
        # De-duplicate consecutive repeats (menus/toolbars repeat names)
        out: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s and (not out or out[-1] != s):
                out.append(s)
        return {
            "title": title,
            "text": "\n".join(out)[:max_chars],
            "fields": fields[:40],
        }
    except Exception:
        return None


def focused_element_info() -> dict[str, Any] | None:
    """Name/role/value of the currently focused UIA element (act→verify evidence)."""
    UIA = _uia_mod()  # noqa: N806
    if UIA is None:
        return None
    try:
        uia = _uia_obj(UIA)
        el = uia.GetFocusedElement()
        if el is None:
            return None
        return {
            "name": _el_name(el)[:120],
            "role": _el_role(el) or "unknown",
            "value": _el_value(el, UIA)[:400],
        }
    except Exception:
        return None


def _find_live_element(hwnd: int, name: str, role: str = ""):
    """Re-locate a snapshot element inside *hwnd* by name (+role) for patterns."""
    UIA = _uia_mod()  # noqa: N806
    if UIA is None or not hwnd:
        return None, None
    try:
        uia = _uia_obj(UIA)
        root = uia.ElementFromHandle(int(hwnd))
        if root is None:
            return None, None
        cond = uia.CreatePropertyCondition(_PROP_NAME, str(name))
        found = root.FindAll(4, cond)  # TreeScope_Descendants
        n = int(found.Length) if found is not None else 0
        want_role = (role or "").strip().lower()
        fallback = None
        for i in range(min(n, 20)):
            el = found.GetElement(i)
            if not want_role or _el_role(el) == want_role:
                return el, UIA
            if fallback is None:
                fallback = el
        return fallback, UIA
    except Exception:
        return None, None


_TOGGLE_ROLES = frozenset(
    {"checkbox", "togglebutton", "switch", "radiobutton"}
)


def preferred_click_action(role: str = "") -> str:
    """Which UIA pattern to try before a pixel click-at-center."""
    r = (role or "").strip().lower()
    if r in _TOGGLE_ROLES:
        return "toggle"
    return "invoke"


def element_action(
    hwnd: int,
    name: str,
    *,
    role: str = "",
    action: str = "invoke",
    text: str = "",
) -> dict[str, Any]:
    """Drive a native control through its UIA pattern — the reliable path.

    action: "invoke" (buttons/menuitems), "set_value" (edit/combobox — sets the
    WHOLE value atomically, no focus/keystroke races), "toggle" (checkboxes),
    "scroll_into_view" (bring an offscreen item onscreen so a click can land).
    Returns {"ok": bool, "message": str, ...}.
    """
    el, UIA = _find_live_element(hwnd, name, role)  # noqa: N806
    if el is None or UIA is None:
        return {
            "ok": False,
            "message": f"UIA element {name!r} not found in hwnd={hwnd} (re-snapshot?)",
        }
    label = f"{_el_role(el) or 'control'} {_el_name(el)!r}"
    try:
        if action == "invoke":
            pat = el.GetCurrentPattern(_PAT_INVOKE)
            if not pat:
                return {"ok": False, "message": f"{label} is not invokable — use a click"}
            pat.QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
            return {"ok": True, "message": f"Invoked {label}"}
        if action == "set_value":
            pat = el.GetCurrentPattern(_PAT_VALUE)
            if not pat:
                return {
                    "ok": False,
                    "message": f"{label} has no value pattern — click it and type instead",
                }
            vp = pat.QueryInterface(UIA.IUIAutomationValuePattern)
            vp.SetValue(str(text))
            got = ""
            with contextlib.suppress(Exception):
                got = str(vp.CurrentValue or "")
            okv = got == str(text)
            return {
                "ok": True,
                "message": (
                    f"Set {label} value ({len(str(text))} chars"
                    + (", verified" if okv else ", readback differs")
                    + ")"
                ),
                "verified": okv,
            }
        if action == "toggle":
            pat = el.GetCurrentPattern(_PAT_TOGGLE)
            if not pat:
                return {"ok": False, "message": f"{label} is not toggleable"}
            tp = pat.QueryInterface(UIA.IUIAutomationTogglePattern)
            tp.Toggle()
            state = ""
            with contextlib.suppress(Exception):
                state = {0: "off", 1: "on", 2: "indeterminate"}.get(
                    int(tp.CurrentToggleState), "?"
                )
            return {"ok": True, "message": f"Toggled {label} → {state}"}
        if action == "scroll_into_view":
            pat = el.GetCurrentPattern(_PAT_SCROLL_ITEM)
            if not pat:
                return {"ok": False, "message": f"{label} has no scroll-item pattern"}
            pat.QueryInterface(UIA.IUIAutomationScrollItemPattern).ScrollIntoView()
            return {"ok": True, "message": f"Scrolled {label} into view"}
        return {"ok": False, "message": f"Unknown UIA action {action!r}"}
    except Exception as e:  # pattern calls can raise COMError on dead elements
        return {"ok": False, "message": f"UIA {action} failed on {label}: {e}"}


def uia_control_snapshot(
    *,
    hwnd: int | None = None,
    max_elements: int = 80,
    preferred_only: bool = True,
) -> list[dict[str, Any]] | None:
    """Walk UIA control tree; return elements with refs c1, c2, … or None if UIA unavailable.

    *hwnd*: limit walk to that window (None = desktop root descendants — capped).
    """
    if sys.platform != "win32":
        return None
    try:
        import comtypes
        import comtypes.client
    except ImportError:
        return None

    with contextlib.suppress(OSError):
        comtypes.CoInitialize()

    try:
        # Generate / load UIAutomationClient typelib
        try:
            from comtypes.gen.UIAutomationClient import (
                CUIAutomation,
                IUIAutomation,
                TreeScope_Children,
                TreeScope_Descendants,
                UIA_BoundingRectanglePropertyId,
                UIA_ControlTypePropertyId,
                UIA_IsEnabledPropertyId,
                UIA_IsOffscreenPropertyId,
                UIA_NamePropertyId,
            )
        except (ImportError, OSError, ValueError):
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import (
                CUIAutomation,
                IUIAutomation,
                TreeScope_Children,
                TreeScope_Descendants,
                UIA_BoundingRectanglePropertyId,
                UIA_ControlTypePropertyId,
                UIA_IsEnabledPropertyId,
                UIA_IsOffscreenPropertyId,
                UIA_NamePropertyId,
            )

        uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        if hwnd:
            root = uia.ElementFromHandle(int(hwnd))
            scope = TreeScope_Descendants
        else:
            root = uia.GetRootElement()
            # Desktop root descendants are huge — walk only top-level children then shallow
            scope = TreeScope_Children

        if root is None:
            return None

        elements: list[dict[str, Any]] = []
        max_n = max(1, min(int(max_elements or 80), 120))

        def _add_from(element, *, depth: int, prefix_hwnd: int | None) -> None:
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
            try:
                rect = element.GetCurrentPropertyValue(UIA_BoundingRectanglePropertyId)
                # rect: (left, top, width, height) as tuple/list or tagRECT-like
                if rect is None:
                    left = top = width = height = 0
                elif hasattr(rect, "left"):
                    left, top = int(rect.left), int(rect.top)
                    width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
                else:
                    # Often (left, top, width, height)
                    seq = list(rect)
                    if len(seq) >= 4:
                        left, top, width, height = (
                            int(seq[0]),
                            int(seq[1]),
                            int(seq[2]),
                            int(seq[3]),
                        )
                    else:
                        left = top = width = height = 0
            except Exception:
                left = top = width = height = 0

            include = True
            if preferred_only and ctrl and ctrl not in _PREFERRED:
                # Still include named buttons-ish at shallow depth
                include = bool(name) and depth <= 2 and ctrl in (
                    50020,
                    50025,
                    50026,
                    50032,
                    50033,
                )
            # Offscreen elements are KEPT (flagged) — anything scrolled below the
            # fold used to be invisible to Remedy; a click on one now scrolls it
            # into view first via ScrollItemPattern.
            usable_size = (width >= 4 and height >= 4) or offscreen
            if include and enabled and usable_size:
                role = _CONTROL_TYPES.get(ctrl, f"type_{ctrl}")
                if name or role in ("button", "edit", "hyperlink", "checkbox", "menuitem"):
                    cx = left + width // 2
                    cy = top + height // 2
                    ref = f"c{len(elements) + 1}"
                    el_dict: dict[str, Any] = {
                        "ref": ref,
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
                        el_dict["offscreen"] = True
                    elements.append(el_dict)

            # Children
            try:
                cond = uia.CreateTrueCondition()
                kids = element.FindAll(TreeScope_Children, cond)
                n = int(kids.Length) if kids is not None else 0
                for i in range(min(n, 40)):
                    if len(elements) >= max_n:
                        break
                    child = kids.GetElement(i)
                    _add_from(child, depth=depth + 1, prefix_hwnd=prefix_hwnd)
            except Exception:
                pass

        if hwnd:
            _add_from(root, depth=0, prefix_hwnd=int(hwnd))
        else:
            # Top-level windows only as roots for control walk (cap)
            try:
                cond = uia.CreateTrueCondition()
                tops = root.FindAll(scope, cond)
                n = int(tops.Length) if tops is not None else 0
                for i in range(min(n, 15)):
                    if len(elements) >= max_n:
                        break
                    win_el = tops.GetElement(i)
                    try:
                        name = str(
                            win_el.GetCurrentPropertyValue(UIA_NamePropertyId) or ""
                        ).strip()
                    except Exception:
                        name = ""
                    if not name:
                        continue
                    # Native window handle if available
                    try:
                        nh = win_el.CurrentNativeWindowHandle
                        wh = int(nh) if nh else None
                    except Exception:
                        wh = None
                    _add_from(win_el, depth=0, prefix_hwnd=wh)
            except Exception:
                _add_from(root, depth=0, prefix_hwnd=None)

        return elements if elements else None
    except Exception:
        return None
