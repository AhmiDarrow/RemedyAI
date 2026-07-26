---
name: datetime-timezone
version: 1.0.0
description: >
  Fix datetime bugs by storing UTC and converting only at the edge.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - quality
  - backend
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: datetime-timezone
  official: true
  security_flags: []
---

# Datetime Timezone

## Steps
1. UTC in storage/APIs internal.
2. Timezone-aware types only.
3. Convert for display in user TZ.
4. Tests around DST if critical.
5. Document behavior.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
