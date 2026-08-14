# Slash commands reference

Type `/` in the composer for autocomplete. Commands also work via  
`POST /api/sessions/{id}/command`.

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/help` | | Command card + tip to open full Help (**F1**) |
| `/new` | | New chat session |
| `/reset` | alias `/clear` | **Full reset** of this session (history, plans, brief, goals buffer, checkpoints, attachments). Same tab — does not open another. Durable `/remember` memory kept. |
| `/sessions` | | List recent sessions |
| `/models` | | Model picker guidance |
| `/thinking` | | Toggle thinking visibility |
| `/memory` | `query` | Search durable memory |
| `/remember` | `text` | Save a durable fact |
| `/forget` | `text` | Remove a matching Partner Memory fact |
| `/pin` | `text` | Pin a fact so it always injects |
| `/whoami` | | Profile / known facts + this-home census |
| `/stretch` | alias `/home` | Re-map this PC (hardware, tools, rooms, local ports) |
| `/goals` | | List life goals and the next action |
| `/goal` | `title` | Hold a life goal; takes one local step (Documents/Remedy Life) |
| `/plans` | | List structured task plans |
| `/plan` | `approve` · `new <title>` · (empty = show latest) | Show / create / approve a plan |
| `/compact` | `focus?` | Memory Harness compress → Session Brief |
| `/harness` | | Show Session Brief / stats |
| `/approve` | `id?` | Approve pending high-impact action |
| `/deny` | `id?` | Deny pending action |
| `/import` | `path` | Import folder of notes into memory |
| `/export` | | Export this session as `.txt` (desktop) |
| `/helper` | `topic` · `error <text>` · alias `/tip` | Offline help tips from the Helper worker |
| `/import-session` | `path?` | Import session from `.txt`/`.md` |
| `/skills` | | List available skills |
| `/handoff` | | List handoff notes |
| `/security-status` | | Show partner control settings |
| `/init` | `path?` | Project scan helpers / AGENTS.md |

## Tips

- Prefer **F1** for the full owner’s manual; `/help` is the quick card.  
- Prefer natural language (“I prefer TypeScript”); `/remember` pins hard; `/forget` fixes mistakes.  
- `/compact` before huge pastes or long tool traces.  

## Related

- [Memory & harness](06-memory-and-harness) · [Shortcuts](12-reference-shortcuts)
