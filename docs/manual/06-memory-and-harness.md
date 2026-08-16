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
| **Goals** | Durable **life goals** (`/goal`, `/goals`) — horizon, next action, evidence. Session tasks stay a chapter checklist. |
| **CAS** | Machine memory: content-addressed objects under `~/.remedy/cas`. Survives restart. Query-keyed, not a journal. |

## In the desktop app

- **Memory** panel (status bar) — tabs:
  - **Memory** — browse / search stored items  
  - **Life** — durable life goals, next action, mark done  
  - **Checkpoint** — latest mid-task progress (auto-saved on long Build runs)  
  - **Plan** — latest structured plan; **Approve** before Build  
- Status bar shows **Memory · CP** when a checkpoint exists  
- Slash commands in chat (see below)  
- Settings → **Your name** syncs into profile  
- Settings → **You & Agent → Wipe persona…** — forget Partner Memory, soul residue, and life goals (type **WIPE**). Chats, keys, and skills stay.  
- Deleting a chat asks first and also drops that chat’s notes, attachments, and plans. Partner Memory is kept until you wipe persona.  
- Settings → **MCP host** — export skills to external MCP clients

## Essential commands

| Command | Purpose |
|---------|---------|
| `/remember <fact>` | Store a durable fact |
| `/forget <text>` | Remove a matching Partner Memory fact |
| `/memory <query>` | Search memory |
| `/whoami` | Show what Remedy knows about you (and this home) |
| `/stretch` | Map this PC — hardware, tools, rooms (`/home`) |
| `/goal <title>` | Hold a life goal; Remedy takes one local step (notes in **Documents/Remedy Life**) |
| `/goals` | List life goals and the next action |
| `what should I do?` / `work on my goals` | Take the next local step and open the note |
| `I did it` | Notice you finished the current move and invent the next one |
| `I'm back` / `what did you do?` | Digest of Life steps Remedy already took |
| `/compact [focus]` | Compress session into a Session Brief |
| `/harness` | Show Session Brief / harness stats |
| `/import <folder>` | Import `.md`/`.txt` notes into memory |
| `/handoff` | List handoff notes |

## Memory Harness

Long chats fill the model context window. The harness:

1. Tracks fill against min/max context percentages (Settings) using your **model’s real window**.  
2. On **soft/strong** fill: **enforces** a leaner send-view (collapse old tool dumps, token budget, optional disk offload) — stored chat is untouched.  
3. Builds a **Session Brief** (intent, decisions with *why*, files, next steps) plus a **cumulative history thread** so multi-step compressions don’t wipe earlier reasoning.  
4. Uses Remedy’s **on-device local model in the background** (when available) to refresh the brief **without** another paid API call and without blocking chat.  
5. **Quality gate:** middle history is replaced with a brief pointer only when the brief retains real paths/decisions (fail-closed otherwise).  
6. **Mid-turn:** long tool chains re-slim before the next model call when fill is high.  
7. **`/compact`** forces a Session Brief pass + queues local enrichment.  

**Harness modes** (Settings):

| Mode | Behavior |
|------|----------|
| **Auto** | Enforce lean send-view + brief maintenance when thresholds say so |
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

## Living memory (the organism)

Remedy is not a session chatbot and not only a coder. Memory is how it *grows
with you* — tasks, goals, and life, not just the repo in focus.

Every turn it injects a small ranked block:

| Section | What it holds |
|---------|----------------|
| **Who you are** | Name, identity, traits |
| **Life & goals** | Family, rhythm, priorities, “don’t mention X” |
| **How we work together** | Tone, craft, design taste, corrections |
| **This chapter** | Decisions for the current project that survive compress |
| **Recalled for this turn** | Query-relevant facts/notes (kids vs ruff vs a deadline) |

Life facts stay **global**. Repo decisions stay **scoped** to that folder.
Corrections (“too generic”, “be blunt”) become durable manners. Taste from
design passes folds into the same Partner Memory so it is not a sidecar.

You still `/forget` a mistake and `/remember` a hard pin. Nothing here is a
second personality — it is the same local organism, denser.

**Dreams.** When Remedy has enough recent residue, a dream pass binds *their*
goals to *how I will show up* (`Toward ship 1.0: act first; verify before done`).
That is memory of the user, memory of the self, and a dream of the future —
not a transcript compress.

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
