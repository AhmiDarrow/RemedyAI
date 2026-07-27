---
name: bundle-size-check
version: 1.0.0
description: >
  Find JS bundle size regressions and propose splits or dependency cuts.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - frontend
  - perf
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: bundle-size-check
  official: true
  security_flags: []
---

# Bundle Size Check

## Steps
1. Production build; note sizes.
2. Analyzer if present (visualizer/bundle analyzers).
3. List heavy/duplicate deps.
4. Propose dynamic import and lighter alternatives.
5. Measure before/after.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
