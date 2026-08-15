# Agent notes — RemedyAI

Durable facts for coding agents working in this repo. Prefer this file + `docs/` over chat memory when they conflict.

## Project identity

- **This repo (`C:\Users\Administrator\Old-Remedy`) is the active multi-stack RemedyAI product** — FastAPI + Tauri + Vite SPA. **Not frozen.** It is product authority for this line.
- **`C:\Users\Administrator\Remedy` is a different product** (machine-only / RDNA). Not a branch of this tree; different bios home (`~/.remedyai` vs `~/.remedy` here). Do not treat one as the archive/replacement of the other.
- See `ARCHIVE.md` for the sibling-path note (filename is historical; content is status, not “frozen archive”).
- **Public GitHub tree is compile/release only.** `tests/`, `community/`, live/soak scripts, review dumps, and desktop `*.test.ts` stay on this clone (gitignored / `.git/info/exclude`) and are not pushed.

## Platforms (Windows + Linux)

One product, one `~/.remedy` home, one local API (`127.0.0.1:7400`). **v0.26.0+**
is the first true Windows **and** Linux desktop (including **WSLg**). Do not
treat Linux as “CLI only” or assume every chrome rule is Windows.

| | **Windows** | **Linux** (native + WSLg) |
|--|-------------|---------------------------|
| **Packaged app** | NSIS `Remedy.Desktop_{ver}_x64-setup.exe` | `.deb` `Remedy.Desktop_{ver}_amd64.deb` + AppImage `Remedy.Desktop_{ver}_amd64.AppImage` |
| **Sidecar binary** | `desktop/bin/remedy-desktop.exe` | `desktop/bin/remedy-desktop` (no `.exe`; do not list the Windows sidecar as a Linux resource) |
| **Tauri overlay** | `desktop/src-tauri/tauri.windows.conf.json` | `desktop/src-tauri/tauri.linux.conf.json` (deb Depends: WebKitGTK / GTK / AppIndicator / Vulkan / OpenMP) |
| **Close ✕** | Hide to **tray** (API stays warm). Quit only from tray | **Minimize to taskbar** (WSLg has no tray). No “Start with Windows” |
| **Maximize** | OS work area | Work area of **the monitor the window is on** (WSLg must not use the Windows display as the Linux work area) |
| **Webview** | WebView2 | WebKitGTK 4.1 |
| **First-run llama.cpp** | Windows zip (`win-*` runtime id) | Ubuntu `tar.gz` (`ubuntu-x64` / `ubuntu-vulkan-x64`); `chmod` `llama-server`. Shared homes remap leftover `win-*` ids |
| **In-app auto-update** | minisign `latest.json` `platforms.windows-x86_64` | Packaged releases exist; **do not** assume the Windows updater URL applies |

Owner install notes: `docs/manual/01-install-windows.md` · `docs/manual/01-install-linux.md`.

**When you touch desktop / sidecar / first-run / release:** reason about **both**
OS jobs in `desktop-release.yml` (`build-sidecar` + `build-tauri` **and**
`build-sidecar-linux` + `build-tauri-linux`). A Windows-only resource path
(`../bin/remedy-desktop.exe` on Linux) or a Windows-only llama zip on Linux
is a ship-blocker.

## Product feel (UI / UX bar)

Every desktop and settings change should feel:

| Quality | Meaning |
|---------|---------|
| **Easy** | Defaults work; rare multi-step flows live in dialogs, not dense panels |
| **Sleek** | Few primary controls; quiet secondary actions (text links, not button forests) |
| **Beautiful** | Consistent theme tokens, spacing, and typography — no visual clutter |
| **Familiar** | Patterns users already know (chat, bottom bar, Simple/Advanced) |
| **Powerful** | Full capability is one click away (Advanced UI / Advanced Settings) — never stripped |

**Practical rules**

- Prefer **Simple** chrome by default; **Advanced** reveals power-user tools.
- Settings can have its own Simple/Advanced for *sections*; main bar Simple/Advanced for *chrome* — do not conflate them.
- Long privacy / OAuth / setup = **modal or dedicated flow**, not a wall inside Settings.
- When in doubt: remove a control from the default surface rather than adding another toggle.

## Project etiquette — ship sequence (default for this repo)

When the user asks to **finish**, **ship**, **release**, or says some form of
**“test everything → if it passes update everything → build → commit to CI → if
CI passes publish to PyPI”**, follow this **gate chain**. Do not skip a gate.
Do not publish before CI is green (unless the user explicitly overrides).

This is the same discipline good teams use on *any* serious project; Remedy
encodes it as skill **`project-etiquette`** (bundled) so chat sessions load it
on demand.

### Gate chain (in order)

