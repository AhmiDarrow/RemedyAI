# What's new (recent)

High-level product notes for owners. Full detail lives in repo `CHANGELOG.md`.

Ship **one** installer/tag for the current series (**v0.15.6**).

## 0.15.6 - Images in chat for every model

- Drag/drop or paste an image → it **shows in the chat bubble** (markdown preview), not only a file path.
- Works with any chat model; vision understanding still uses the provider or local visual decoder when available.
- Stream finish is smoother (no empty flash before the reply lands).

## 0.15.5 - Popout exit, embed browser, homepage

- **Fullscreen** (Terminal / Browser / Scratch): top bar **Exit fullscreen** + **Close**, or **Esc**.
- **Browser:** stays embedded in the panel; default homepage is the **Remedy GitHub** repo (change under **Settings → Project workspace → Browser homepage**).
- Quit and window chrome reliability improvements from the 0.15.x shell work.

## 0.15.4 - Chrome, chat, rails, browser

- **Title bar:** minimize / maximize / close work again (drag strip no longer steals clicks; close hides to tray).
- **Chat:** prompt stays at the **bottom**; empty-session landing page restored; session list clicks always load history.
- **Sessions:** open-tab chip strip removed; **Browse…** for project folders uses the native picker on the UI thread.
- **Browser:** embedded panel (iframe), not a blank popup window.
- **Terminal:** bright blinking block cursor; click to focus.
- **Rails:** thin strip → icons → open panel (both sides).
- **Usage ticker** above the composer; **About** includes Ahmi’s note.

## 0.15.3 - Shell + in-app tools

- True three-column workspace; in-app PowerShell and browser; original shell icons; path images.
- **New Session = root** (no project). New Project folder is first-run only, not every session.

## 0.15.2 - Workspace harden

- Safer workspace prefs (bad slide ids no longer crash).
- Plan stays visible after Approve -> Build until you Hide it.
- Browser URL hardening; scratch pad writes debounced.

## 0.15.1 - Workspace polish

- PowerShell terminal, Firefox browser open, archive fix, quieter plan banner.

## 0.15.0 - Workspace / plan mode / images

- Three-frame slides, image markup attach, session archive, Plan approve banner.

## 0.14.10 - Image viewer + markup

- Full-screen viewer for any chat image.
- Snipping-Tool-style markup; attach annotated PNG to your next prompt.

## 0.14.9 - Icons + faster export/import

- Theme-aware alpha chat monogram; bold tray plate; clearer taskbar icon.
- Native save/open dialogs; smaller/faster session export (tool dumps capped).

## 0.14.8 - Project etiquette (ship skill)

- Bundled **`project-etiquette`** skill: test -> docs -> build -> commit -> CI -> publish only if green.
- Same gate chain is default ship protocol in `AGENTS.md` (works for any serious project).

## 0.14.7 - Calmer update + install always starts

- One clear update message (download, then restart to finish).
- App waits until the install script is alive before closing.
- Multi-path install schedule from 0.14.6 kept (PowerShell + WScript + schtasks).

## 0.14.6 - Autoupdate install reliability + alpha logos

- Multi-path install schedule so install runs after close.
- Full alpha brand kit regenerated for public/ + Tauri icons.
