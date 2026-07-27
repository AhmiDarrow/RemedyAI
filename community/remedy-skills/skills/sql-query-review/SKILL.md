---
name: sql-query-review
version: 1.0.0
description: >
  Review SQL/ORM usage for N+1, injection, and missing indexes.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - database
  - perf
  - security
kind: native
status: discovered
tools:
  - file_read
  - bash_exec
metadata:
  source: library
  library_id: sql-query-review
  official: true
  security_flags: []
---

# Sql Query Review

## Steps
1. Locate queries on the hot path.
2. Eliminate N+1 with join/prefetch.
3. Ensure parameterization — never string-built SQL with user input.
4. Suggest indexes with rationale (and EXPLAIN when available).
5. Avoid SELECT * on wide rows in hot paths.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
