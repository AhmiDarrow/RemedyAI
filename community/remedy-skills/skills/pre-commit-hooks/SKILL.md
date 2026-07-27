---
name: pre-commit-hooks
version: 1.0.0
description: >
  Configure pre-commit/husky hooks for format, lint, and optional secret scan.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - tooling
  - quality
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: pre-commit-hooks
  official: true
  security_flags: []
---

# Pre Commit Hooks

## Steps
1. Use existing hook system if any.
2. Fast checks on commit; heavy tests in CI.
3. Secret scan when available.
4. Document install for contributors.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
