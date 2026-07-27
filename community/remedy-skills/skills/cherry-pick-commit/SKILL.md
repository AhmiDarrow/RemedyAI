---
name: cherry-pick-commit
version: 1.0.0
description: >
  Cherry-pick specific commits onto the current branch with careful conflict resolution.
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
  library_id: cherry-pick-commit
  official: true
  security_flags: []
---

# Cherry Pick Commit

## Steps
1. Identify SHAs with `git log` / `git show`.
2. Ensure clean tree and correct target branch.
3. `git cherry-pick <sha>` (use `-x` on shared repos for audit trail).
4. Resolve conflicts; run related tests.
5. Summarize resulting history.

## Avoid
Cherry-picking merge commits unless `-m` parent is understood.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
