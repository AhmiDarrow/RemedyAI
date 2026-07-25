# Coding agency (Build-class power)

Remedy is a **local continuity system**. When paired with a frontier model (Grok 4.5, Claude, GPT, …), the **tools + mission loop** should let you finish multi-hour software work with the same *class* of agency as a strong IDE coding agent — without multi-agent theater.

You still talk to **one Remedy**.

## Tools for coding

| Tool | Use |
|------|-----|
| **`file_edit`** | Precise search/replace on existing files (prefer over rewriting whole files) |
| **`file_write`** | Create or fully overwrite a file |
| **`file_read`** | Read text (optional line offset/limit) |
| **`repo_search`** | Find code by pattern (ripgrep if installed, else built-in) |
| **`list_dir`** | Browse the project |
| **`bash_exec`** | Builds, tests, git (approval mode still applies) |
| **`job_run`** | Silent **explore** or **verify** job — returns a summary, not a second chat persona |
| **`mission_*`** | Durable checklist + verify for work-alone builds |
| **`web_fetch`** | Optional HTTP fetch — enable `web_tools_enabled: true` in config |

## Missions (work alone)

When you say **work alone** / **handle this on your own**, continuity steers Remedy to:

1. `mission_start` with a goal, steps, and `verify_command` (e.g. `pytest -q`)  
2. Implement with `file_edit` / `repo_search`  
3. `mission_update` as steps complete  
4. `mission_verify` before claiming done  
5. Fix and re-verify on failure  

## Optional web tools

In config (`~/.remedy/config.toml` / Settings store):

```toml
web_tools_enabled = true
```

Then `web_fetch` can load documentation URLs. Offline coding does not require this.

## What stays unique to Remedy

- **Partner Memory** and Session Brief across sessions  
- **Skills lifecycle** (probation → active, hard-won protection)  
- Local vision / ComfyUI  
- Desktop sessions, projects, and signed updates  

## Related

- [How Remedy works (continuity)](16-continuity-philosophy)  
- [Skills](07-skills)  
- [Memory & harness](06-memory-and-harness)  
