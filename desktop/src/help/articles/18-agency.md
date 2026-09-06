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

Tool ABI ids (what the model calls on `:7400`):

| Tool | Use |
|------|-----|
| **`workspace.edit`** | Precise search/replace; multi-hunk via `edits=` JSON; CRLF + indent-tolerant unique hunks; failed hunks are not retried blindly |
| **`workspace.write`** | Create or fully overwrite a file |
| **`workspace.read`** | Read text (optional line offset/limit) |
| **`workspace.list`** | Browse a directory (relative or absolute) |
| **`workspace.search`** | Any text language; `symbol=` for definitions; context lines; absolute `path` for multi-tree |
| **`shell.exec`** | Host command (Windows = **cmd.exe**, not bash). POSIX strings are rewritten; PowerShell goes through a temp `.ps1` + `pwsh -File` |
| **`computer.*`** | Screenshot, click, type, navigate, and other GUI / Browser-rail actions |
| **`todo_write` / `todo_read`** | Short build checklist (pending → in_progress → completed). Shown in chat and crossed off as items finish. Do not claim done while open |
| **`build_drive`** | Machine-owned loop: lock spec → write failing TDD tests → isolated hops → gate tower → review-fix |
| **`build_parallel`** | Isolated overlays per unit; merge only if that unit’s oracle is green |
| **`apply_patch`** | Unified diff / Begin-Patch block through the write jail |
| **`build_review_fix`** | Second pass: TODO / bare except / syntax / missing tests → isolated hops |
| **`job_run`** | Silent **explore** or **verify** job — returns a summary, not a second chat persona |
| **`spread_run`** | Silent **fan-out** of several jobs in parallel (cover more ground) — one merged digest |
| **`mission_*`** | Durable checklist + verify for work-alone builds |
| **`web_fetch` / `web_search`** | Public HTTP fetch and search — on by default; `web_tools_enabled: false` to disable |
| **`skill.activate` / `skill.search`** | Load / discover procedure packs; scripts stay blocked until Trust |
| **`settings.get` / `settings.patch`** | Read or change jailed safe prefs (approval mode, UI, messenger enable flags) — never secrets |

### Review / implement (must use tools) — **0.20.0+**

Phrases like **“review project”**, **“implement the fix”**, **“run the tests”** stay in
**agency mode** (tools on). If the model only *narrates* “activating skill” without a
function call, Remedy **re-arms tools** and requires real `skill.activate` /
`workspace.list` / `workspace.read` / etc. — you should see process trail activity,
not a one-line promise.

Shell mutations stay inside **write roots** (project / home scope); opaque payloads
(`EncodedCommand`, WebClient download, certutil urlcache, …) fail closed when bound.

### Search (language-agnostic)

- **No extension allowlist.** GDScript, Zig, Rust, Makefiles, etc. are searchable without special config.
- Prefer **bundled or system `rg`** (ripgrep, MIT/Unlicense). Remedy can install a pinned build under `~/.remedy/bin`.
- Pure-Python fallback sniffs **text vs binary** (skips PNGs and other binaries).
- Zero matches include a **recovery hint** — re-scope path / simplify pattern; do not invent symbols.

### Shell and edits

- **`shell.exec`:** optional `timeout_seconds` (up to 600) and `workdir` for long Godot/cargo builds; local `.venv` / `node_modules/.bin` / repo-root tools are on `PATH`. On Windows the host is **cmd.exe**. `session=true` keeps cwd/env in a persistent session; `conpty=true` attaches a real console when the program needs a TTY.
- Prefer argv-style shell calls over quoted bash/PowerShell when the host dialect is unclear. Failed commands return a clear diagnostic (dialect / quoting / not-found / interactive) when one exists.
- **`workspace.edit`:** multi-hunk with `edits='[{"old_string":"…","new_string":"…"}]'` to cut round-trips. Unique hunks survive CRLF / trailing-space / leading-indent drift. The same failed hunk is refused a second time this turn — `workspace.read` and copy a real snippet.
- **Windows:** paths named `nul` / other reserved device names are rejected with a clear error (do not open them).

### Explore / verify jobs

- **`job_run kind=explore`:** tree sample + stack fingerprint + orientation pointers + optional search under `path=` (absolute OK).
- **`job_run kind=verify`:** runs a command (or fingerprint default) with local PATH and longer timeout. Same Ask-mode approval gate as `shell.exec`.
- **`job_run kind=diff`:** `git status` / `diff --stat` summary.

### Spread (parallel silent workers)

