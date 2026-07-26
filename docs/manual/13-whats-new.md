# What's new (recent)

High-level product notes for owners. Full detail lives in repo `CHANGELOG.md`.

Ship **one** installer/tag for the current series (**v0.14.8**).

## 0.14.8 — Project etiquette (ship skill)

- Bundled **`project-etiquette`** skill: test → docs → build → commit → CI → publish only if green.
- Same gate chain is default ship protocol in `AGENTS.md` (works for any serious project).

## 0.14.7 — Calmer update + install always starts

- One clear update message (download, then restart to finish).
- App waits until the install script is alive before closing.
- Multi-path install schedule from 0.14.6 kept (PowerShell + WScript + schtasks).

## 0.14.6 — Autoupdate install reliability + alpha logos

- Multi-path install schedule so install runs after close.
- Full alpha brand kit regenerated for public/ + Tauri icons.

## 0.14.5 — Stream queue, sticky answer, usage ticker, export

- Send while Remedy streams (queue or interrupt).  
- Thinking + answer docked at the bottom of chat.  
- Live usage ticker + correct Grok 4.5 cost estimate.  
- Faster session export/import; alpha chat monogram.

## 0.14.4 — Brand assets + silent update host

- Alpha logo/icon kit wired through splash, Setup, About, Update screen, chat, tray.
- Autoupdate install popup stays; **no black CMD flashes** (hidden PowerShell host).
- Update status copy is ASCII-safe (no mojibake on Windows PowerShell 5.1).

## 0.14.3 — Chat images · session export · stay-on-task

- Local image paths in chat markdown render for **any** provider (`/api/media`).
- Session **Export** uses a real Save dialog in the desktop shell.
- Auto-approve + tool continuity fixes so short follow-ups keep agency.

## 0.14.1 — Autoupdate: download UI, install popup, one restart

- Download progress stays **inside** Remedy.  
- When Remedy closes, a **new** install-progress window shows silent install.  
- App restarts **once** (no second window from the installer).  

## 0.14.0 — Maintainability: ReAct peel + Settings modules

- Same product behavior; safer internals for agency and Settings work.
- Agent stream/tool-batch split into dedicated modules (faster fixes, more tests).
- Settings UI split into shell + form sections (search/sections unchanged).
- CI: Windows path-sensitive tests + desktop unit build gate.
