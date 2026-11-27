---
name: ship-release
description: After green verify, push and optionally create a GitHub release without rewriting green code.
version: 1.0.0
author: Remedy
tags: [git, ship, release, github]
---

# Ship / Release

## When to use
User asked to **push**, **ship**, **publish**, **tag**, or **gh release** after work is green (or mid-ship resume).

## Hard rules (refactor-only after green)
1. **Do not re-run pytest** unless you changed **source** (`.py`/`.ts`/`.c`/…).
2. **Do not rewrite green product code** just to feel busy — ship tools only.
3. Temp helpers → **`.remedy-build/tmp/`** only (never `_retag.py` at repo root).
4. Prefer **`run_python_file`** over `python -c` blobs.
5. Prefer **`git_status` → `git_push` → `gh_release`** over raw bash thrash.

## Steps
1. Confirm machine green (build ledger / last verify) or run verify **once**.
2. `git_status` — branch, dirty, remotes.
3. If dirty and intentional: commit only when user asked; otherwise push existing commits.
4. `git_push` (origin HEAD `-u` by default). Honor approval UI if prompted.
5. If goal includes release/tag: `gh_release` with `tag=vX.Y.Z` (notes auto if empty).
6. `ship_status` — confirm `ship_pushed` / `ship_released` URLs.
7. Short user summary: branch, remote, release URL. Stop.

## Do not
- Auth-probe loops (`gh auth login` spam) — fix token once, then retry push/release.
- Ledger noise: do not treat shell commands as “paths touched”.
- Claim DONE without push when the goal required ship.