When a request spans **independent** modules/paths (or you say “in parallel” / “cover more ground”), Remedy can **fan out**:

- **`spread_run`** — runs several silent workers at once (explore / search / verify / diff / review), then returns **one merged digest** to the main agent.
  Pass `tasks` as a **JSON array** (native tool-call list) or a JSON string, or use `goal=` for auto-plan.
- You still talk to **one Remedy** — workers are not separate chat personas.
- Workers are **depth-1** (they cannot spawn more workers).
- Work that needs its own memory window is a [hive daughter](28-hive.md), not `spread_run`.
- Most workers are **non-LLM** jobs (fast). Optional local SmolVLM2 only refines the plan or compresses long digests when the server is already up.
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
| **Machine drive** | After explore thrash, Remedy starts TDD + hops itself (`build_drive`). On red verify it auto-runs repair hops — the model continues from those results, it does not restart |

Remedy does **not** force a final “tool limit” answer mid-mission. If the model
loops the same tools, it is nudged to change approach; idle pauses only after
many epochs with **zero** tool activity.

Optional env overrides (advanced):

```text
REMEDY_REACT_EPOCH_STEPS=256
REMEDY_REACT_MAX_TOTAL_STEPS=1000000
REMEDY_REACT_MAX_TOOL_CALLS=10000000
REMEDY_MAX_PARALLEL_TOOLS=32
REMEDY_REACT_MAX_STALE_EPOCHS=8
```

Soft epochs compact context and **continue**. While tools are making progress,
Remedy extends its own runway — a real build can run for as long as the project
needs. Only a pathological safety ceiling, repeated no-progress loops, or Stop
ends a coding turn — never a mid-mission “tool-call limit.”

## Missions (work alone)

When you say **work alone** / **handle this on your own**, continuity steers Remedy to:

1. `mission_start` with a goal, steps, and `verify_command` (e.g. `pytest -q`) — if verify is omitted, stack fingerprint may suggest one  
2. Implement with `workspace.edit` / `workspace.search`  
3. `mission_update` as steps complete  
4. `mission_verify` before claiming done (nudged when steps are done but verify has not passed)  
5. Fix and re-verify on failure  
6. Soft epochs compact context — the agent keeps going until verify passes / work is done  

Orientation: if the focus folder has `AGENTS.md`, `memory/LATEST_HANDOFF.md`, etc., Remedy surfaces short pointers automatically.

## Web tools

On by default after install. To keep Remedy offline:

```toml
web_tools_enabled = false
```

First run downloads a local OpenSERP (~10 MB) for `web_search`. Offline coding does not require the web.

## Computer use (browser rail + full desktop)

Remedy can **drive the GUI** on this PC with **in-house tools** (any chat model — not a vendor “computer use” beta).

| Surface | Tools | Notes |
|---------|--------|------|
| **In-app browser** | `computer_navigate`, `computer_snapshot`, `computer_click` (`ref` or x/y), type/key/scroll | Needs **Remedy Desktop** open (host). Rail opens automatically on navigate. |
| **Full desktop** | `computer_screenshot`, `computer_monitors`, `computer_click`, type/key, `computer_windows` | Works via local Win32 even without the rail. |

**Workflow (web):** navigate → **snapshot** (refs e1, e2, …) → **click ref=eN** → type if needed → screenshot to verify.  
**Workflow (native app):** **snapshot** (w1… windows, c1… UIA controls when comtypes is available) → **click ref=…** → type; or screenshot + x/y.  
**Multi-monitor:** `computer_monitors` then `computer_screenshot` with `monitor=0` (or 1, …).  
**Capture:** browser rail prefers WebView **PrintWindow** when found; else region crop; else full desktop.

Coding tools stay better for repo work. Computer use is for **GUI reality** files and shell cannot see. **Stop** cancels pending browser jobs and aborts mid-type.

Plan mode allows see/navigate/list only (no click/type). Build runs the full surface — no separate “enable computer use” gate when the task needs it.

Status bar **PC host** means the Desktop app is claiming browser jobs.

Local soak (feature branch only): [computer-use-soak.md](computer-use-soak.md).

## What stays unique to Remedy

- **Partner Memory** and Session Brief across sessions  
- **Skills lifecycle** (probation → active, hard-won protection)  
- Local vision / ComfyUI  
- Desktop sessions, optional focus folders, and signed updates  

## Related

- [How Remedy works (continuity)](16-continuity-philosophy)  
- [Skills](07-skills)  
- [Memory & harness](06-memory-and-harness)  
