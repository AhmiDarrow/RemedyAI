---
name: test-selection
version: 1.0.0
description: >
  Select and run the smallest high-value tests for the current change set.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - testing
  - ci
kind: native
status: discovered
tools:
  - bash_exec
  - file_read
metadata:
  source: library
  library_id: test-selection
  official: true
  security_flags: []
---

# Test Selection

## Steps
1. `git diff --name-only` (and staged) to list changed files.
2. Map to nearby tests by project convention.
3. Run targeted tests first for fast feedback.
4. Recommend full suite before merge/release.
5. Report failures with file:line and next debug step.

## Operating rules
- Prefer **read-only** exploration before edits.
- Show commands run and their outcomes.
- Ask before destructive git, production changes, or secret access.
- Never commit or print live secrets.
- Stop with a clear blocker list if environment tools are missing.

## Done when
The user's goal is met **or** you report exactly what remains blocked and why.
