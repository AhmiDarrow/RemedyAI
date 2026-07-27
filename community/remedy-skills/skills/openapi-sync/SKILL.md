---
name: openapi-sync
version: 1.0.0
description: >
  Regenerate or manually sync OpenAPI with implemented routes and flag breaking changes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - docs
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: openapi-sync
  official: true
  security_flags: []
---

# Openapi Sync

## Steps
1. Find OpenAPI source of truth (generated or hand-written).
2. Sync paths/schemas to code.
3. Diff for removals/renames → breaking.
4. Examples for public endpoints.
5. Note versioning strategy.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
