# Slash commands reference

Type `/` in the composer for autocomplete. Commands also work via  
`POST /api/sessions/{id}/command`.

| Command | Arguments | Description |
|---------|-----------|-------------|
| `/help` | `command?` | Command card, or help for one command |
| `/house-rules` | `/houserules` | Read the house rules you set |
| `/commands` | | Every command, grouped |
| `/new` | | New chat session |
| `/reset` | alias `/clear` | **Full reset** of this session (history, plans, brief, goals buffer, checkpoints, attachments). Same tab — does not open another. Durable `/remember` memory kept. |
| `/sessions` | | List recent sessions |
| `/model` | `name?` · aliases `/models`, `/m` | Show this conversation's model, list the others, or switch |
| `/memory` | `query` | Search memory |
| `/skills` | | List available skills |
| `/export` | | Export this session as `.txt` (desktop) |
| `/whoami` | | Show what Remedy knows about you |
| `/goals` | | List open goals |
| `/plans` | | List structured task plans |
| `/security-status` | aliases `/security`, `/secstatus` | Show partner control settings |
| `/stop` | alias `/abort` | Stop the reply being written |
| `/status` | | What this conversation is on and whether Remedy is working |
| `/think` | `off`·`low`·`medium`·`high` · alias `/thinking` | Show or set how much she thinks before answering |
| `/search` | `words` · alias `/find` | Find earlier conversations by what was said |
| `/rename` | `title` | Give this conversation a title |
| `/remember` | `text` | Save a fact to memory |
| `/forget` | `text` | Remove something from memory |
| `/goal` | `title` | Add a goal |
| `/plan` | `approve` · `cancel` · (empty = show latest) | Show the latest plan, approve it or cancel it |
| `/approve` | `id?` · alias `/yes` | Say yes to what Remedy is waiting to do |
| `/deny` | `id?` · alias `/no` | Say no to what Remedy is waiting to do |
| `/harness` | | Show this session's context figures |
| `/settings` | `filter?` · alias `/config` | List settings and their values |
| `/set` | `name value` | Change a setting (names from `/settings`) |
| `/get` | `name` | Show one setting |
| `/theme` | `name?` | Show or set the look |
| `/name` | `name?` | What she calls you |
| `/partner` | `name?` | Her name |
| `/persona` | `style?` | How she talks |
| `/language` | `code?` · alias `/lang` | The language of the menus |
| `/web` | `on`·`off` | Let her search and read the web |
| `/privacy` | `on`·`off` | Privacy mode |
| `/approvals` | `ask`·`auto`·`full` · alias `/mode` | When she asks before acting |
| `/scope` | `project`·`workspace`·`full` | Which folders her tools may use |
| `/effort` | `level?` | Reasoning effort, for models that take one |
| `/learning` | `on`·`off` | Let her write new skills from what works |
| `/vigil` | `on`·`off` | Night work while you are away |
| `/safeguards` | alias `/guards` | The four safeguards and their values |
| `/usage` | `days?` · alias `/cost` | Tokens used by each model round |
| `/context` | | How full this conversation's context is |
| `/providers` | | Model providers and which are connected |
| `/tools` | `filter?` | The tools she can use |
| `/todos` | | Her checklist in this conversation |
| `/routines` | | Her standing routines and when they next run |
| `/jobs` | alias `/children` | Background jobs she has running |
| `/notifications` | alias `/inbox` | What she left for you while you were away |
| `/connect` | `pause`·`resume` · alias `/phone` | Phone access |
| `/voice` | | Speaking and listening: what is installed and switched on |
| `/doctor` | alias `/diagnostics` | A health check of Remedy and this PC |
| `/version` | | The version that is running |
| `/update` | alias `/updates` | Check whether a newer version is out |

## Tips

- Prefer **F1** for the full owner’s manual; `/help` is the quick card.  
- Prefer natural language (“I prefer TypeScript”); `/remember` pins hard; `/forget` fixes mistakes.  
- `/commands` lists every command, grouped. Commands also work in Grove and on a paired phone.

## Related

- [Memory & harness](06-memory-and-harness) · [Shortcuts](12-reference-shortcuts)
