---
name: idempotent-api
version: 1.0.0
description: >
  Make a mutating endpoint safely retryable with idempotency keys.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - api
  - reliability
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: idempotent-api
  official: true
  security_flags: []
---

# Idempotent Api

## Steps
1. Accept Idempotency-Key (or domain natural key).
2. Persist first response for a TTL.
3. Replay on duplicate; prevent double side effects under concurrency.
4. Tests for parallel duplicates.
5. Document client requirements.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
