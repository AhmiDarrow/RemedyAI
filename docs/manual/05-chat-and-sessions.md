# Chat & sessions

## Main window map

```
┌ Title bar (logo menu) ──────────────────────────────┐
│ Session tabs                                         │
├ Sidebar ───┬─ Message feed ──────────────┬─ Panel ──┤
│ sessions   │  bubbles + tools            │ Memory / │
│            │  composer                   │ Skills / │
│            │                             │ Settings │
├────────────┴─────────────────────────────┴──────────┤
│ Status: model · think · ask · Proc · Plan · panels  │
└─────────────────────────────────────────────────────┘
```

**F1** / **Ctrl+/** open this Help wiki (not only a chat dump).

## Sending messages

| Input | Action |
|-------|--------|
| **Enter** | Send |
| **Shift+Enter** | New line |
| **↑ / ↓** | Previous / next prompt (composer history) |
| **@** | Search project files to attach references |
| **/** | Slash command menu |
| Drag / paste | Attach files or images |

## Sessions

- **New session** — Ctrl+N, logo menu, or `/new`  
- **Tabs** — multiple open chats  
- **Auto-title** — from the first prompt  
- **Rename / pin / search / tags** — session sidebar features  
- **Export** — `/export` or command palette → `.txt`  
- **Import** — `/import-session` or palette → `.txt` / `.md`  

## Plan vs Build

| Mode | Use when |
|------|----------|
| **Plan** | Explore and design; **shell/file tools blocked**. Remedy can save a structured plan (`plan_save`) with steps + risks. |
| **Build** | Implement changes, run tools, write files — follows the latest plan when present. |

Status bar toggle or **Ctrl+B**. Desktop sends `plan_mode` to the local API so the server enforces the allowlist.

**Structured plans** live under `~/.remedy/plans/`. Slash commands:

| Command | Action |
|---------|--------|
| `/plans` | List plans |
| `/plan` | Show latest plan |
| `/plan new <title>` | Create an empty draft |
| `/plan approve` | Mark latest plan approved before Build |

API: `GET/POST /api/plans`, `GET /api/plans/latest`, `POST /api/plans/{id}/status`.

## Mid-task checkpoints (Build)

On long tool runs, Remedy auto-saves **checkpoints** under `~/.remedy/checkpoints/` (every few tool steps, after recovery, and at turn end). Tools: `checkpoint_save`, `checkpoint_show`. API: `GET /api/checkpoints`, `GET /api/checkpoints/latest`.

If a long task soft-fails, open the latest checkpoint (or ask “show last checkpoint”) to resume without losing done/next context.

## Tool process (Proc)

Controls how much tool activity appears under assistant messages:

| Mode | You see |
|------|---------|
| **Off** (default) | Minimal progress |
| **Medium** | Labels + status + short results |
| **Full** | Near-raw args and stdout (capped) |

Change via status bar **Proc** or Settings. Expand/collapse process under a message after the turn.

## Thinking & approvals

- **Think** level (Off–High) — how much reasoning detail the UI emphasizes  
- **Ask / Auto** — approval policy for high-impact tools  
- Live approvals appear as a banner above the feed  

## Streaming & stick-to-bottom

- Tokens stream into the assistant bubble.  
- Feed follows the bottom unless you scroll up; **↓** resumes follow.  
- **Stop** aborts the current generation.  

## Editing & regenerate

- Edit a prior user message (when available) to branch the conversation.  
- Regenerate an assistant reply when the UI offers refresh.  

## Themes & density

Settings → Appearance: system/dark/light themes, density, custom accent. Does not change provider or data.

## Related

- [Commands](11-reference-commands) · [Shortcuts](12-reference-shortcuts) · [Memory & harness](06-memory-and-harness)
