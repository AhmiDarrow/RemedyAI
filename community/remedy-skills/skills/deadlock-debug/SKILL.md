---
name: deadlock-debug
version: 1.0.0
description: >
  Debug deadlocks via stack dumps and lock-order fixes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - debug
  - concurrency
kind: native
status: discovered
tools:
  - file_read
  - bash_exec
metadata:
  source: library
  library_id: deadlock-debug
  official: true
  security_flags: []
---

# Deadlock Debug

## Steps
1. Capture stacks of stuck processes.
2. Identify lock inversion / missing timeouts.
3. Fix ordering or redesign synchronization.
4. Add regression test if feasible.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
