# What's new (recent)

High-level product notes for owners. Full detail: repo `CHANGELOG.md`.

Current series: **v0.18.5**.

## Contents

- [0.18.5](#0185---telegram-poll-lock-recovery) · [0.18.4](#0184---messenger-realtime--sync) · [0.18.3](#0183---provider-switch--stability) · [0.18.2](#0182---spread-run-fix) · [0.18.1](#0181---run-until-finished--title-bar) · [0.18.0](#0180---spread--library-suggest) · [0.17.0](#0170---coding-agency--process-trail) · [0.16.0](#0160---messengers--polish) · older below

## 0.18.5 - Telegram poll lock recovery

- **Messenger stays live after restarts:** a dead process can no longer “own” the Telegram poll forever on Windows.
- **Auto-recover** if a second instance or crash left the bot lock behind (heartbeat + retry).

## 0.18.4 - Messenger realtime + sync

- **Telegram realtime:** only one Remedy process may long-poll the bot (stops HTTP 409 “another poller” thrash).
- **No catch-up flood** on restart — update offset is saved; first run drains backlog without replaying into chat.
- **Desktop replies reach Telegram** when you chat in a messenger session (was inbound-only).
- **Smoother live sync** while a reply is streaming (no force full-thread reload mid-turn).
- **Concurrent sessions:** safer provider/model bind across tabs and messengers.

## 0.18.3 - Provider switch + stability

- **Status bar provider/model switch** sticks for the session (no more DeepSeek API + Grok model name mismatch).
- **Missing model / HTTP 404** stops cleanly with “switch model” (no soft-retry spam).
- **Quit warning “Don’t show again”** is saved before exit.
- **Update check on launch** after the local server is ready.
- **Fewer Windows cmd flashes** during search/spread/git tool work.

## 0.18.2 - Spread run fix

- **`spread_run` no longer fails** when the model passes `tasks` as a native list (common with tool calling). Process trail showed a red **Spread Run** error; that path is fixed.
- `tasks` accepts a JSON array, a single task object, or a JSON string; `goal=` still auto-plans workers.

## 0.18.1 - Run until finished + title bar

- **Long coding / missions keep going** until the work is done — soft “epochs” only compact context and checkpoint; they do **not** stop tools with a fake tool-limit answer (Build-class agency).
- Pathological loops still have a high safety ceiling; idle pauses only after long stretches with **no** tool activity.
- **Title bar:** min / max / close stay clickable after you move, minimize, or maximize the window (explicit drag on the middle strip; controls never steal-hit as drag).

## 0.18.0 - Spread + Library suggest

- **`spread_run`:** silent parallel explore/search/verify workers so multi-area tasks cover ground faster — still one Remedy voice.
- **Library skill check:** on real work, a soft tip when a signed Library pack would help; **Install** from the chip (or open Skills); never auto-installs without a click.
- **Hardening:** tighter path jail and shell approvals on jobs; Stop kills in-flight shell trees; chat hot path stays free of blocking local-model waits.

## 0.17.0 - Coding agency + Process trail

- **Code search** works for any text language (not just Python/JS) — optional bundled ripgrep; no need to install tools for basic discovery.
- Work on **any folder path** without forcing a project jail; focus folder is optional convenience.
- Stronger **Build** tools: longer shell timeouts, multi-file edits, explore/verify jobs, mission verify before “done”.
- **Process** Min / Med / Full is readable on long tool runs (no double chip clouds; grouped steps on Min/Med; full dumps on Full).

## 0.16.0 - Messengers + polish

- **Settings → Messengers:** connect Telegram (live) and modular Discord / Slack / Mattermost / Matrix / WhatsApp / Teams / Google Chat / Signal adapters — tokens stay in the secret store.
- Messenger threads show up as normal sessions in the desktop; history and live updates stay in sync.
- Skills Library refresh is smoother; empty chat shows a clean monogram; Memory Progress is calmer.
- Owner docs showcase workspace tools, local Qwen, and messengers; download link always means **latest**.
- WebUI and desktop share one SPA; rebuild + restart picks up UI changes correctly.

## 0.15.9 - Skills Library visibility + first-session fix

- **Installed | Library** tabs fixed under the Skills title (not clipped by chrome).
- **Memory → Progress**: calmer checkpoint wording (not raw scare-logs).
- Empty chat monogram (no plate bubble); WebUI uses same SPA as desktop.
- First message after boot waits for sessions/model; re-bootstrap token on 401 after update.
- Messengers + session history SQL fix for long / messenger chats.

## 0.15.8 - Skills Library

- **Skills → Library:** browse the signed community catalog, install into quarantine, then **Trust**.
- **Installed** panel cleaned up: Trust / Promote / Quarantine / Archive / Edit / Delete without the old control clutter.
- Library installs are checksummed and path-safe; delete removes user packs under `~/.remedy/skills/`.

## 0.15.7 - Memory Harness v2

- Long chats stay sharp: Auto harness **enforces** a lean model send-view (your full transcript is still saved).
- **Session Brief** keeps intent, decisions *why*, files, and a history thread; local model can refresh the brief in the background.
- Process trail is **Min / Med / Full** (Full+ removed). Plan mode is per chat session.
- Browser only on one rail at a time (stable embed).

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
