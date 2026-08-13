---
name: self-inject
description: >
  Run one test-gated self-improvement round on the Remedy codebase: draft a change,
  snapshot the diff, gate it with tests only, apply (restart sidecar / rebuild SPA)
  on green or roll back on red, record in the ledger, and continue. Use when
  self-improve, hot-inject, self-dev, fix our own code, "improve yourself", or
  run the auto-improvement loop.
version: 1.0.0
author: Remedy
tags: [self-inject, self-dev, hot-inject, loop, auto-improve, remedy]
---

# Self-inject (Remedy improves Remedy)

## Goal

Close the loop on Remedy improving **this** repo. One round = draft a change,
**audit it with tests only**, apply it to the running product on green or roll it
back on red, record the result, and keep going. Never commit red; never leave the
tree or the running product broken.

## When to use

- User says self-improve, hot-inject, improve the codebase, fix our own code, self-dev.
- Triggered on command, or automatically by the sidecar scheduler (starts at
  boot when enabled). Organism learning ticks every ~60s with no user prompt;
  ruff self-heal waits for ~5 minutes of *user-turn* idle on a clean tree.

## Round steps (in order)

### 1. Orient + blast radius

```powershell
git status -sb
git log -5 --oneline
```

- Name which tree(s) the change touches: **Python sidecar** (`src/remedy/`) and/or
  **desktop SPA** (`desktop/src/`).
- Do a `change-safety` blast-radius pass before drafting. Bound the diff.

### 2. Draft

- Use normal `file_edit` / `file_write` tools. Keep the change minimal and focused.
- Do not touch generated/lock files unless the change requires it.

### 3. Snapshot (rollback point)

- Capture `git diff` before gating so a red round can be reverted exactly.

### 4. Gate — tests only

Run the verification for the changed tree(s):

| Tree | Gate |
|------|------|
| Python | `uv run pytest -q` (ruff/mypy as warnings, not hard fails) |
| Desktop | `cd desktop && npm test` |

Use the `self_inject.gate()` helper (driven by `project_fingerprint.suggest_verify`)
where available instead of typing commands by hand.

### 5. Apply or roll back

- **GREEN** → apply:
  - Python change → restart the sidecar so live source takes effect.
  - Desktop change → rebuild the SPA (`cd desktop && npm run build`) + re-resolve
    WebUI dir + hard-refresh, if the change must reach the WebUI.
- **RED** → roll back (`git checkout -- <paths>`), record the failure, **stop the round**.
  Do not retry-loop or silently keep a failing diff.

### 6. Record

- Append the outcome to the ledger: `~/.remedy/self_inject_ledger.jsonl`
  (diff-id / gate commands + exit codes / outcome / commit or revert ref).

### 7. Continue

- If the user asked for the loop, draft the next improvement and repeat.
- Otherwise return to idle (next round waits for the idle trigger or another command).

## Safety

| Do | Don't |
|----|--------|
| Test-gate every round | Commit a red diff |
| Snapshot before editing | Leave a broken working tree |
| Respect `instance_lock` / single serve | Restart mid-messenger-poll |
| Stop on first red | Silent retry loops / thrash |

## Related skills

- **self-dev-loop** — full orchestration (inject → gauntlet → soak → stress → ship)
- **change-safety** — blast radius before multi-file work
- **project-etiquette** — ship gates
- **gauntlet-security** · **soak-product** · **stress-suite**
