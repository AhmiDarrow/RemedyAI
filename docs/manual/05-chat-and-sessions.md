# Chat & sessions

## Main window map

```
┌ Title bar (logo menu · min / max / close→tray) ─────┐
├ rail ┬ left slide ┬─ Message feed (landing when empty) ┬ right ─┤
│ icons│ sessions   │                                    │ slide  │
│ thin │ …          │  usage ticker                      │        │
│      │            │  composer (bottom)                 │        │
├──────┴────────────┴────────────────────────────────────┴────────┤
│ Status: model · think · ask · Proc · Plan · panels             │
└────────────────────────────────────────────────────────────────┘
```

**F1** / **Ctrl+/** open this Help wiki (not only a chat dump).  
Minimize / maximize / close are the top-right buttons (close hides to tray when Always ready is on).

## Sending messages

| Input | Action |
|-------|--------|
| **Enter** | Send |
| **Shift+Enter** | New line |
| **↑ / ↓** | Previous / next prompt (composer history) |
| **@** | Search project files to attach references |
| **/** | Slash command menu |
| Drag / paste | Attach files or images |
| **Click any chat image** | Open the **image viewer** |

### Image viewer & markup

Any image in the session (markdown, Comfy, local path via `/api/media`, or URL)
scales in the bubble. **Hover** for **Edit · Copy · Save**. **Click** or **Edit**
opens the full viewer:

| Tool | Shortcut | Purpose |
|------|----------|---------|
| **Pen** | `P` | Freehand draw |
| **Highlight** | `H` | Semi-transparent marker |
| **Arrow** | `A` | Point at a region |
| **Box** | `R` | Rectangle callout |
| **Text** | `T` | Place a short label |
| **Undo** | `Ctrl+Z` | Remove last stroke |
| **Zoom** | `+` / `−` / `0` | Enlarge / shrink / reset |

**Save & attach to prompt** exports the (annotated) PNG, closes the editor, and
puts the file on the composer rail so you can point things out to Remedy.

## Sessions

- **New session** — Ctrl+N, logo menu, or `/new` (root session — no project)  
- **Click a session** — loads that chat in the middle pane  
- **Add project folder** — paste a path or **Browse…** (native folder picker)  
- **Archive** — filter + auto-hide after 30 days (not pinned); row toggle  
- **Auto-title** — from the first prompt  
- **Rename / pin / search / tags** — session sidebar features  
- **Export** — `/export` or command palette → `.txt`  
- **Import** — `/import-session` or palette → `.txt` / `.md`  

## Three-frame workspace

```
[ left slide ] | chat | [ right slide ]
```

Remedy is a **workbench**: chat in the center, real tools on the rails.

| Rail tool | What it is |
|-----------|------------|
| **Sessions** | Chats, projects, messenger threads |
| **Settings** | Provider, vision/Qwen, messengers, appearance |
| **Files** | In-app file browser for the session/project |
| **Terminal** | In-app PowerShell (ConPTY) on this PC |
| **Browser** | Embedded WebView2 research pane (**↗** = system browser) |
| **Scratch** | Session-linked notepad |

Each side: **thin strip** → **icon rail** → **open panel** (× collapses).  
**⇄ Swap sides** · Terminal / Browser / Scratch: popout (↗) or fullscreen (⛶).  
Fullscreen: **Exit fullscreen** / **Close** on the top bar, or **Esc**.

## Plan vs Build

| Mode | Use when |
|------|----------|
| **Plan** | Research & design only: **read/search/list/fetch OK**; **writes/shell blocked**. Ask clarifying questions; save a structured plan (`plan_save`); ASCII outline in chat. |
| **Build** | Implement changes, run tools, write files — follows the latest plan when present. |

Status bar toggle or **Ctrl+B** / Shift+Tab in composer. Desktop sends `plan_mode` so the server enforces the allowlist.

**Plan banner** (above chat): **Approve → Build**, **Request changes**, refresh. Approve leaves Plan mode and seeds a kickoff prompt.

**Structured plans** live under `~/.remedy/plans/`. Slash commands:

| Command | Action |
|---------|--------|
| `/plans` | List plans |
| `/plan` | Show latest plan |
| `/plan new <title>` | Create an empty draft |
| `/plan approve` | Mark latest plan approved before Build |

