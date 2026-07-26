---
name: memory-leak-hunt
version: 1.0.0
description: >
  Find memory growth in long-running services via profiles and retained allocations.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - perf
  - debug
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: memory-leak-hunt
  official: true
  security_flags: []
---

# Memory Leak Hunt

## Steps
1. Reproduce growth under load.
2. Capture heap profiles (pprof/memray/clinic/etc.).
3. Look for unbounded caches and listener growth.
4. Fix with LRU/TTL/dispose.
5. Soak-test verification.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
