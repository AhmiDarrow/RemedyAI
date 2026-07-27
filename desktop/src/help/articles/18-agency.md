# Coding agency (Build-class power)

Remedy is a **local continuity system**. When paired with a frontier model (Grok 4.5, Claude, GPT, …), the **tools + mission loop** should let you finish multi-hour software work with the same *class* of agency as a strong IDE coding agent — without multi-agent theater.

You still talk to **one Remedy**.

## Focus folder is optional

A **focus folder** (Settings project path / session workspace) only sets the **default cwd** for relative paths. It is **not** required to code.

| Mode | Behavior |
|------|----------|
| **No focus** | Default cwd is usually your home profile; use **absolute paths** for any tree |
| **Focus set** | Relative paths resolve there; **absolute paths still work** for other trees in access scope |

Access scope (`project` / `home` / `full`) is a **security** control, separate from “must open a project.”

## Tools for coding

| Tool | Use |
|------|-----|
| **`file_edit`** | Precise search/replace; multi-hunk via `edits=` JSON; prefer over full rewrites |
| **`file_edit_batch`** | Multi-file search/replace in one call |
| **`file_write`** | Create or fully overwrite a file |
| **`file_read`** | Read text (optional line offset/limit) |
| **`repo_search`** | Any text language; `symbol=` for definitions; `context_before`/`after`; absolute `path` for multi-tree |
| **`list_dir`** | Browse a directory (relative or absolute) |
| **`bash_exec`** | Builds, tests, git (approval mode still applies; cwd defaults to focus/home) |
| **`job_run`** | Silent **explore** or **verify** job — returns a summary, not a second chat persona |
| **`spread_run`** | Silent **fan-out** of several jobs in parallel (cover more ground) — one merged digest |
| **`mission_*`** | Durable checklist + verify for work-alone builds |
| **`web_fetch`** | Optional HTTP fetch — enable `web_tools_enabled: true` in config |

### Search (language-agnostic)

- **No extension allowlist.** GDScript, Zig, Rust, Makefiles, etc. are searchable without special config.
- Prefer **bundled or system `rg`** (ripgrep, MIT/Unlicense). Remedy can install a pinned build under `~/.remedy/bin`.
- Pure-Python fallback sniffs **text vs binary** (skips PNGs and other binaries).
- Zero matches include a **recovery hint** — re-scope path / simplify pattern; do not invent symbols.

### Shell and edits

- **`bash_exec`:** optional `timeout_seconds` (up to 600) and `workdir` for long Godot/cargo builds; local `.venv` / `node_modules/.bin` / repo-root tools are on `PATH`.
- **`file_edit`:** multi-hunk with `edits='[{"old_string":"…","new_string":"…"}]'` to cut round-trips.
- **Windows:** paths named `nul` / other reserved device names are rejected with a clear error (do not open them).

### Explore / verify jobs

- **`job_run kind=explore`:** tree sample + stack fingerprint + orientation pointers + optional search under `path=` (absolute OK).
- **`job_run kind=verify`:** runs a command (or fingerprint default) with local PATH and longer timeout. Same Ask-mode approval gate as `bash_exec`.
- **`job_run kind=diff`:** `git status` / `diff --stat` summary.

### Spread (parallel silent workers)

When a request spans **independent** modules/paths (or you say “in parallel” / “cover more ground”), Remedy can **fan out**:

- **`spread_run`** — runs several silent workers at once (explore / search / verify / diff / review), then returns **one merged digest** to the main agent.
- You still talk to **one Remedy** — workers are not separate chat personas.
- Workers are **depth-1** (they cannot spawn more workers).
- Most workers are **non-LLM** jobs (fast). Optional local Qwen only refines the plan or compresses long digests when the server is already up.
- Continuity may inject a **[Spread]** system hint when fan-out looks useful; pure chat and single-file edits do not spread.

Config (optional, under `~/.remedy/config.toml`):

```toml
[spread]
enabled = true
max_workers = 4
max_tasks = 6
use_local_plan = true
```

**When spread is faster:** independent branches and noisy surveys.  
**When it is not:** serial edit→test chains; always-on fan-out on every message (disabled by design).

## Run until finished (long coding)

Long coding / project turns use the **same operating model as a Build agent**: keep
using tools until the request is actually done — not until an arbitrary step count.

| Mechanism | Behavior |
|-----------|----------|
| **Soft epochs** | Every N model rounds: checkpoint + context compact, then **continue with tools** |
| **Absolute ceiling** | Very high safety net for pathological loops only (not a task budget) |
| **User abort** | Stop generation still ends the turn immediately |

Remedy does **not** force a final “tool limit” answer mid-mission. If the model
loops the same tools, it is nudged to change approach; idle pauses only after
many epochs with **zero** tool activity.

Optional env overrides (advanced):

```text
REMEDY_REACT_EPOCH_STEPS=256
REMEDY_REACT_MAX_TOTAL_STEPS=10000
REMEDY_REACT_AUTO_CONTINUE=1
REMEDY_REACT_MAX_STALE_EPOCHS=8
```

## Missions (work alone)

When you say **work alone** / **handle this on your own**, continuity steers Remedy to:

1. `mission_start` with a goal, steps, and `verify_command` (e.g. `pytest -q`) — if verify is omitted, stack fingerprint may suggest one  
2. Implement with `file_edit` / `repo_search`  
3. `mission_update` as steps complete  
4. `mission_verify` before claiming done (nudged when steps are done but verify has not passed)  
5. Fix and re-verify on failure  
6. Soft epochs compact context — the agent keeps going until verify passes / work is done  

Orientation: if the focus folder has `AGENTS.md`, `memory/LATEST_HANDOFF.md`, etc., Remedy surfaces short pointers automatically.

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
- Desktop sessions, optional focus folders, and signed updates  

## Related

- [How Remedy works (continuity)](16-continuity-philosophy)  
- [Skills](07-skills)  
- [Memory & harness](06-memory-and-harness)  
