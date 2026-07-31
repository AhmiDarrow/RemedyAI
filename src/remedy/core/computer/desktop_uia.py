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


def uia_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import comtypes  # noqa: F401
        import comtypes.client  # noqa: F401

        return True
    except ImportError:
        return False


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
            if include and enabled and not offscreen and width >= 4 and height >= 4:
                role = _CONTROL_TYPES.get(ctrl, f"type_{ctrl}")
                if name or role in ("button", "edit", "hyperlink", "checkbox", "menuitem"):
                    cx = left + width // 2
                    cy = top + height // 2
                    ref = f"c{len(elements) + 1}"
                    elements.append(
                        {
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
                    )

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
