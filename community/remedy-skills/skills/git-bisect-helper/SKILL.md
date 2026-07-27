---
name: git-bisect-helper
version: 1.0.0
description: >
  Drive git bisect with a clear good/bad test command to find a regression-introducing commit.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - git
  - debug
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: git-bisect-helper
  official: true
  security_flags: []
---

# Git Bisect Helper

## Steps
1. Agree a **test command** (exit 0 = good).
2. `git bisect start` → mark bad HEAD → mark good known SHA.
3. Each step: run test → `git bisect good|bad`.
4. On finish: show culprit `git show`, then `git bisect reset`.
5. Propose fix or targeted revert.

## Tip
Automated tests beat manual clicking for bisect reliability.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
