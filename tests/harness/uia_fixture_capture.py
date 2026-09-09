"""Capture UIA return shapes for Zig / host_binding parity fixtures.

Harness path mocks ``host_binding`` via ``fake_host_binding`` (no live COM).
Live path may launch Notepad briefly and terminate it. Read-only: never
SendInput / keybd_event / mouse_event, never write clipboard.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "uia"


def fixture_dir() -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    return FIXTURE_DIR


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write(name: str, payload: dict[str, Any]) -> Path:
    path = fixture_dir() / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def harness_notepad_tree():
    """Canonical fake tree matching the shapes in test_desktop_uia_reads."""
    from tests.harness.fake_win32 import FakeUIAutomation, uia_element

    win = uia_element(
        "Untitled - Notepad",
        "window",
        hwnd=101,
        bounds=(0, 0, 800, 600),
        children=[
            uia_element("File", "menuitem", invokable=True, bounds=(10, 10, 40, 20)),
            uia_element(
                "Edit Document",
                "document",
                text="hello world",
                bounds=(10, 40, 400, 300),
            ),
            uia_element("Query", "edit", value="pinned", bounds=(10, 360, 200, 24)),
            uia_element("Save", "button", invokable=True, bounds=(10, 400, 80, 28)),
            uia_element(
                "Word wrap",
                "checkbox",
                toggle="off",
                bounds=(100, 400, 100, 24),
            ),
            uia_element(
                "Below",
                "listitem",
                bounds=(0, 5000, 120, 20),
                offscreen=True,
                scrollable=True,
            ),
            uia_element("Status", "text", bounds=(10, 440, 100, 16)),
        ],
    )
    root = uia_element("Desktop", "pane", children=[win])
    uia = FakeUIAutomation(root)
    uia.focused = win.children[2]  # Query edit
    return uia


def write_harness_contract_fixtures() -> list[Path]:
    """Field-for-field contract JSON from fake_host_binding (always available)."""
    from remedy.core.computer import guidance as desktop_uia
    from tests.harness.fake_host_binding import install_fake_host_uia
    from tests.harness.fake_win32 import FakeUIAutomation, uia_element

    written: list[Path] = []
    notes = (
        "Contract fixture from tests.harness.fake_host_binding (reference walker "
        "over FakeUIAutomation trees). Shapes must match live Zig UIA output "
        "field-for-field."
    )

    with install_fake_host_uia(harness_notepad_tree()):
        snap = desktop_uia.uia_control_snapshot(
            hwnd=101, max_elements=80, preferred_only=True
        )
        text = desktop_uia.read_window_text(101)
        focus = desktop_uia.focused_element_info()

    written.append(
        _write(
            "contract_uia_control_snapshot.json",
            {
                "source": "fake_host_binding",
                "captured_at": _now(),
                "api": "uia_control_snapshot",
                "platform": "harness",
                "notes": notes,
                "args": {
                    "hwnd": 101,
                    "max_elements": 80,
                    "preferred_only": True,
                },
                "result": snap,
                "element_schema": {
                    "ref": "str (cN)",
                    "tag": "str (control role)",
                    "role": "str (same as tag)",
                    "name": "str (<=120)",
                    "x": "int (center)",
                    "y": "int (center)",
                    "w": "int",
                    "h": "int",
                    "hwnd": "int | null",
                    "bounds": {
                        "left": "int",
                        "top": "int",
                        "right": "int",
                        "bottom": "int",
                    },
                    "uia": "bool (always true)",
                    "offscreen": "bool (optional, only when true)",
                },
            },
        )
    )
    written.append(
        _write(
            "contract_read_window_text.json",
            {
                "source": "fake_host_binding",
                "captured_at": _now(),
                "api": "read_window_text",
                "platform": "harness",
                "notes": notes,
                "args": {"hwnd": 101, "max_chars": 12000},
                "result": text,
                "result_schema": {
                    "title": "str",
                    "text": "str (<=max_chars)",
                    "fields": [
                        {
                            "name": "str (<=80)",
                            "role": "str",
                            "value": "str (<=4000)",
                        }
                    ],
                },
            },
        )
    )
    written.append(
        _write(
            "contract_focused_element_info.json",
            {
                "source": "fake_host_binding",
                "captured_at": _now(),
                "api": "focused_element_info",
                "platform": "harness",
                "notes": notes,
                "args": {},
                "result": focus,
                "result_schema": {
                    "name": "str (<=120)",
                    "role": "str (or 'unknown')",
                    "value": "str (<=400)",
                },
            },
        )
    )

    # element_action shapes (separate trees so actions don't collide)
    action_cases: list[tuple[str, Any, dict[str, Any]]] = []

    def _app_tree(*children: Any) -> FakeUIAutomation:
        return FakeUIAutomation(
            uia_element(
                "Desktop",
                "pane",
                children=[uia_element("App", "window", hwnd=101, children=list(children))],
            )
        )

    btn = uia_element("Save", "button", invokable=True)
    with install_fake_host_uia(_app_tree(btn)):
        action_cases.append(
            ("invoke", desktop_uia.element_action(101, "Save", action="invoke"), {"ok": "bool", "message": "str"})
        )

    box = uia_element("Amount", "edit", value="")
    with install_fake_host_uia(_app_tree(box)):
        action_cases.append(
            (
                "set_value",
                desktop_uia.element_action(101, "Amount", action="set_value", text="1234"),
                {"ok": "bool", "verified": "bool", "message": "str"},
            )
        )

    chk = uia_element("Word wrap", "checkbox", toggle="off")
    with install_fake_host_uia(_app_tree(chk)):
        action_cases.append(
            ("toggle", desktop_uia.element_action(101, "Word wrap", action="toggle"), {"ok": "bool", "message": "str"})
        )

    item = uia_element("Below", "listitem", offscreen=True, scrollable=True)
    with install_fake_host_uia(_app_tree(item)):
        action_cases.append(
            (
                "scroll_into_view",
                desktop_uia.element_action(101, "Below", action="scroll_into_view"),
                {"ok": "bool", "message": "str"},
            )
        )

    with install_fake_host_uia(_app_tree()):
        action_cases.append(
            (
                "not_found",
                desktop_uia.element_action(101, "Missing", action="invoke"),
                {"ok": "bool", "message": "str"},
            )
        )

    written.append(
        _write(
            "contract_element_action.json",
            {
                "source": "fake_host_binding",
                "captured_at": _now(),
                "api": "element_action",
                "platform": "harness",
                "notes": notes,
                "cases": [
                    {"action": name, "result": result, "result_schema": schema}
                    for name, result, schema in action_cases
                ],
            },
        )
    )

    written.append(
        _write(
            "contract_misc.json",
            {
                "source": "fake_host_binding",
                "captured_at": _now(),
                "api": "misc",
                "platform": "harness",
                "notes": notes,
                "uia_available": True,  # under install_fake_host_uia(platform=win32)
                "preferred_click_action": {
                    "button": desktop_uia.preferred_click_action("button"),
                    "checkbox": desktop_uia.preferred_click_action("checkbox"),
                    "radiobutton": desktop_uia.preferred_click_action("radiobutton"),
                    "togglebutton": desktop_uia.preferred_click_action("togglebutton"),
                    "switch": desktop_uia.preferred_click_action("switch"),
                    "edit": desktop_uia.preferred_click_action("edit"),
                },
                "structured_observe_hint": {
                    "with_controls": desktop_uia.structured_observe_hint(
                        n_windows=2, n_controls=5
                    ),
                    "windows_only": desktop_uia.structured_observe_hint(
                        n_windows=2, n_controls=0
                    ),
                    "empty": desktop_uia.structured_observe_hint(
                        n_windows=0, n_controls=0
                    ),
                },
            },
        )
    )
    return written


def _find_window_hwnd(title_substr: str) -> int | None:
    """Find a visible top-level window whose title contains *title_substr*."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []
    needle = title_substr.lower()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lp):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if needle in buf.value.lower():
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(_enum, 0)
    return found[0] if found else None


