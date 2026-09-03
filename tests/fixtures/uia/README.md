# UIA fixtures (Phase 2 native cutover)

Captured from the **comtypes** `desktop_uia` path before the Zig UI
Automation port. Zig parity tests should match these shapes
field-for-field.

## Sources

| Prefix | Meaning |
|--------|---------|
| `contract_*` | Deterministic shapes from `tests.harness.fake_win32` |
| `live_*` | Real comtypes capture on Windows when UIA is available |

## Regenerating

```text
uv run python -m tests.harness.uia_fixture_capture
# or
uv run pytest tests/test_uia_fixture_capture.py -q
```

The capture is **read-only**: no SendInput, no clipboard writes.
Live mode may launch Notepad briefly and terminate it.

## Capture status (last write)

- Contract fixtures: always written
- Live capture: succeeded

## Key result shapes

- `uia_control_snapshot` → `list[dict] | null` with keys
  `ref, tag, role, name, x, y, w, h, hwnd, bounds, uia`
  and optional `offscreen`
- `read_window_text` → `{title, text, fields[{name, role, value}]} | null`
- `focused_element_info` → `{name, role, value} | null`
- `element_action` → `{ok, message, verified?}`