| # | Gate | Pass criteria | On fail |
|---|------|---------------|---------|
| 1 | **Fix / implement** | Requested behavior works; no known regressions you introduced | Keep fixing |
| 2 | **Test** | Full suite (or documented subset) green; targeted tests for the change | Fix + re-run; **do not commit “red”** |
| 3 | **Update project** | Version bump **only** when shipping runtime/API/installer behavior (not docs-only) | Align versions / assets |
| 4 | **Update documentation** | CHANGELOG + user/manual notes for user-visible change; run docs sync/check if the repo has it. **Do not bump version for docs-only fixes** (e.g. What's new catch-up) | Sync docs; re-check |
| 5 | **Build** | Package / desktop / artifacts the project expects still build | Fix build; re-test if needed |
| 6 | **Commit** | Clear conventional message; only intentional files | Split noise out of the commit |
| 7 | **Push → CI** | Remote CI green for that commit | Fix on a follow-up commit; **do not publish** |
| 8 | **Publish** (when asked) | Tag / PyPI / release only **after** CI success | Hold publish; report blocker |

### Format conventions (this repo)

- **Version surfaces:** `python scripts/sync_version.py {X.Y.Z}` (or `uv run python …`).
- **Docs gate:** `python scripts/check_docs.py` (and `scripts/sync_help_manual.py` when manuals change).
- **Tests:** local-only (`tests/` and `desktop/**/*.test.ts` are gitignored, not in the public tree). When present: `uv run pytest -q`; desktop `cd desktop && npm test && npm run build`. Public CI is compile-only (ruff / mypy / import / `check_docs` / `npm run build`).
- **Python package:** `uv build` then, after CI green, `uv publish` (credentials via env / `~/.pypirc`).
- **Desktop release:** git tag `v{X.Y.Z}` → GitHub Actions `desktop-release` (see naming rules below).
- **Commit style:** complete sentences; `release:` / `fix:` / `docs:` prefixes as appropriate.
- **Never:** force-push `master`/`main`; publish on red CI; leave version surfaces mismatched.

### Skill pointer

Activate **`project-etiquette`** for the full portable checklist (any repo) plus
Remedy-specific commands. For handoffs between agents, also use **`session-handoff`**.

**In-product (Remedy desktop/agent):** change-safety is **baked in**, not only this
file — bundled skill **`change-safety`**, Build/tool intent packs inject a standing
snippet, and **`project-etiquette`** gate 0 requires blast radius. Prefer fixing the
product skill over only editing AGENTS.md.

## Change-safety protocol (blast radius — do this *before* coding)

Ship gates catch **red tests**. They do **not** catch “fixed messenger, broke title
bar” or “desktop looks new, WebUI is stale.” Before non-trivial edits, run this
**impact pass** (takes minutes; saves multi-hour thrash).

*Product source of truth for the same protocol:* skill **`change-safety`**
(`skill_activate(name=change-safety)`). Keep AGENTS.md aligned when the skill changes.

### 1. Name the change

One sentence: *what user-visible or API-visible behavior changes?*

### 2. Classify the surface (pick primary + any secondary)

| Surface | Typical paths | High-risk neighbors |
|---------|---------------|---------------------|
| **Chat / ReAct / tools** | `src/remedy/core/`, tools, sessions routes | Concurrent streams, session LLM bind, messenger turns |
| **Messengers / gateway** | `src/remedy/gateway/` | Dual pollers, desktop SSE, outbound mirror, allowlists |
| **Desktop shell / chrome** | `desktop/src/components/TitleBar*`, `App.tsx`, Tauri `lib.rs`, `tauri.conf` + `tauri.windows.conf.json` / `tauri.linux.conf.json` | Window controls, **Windows tray vs Linux taskbar**, drag/hit-test |
| **Workspace rails** | slides, Browser, Terminal, Files | Popout/fullscreen z-index, WebView2 **or** WebKitGTK child bounds |
| **Settings / secrets** | settings routes, secret store, providers | Provider switch mid-session, keys, setup gate |
| **Docs / help only** | `docs/manual/`, help articles | Version bump **not** required; still sync help copies |
| **Release / packaging** | `scripts/sync_version.py`, CI, installers | All version surfaces + `latest.json` naming |

### 3. Blast-radius checklist (answer in the session, even briefly)

1. **Same SPA?** Desktop + WebUI share `desktop/src/` — UI change may need `npm run build` + **serve restart** for WebUI.  
2. **Two processes?** Sidecar + UI, or dual `serve` / Telegram pollers — avoid dual ownership of bot tokens or ports.  
3. **Cross-path behavior?** Messenger turn vs desktop stream vs legacy chat stream — session model/provider must match.  
4. **OS-specific?** Windows: paths, hidden processes, DPAPI, WebView2, NSIS. Linux: WebKitGTK/GTK deps, AppImage/deb, no tray, llama `tar.gz` + chmod, no `.exe` sidecar resource. Run or reason about **both** CI subsets.  
5. **Hard to unit-test?** Title bar, tray/taskbar, embedded browser, native drag — schedule a **manual smoke** (below) on the OS you changed.  
6. **Architecture traps?** Prefer durable design over band-aids for known failure classes (e.g. OS decorations for window buttons, not WebView fake chrome).

### 4. Required checks by surface (minimum)

Always when shipping runtime/UI: if the local test tree is present, full `pytest` + when desktop touched: `cd desktop && npm test && npm run build`. Public CI does not run those suites.

| If you touch… | Also verify… |
|---------------|--------------|
| Gateway / Telegram | Poll lock acquire in logs; single instance; inbound + desktop→outbound; no 409 spam |
| Session stream / LLM bind | Provider switch on one tab doesn’t poison another; messenger session uses session provider |
| `App.tsx` / SSE / messages | Streaming not force-reloaded mid-turn; session list still refreshes |
| Title bar / `decorations` / window cmds | **OS** min/max/close still work. Windows: tray + close-to-tray + quit. Linux: ✕ minimizes to taskbar |
| Browser slide / `browser_host` | Auto-load or Go works; ↗ external open; popout chrome still clickable |
| Settings / secrets | Save settings, reconnect, no plaintext tokens in config |
| Manuals | `sync_help_manual.py` + `check_docs.py` |
| Version / release | All surfaces via `sync_version.py`; installer asset naming rules below |

### 5. Manual smoke matrix (desktop — tests miss this)

Run when the change touches shell, chrome, messengers, or browser. **One** clean app instance:

| # | Smoke | Pass |
|---|-------|------|
| 1 | Launch → server ready | Status connected |
| 2 | New chat → short reply | Stream completes |
| 3 | Min / max / restore / close | **Windows:** ✕ → tray (Quit only from tray). **Linux / WSLg:** ✕ → taskbar; maximize stays on that monitor |
| 4 | Open Browser rail | Page loads (or clear error + ↗ works) |
| 5 | If messengers enabled | Telegram in → desktop; desktop reply → Telegram |
| 6 | Quit fully → relaunch | No dual serve / dual poller |

Log greps when debugging: `poll lock`, `getUpdates 409`, `browser embed`, `add_child`.

### 6. Pre-commit “neighbor” rule

Before commit, list **files you did not edit** that share coupling with your change
and confirm you either tested them or have a reason they are safe. If unsure,
add a targeted test or a one-line note in the commit body (`Risk: … / Smoke: …`).

### 7. What CI does *not* prove

CI does not click the title bar, drive Telegram, or exercise WebView2 / WebKitGTK
multiwebview on a real GPU. Treat green CI as necessary, not sufficient, for
those zones. Desktop-release **does** build Windows NSIS **and** Linux `.deb` +
AppImage — a red Linux job is a failed release even if Windows is green.

## Desktop installer / auto-update naming

**Critical for in-app updates (Windows).** The signed `latest.json` URL must match the GitHub Release asset name **exactly**. Linux packages ship on the **same** tag; they are not in `latest.json` today.

| Item | Canonical form |
|------|----------------|
| Git tag | `v{X.Y.Z}` (e.g. `v0.26.2`) |
| Release title | `Remedy Desktop v{X.Y.Z}` |
| Windows installer | **`Remedy.Desktop_{X.Y.Z}_x64-setup.exe`** |
| Linux deb | **`Remedy.Desktop_{X.Y.Z}_amd64.deb`** |
| Linux AppImage | **`Remedy.Desktop_{X.Y.Z}_amd64.AppImage`** |
| Metadata asset | `latest.json` (same release; Windows updater) |
| Windows installer URL | `https://github.com/AhmiDarrow/RemedyAI/releases/download/v{X.Y.Z}/Remedy.Desktop_{X.Y.Z}_x64-setup.exe` |

### Rules

1. **Dots, not underscores**, between product words: `Remedy.Desktop_*` — never `Remedy_Desktop_*`.
2. Tauri may emit a **space** (`Remedy Desktop_…`); CI renames spaces → dots before upload for **Windows and Linux** (`.github/workflows/desktop-release.yml`).
3. `scripts/sync_version.py` stamps `scripts/latest.json` with the Windows `Remedy.Desktop_*_x64-setup.exe` URL. Linux `.deb` / AppImage are extra files on the same tag, not updater platforms yet.
4. `platforms.windows-x86_64.url` in published `latest.json` must equal the Windows asset’s `browser_download_url`. Signature must be **raw minisign** (`untrusted comment:`), not Tauri’s base64 wrapper.
5. **Easy ops fix** when update fails with  
   `Download URL does not match signed latest.json asset`:  
   **rename the GitHub Release asset** to `Remedy.Desktop_{ver}_x64-setup.exe` (and ensure `latest.json` points at that name). Do not disable the URL match check.
6. Install path in `desktop/src-tauri/src/lib.rs` **re-reads** signed `latest.json` at download time so a stale UI-held URL cannot fail after a multi-MB pull. Dual-decode (raw or base64) is on master for older clients.

### Related docs

- `docs/DESKTOP.md` — full auto-update pipeline + naming table  
- `docs/WINDOWS_SIGNING.md` — minisign + Authenticode  
- `docs/manual/08-updates-and-uninstall.md` — user-facing update flow  

## Desktop SPA vs WebUI — same code, different load paths (critical)

Desktop UI and browser **WebUI share one React SPA** under `desktop/src/`.
There is no separate WebUI frontend. What differs is **how the built assets are
loaded**:

| Surface | How it gets UI code |
|---------|---------------------|
| **Tauri desktop (`tauri:dev`)** | Vite dev server (HMR) — always latest `desktop/src` |
| **WebUI** `http://127.0.0.1:7400/` | Static files from a built SPA directory mounted by the local API |

### Where WebUI assets come from

`remedy.interfaces.api.find_webui_dir()` resolves the SPA root (see that function
for the full ordered list). **Prefer live Vite output:**

1. `REMEDY_WEBUI_DIR` if set  
2. `REMEDY_DEV_ROOT/desktop/dist`  
3. Repo `desktop/dist` (discovered from source tree)  
4. `…/desktop/dist` next to a Tauri debug layout  
5. **Staged / packaged copies last:**  
   `desktop/src-tauri/target/debug/webui`, `desktop/bin/webui`, bundle `webui/`, etc.

`desktop/dist` is **gitignored**. Building is required for WebUI to see SPA
changes that are not on the Vite dev server.

### Desync pitfall (2026-07 — fixed lookup, still easy to hit)

**Symptom:** Desktop (tauri:dev) shows new UI; browser WebUI still looks old after
refresh. Network tab may show an **old hashed** script name (e.g. `index-BNJOTVWc.js`)
while `desktop/dist/index.html` references a **new** hash (`index-C7Fni8m6.js`).

**Cause:** The API/sidecar process mounted a **stale staged** folder
(`target/debug/webui` or `bin/webui`) at **startup**, not the freshly rebuilt
`desktop/dist`. Refreshing the browser only reloads that old mount.

**Also:** The SPA mount directory is chosen once at server start. Changing
`find_webui_dir` priority or rebuilding dist does not remount until **serve
restarts**.

### Agent / dev procedure after UI changes

When the user cares about **WebUI parity** with desktop:

1. Edit `desktop/src/…` as usual.  
2. `cd desktop && npm run build` → writes `desktop/dist`.  
3. **Restart** the local API / desktop app so serve re-resolves `find_webui_dir`
   (or at least restarts after the 0fa331a lookup fix so it prefers `desktop/dist`).  
4. If a frozen/debug sidecar is still bound to staged `webui/`, either restart
   after the code preference is live, **or** sync:
   ```text
   Copy-Item -Recurse -Force desktop\dist\* desktop\src-tauri\target\debug\webui\
   # and if present:
   Copy-Item -Recurse -Force desktop\dist\* desktop\bin\webui\
   ```
5. Hard-refresh the browser (**Ctrl+F5**). HTML is served with **no-cache**
   headers so the entry document should pick new hashed assets; assets themselves
   are content-hashed.

### Quick verification

```text
# Disk (after build)
Select-String -Path desktop\dist\index.html -Pattern 'index-.*\.js'

# Live WebUI (must match after restart/sync)
# Fetch http://127.0.0.1:7400/ and check the same index-*.js name
```

### Do not

- Assume “refresh the WebUI tab” alone picks up `npm run build` if serve is still
  pointing at an old `webui/` tree.  
- Edit only `target/debug/webui` — that is a **staging mirror**, not the source of truth.  
- Forget that packaged releases still ship a staged `webui`; release pipelines must
  continue to stage `desktop/dist` into the install layout.

### Related

- `src/remedy/interfaces/api.py` — `find_webui_dir`, `_mount_web_ui`  
- `docs/DESKTOP.md` — Switch to Web UI  
- Commit `0fa331a` — prefer `desktop/dist` over stale sidecar `webui`

## Version surfaces

Keep these aligned via `python scripts/sync_version.py {X.Y.Z}`:

- `pyproject.toml`
- `desktop/package.json` (+ lock)
- `desktop/src-tauri/tauri.conf.json`
- `desktop/src-tauri/Cargo.toml` (+ lock)
- `scripts/latest.json`
