# Memory & Memory Harness

Remedy keeps durable knowledge so it can remember facts, goals, and session context across restarts.

## Concepts

| Piece | Role |
|-------|------|
| **Memory entries** | Searchable facts / notes (FTS5) |
| **Partner Memory** | Durable identity + preferences injected every turn (budget-capped) |
| **User profile** | Display name, traits, and facts that back Partner Memory |
| **Handoff notes** | Structured “what was done / next” |
| **Session Brief** | Compressed summary of the current chat (Harness) |
| **Goals** | Lightweight checklist (`/goal`, `/goals`) |

## In the desktop app

- **Memory** panel (status bar) — three tabs:
  - **Memory** — browse / search stored items  
  - **Checkpoint** — latest mid-task progress (auto-saved on long Build runs)  
  - **Plan** — latest structured plan; **Approve** before Build  
- Status bar shows **Memory · CP** when a checkpoint exists  
- Slash commands in chat (see below)  
- Settings → **Your name** syncs into profile  
- Settings → **MCP host** — export skills to Cursor / Claude Desktop

## Essential commands

| Command | Purpose |
|---------|---------|
| `/remember <fact>` | Store a durable fact |
| `/forget <text>` | Remove a matching Partner Memory fact |
| `/memory <query>` | Search memory |
| `/whoami` | Show what Remedy knows about you |
| `/goal <title>` | Add a goal |
| `/goals` | List open goals |
| `/compact [focus]` | Compress session into a Session Brief |
| `/harness` | Show Session Brief / harness stats |
| `/import <folder>` | Import `.md`/`.txt` notes into memory |
| `/handoff` | List handoff notes |

## Memory Harness

Long chats fill the model context window. The harness:

1. Tracks fill against min/max context percentages (Settings).  
2. Can auto-compress when fill is high.  
3. **`/compact`** forces a Session Brief so you keep continuity without raw history.  

**Harness modes** (Settings):

| Mode | Behavior |
|------|----------|
| **Auto** | Compress when thresholds say so |
| **Manual** | Only when you `/compact` |
| **Off** | No harness compression |

## Continuity quality

Remedy tracks **session quality** quietly: tokens saved by compress, stuck signals,
and whether compress kept important files/decisions. Type **`/harness`** for a
snapshot. Background continuity also:

- Collapses old completed tool spans when context grows  
- Injects short recovery guidance if you re-explain or loops appear  
- Learns lightly per project folder (e.g. compress a bit earlier next time)  

You should not need to manage this. For philosophy, see
[How Remedy works (continuity)](16-continuity-philosophy).

## Partner Memory (just works)

Remedy quietly keeps a small **Partner Memory** block so it feels like the same
partner next session:

- Prefer / always / never phrasing in chat is distilled automatically when safe
  (one-off “always run the tests now” chatter is ignored).  
- High-confidence facts are injected every turn (size-capped so the model stays sharp).  
- `/whoami` lists what it knows; `/forget <text>` removes a mistake; `/pin` keeps a fact always ready.  
- Secrets (API keys, passwords) are **never** auto-stored.  
- Click the **$** on the usage ticker to hide estimated cost (tokens stay visible).

You do not need to configure anything. `/remember` is still the explicit pin when
you want certainty.

## Best practices

- Natural language is enough (“I prefer TypeScript”); `/remember` for hard pins.  
- Use `/forget …` if something was mis-learned.  
- Use `/compact` before a big context switch.  
- Import project notes with `/import` rather than pasting megabytes into chat.  
- Treat memory as **local** — back up `memory.db` if it matters (CLI: `remedy memory backup`).  

## CLI counterparts

```bash
remedy memory add "title" "content" --tags "tag1"
remedy memory search "query"
remedy memory list
remedy memory backup
remedy handoff create "title" "content"
```

## Related

- [How Remedy works](16-continuity-philosophy) · [Chat & sessions](05-chat-and-sessions) · [CLI & API](10-cli-and-api) · [Security & data](04-security-and-data)
