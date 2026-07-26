---
name: cache-invalidation
version: 1.0.0
description: >
  Design cache keys and invalidation to prevent stale reads and stampedes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - perf
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: cache-invalidation
  official: true
  security_flags: []
---

# Cache Invalidation

## Steps
1. Map read/write paths and consistency needs.
2. Key namespace including version/tenant/user when needed.
3. Invalidate or bump version on writes.
4. Stampede strategy (singleflight/soft TTL).
5. Document flush procedure for ops.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
