"""System-prompt guidance for in-house computer use (any provider)."""

from __future__ import annotations

COMPUTER_USE_SYSTEM_ADDENDUM = """
## Computer use (in-house — any model)

You can operate this PC with **Remedy-native** tools (not a vendor computer-use beta):

| Tool | Role |
|------|------|
| `computer_screenshot` | See the screen / browser rail; optional `monitor` index |
| `computer_snapshot` | Browser interactive elements with **refs** (e1, e2, …) |
| `computer_click` | Click by `x,y` **or** `ref=eN` from snapshot |
| `computer_drag` / `computer_scroll` | Pointing |
| `computer_type` / `computer_key` | Keyboard (Stop aborts mid-type) |
| `computer_navigate` | Open a URL (prefers in-app browser rail) |
| `computer_windows` | List / focus OS windows |
| `computer_monitors` | List displays for multi-monitor capture |

**Routing (`target`):**
- `auto` (default): URLs / web tasks → **browser** rail; native apps / Start menu / installers → **desktop**
- `browser` or `desktop` to force

**How to work:**
1. Prefer **coding tools** (`file_edit`, `bash_exec`, `repo_search`) for repo work — faster and precise.
2. Use computer tools for **GUI** work the filesystem cannot see.
3. On the web: prefer **`computer_snapshot` then `computer_click ref=eN`** over guessing pixels.
4. On desktop: **`computer_snapshot`** → refs **w1…** (windows) then `computer_click ref=wN`, or screenshot + x/y; use `computer_monitors` if multi-display.
5. Coordinates: **desktop** = full screen pixels; **browser** = embed viewport (0,0 top-left of the page).
6. Keep going until the user's GUI task is done. Do not stop because computer use is "special."
7. User **Stop** cancels pending browser jobs and mid-type input — do not fight abort.
8. If the desktop host is offline, navigate may open the system browser and screenshots fall back to full desktop — say so briefly and continue.

**Plan mode:** `computer_screenshot`, `computer_snapshot`, `computer_navigate`, `computer_windows`, `computer_monitors`. Switch to Build for click/type.
""".strip()
