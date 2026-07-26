---
name: branch-hygiene
version: 1.0.0
description: >
  Prune merged local branches, fetch --prune, and name a clean branch for the next task.
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
  library_id: branch-hygiene
  official: true
  security_flags: []
---

# Branch Hygiene

## Steps
1. `git fetch --prune` and `git branch -vv`.
2. List merged locals: `git branch --merged main` (or master).
3. Propose deletions only for merged branches; confirm unmerged deletes.
4. Suggest `fix/…`, `feat/…`, or `chore/…` names from the task.
5. Warn on detached HEAD or severely behind remote.

## Safety
No force-push to main/master; no hard reset of shared branches.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