def _launch_notepad() -> tuple[subprocess.Popen[bytes] | None, int | None]:
    """Start notepad.exe; return (proc, hwnd) or (None, None). No keyboard/mouse."""
    if sys.platform != "win32":
        return None, None
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            ["notepad.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None, None
    hwnd: int | None = None
    for _ in range(40):  # ~4s
        time.sleep(0.1)
        hwnd = _find_window_hwnd("notepad")
        if hwnd:
            break
    return proc, hwnd


def _terminate(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def try_live_capture() -> dict[str, Any]:
    """Capture live Zig/host_binding UIA shapes. Read-only; optional Notepad launch.

    Returns a status dict: ok, paths, error, source_window.
    """
    from remedy.core.computer import guidance as desktop_uia

    status: dict[str, Any] = {
        "ok": False,
        "paths": [],
        "error": None,
        "source_window": None,
        "uia_available": desktop_uia.uia_available(),
    }
    if not desktop_uia.uia_available():
        status["error"] = "uia_unavailable"
        return status

    proc: subprocess.Popen[bytes] | None = None
    hwnd: int | None = None
    source = "existing"
    try:
        hwnd = _find_window_hwnd("notepad")
        if hwnd is None:
            proc, hwnd = _launch_notepad()
            source = "launched_notepad"
        if hwnd is None:
            # Fall back to any visible top-level window (still read-only).
            hwnd = _find_any_visible_hwnd()
            source = "any_visible"
        if not hwnd:
            status["error"] = "no_window"
            return status

        status["source_window"] = {"hwnd": hwnd, "how": source}
        snap = desktop_uia.uia_control_snapshot(
            hwnd=hwnd, max_elements=80, preferred_only=True
        )
        text = desktop_uia.read_window_text(hwnd)
        focus = desktop_uia.focused_element_info()

        notes = (
            "Live capture via desktop_uia → host_binding → Zig COM. "
            "Do not inject input; do not write clipboard. "
            f"Window acquisition: {source}."
        )
        meta = {
            "source": "host_binding",
            "captured_at": _now(),
            "platform": sys.platform,
            "notes": notes,
            "window": status["source_window"],
        }
        paths = [
            _write(
                "live_uia_control_snapshot.json",
                {
                    **meta,
                    "api": "uia_control_snapshot",
                    "args": {
                        "hwnd": hwnd,
                        "max_elements": 80,
                        "preferred_only": True,
                    },
                    "result": snap,
                },
            ),
            _write(
                "live_read_window_text.json",
                {
                    **meta,
                    "api": "read_window_text",
                    "args": {"hwnd": hwnd, "max_chars": 12000},
                    "result": text,
                },
            ),
            _write(
                "live_focused_element_info.json",
                {
                    **meta,
                    "api": "focused_element_info",
                    "args": {},
                    "result": _redact_focus(focus),
                },
            ),
        ]
        status["paths"] = [str(p) for p in paths]
        status["ok"] = snap is not None or text is not None or focus is not None
        if not status["ok"]:
            status["error"] = "all_reads_none"
        return status
    except Exception as exc:  # capture must never crash the suite
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status
    finally:
        _terminate(proc)


def _find_any_visible_hwnd() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lp):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        found.append(int(hwnd))
        return False

    user32.EnumWindows(_enum, 0)
    return found[0] if found else None


def write_readme(*, live_ok: bool, live_error: str | None = None) -> Path:
    lines = [
        "# UIA fixtures (Phase 2 native cutover)",
        "",
        "Contract shapes from ``fake_host_binding``; live shapes from",
        "``desktop_uia`` → ``host_binding`` → Zig COM. Parity tests should",
        "match these shapes field-for-field.",
        "",
        "## Sources",
        "",
        "| Prefix | Meaning |",
        "|--------|---------|",
        "| `contract_*` | Deterministic shapes from `tests.harness.fake_host_binding` |",
        "| `live_*` | Real Zig/host_binding capture on Windows when UIA is available |",
        "",
        "## Regenerating",
        "",
        "```text",
        "uv run python -m tests.harness.uia_fixture_capture",
        "# or",
        "uv run pytest tests/test_uia_fixture_capture.py -q",
        "```",
        "",
        "The capture is **read-only**: no SendInput, no clipboard writes.",
        "Live mode may launch Notepad briefly and terminate it.",
        "",
        "## Capture status (last write)",
        "",
        "- Contract fixtures: always written",
        f"- Live capture: {'succeeded' if live_ok else 'did not succeed'}"
        + (f" (`{live_error}`)" if live_error else ""),
        "",
        "## Key result shapes",
        "",
        "- `uia_control_snapshot` → `list[dict] | null` with keys",
        "  `ref, tag, role, name, x, y, w, h, hwnd, bounds, uia`",
        "  and optional `offscreen`",
        "- `read_window_text` → `{title, text, fields[{name, role, value}]} | null`",
        "- `focused_element_info` → `{name, role, value} | null`",
        "- `element_action` → `{ok, message, verified?}`",
        "",
    ]
    path = fixture_dir() / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _redact_focus(focus: Any) -> Any:
    """Strip owner activity from a focused-element capture.

    ``focused_element_info`` returns whichever window the owner had in front of
    them, which has already put an application path from their home directory
    into a tracked fixture. The shape is what the fixture is for, so keep the
    keys and drop the values.
    """
    if not isinstance(focus, dict):
        return focus
    out = dict(focus)
    for key in ("name", "value", "title", "text"):
        if key in out and isinstance(out[key], str) and out[key]:
            out[key] = f"redacted-{key}"
    return out


def main() -> int:
    contract = write_harness_contract_fixtures()
    live = try_live_capture()
    readme = write_readme(live_ok=bool(live.get("ok")), live_error=live.get("error"))
    print(f"contract: {len(contract)} files under {fixture_dir()}")
    for p in contract:
        print(f"  {p.name}")
    print(f"live: ok={live.get('ok')} error={live.get('error')} window={live.get('source_window')}")
    for p in live.get("paths") or []:
        print(f"  {Path(p).name}")
    print(f"readme: {readme}")
    return 0 if contract else 1


if __name__ == "__main__":
    raise SystemExit(main())
