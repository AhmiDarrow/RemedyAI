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

### 2. Sidecar auto-restart + crash failsafe (implemented)

Python changes run *inside* the sidecar, which cannot cleanly kill-and-respawn its
own process. So the apply path is **marker-file + parent poller**:

1. On a green Python gate, `self_inject.py` writes
   `<home>/locks/self_inject_apply` containing a **full rollback payload**
   (`repo`, `head`, `changed` files, `untracked` files, `round_id`).
2. The Rust desktop polls for that marker (`self_inject_apply_poller` in
   `desktop/src-tauri/src/lib.rs`), removes it, and restarts the sidecar via the
   existing `start_sidecar` + `wait_for_health`.
3. **Failsafe:** if the restarted sidecar is unhealthy after `~45s` (the injected
   change crashed it), the poller rolls the change back with git
   (`checkout -- <changed>` + delete `untracked`) using the payload that survived
   on disk, then restarts once more. If still unhealthy, it **stops** (no restart
   storm) and logs the payload for investigation.

The marker survives a sidecar crash by design — the crashed process cannot roll
itself back, so the rollback payload lives on disk before the restart is requested.
This satisfies "relaunch after an injected change crashes → rollback → investigate
→ move forward".

Guard: restart only after a **green** gate; the poller never restarts the sidecar
unless the marker (written only on green) is present.

### 3. Desktop rebuild + relaunch (implemented for SPA rebuild)

- After a green desktop gate, `apply_or_rollback` runs `cd desktop && npm run build`
  to write `desktop/dist` so the static WebUI reflects the change. **A build failure
  is treated as red and rolls back** (shipping a broken frontend is worse than not
  applying).
- In Tauri dev the SPA is served by the Vite dev server (HMR), so a TS change is
  already live in the desktop window; the rebuild matters when the change must also
  reach the WebUI (`find_webui_dir` / `_mount_web_ui`). Re-resolve + hard-refresh
  still applies on serve restart (see the AGENTS.md WebUI parity section).

### 4. Idle trigger (5-minute)

- A lightweight scheduler in the sidecar that, when no user turn has arrived for `N`
  seconds (default 300) and self-inject is enabled, invokes `SelfInjectRound`.
  `self_inject.should_run_now()` gates on `REMEDY_SELF_INJECT` (or config
  `self_inject.enabled`) + the idle window; `REMEDY_SELF_INJECT_FORCE=1` bypasses
  the idle check.
- On-command path: the `self_inject_round` / `self_inject_status` tools registered by
  `agent_self_inject_tools.py`, and `skill_activate(skill="self-inject")`.

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

- **Sidecar restart is parent-mediated.** Implemented via a marker file + Rust poller
  (the sidecar never kills itself, so in-flight turns are not dropped). The marker
  carries the rollback payload so a crash on the injected code is recoverable by the
  parent. See §2.
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