API: `GET/POST /api/plans`, `GET /api/plans/latest`, `POST /api/plans/{id}/status`.

## Time Travel (undo browser)

Long multi-step runs can go wrong mid-way. Use **⏱ Time travel** on the status bar
(or Command Palette → *Time Travel*):

1. Open the timeline of user/assistant steps for this session.  
2. Click the step you want to return to (e.g. step 3 of 6).  
3. Confirm **Restore here**.

Remedy soft-deletes later chat messages, best-effort restores workspace files
touched via `file_write` (undo log under `~/.remedy/undo/`), drops mid-task
checkpoints after that point, and clears the live session brief.

API: `GET /api/sessions/{id}/timeline`, `POST /api/sessions/{id}/time-travel`
with `{ "message_id": "…" }`.

## Token & cost ticker

A small **Usage** chip (bottom-right) tracks tokens and estimated API cost for
the current run and the session. Expand for in/out breakdown and model. Hide
with **×** (re-open from the `$ tokens` chip). Prefers provider-reported usage
when available; otherwise estimates from text + published list prices.

## Mid-task checkpoints (Build)

On long tool runs, Remedy auto-saves **checkpoints** under `~/.remedy/checkpoints/` (every few tool steps, after recovery, and at turn end). Tools: `checkpoint_save`, `checkpoint_show`. API: `GET /api/checkpoints`, `GET /api/checkpoints/latest`.

If a long task soft-fails, open the latest checkpoint (or ask “show last checkpoint”) to resume without losing done/next context.

## Tool process (Min / Med / Full)

Controls the **tool trail** under assistant messages. The model’s **chat answer is always complete** — process mode never hides or truncates what the model said to you.

| Mode | You see |
|------|---------|
| **Min** (default) | Process step names + status |
| **Med** | Same list + path/command one-liners and short results |
| **Full** | Same list + complete args/results (expanded by default) |

Change via status bar or Settings. Full keeps process expanded after the turn so nothing important is buried.

## Thinking & approvals

- **Think** level (Off–High) — how much reasoning detail the UI emphasizes  
- **Ask / Auto** — approval policy for high-impact tools  
- Live approvals appear as a banner above the feed  

## Streaming & stick-to-bottom

- Tokens stream into the assistant bubble.  
- Feed follows the bottom unless you scroll up; **↓** resumes follow.  
- **Stop** aborts the current generation.  

## Sessions by project

The left sidebar groups chats:

| Group | Contents |
|-------|----------|
| **No project** | Sessions not attached to a folder (tools use full access that turn) |
| **📁 Project name** | Sessions under that directory; tools are jailed to that project for the turn |

- **+** on a project header — new session in that project  
- **+ Add project folder** — register a folder (browse or type path) even before any chats  
- **📁** on a session row — move the chat to another project / No project  
- **Checkbox** multi-select + toolbar move; **Shift+click** range; **drag** sessions onto a folder  
- **Load more** when you have many sessions (paginated)  
- **New-in-project sets default** — optional checkbox to also write Settings → project path  

Default **New Session** still uses Settings → default project folder when set.

## Editing & regenerate

- Edit a prior user message (when available) to branch the conversation.  
- Regenerate an assistant reply when the UI offers refresh.  

## Themes & density

Settings → Appearance: system/dark/light themes, density, custom accent. Does not change provider or data.

## Related

- [Commands](11-reference-commands) · [Shortcuts](12-reference-shortcuts) · [Memory & harness](06-memory-and-harness)

## Three-frame workspace

`
[ left slide ] | chat | [ right slide ]
`

- **Left / right rails:** Sessions · Settings · Files · Terminal · Browser · Scratch
- **⇄ Swap sides** centers above chat
- Hide a side with **×**; reopen from the edge strip
- Terminal / Browser / Scratch support **pop out** (↗) and **fullscreen** (⛶). Exit with the top bar buttons or **Esc**.
- Browser **⌂ Home** defaults to the Remedy GitHub page (change in **Settings → Project workspace → Browser homepage**).
- **Images** in chat (attach, paste, or paths the model writes) always render as previews — independent of which model you use.
- Open **session tabs** live only inside the **Sessions** slide (not above chat)
- **Archive** filter + auto-hide sessions older than 30 days (not pinned/open)

