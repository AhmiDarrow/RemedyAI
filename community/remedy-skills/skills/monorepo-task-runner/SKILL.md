---
name: monorepo-task-runner
version: 1.0.0
description: >
  Fix monorepo task graphs (turbo/nx/pnpm) for filtered build/test.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - monorepo
  - tooling
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: monorepo-task-runner
  official: true
  security_flags: []
---

# Monorepo Task Runner

## Steps
1. Detect workspace tool.
2. Define dependency pipeline.
3. Filter to changed packages in CI.
4. Cache outputs correctly.
5. Document developer commands.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
