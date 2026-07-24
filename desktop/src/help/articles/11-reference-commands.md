# Slash commands reference

Type `/` in the composer for autocomplete. Commands also work via  
`POST /api/sessions/{id}/command`.

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/help` | | Command card + tip to open full Help (**F1**) |
| `/new` | | New chat session |
| `/sessions` | | List recent sessions |
| `/models` | | Model picker guidance |
| `/thinking` | | Toggle thinking visibility |
| `/memory` | `query` | Search durable memory |
| `/remember` | `text` | Save a durable fact |
| `/whoami` | | Profile / known facts |
| `/goals` | | List open goals |
| `/goal` | `title` | Add a goal |
| `/plans` | | List structured task plans |
| `/plan` | `approve` · `new <title>` · (empty = show latest) | Show / create / approve a plan |
| `/compact` | `focus?` | Memory Harness compress → Session Brief |
| `/harness` | | Show Session Brief / stats |
| `/approve` | `id?` | Approve pending high-impact action |
| `/deny` | `id?` | Deny pending action |
| `/import` | `path` | Import folder of notes into memory |
| `/export` | | Export this session as `.txt` (desktop) |
| `/import-session` | `path?` | Import session from `.txt`/`.md` |
| `/skills` | | List available skills |
| `/handoff` | | List handoff notes |
| `/init` | `path?` | Project scan helpers / AGENTS.md |

## Tips

- Prefer **F1** for the full owner’s manual; `/help` is the quick card.  
- `/remember` is better than hoping the model stores facts in chat alone.  
- `/compact` before huge pastes or long tool traces.  

## Related

- [Memory & harness](06-memory-and-harness) · [Shortcuts](12-reference-shortcuts)
