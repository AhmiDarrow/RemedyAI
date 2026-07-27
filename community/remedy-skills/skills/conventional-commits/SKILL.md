---
name: conventional-commits
version: 1.0.0
description: >
  Propose or write Conventional Commit messages (feat/fix/docs/chore) matching the diff.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - git
  - docs
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: conventional-commits
  official: true
  security_flags: []
---

# Conventional Commits

## Format
`<type>(optional-scope): <description>` — imperative, ≤72 chars, no trailing period.

Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.

## Steps
1. Inspect staged/unstaged changes.
2. Split into logical commits if mixed concerns.
3. Body explains *why*; footers for `BREAKING CHANGE` and issue refs.
4. Commit only when the user wants a commit created.

## Note
Match repo conventions if they already use a close variant.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
