---
name: ts-strict-migration
version: 1.0.0
description: >
  Incrementally enable TypeScript strictness without a big-bang freeze.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - typescript
  - quality
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: ts-strict-migration
  official: true
  security_flags: []
---

# Ts Strict Migration

## Steps
1. List disabled strict flags.
2. Enable one flag at a time; keep build green.
3. Replace `any` with `unknown` + narrowing at boundaries.
4. Document remaining escapes.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
