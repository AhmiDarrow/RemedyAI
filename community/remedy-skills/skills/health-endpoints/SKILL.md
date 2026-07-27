---
name: health-endpoints
version: 1.0.0
description: >
  Add liveness vs readiness endpoints with appropriate dependency checks.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - backend
  - ops
kind: native
status: discovered
tools:
  - file_read
  - file_write
metadata:
  source: library
  library_id: health-endpoints
  official: true
  security_flags: []
---

# Health Endpoints

## Rules
1. Liveness = process healthy (cheap).
2. Readiness = can serve traffic.
3. Don't make liveness depend on flaky deps.
4. Document probe paths for deploy config.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
