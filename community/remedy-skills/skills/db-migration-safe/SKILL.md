---
name: db-migration-safe
version: 1.0.0
description: >
  Write or review DB migrations using expand/contract safety and rollback notes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - database
  - backend
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - bash_exec
metadata:
  source: library
  library_id: db-migration-safe
  official: true
  security_flags: []
---

# Db Migration Safe

## Rules
1. Add nullable → backfill → constrain (expand/contract).
2. Avoid long locks; batch big updates.
3. Don't drop columns still read by running app versions.
4. Document down migration or forward-fix.
5. Test up (and down if supported) on sample data.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
