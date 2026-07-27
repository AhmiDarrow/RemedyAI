---
name: coverage-gap
version: 1.0.0
description: >
  Find coverage gaps on changed critical code and add focused tests.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - testing
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
  - file_write
metadata:
  source: library
  library_id: coverage-gap
  official: true
  security_flags: []
---

# Coverage Gap

## Steps
1. Run coverage tooling if available.
2. Prioritize changed files with logic/auth/money/parsers.
3. Propose 1–3 high-value tests (not 100% line chasing).
4. Implement with clear assertions.
5. Re-run targeted coverage.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
