# Hot-inject self-improvement loop (Remedy on Remedy)

**Status:** Design (Draft) — supersedes the removed isolated-dogfood (`dogfood-isolated`)
concept and the "dual instance" workflow. This document is the source of truth for the
build that follows.

## Problem

Remedy should be able to **improve its own code** — draft a fix or improvement against
`src/remedy/` (Python sidecar) or `desktop/src/` (TS/React SPA), run it through an
**automated, test-only audit gate**, and on green apply the change to the **running**
product and keep looping. The old approach (run a separate `tauri:dev:isolated` profile
side-by-side with release on `:7410`/`~/.remedy-dev`) was removed because it fought over
ports/homes and never closed the loop: it validated WIP in a separate window but never
auto-applied.

## Principles

- **Source of changes:** Remedy's own generated code (its normal `file_edit`/`file_write`
  tools). No external PR pipeline.
- **Audit gate:** full-auto, **tests only** — no human approval step. Green = apply;
  red = roll back and record. The loop must never leave the tree or the running product
  in a broken state.
- **Live effect:**
  - **Python sidecar change** → restart the sidecar (cheap; the dev build already runs
    live `.venv/Scripts/remedy.exe`).
  - **Desktop/TS change** → rebuild the SPA + relaunch (harder; staged and gated).
- **Trigger:** on command, or automatically after the product sits idle for ~5 minutes.
- **Always auditable:** every attempt (pass/fail, diff, gate output) persists to a result
  ledger; a bad change rolls back to a pre-edit snapshot.

## Loop stages

```text
[trigger: on-command | idle 5min]
        │
        v
  1. DRAFT   Remedy generates a change (file_edit/file_write) + names blast radius
        │
        v
  2. SNAPSHOT  git diff captured pre-gate (evidence / rollback point)
        │
        v
  3. GATE   run verification per changed tree:
              Python → uv run pytest -q (+ ruff, mypy)
              Desktop → npm test (+ tsc)
              both   → each tree's gate
        │
        ├─ RED ──► 4a. ROLLBACK  git checkout the change; log failure; STOP round
        │
        └─ GREEN ─► 4b. APPLY
                        │
                        ├─ Python-only → restart sidecar (live)
                        ├─ Desktop     → rebuild SPA + relaunch (full auto)
                        └─ both        → both of the above, sequentially
                              │
                              v
  5. LEDGER  persist result (commit hash / diff id / gate output / outcome)
        │
        v
  6. CONTINUE  → next round (draft a further improvement), or idle-return
```

## Components to build

### 1. `src/remedy/core/self_inject.py` — loop controller (new)

Pure-Python orchestrator with no dependency on the desktop shell.

- `SelfInjectRound` dataclass: `tree`, `edits`, `pre_diff`, `gate_cmds`, `passed`,
  `outcome` (`applied`|`rolled_back`|`skipped`), `commit`/`revert` refs.
- `snapshot()` → `git diff` (via existing `jobs.run_diff_job` semantics).
- `gate(tree)` → pick verify commands via `project_fingerprint.suggest_verify`
  (reuses `jobs.run_verify_job`).
- `apply_or_rollback()` → on green: `git add`/`commit` (auto); on red:
  `git checkout -- <paths>` (revert the round's edits).
- `record()` → append JSON line to the ledger (`~/.remedy/self_inject_ledger.jsonl`).
- `should_run()` → true if the last run is older than the idle threshold (default 300s)
  and the product is otherwise idle.

### 2. Sidecar auto-restart (Python change goes live)

- Add a **restart-self** path. Two options, prefer the lower-risk one:
  - **(a)** `self_inject.py` returns an IPC signal (`restart_requested`) that the desktop
    layer picks up and calls existing `shutdown_sidecar()` + `spawn_remedy()`
    (`desktop/src-tauri/src/lib.rs:699,468`).
  - **(b)** enable uvicorn `reload=True` in `cli.py:1632` **only** under a dev/self-inject
    flag — but AGENTS.md forbids dual pollers/locks; a supervised restart is safer than
    hot reload. Prefer (a).
- Guard: restart only after a **green** gate; never mid-messenger-poll (respect
  `instance_lock` single-process rule).

### 3. Desktop rebuild + relaunch (full auto)

- After a green desktop gate: `cd desktop && npm run build` (writes `desktop/dist`),
  then signal the desktop shell to reload the SPA. In Tauri dev the SPA is served by the
  Vite dev server (HMR), so a TS change is already live; a **rebuild is required only
  when the change must also reach the WebUI** (`find_webui_dir` / `_mount_web_ui`). Stage:
  rebuild dist → re-resolve `find_webui_dir` (serve restart) → hard-refresh.
- This is the "harder" path the user flagged; gate it behind the same green test gate.

### 4. Idle trigger (5-minute)

- A lightweight scheduler in the sidecar that, when no user turn has arrived for `N`
  seconds (default 300) and `REMEDY_SELF_INJECT` is enabled, invokes `SelfInjectRound`.
- On-command path: a dedicated tool / skill (`skill_activate(skill="self-inject")`) the
  agent calls to run one round explicitly.

### 5. `self-inject` skill (new, replaces `dogfood-isolated`)

Bundled SKILL.md that teaches the agent to run one self-improvement round: orient →
draft with `change-safety` → snapshot → gate (tests only) → apply/rollback → restart →
record → continue. This supersedes the dead `dogfood-isolated` skill (remove it).

## Audit guarantees

- **Never red-commit.** A round that fails the gate is reverted to the pre-round
  snapshot; no failing diff is committed or left in the working tree.
- **Bound the blast radius.** `change-safety` blast-radius pass before drafting; only
  changes confined to allowed modules pass the gate (mirror `check_shell_write_jail`).
- **Full ledger.** `~/.remedy/self_inject_ledger.jsonl` records every round with the
  diff-id, gate commands + exit codes, and outcome. Reviewable and replayable.
- **No dual ownership.** A self-inject restart never fights a messenger poller or a
  concurrent serve lock (`interfaces/instance_lock.py`).
- **Stops on red.** One failure ends the current round (no silent retry loop that could
  thrash the product).

## Open decisions / risks

- **How the desktop shell learns a Python restart is needed.** Prefer an IPC signal the
  Tauri layer already watches, rather than the sidecar killing itself (which could drop
  in-flight turns). See §2.
- **WebUI vs desktop parity after a TS rebuild.** Same `find_webui_dir` desync pitfall
  already documented in `AGENTS.md`; the rebuild+restart procedure must re-resolve and
  hard-refresh.
- **Ruff/mypy as gate vs pytest-only.** The user said "gated by tests only." Default the
  gate to `pytest` (+ `npm test`); run `ruff`/`mypy`/`tsc` as warnings, not hard fails,
  unless a change clearly warrants them. Configurable.
- **Idle detection.** "Idle" = no recent user turn + no in-flight job. Define precisely
  in `should_run()`; avoid firing mid-stream.

## Migration / cleanup

- Remove `dogfood-isolated` skill (bundled + `skills/`) — dead after isolated removal.
- Update `agent_context.py:313` auto-suggest: replace `dogfood-isolated` with
  `self-inject`.
- Update `self-dev-loop` SKILL.md references to `dogfood-isolated` → `self-inject`.
- Update `tests/test_bundled_skills.py` accordingly.
- Add tests: `tests/test_self_inject.py` (snapshot/gate/rollback/ledger, red-never-commit).
