# What's new (recent)

High-level product notes for owners. Full detail lives in repo `CHANGELOG.md`.

Ship **one** installer/tag for the current series (**v0.15.3-unreleased**).

## 0.15.3 - Shell + in-app tools (unreleased)

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
