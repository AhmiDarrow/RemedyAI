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
3. **Open-only requests** (“goto gmail”, “bring up google”, “open X”): call **`computer_navigate` once**, then **stop**. One short confirmation. No screenshot, no snapshot, no extra tools, no long thinking.
4. To show a website or wiki: **`computer_navigate`** (rail) with a **full https URL**. Nicknames work (`gmail` → mail.google.com). Do **not** open Firefox/Chrome/system browser unless the user explicitly asks.
5. **Never** reply with only “I'll open …” without a real `computer_navigate` tool call.
6. If `computer_navigate` returns **ok: true** / **SUCCESS** / **user_visible** / **reconciled** / **optimistic**: the page **is open**. Confirm in **one short sentence** and **end the turn**. Do not claim failure, web_fetch, open system browser, or “verify” with more tools unless the user asked to interact with the page.
7. **Page interaction** (“click membership options”, fill a form):  
   `computer_snapshot` → `computer_click ref=eN` (or type/key). **One snapshot, then act.**  
   **Do not** loop screenshot → vision decode → snapshot — that is slow and often captures the wrong window.  
   If snapshot fails: navigate once to the right URL, snapshot again, then stop or click — no vision thrash.
8. **Do not** use `web_fetch` for wikis that block bots when the user wants to *see* the page.
9. Coordinates: **desktop** = screen pixels; **browser** = embed viewport.
10. User **Stop** cancels pending browser jobs.
11. If navigate **fails** (ok: false): one short error; retry once at most; never open system browser unless asked.

**Plan mode:** `computer_screenshot`, `computer_snapshot`, `computer_navigate`, `computer_windows`, `computer_monitors`. Switch to Build for click/type.

**Default open location for the web:** Remedy **Browser rail** (workspace slide), not the OS browser.
""".strip()
