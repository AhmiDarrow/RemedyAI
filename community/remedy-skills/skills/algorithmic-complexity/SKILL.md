---
name: algorithmic-complexity
version: 1.0.0
description: >
  Find accidental quadratic patterns and propose better data structures.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - perf
kind: native
status: discovered
tools:
  - file_read
metadata:
  source: library
  library_id: algorithmic-complexity
  official: true
  security_flags: []
---

# Algorithmic Complexity

## Steps
1. Nested loops over large collections.
2. Estimate sizes.
3. Maps/sets/indexes/batching.
4. Time with larger fixtures.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
