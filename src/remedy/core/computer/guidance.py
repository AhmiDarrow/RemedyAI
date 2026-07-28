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
| `computer_navigate` | Open a URL in the **in-app Browser rail** (default) |
| `computer_windows` | List / focus OS windows |
| `computer_monitors` | List displays for multi-monitor capture |

**Routing (`target`):**
- **Web / wiki / URL / “show me the site” → always the Browser rail** unless the user asks for system/external browser (Firefox/Chrome outside Remedy).
- `computer_navigate` defaults to `target=browser` (rail).
- Native apps / Start menu / installers → **desktop** computer tools.
- `desktop` / “system browser” / “open externally” only when the user says so.

**How to work:**
1. Prefer **coding tools** (`file_edit`, `bash_exec`, `repo_search`) for repo work — faster and precise.
2. Use computer tools for **GUI** work the filesystem cannot see.
3. To show a website or wiki: **`computer_navigate`** (rail). Do **not** open Firefox/Chrome/system browser unless the user explicitly asks. Phrases like “remedy browser”, “in the rail”, “in-app” mean the **Browser workspace rail**.
4. **Do not** use `web_fetch` for wikis that block bots (Fandom often returns **403**) when the user wants to *see* the page — use `computer_navigate` so they view it in the rail.
5. On the web after open: prefer **`computer_snapshot` then `computer_click ref=eN`** over guessing pixels.
6. On desktop OS apps: **`computer_snapshot`** → **w1…** / **c1…**; or screenshot + x/y.
7. Coordinates: **desktop** = screen pixels; **browser** = embed viewport (0,0 top-left of the page).
8. Keep going until the GUI task is done.
9. User **Stop** cancels pending browser jobs and mid-type input.
10. If rail navigate fails, report the error and retry — **do not** silently open the system browser or only summarize the page unless the user wants a summary.

**Plan mode:** `computer_screenshot`, `computer_snapshot`, `computer_navigate`, `computer_windows`, `computer_monitors`. Switch to Build for click/type.

**Default open location for the web:** Remedy **Browser rail** (workspace slide), not the OS browser.
""".strip()
