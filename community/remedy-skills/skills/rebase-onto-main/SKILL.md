---
name: rebase-onto-main
version: 1.0.0
description: >
  Update the current branch onto latest main/master via rebase or merge with conflict handling.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - git
kind: native
status: discovered
tools:
  - bash_exec
metadata:
  source: library
  library_id: rebase-onto-main
  official: true
  security_flags: []
---

# Rebase Onto Main

## Steps
1. Detect default branch (`main` or `master`).
2. Clean tree or stash with consent.
3. `git fetch` then rebase onto `origin/main` for private branches; merge if shared/preferred.
4. Resolve conflicts file-by-file; re-run tests; continue.
5. If history was rewritten and already pushed, warn that push needs `--force-with-lease` and approval.

## Never
Force-push the default branch.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
