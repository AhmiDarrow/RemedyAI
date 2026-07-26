---
name: graceful-shutdown
version: 1.0.0
description: >
  Implement SIGTERM-aware graceful shutdown and drain for servers/workers.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - reliability
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: graceful-shutdown
  official: true
  security_flags: []
---

# Graceful Shutdown

## Steps
1. Trap SIGTERM/SIGINT.
2. Stop accepting new work; drain in-flight with deadline.
3. Close pools/consumers cleanly.
4. Align with orchestrator grace period.
5. Test with kill signals.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
