"""Desktop UI Automation via ``remedy_core`` (no comtypes).

Walks, reads and pattern actions are implemented in Zig COM vtable calls
exported through :mod:`remedy.core.computer.host_binding`. Pure helpers
(:func:`structured_observe_hint`, :func:`preferred_click_action`) stay here.
OCR remains on the Python/WinRT path.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any

from remedy.core.computer import host_binding as H
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

_TOGGLE_ROLES = frozenset({"checkbox", "togglebutton", "switch", "radiobutton"})


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


def uia_available() -> bool:
    if sys.platform != "win32":
        return False
    with contextlib.suppress(NativeRuntimeUnavailableError, H.HostError):
        return H.uia_available()
    return False


def preferred_click_action(role: str = "") -> str:
    """Which UIA pattern to try before a pixel click-at-center."""
    r = (role or "").strip().lower()
    if r in _TOGGLE_ROLES:
        return "toggle"
    return "invoke"


def read_window_text(hwnd: int, *, max_chars: int = 12000) -> dict[str, Any] | None:
    """Read the visible TEXT CONTENT of a native window via UIA.

    Returns ``{"title", "text", "fields"}`` or ``None`` when UIA is unavailable.
    """
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        return H.uia_read_window_text(int(hwnd), int(max_chars))
    except (NativeRuntimeUnavailableError, H.HostError):
        return None


def focused_element_info() -> dict[str, Any] | None:
    """Name/role/value of the currently focused UIA element (act→verify evidence)."""
    if sys.platform != "win32":
        return None
    try:
        return H.uia_focused_element()
    except (NativeRuntimeUnavailableError, H.HostError):
        return None


def element_action(
    hwnd: int,
    name: str,
    *,
    role: str = "",
    action: str = "invoke",
    text: str = "",
) -> dict[str, Any]:
    """Drive a native control through its UIA pattern — the reliable path.

    action: ``invoke``, ``set_value``, ``toggle``, ``scroll_into_view``.
    Always returns ``{"ok": bool, "message": str, ...}``.
    """
    if sys.platform != "win32":
        return {
            "ok": False,
            "message": f"UIA element {name!r} not found in hwnd={hwnd} (re-snapshot?)",
        }
    try:
        return H.uia_element_action(
            int(hwnd),
            str(name),
            role=str(role or ""),
            action=str(action or "invoke"),
            text=str(text),
        )
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        return {"ok": False, "message": f"UIA {action} failed on control {name!r}: {exc}"}


def uia_control_snapshot(
    *,
    hwnd: int | None = None,
    max_elements: int = 80,
    preferred_only: bool = True,
) -> list[dict[str, Any]] | None:
    """Walk UIA control tree; return elements with refs c1, c2, … or None."""
    if sys.platform != "win32":
        return None
    try:
        return H.uia_control_snapshot(
            0 if hwnd is None else int(hwnd),
            int(max_elements) if max_elements is not None else 80,
            bool(preferred_only),
        )
    except (NativeRuntimeUnavailableError, H.HostError):
        return None
