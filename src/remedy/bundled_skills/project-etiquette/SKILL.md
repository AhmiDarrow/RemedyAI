---
name: project-etiquette
description: >
  Default ship discipline for any serious project — blast radius first, then
  fix, test, update code/docs, build, commit, wait for CI, publish only if green.
  Use when the user says ship, release, finish, or "test everything then
  update/build/commit/CI/PyPI".
version: 1.1.0
author: Remedy
tags: [quality, release, git, ci, docs, etiquette]
---

# Project etiquette (ship sequence)

## Why this exists

Shipping is a **pipeline of gates**, not a single “push and hope.”  
The same sequence works for **almost any project** (Python, Node, Rust, monorepos):

**Blast radius → Fix → Test → Update project → Update docs → Build → Commit → CI green → Publish**

Skip a gate only when the user **explicitly** says so (e.g. “docs later”, “no publish”).

## When to use

- User asks to finish / ship / release a change.
- Phrases like: “test everything, if it passes update everything, build, commit
  to CI, if it passes publish to PyPI.”
- Multi-hour builds where you must not leave the tree half-shipped.

## Portable gate chain

Execute **in order**. After any failure, stop advancement until that gate is green.

### 0. Blast radius (before large edits)

- Activate **change-safety** (`skill_activate(name=change-safety)`) when available.
- Name the surface, list coupled neighbors, plan paired checks + manual smoke for
  UI/chrome/messengers that unit tests miss.
- Prefer durable architecture over patch loops for known failure classes.

### 1. Fix / implement

- Complete the requested behavior.
- Prefer small, verifiable steps; keep the tree runnable.
- Note risks (migrations, breaking APIs, signing, secrets).

### 2. Test (hard gate)

- Discover how *this* repo tests (pytest, npm test, cargo test, `make check`, …).
- Run the **full** suite when the user asked for “everything”; otherwise run
  the subset that covers the change **plus** any project-required CI subset.
- Add or update tests when behavior changed.
- **Do not commit known-failing tests** unless the user wants a WIP commit.

### 3. Update the project

- Bump version **only when shipping a real release** (semver: patch for fixes, minor for features).
- **Do not bump version for docs-only fixes** (manuals, What's new catch-up, README typos, help sync).
  Commit as `docs:` on the current version; users get the notes on the next real release or from git/F1 after rebuild.
- Align version surfaces if the repo has more than one (package.json, Cargo.toml, …) **when you do bump**.
- Keep assets/config consistent with the change.

### 4. Update documentation

- User-visible change → CHANGELOG / release notes / help docs.
- Developer-facing change → README / architecture notes as the repo expects.
- Run any **docs sync/check** scripts the project provides.
- Docs should describe **what the user does**, not only internal renames.
- Docs-only → no version bump / PyPI / release tag unless the user explicitly asks.

### 5. Build

- Produce what CI/release expects (wheels, bundles, desktop artifacts).
- A green unit suite with a broken package build is **not** done.

### 6. Commit

- Stage only intentional files (no secrets, no local noise).
- Message: complete sentences; conventional prefix when it fits
  (`fix:`, `feat:`, `docs:`, `release:`).
- One logical theme per commit when practical.
- Note `Risk:` / `Smoke:` when fragile zones were involved.

### 7. Push and wait for CI

- Push to the integration branch the project uses (`main` / `master`).
- **Wait for CI to finish.** Report the run URL when available.
- Red CI → fix, commit, push again. **No publish while red.**

### 8. Publish (only if asked and CI is green)

- Tag release if the project uses tags for installers/changelogs.
- Publish packages (PyPI, npm, crates.io, …) only with existing credentials
  and project conventions.
- Confirm the published version is reachable (e.g. PyPI JSON, `npm view`).

## Response format (report back to the user)

After a ship sequence, summarize with a short table:

| Gate | Result |
|------|--------|
| Blast radius | surfaces + smokes run |
| Tests | pass / fail (+ command) |
| Docs | updated / n/a |
| Build | pass / fail |
| Commit | hash + subject |
| CI | green / red + URL |
| Publish | version + registry / skipped |

If blocked, state **which gate** and the **smallest next action**.

## Anti-patterns

- “It works on my machine” without running the project’s test command.
- Committing version bumps without changelog/docs.
- Publishing before CI finishes (or ignoring a red run).
- Force-pushing shared mainline branches.
- Leaving half a release without telling the user.
- Treating green CI as proof that title bar / live bots / WebView embeds work.

## Adapting to a new repo

1. Read README / CONTRIBUTING / AGENTS.md / CI workflow.
2. Map each gate to local commands (record them in the session brief if long-lived).
3. Prefer project scripts over inventing one-off publish paths.
4. Keep a **change-safety** checklist for this repo’s fragile zones.

---

## RemedyAI appendix (this monorepo)

Use when `cwd` is the RemedyAI tree (or user says “this project”).

| Gate | Remedy command / note |
|------|------------------------|
| Blast radius | Root `AGENTS.md` Change-safety protocol; skill **change-safety** |
| Test (Python) | `uv run pytest -q` |
| Test (desktop) | `cd desktop && npm test && npm run build` |
| Docs | Manuals in `docs/manual/`; `uv run python scripts/sync_help_manual.py`; gate `uv run python scripts/check_docs.py` |
| Version | `uv run python scripts/sync_version.py {X.Y.Z}` |
| Build wheel | `uv build` → `dist/remedy_ai-{ver}*` |
| Commit | Push `master` (or current integration branch) |
| CI | GitHub Actions workflow **CI** on that commit — wait for success |
| Publish PyPI | After CI green: `uv publish dist/remedy_ai-{ver}.*` (token via env / `~/.pypirc`) |
| Desktop installer | Tag `v{X.Y.Z}` → **desktop-release**; asset must be `Remedy.Desktop_{X.Y.Z}_x64-setup.exe` (see root `AGENTS.md`) |

Also respect root **`AGENTS.md`** (installer naming, version surfaces, smoke matrix).
For end-of-session continuity use skill **`session-handoff`**.

When **developing Remedy itself** (dogfood + gauntlet + soak + stress), activate
**`self-dev-loop`** first — it sequences **dogfood-isolated**, **gauntlet-security**,
**soak-product**, **stress-suite**, and this ship chain.
