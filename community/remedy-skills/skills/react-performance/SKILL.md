---
name: react-performance
version: 1.0.0
description: >
  Fix common React performance issues after identifying hot components.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - react
  - perf
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: react-performance
  official: true
  security_flags: []
---

# React Performance

## Steps
1. Identify hot paths (profiler or obvious parent state churn).
2. Check inline objects/functions, context breadth, list keys.
3. Virtualize long lists when needed.
4. Colocate state; memo only where measured.
5. Validate with interaction timing.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
