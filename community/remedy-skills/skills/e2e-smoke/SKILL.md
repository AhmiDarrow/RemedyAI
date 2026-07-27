---
name: e2e-smoke
version: 1.0.0
description: >
  Define or run a short end-to-end smoke path for the critical user journey.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - testing
  - e2e
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: e2e-smoke
  official: true
  security_flags: []
---

# E2E Smoke

## Steps
1. Identify the #1 user journey.
2. Use existing the browser test runner/browser tests/etc., or a minimal checklist/script.
3. Run against local/staging as documented.
4. Capture artifacts on failure.
5. State clearly what smoke does **not** prove.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
