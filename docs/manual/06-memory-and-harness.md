# Memory & Memory Harness

Remedy keeps durable knowledge so it can remember facts, goals, and session context across restarts.

## Concepts

| Piece | Role |
|-------|------|
| **Memory entries** | Searchable facts / notes (FTS5) |
| **User profile** | Display name and durable profile fields |
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

## Best practices

- Store stable facts with `/remember` (“I prefer TypeScript”, “Deploy host is …”).  
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

- [Chat & sessions](05-chat-and-sessions) · [CLI & API](10-cli-and-api) · [Security & data](04-security-and-data)
